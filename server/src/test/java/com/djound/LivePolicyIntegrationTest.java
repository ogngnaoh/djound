package com.djound;

import static org.junit.jupiter.api.Assertions.*;

import com.djound.availability.AvailabilityService;
import com.djound.generation.*;

import org.junit.jupiter.api.*;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.mock.web.MockMultipartFile;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.*;

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

    MockMultipartFile file(int samples) {
        byte[] wav = BackendIntegrationTest.wave(44100, 1, 16, 1);
        byte[] data = Arrays.copyOf(wav, 44 + samples * 2);
        ByteBuffer.wrap(data)
                .order(ByteOrder.LITTLE_ENDIAN)
                .putInt(4, data.length - 8)
                .putInt(40, samples * 2);
        return new MockMultipartFile("file", "source.wav", "audio/wav", data);
    }

    @Test
    void unavailableSourceRejectsCreateAndRetryBeforeAdmissionWithoutConsumingAllowance()
            throws Exception {
        mode("LIVE");
        provision();
        var controller = h.app.getBean(ApiController.class);
        assertNull(controller.workspace().get("activeAttemptId"));
        assertNull(controller.workspace().get("activeExplorationId"));
        var policy = (Map<?, ?>) controller.workspace().get("inputPolicy");
        int overhead = ((Number) policy.get("instructionOverheadCodePoints")).intValue();
        String example = new PromptAssembler().assemble("warm", "🎵").instructions();
        assertEquals(example.codePointCount(0, example.length()), overhead + 4 + 1);
        for (boolean unreadable : List.of(false, true)) {
            var source = h.source("unavailable-source-" + unreadable);
            var command = new Models.CreateRequestCommand("strings", null, 1L, 1L);
            String acceptedKey = "prior-source-request-" + unreadable;
            var accepted = h.service().create(source.explorationId(), command, acceptedKey);
            var active = controller.workspace();
            assertEquals(accepted.attemptId(), active.get("activeAttemptId"));
            assertEquals(source.explorationId(), active.get("activeExplorationId"));
            var owner = h.service().claimNext();
            h.service().fail(owner, "GENERATION_FAILED", "Local failure before any dispatch");
            assertNull(controller.workspace().get("activeAttemptId"));
            assertNull(controller.workspace().get("activeExplorationId"));
            h.explorations()
                    .saveDraft(
                            source.explorationId(),
                            new Models.DraftUpdate(
                                    "Keep these newer notes",
                                    source.originalRecordingId(),
                                    accepted.nextDraftRevision()));
            var before = h.explorations().get(source.explorationId());
            var allowance = h.app.getBean(LiveAllowance.class).view();
            long receiptCount =
                    h.jdbc().sql("SELECT count(*) FROM command_receipt").query(Long.class).single();
            long attemptCount =
                    h.jdbc()
                            .sql("SELECT count(*) FROM generation_attempt")
                            .query(Long.class)
                            .single();
            long requestCount =
                    h.jdbc()
                            .sql("SELECT count(*) FROM generation_request")
                            .query(Long.class)
                            .single();
            String storage =
                    h.jdbc()
                            .sql("SELECT storage_key FROM recording WHERE id=?")
                            .param(before.original().id())
                            .query(String.class)
                            .single();
            Path media = h.app.getBean(MediaStore.class).path(storage);
            byte[] bytes = Files.readAllBytes(media);
            Files.delete(media);
            // A directory at the expected immutable file path is deterministically unreadable
            // as audio, even on test hosts where chmod cannot remove the process's access.
            if (unreadable) Files.createDirectory(media);
            try {
                var createError =
                        assertThrows(
                                ApiProblem.class,
                                () ->
                                        h.service()
                                                .create(
                                                        source.explorationId(),
                                                        new Models.CreateRequestCommand(
                                                                "new words",
                                                                before.original().id(),
                                                                1L,
                                                                before.draft().revision()),
                                                        "missing-source-create-" + unreadable));
                var retryError =
                        assertThrows(
                                ApiProblem.class,
                                () ->
                                        h.service()
                                                .retry(
                                                        accepted.requestId(),
                                                        new Models.RetryCommand(owner.id()),
                                                        "missing-source-retry-" + unreadable));
                assertEquals("SOURCE_UNAVAILABLE", createError.code());
                assertEquals("SOURCE_UNAVAILABLE", retryError.code());
                assertEquals(503, createError.status);
                assertEquals(503, retryError.status);
                assertTrue(
                        h.service().create(source.explorationId(), command, acceptedKey).replayed(),
                        "An accepted receipt still replays before source availability checks");
                assertEquals(before, h.explorations().get(source.explorationId()));
                assertEquals(allowance, h.app.getBean(LiveAllowance.class).view());
                assertEquals(
                        receiptCount,
                        h.jdbc()
                                .sql("SELECT count(*) FROM command_receipt")
                                .query(Long.class)
                                .single());
                assertEquals(
                        attemptCount,
                        h.jdbc()
                                .sql("SELECT count(*) FROM generation_attempt")
                                .query(Long.class)
                                .single());
                assertEquals(
                        requestCount,
                        h.jdbc()
                                .sql("SELECT count(*) FROM generation_request")
                                .query(Long.class)
                                .single());
                assertEquals(
                        0L,
                        h.jdbc()
                                .sql("SELECT count(*) FROM generation_operation")
                                .query(Long.class)
                                .single());
                assertNull(h.service().activeAttemptId());
                assertNull(h.service().claimNext(), "Rejected work cannot be dispatched");
            } finally {
                Files.deleteIfExists(media);
                Files.write(media, bytes);
            }
        }
    }

    @Test
    void exactDurationAndReplayUsePreciseSecondsWithoutChangingMetadata() throws Exception {
        var over = file(44100 * 30 + 1);
        var old = h.explorations().create(over, "draft", "legacy-over-duration");
        assertEquals(30000, h.explorations().get(old.explorationId()).original().durationMs());
        mode("LIVE");
        assertTrue(h.explorations().create(over, "draft", "legacy-over-duration").replayed());
        var error =
                assertThrows(
                        ApiProblem.class,
                        () -> h.explorations().create(over, "draft", "new-over-duration"));
        assertEquals("PROVIDER_DURATION_UNSUPPORTED", error.code());
        var valid = h.explorations().create(file(44100 * 30), "draft", "exact-thirty-source");
        assertEquals(30000, h.explorations().get(valid.explorationId()).original().durationMs());
        assertEquals(
                "PROVIDER_DURATION_UNSUPPORTED",
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        h.service()
                                                .create(
                                                        old.explorationId(),
                                                        new Models.CreateRequestCommand(
                                                                "strings", null, 1L, 1L),
                                                        "old-long-admission"))
                        .code());
        assertEquals(
                "LIVE_ALLOWANCE_UNPROVISIONED",
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        h.service()
                                                .create(
                                                        valid.explorationId(),
                                                        new Models.CreateRequestCommand(
                                                                "strings", null, 1L, 1L),
                                                        "unprovisioned-request"))
                        .code());
        assertEquals(
                0L,
                h.jdbc().sql("SELECT count(*) FROM generation_request").query(Long.class).single());
        assertEquals(
                0L,
                h.jdbc()
                        .sql("SELECT count(*) FROM live_submission_reservation")
                        .query(Long.class)
                        .single());
    }

    @Test
    void assembledCodePointLimitPreservesDraftAndAdmissionReceiptSurvivesModeChange()
            throws Exception {
        mode("LIVE");
        provision();
        var source = h.source("live-text-source");
        int overhead =
                new PromptAssembler()
                                .assemble("", "x")
                                .instructions()
                                .codePointCount(
                                        0,
                                        new PromptAssembler()
                                                .assemble("", "x")
                                                .instructions()
                                                .length())
                        - 1;
        String exact = "🎵".repeat(500 - overhead);
        var tooLong = new Models.CreateRequestCommand(exact + "x", null, 1L, 1L);
        assertEquals(
                "ASSEMBLED_DIRECTION_TOO_LONG",
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        h.service()
                                                .create(
                                                        source.explorationId(),
                                                        tooLong,
                                                        "too-long-live-request"))
                        .code());
        assertEquals("draft", h.explorations().get(source.explorationId()).draft().text());
        assertEquals(
                0L,
                h.jdbc().sql("SELECT count(*) FROM generation_attempt").query(Long.class).single());
        var command = new Models.CreateRequestCommand(exact, null, 1L, 1L);
        var receipt = h.service().create(source.explorationId(), command, "exact-live-request");
        var attempt = h.service().claimNext();
        assertEquals("LIVE", attempt.request().provenance());
        h.app.getBean(LiveAllowance.class).reserve(attempt);
        h.service().fail(attempt, "OUTCOME_UNKNOWN", "test lost acknowledgement");
        mode("UNCONFIGURED");
        assertTrue(
                h.service()
                        .create(source.explorationId(), command, "exact-live-request")
                        .replayed());
        assertEquals("LIVE", h.service().unknownOutcome().provenance());
        assertEquals(receipt.attemptId(), h.service().unknownOutcome().attemptId());
    }

    @Test
    void allowanceIsInstallationBoundAppendOnlyAndNeverRefunded() throws Exception {
        mode("LIVE");
        provision();
        for (int i = 0; i < 2; i++) {
            var source = h.source("ledger-source-" + i);
            h.service()
                    .create(
                            source.explorationId(),
                            new Models.CreateRequestCommand("strings", null, 1L, 1L),
                            "ledger-request-" + i);
            var attempt = h.service().claimNext();
            try (var workers = java.util.concurrent.Executors.newVirtualThreadPerTaskExecutor()) {
                var gate = new java.util.concurrent.CountDownLatch(1);
                java.util.concurrent.Callable<Boolean> reserve =
                        () -> {
                            gate.await();
                            try {
                                h.app.getBean(LiveAllowance.class).reserve(attempt);
                                return true;
                            } catch (IllegalStateException duplicate) {
                                return false;
                            }
                        };
                var first = workers.submit(reserve);
                var second = workers.submit(reserve);
                gate.countDown();
                assertNotEquals(
                        first.get(),
                        second.get(),
                        "Exactly one concurrent reservation must commit");
            }
            assertThrows(
                    IllegalStateException.class,
                    () -> h.app.getBean(LiveAllowance.class).reserve(attempt));
            h.service().fail(attempt, "GENERATION_FAILED", "intentional local failure");
        }
        assertThrows(
                Exception.class,
                () -> h.jdbc().sql("DELETE FROM live_submission_reservation").update());
        assertThrows(
                Exception.class,
                () ->
                        h.jdbc()
                                .sql("UPDATE live_submission_allowance SET submission_limit=2")
                                .update());
        mode("LIVE");
        assertEquals(2L, h.app.getBean(LiveAllowance.class).view().get("used"));
        assertEquals(0L, h.app.getBean(LiveAllowance.class).view().get("remaining"));
        var source = h.source("exhausted-new-source");
        assertEquals(
                "LIVE_ALLOWANCE_EXHAUSTED",
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        h.service()
                                                .create(
                                                        source.explorationId(),
                                                        new Models.CreateRequestCommand(
                                                                "strings", null, 1L, 1L),
                                                        "exhausted-new-request"))
                        .code());
    }

    @Test
    void fixtureObservationsCannotAuthorizeOrEraseLiveCapacity() {
        h.app.getBean(AvailabilityService.class).setScenario("READY");
        mode("LIVE");
        var availability = h.app.getBean(AvailabilityService.class);
        assertEquals("UNKNOWN", availability.view().eligibility());
        assertFalse(availability.view().supportsStatusCheck());
        availability.observeCapacityFailure("LIVE");
        mode("FIXTURE");
        assertEquals("ELIGIBLE", h.app.getBean(AvailabilityService.class).view().eligibility());
        h.app.getBean(AvailabilityService.class).setScenario("READY");
        mode("LIVE");
        assertEquals("BLOCKED", h.app.getBean(AvailabilityService.class).view().eligibility());
    }

    @Test
    void consumedLiveEventBecomesUnknownOnRestartWithoutReobservation() throws Exception {
        mode("LIVE");
        provision();
        var source = h.source("consumed-event-source");
        var receipt =
                h.service()
                        .create(
                                source.explorationId(),
                                new Models.CreateRequestCommand("strings", null, 1L, 1L),
                                "consumed-event-request");
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
        mode("LIVE");
        provision();
        var source = h.source("startup-guard-source");
        h.service()
                .create(
                        source.explorationId(),
                        new Models.CreateRequestCommand("strings", null, 1L, 1L),
                        "startup-guard-request");
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
