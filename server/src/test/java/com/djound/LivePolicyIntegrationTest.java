package com.djound;

import static org.junit.jupiter.api.Assertions.*;

import com.djound.generation.*;

import org.junit.jupiter.api.*;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Path;

/** Real PostgreSQL, immutable local media, and zero provider traffic. Schedulers stay stopped. */
class LivePolicyIntegrationTest {
    @TempDir Path root;
    RestartIntegrationTest h;

    @BeforeEach
    void start() throws Exception {
        h = new RestartIntegrationTest();
        h.root = root;
        h.start();
    }

    @AfterEach
    void stop() throws Exception {
        h.stop();
    }

    void mode(String mode) {
        h.app.close();
        h.app = h.launch(mode);
        h.stopScheduler();
    }

    void provision() {
        h.jdbc()
                .sql(
                        "INSERT INTO live_submission_allowance(installation_id,submission_limit)"
                                + " SELECT installation_id,2 FROM workspace WHERE id=1")
                .update();
    }

    Models.CreationReceipt historicalRequest(String key) throws Exception {
        var source = h.source(key + "-source");
        var receipt =
                h.service()
                        .create(
                                source.explorationId(),
                                new Models.CreateRequestCommand("strings", null, 1L, 1L),
                                key);
        h.jdbc()
                .sql(
                        "UPDATE generation_request SET"
                            + " adapter_configuration_id='melodyflow-anonymous-v1',provenance='LIVE'"
                            + " WHERE id=?")
                .param(receipt.requestId())
                .update();
        return receipt;
    }

    @Test
    void springRejectsRetiredModeEvenWithAPreviouslyGrantedAllowance() {
        provision();
        h.app.close();
        h.app = null;
        var failure = assertThrows(Exception.class, () -> h.launch("LIVE"));
        Throwable cause = failure;
        while (cause.getCause() != null) cause = cause.getCause();
        assertTrue(cause.getMessage().contains("LIVE is retired"));
    }

    @Test
    void localRestartClosesUntouchedHostedWorkAndRejectsNewHostedRetries() throws Exception {
        provision();
        var receipt = historicalRequest("untouched-request");
        mode("LOCAL");
        var closed = h.service().get(receipt.attemptId());
        assertEquals("LIVE", closed.request().provenance());
        assertEquals("FAILED", closed.state());
        assertFalse(closed.canRetry());
        assertEquals("RETRY_CONTEXT_UNAVAILABLE", closed.outcome().code());
        assertEquals(
                0L,
                h.jdbc()
                        .sql("SELECT execution_count FROM generation_attempt WHERE id=?")
                        .param(closed.id())
                        .query(Long.class)
                        .single());
        assertEquals(0L, h.app.getBean(LiveAllowance.class).view().get("used"));
        assertNull(h.service().activeAttemptId());
        assertEquals(
                closed.id(),
                h.service()
                        .create(
                                closed.explorationId(),
                                new Models.CreateRequestCommand("strings", null, 1L, 1L),
                                "untouched-request")
                        .attemptId());

        // An ordinary provider failure used to allow a deliberate retry. Even this older row
        // cannot reenter the retired adapter under a new idempotency key.
        h.jdbc()
                .sql("UPDATE generation_attempt SET outcome_code='GENERATION_FAILED' WHERE id=?")
                .param(closed.id())
                .update();
        assertEquals(
                "RETRY_CONTEXT_UNAVAILABLE",
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        h.service()
                                                .retry(
                                                        receipt.requestId(),
                                                        new Models.RetryCommand(closed.id()),
                                                        "retired-retry-key"))
                        .code());
        assertEquals(
                1L,
                h.jdbc().sql("SELECT count(*) FROM generation_attempt").query(Long.class).single());
    }

    @Test
    void consumedLiveEventBecomesUnknownOnRestartWithoutReobservation() throws Exception {
        provision();
        var receipt = historicalRequest("consumed-event-request");
        var owner = h.service().claimNext();
        h.app.getBean(LiveAllowance.class).reserve(owner);
        h.jdbc()
                .sql(
                        "INSERT INTO"
                            + " generation_operation(id,attempt_id,adapter_configuration_id,provenance,event_id,observation_started_at)"
                            + " VALUES('saved-live-event',?,'melodyflow-anonymous-v1','LIVE','already-consumed',now())")
                .param(owner.id())
                .update();
        h.service().saveHandle(owner, "saved-live-event");
        mode("UNCONFIGURED");
        h.app.getBean(GenerationRunner.class).tick();
        var failed = h.service().get(receipt.attemptId());
        assertEquals("OUTCOME_UNKNOWN", failed.outcome().code());
        assertEquals(
                1L,
                h.jdbc()
                        .sql("SELECT count(*) FROM live_submission_reservation")
                        .query(Long.class)
                        .single());
        assertNull(h.service().activeAttemptId());
    }

    @Test
    void missingRetryFlagsFailBeforeRecoveryMutationButCompletedHistoryRemainsReadable()
            throws Exception {
        provision();
        historicalRequest("startup-guard-request");
        var owner = h.service().claimNext();
        h.app.getBean(LiveAllowance.class).reserve(owner);
        h.jdbc()
                .sql(
                        "INSERT INTO"
                            + " generation_operation(id,attempt_id,adapter_configuration_id,provenance,event_id,observation_started_at,result_url)"
                            + " VALUES('guard-saved-result',?,'melodyflow-anonymous-v1','LIVE','consumed-event',now(),'https://facebook-melodyflow.hf.space/gradio_api/file=/tmp/gradio/saved.wav')")
                .param(owner.id())
                .update();
        var saved = h.service().saveHandle(owner, "guard-saved-result");
        h.app.close();
        h.app = null;
        String property = "jdk.httpclient.redirects.retrylimit",
                previous = System.getProperty(property);
        System.clearProperty(property);
        try {
            assertThrows(Exception.class, () -> h.launch("UNCONFIGURED"));
            try (var connection =
                    java.sql.DriverManager.getConnection(
                            h.url, BackendIntegrationTest.USER, BackendIntegrationTest.PASSWORD)) {
                try (var query =
                        connection.prepareStatement(
                                "SELECT"
                                    + " a.state,a.version,a.operation_handle,o.result_url,w.active_attempt_id"
                                    + " FROM generation_attempt a JOIN generation_operation o ON"
                                    + " o.id=a.operation_handle CROSS JOIN workspace w WHERE"
                                    + " a.id=?")) {
                    query.setString(1, owner.id());
                    try (var row = query.executeQuery()) {
                        assertTrue(row.next());
                        assertEquals("RUNNING", row.getString("state"));
                        assertEquals(saved.version(), row.getLong("version"));
                        assertEquals("guard-saved-result", row.getString("operation_handle"));
                        assertTrue(row.getString("result_url").endsWith("saved.wav"));
                        assertEquals(owner.id(), row.getString("active_attempt_id"));
                    }
                }
                // Mark a terminal historical outcome locally; no transport is involved.
                try (var statement = connection.createStatement()) {
                    statement.executeUpdate(
                            "UPDATE generation_attempt SET"
                                + " state='FAILED',outcome_code='GENERATION_FAILED',outcome_detail='local"
                                + " terminal test' WHERE id='"
                                    + owner.id()
                                    + "'");
                    statement.executeUpdate(
                            "UPDATE workspace SET active_attempt_id=NULL WHERE id=1");
                }
            }
            h.app = h.launch("UNCONFIGURED");
            h.stopScheduler();
            assertEquals("FAILED", h.service().get(owner.id()).state());
            assertEquals("LIVE", h.service().get(owner.id()).request().provenance());
        } finally {
            if (previous == null) System.clearProperty(property);
            else System.setProperty(property, previous);
        }
    }
}
