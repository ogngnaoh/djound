package com.djound;

import static org.junit.jupiter.api.Assertions.*;

import com.djound.generation.*;

import org.flywaydb.core.Flyway;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.transaction.support.TransactionTemplate;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.nio.file.Path;
import java.sql.DriverManager;
import java.util.*;
import java.util.concurrent.*;

/** Real PostgreSQL operator authorization tests. No runner dispatch or provider requests. */
class AllowanceGrantIntegrationTest {
    @TempDir Path root;
    RestartIntegrationTest h;
    final ObjectMapper json = new ObjectMapper();

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

    String installation() {
        return h.jdbc()
                .sql("SELECT installation_id FROM workspace WHERE id=1")
                .query(String.class)
                .single();
    }

    JsonNode grant(
            String installation,
            String directory,
            String id,
            int count,
            String reason,
            boolean apply) {
        return json.readTree(
                h.jdbc()
                        .sql("SELECT grant_live_submissions(?,?,?,?,?,?)::text")
                        .params(installation, directory, id, count, reason, apply)
                        .query(String.class)
                        .single());
    }

    JsonNode grant(String id, int count, String reason, boolean apply) throws Exception {
        return grant(installation(), root.toRealPath().toString(), id, count, reason, apply);
    }

    LiveAllowance allowance() {
        return h.app.getBean(LiveAllowance.class);
    }

    @Test
    void freshGrantPreviewReplayAndReservationUseOnlyExplicitAuthorization() throws Exception {
        assertFalse((Boolean) allowance().view().get("provisioned"));
        assertEquals(0L, allowance().view().get("limit"));
        var preview = grant("operator-grant-one", 1, "Approved one local test submission", false);
        assertTrue(preview.path("preview").asBoolean());
        assertFalse(preview.path("applied").asBoolean());
        assertEquals(0, preview.path("before").path("limit").asInt());
        assertEquals(1, preview.path("after").path("limit").asInt());
        assertFalse((Boolean) allowance().view().get("provisioned"));
        assertEquals(
                0L,
                h.jdbc()
                        .sql("SELECT count(*) FROM live_submission_grant")
                        .query(Long.class)
                        .single());
        var applied = grant("operator-grant-one", 1, "Approved one local test submission", true);
        assertTrue(applied.path("applied").asBoolean());
        assertEquals(1L, allowance().view().get("limit"));
        assertEquals(1L, allowance().view().get("remaining"));
        assertEquals(
                0L,
                h.jdbc()
                        .sql("SELECT count(*) FROM live_submission_allowance")
                        .query(Long.class)
                        .single());
        var replay = grant("operator-grant-one", 1, "Approved one local test submission", true);
        assertTrue(replay.path("replayed").asBoolean());
        assertFalse(replay.path("applied").asBoolean());
        assertEquals(0, replay.path("additionalSubmissions").asInt());
        assertEquals(1L, allowance().view().get("limit"));

        var source = h.source("grant-only-original");
        var command = new Models.CreateRequestCommand("piano", null, 1L, 1L);
        var receipt = h.service().create(source.explorationId(), command, "grant-only-command");
        h.jdbc()
                .sql(
                        "UPDATE generation_request SET provenance='LIVE',"
                            + " adapter_configuration_id='melodyflow-anonymous-v1' WHERE id=?")
                .param(receipt.requestId())
                .update();
        var owner = h.service().claimNext();
        allowance().reserve(owner);
        h.service()
                .fail(owner, "GENERATION_FAILED", "Local test stops before any provider dispatch");
        assertEquals(0L, allowance().view().get("remaining"));
        assertEquals(1L, allowance().view().get("used"));
        assertTrue(
                h.service()
                        .create(source.explorationId(), command, "grant-only-command")
                        .replayed());
        assertEquals(
                receipt.attemptId(),
                h.explorations().get(source.explorationId()).latestAttempt().id());
        h.app.close();
        h.app = h.launch("UNCONFIGURED");
        h.stopScheduler();
        assertEquals(1L, allowance().view().get("limit"));
        assertEquals(1L, allowance().view().get("used"));
        assertEquals(0L, allowance().view().get("remaining"));
        assertEquals(
                0L,
                h.jdbc()
                        .sql("SELECT count(*) FROM generation_operation")
                        .query(Long.class)
                        .single());
    }

    @Test
    void grantsRejectChangedIdentityCountsAndBindingAndCannotBeMutated() throws Exception {
        String reason = "Owner's exact reason; 'quoted SQL' is data";
        grant("operator-grant-stable", 2, reason, true);
        assertThrows(Exception.class, () -> grant("operator-grant-stable", 3, reason, true));
        assertThrows(
                Exception.class, () -> grant("operator-grant-stable", 2, "changed reason", true));
        assertThrows(
                Exception.class,
                () ->
                        grant(
                                UUID.randomUUID().toString(),
                                root.toString(),
                                "wrong-installation",
                                1,
                                reason,
                                true));
        assertThrows(
                Exception.class,
                () ->
                        grant(
                                installation(),
                                root.resolve("other").toString(),
                                "wrong-directory",
                                1,
                                reason,
                                true));
        for (int count : List.of(0, -1))
            assertThrows(
                    Exception.class, () -> grant("invalid-count-" + count, count, reason, true));
        assertThrows(Exception.class, () -> grant("invalid-reason-id", 1, "   ", true));
        assertThrows(Exception.class, () -> grant("bad", 1, reason, true));
        assertThrows(
                Exception.class,
                () ->
                        h.jdbc()
                                .sql("UPDATE live_submission_grant SET additional_submissions=10")
                                .update());
        assertThrows(
                Exception.class, () -> h.jdbc().sql("DELETE FROM live_submission_grant").update());
        assertEquals(2L, allowance().view().get("limit"));
        assertEquals(
                1L,
                h.jdbc()
                        .sql("SELECT count(*) FROM live_submission_grant")
                        .query(Long.class)
                        .single());
        assertEquals(
                reason,
                h.jdbc()
                        .sql("SELECT reason FROM live_submission_grant")
                        .query(String.class)
                        .single());
        assertEquals(
                0L,
                h.jdbc().sql("SELECT count(*) FROM generation_attempt").query(Long.class).single());
        assertEquals(
                0L,
                h.jdbc()
                        .sql("SELECT count(*) FROM live_submission_reservation")
                        .query(Long.class)
                        .single());
    }

    @Test
    void concurrentSameGrantCommitsOnceAndDistinctGrantsAddExactly() throws Exception {
        try (var threads = Executors.newVirtualThreadPerTaskExecutor()) {
            var gate = new CountDownLatch(1);
            Callable<JsonNode> operation =
                    () -> {
                        gate.await();
                        return grant("concurrent-grant", 2, "Exact shared authorization", true);
                    };
            var first = threads.submit(operation);
            var second = threads.submit(operation);
            gate.countDown();
            assertNotEquals(
                    first.get().path("applied").asBoolean(),
                    second.get().path("applied").asBoolean());
        }
        grant("separate-explicit-grant", 1, "Separate authorization", true);
        assertEquals(3L, allowance().view().get("limit"));
        assertEquals(3L, allowance().view().get("remaining"));
        assertEquals(
                2L,
                h.jdbc()
                        .sql("SELECT count(*) FROM live_submission_grant")
                        .query(Long.class)
                        .single());
    }

    @Test
    void actualV6UpgradePreservesExhaustedBaselineAndAddsNothing() throws Exception {
        String schema = "grant_upgrade_" + UUID.randomUUID().toString().replace("-", "");
        try (var connection =
                        DriverManager.getConnection(
                                BackendIntegrationTest.BASE,
                                BackendIntegrationTest.USER,
                                BackendIntegrationTest.PASSWORD);
                var statement = connection.createStatement()) {
            statement.execute("CREATE SCHEMA " + schema);
        }
        String url =
                BackendIntegrationTest.BASE
                        + (BackendIntegrationTest.BASE.contains("?") ? "&" : "?")
                        + "currentSchema="
                        + schema;
        var ds =
                new DriverManagerDataSource(
                        url, BackendIntegrationTest.USER, BackendIntegrationTest.PASSWORD);
        var jdbc = JdbcClient.create(ds);
        try {
            Flyway.configure().dataSource(ds).target("6").load().migrate();
            String installation = UUID.randomUUID().toString();
            new TransactionTemplate(new DataSourceTransactionManager(ds))
                    .executeWithoutResult(
                            status -> {
                                jdbc.sql(
                                                "INSERT INTO"
                                                    + " workspace(id,installation_id,canonical_directory)"
                                                    + " VALUES(1,?,?)")
                                        .params(installation, root.toString())
                                        .update();
                                jdbc.sql(
                                                "INSERT INTO"
                                                    + " live_submission_allowance(installation_id,submission_limit)"
                                                    + " VALUES(?,2)")
                                        .param(installation)
                                        .update();
                                jdbc.sql(
                                                "INSERT INTO"
                                                    + " exploration(id,title,original_id,source_revision,draft_text,draft_basis_id,draft_revision,created_at,updated_at)"
                                                    + " VALUES('old-exp','old','old-source',1,'preserved"
                                                    + " notes','old-source',1,now(),now())")
                                        .update();
                                jdbc.sql(
                                                "INSERT INTO"
                                                    + " recording(id,exploration_id,title,kind,file_name,storage_key,content_type,byte_size,duration_ms,sample_rate,channels,codec,sha256,provenance,created_at)"
                                                    + " VALUES('old-source','old-exp','source','ORIGINAL','source.wav','old-file','audio/wav',100,1000,44100,1,'pcm_s16le','old-hash','ORIGINAL',now())")
                                        .update();
                                jdbc.sql(
                                                "INSERT INTO"
                                                    + " generation_request(id,exploration_id,source_recording_id,source_revision,basis_recording_id,submitted_text,inherited_direction,direction,assembled_instructions,assembler_version,adapter_configuration_id,provenance,created_at)"
                                                    + " VALUES('old-request','old-exp','old-source',1,'old-source','piano','','piano','piano','literal-v1','melodyflow-anonymous-v1','LIVE',now())")
                                        .update();
                                for (int i = 0; i < 2; i++) {
                                    jdbc.sql(
                                                    "INSERT INTO"
                                                        + " generation_attempt(id,request_id,exploration_id,state,created_at,updated_at)"
                                                        + " VALUES(?,'old-request','old-exp','FAILED',now(),now())")
                                            .param("old-attempt-" + i)
                                            .update();
                                    jdbc.sql(
                                                    "INSERT INTO"
                                                        + " live_submission_reservation(attempt_id,installation_id)"
                                                        + " VALUES(?,?)")
                                            .params("old-attempt-" + i, installation)
                                            .update();
                                }
                            });
            var before =
                    jdbc.sql("SELECT * FROM live_submission_reservation ORDER BY attempt_id")
                            .query()
                            .listOfRows();
            Flyway.configure().dataSource(ds).load().migrate();
            var view = new LiveAllowance(jdbc, new DataSourceTransactionManager(ds)).view();
            assertEquals(
                    Map.of("limit", 2L, "used", 2L, "remaining", 0L, "provisioned", true), view);
            assertEquals(
                    before,
                    jdbc.sql("SELECT * FROM live_submission_reservation ORDER BY attempt_id")
                            .query()
                            .listOfRows());
            assertEquals(
                    0L,
                    jdbc.sql("SELECT count(*) FROM live_submission_grant")
                            .query(Long.class)
                            .single());
            assertEquals(
                    "preserved notes",
                    jdbc.sql("SELECT draft_text FROM exploration").query(String.class).single());
            assertThrows(
                    Exception.class,
                    () ->
                            jdbc.sql("UPDATE live_submission_allowance SET submission_limit=2")
                                    .update());
            assertThrows(
                    Exception.class,
                    () -> jdbc.sql("DELETE FROM live_submission_reservation").update());
        } finally {
            try (var connection =
                            DriverManager.getConnection(
                                    BackendIntegrationTest.BASE,
                                    BackendIntegrationTest.USER,
                                    BackendIntegrationTest.PASSWORD);
                    var statement = connection.createStatement()) {
                statement.execute("DROP SCHEMA " + schema + " CASCADE");
            }
        }
    }
}
