package com.djound;

import static org.junit.jupiter.api.Assertions.*;

import com.djound.generation.*;

import org.junit.jupiter.api.*;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.boot.SpringApplication;
import org.springframework.context.ConfigurableApplicationContext;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.mock.web.MockMultipartFile;

import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.sql.*;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.*;

class RestartIntegrationTest {
    @TempDir Path root;
    String schema, url;
    ConfigurableApplicationContext app;

    @BeforeEach
    void start() throws Exception {
        schema = "restart_" + UUID.randomUUID().toString().replace("-", "");
        try (var c =
                        DriverManager.getConnection(
                                BackendIntegrationTest.BASE,
                                BackendIntegrationTest.USER,
                                BackendIntegrationTest.PASSWORD);
                var statement = c.createStatement()) {
            statement.execute("CREATE SCHEMA " + schema);
        }
        url =
                BackendIntegrationTest.BASE
                        + (BackendIntegrationTest.BASE.contains("?") ? "&" : "?")
                        + "currentSchema="
                        + schema;
        app = launch("FIXTURE");
        stopScheduler();
    }

    ConfigurableApplicationContext launch(String mode) {
        return SpringApplication.run(
                DjoundApplication.class,
                "--server.port=0",
                "--spring.datasource.url=" + url,
                "--spring.datasource.username=" + BackendIntegrationTest.USER,
                "--spring.datasource.password=" + BackendIntegrationTest.PASSWORD,
                "--djound.data-dir=" + root,
                "--djound.mode=" + mode,
                "--djound.fixture-review-controls=true",
                "--djound.fixture-delay-ms=0",
                "--spring.main.banner-mode=off");
    }

    void stopScheduler() {
        app.getBean(org.springframework.scheduling.concurrent.ThreadPoolTaskScheduler.class)
                .shutdown();
    }

    @AfterEach
    void stop() throws Exception {
        if (app != null) app.close();
        try (var c =
                        DriverManager.getConnection(
                                BackendIntegrationTest.BASE,
                                BackendIntegrationTest.USER,
                                BackendIntegrationTest.PASSWORD);
                var statement = c.createStatement()) {
            statement.execute("DROP SCHEMA " + schema + " CASCADE");
        }
    }

    JdbcClient jdbc() {
        return app.getBean(JdbcClient.class);
    }

    GenerationService service() {
        return app.getBean(GenerationService.class);
    }

    Explorations explorations() {
        return app.getBean(Explorations.class);
    }

    Models.Receipt source(String key) throws Exception {
        return explorations()
                .create(
                        new MockMultipartFile(
                                "file",
                                "restart.wav",
                                "audio/wav",
                                BackendIntegrationTest.wave(44100, 1, 16, 1)),
                        "draft",
                        key);
    }

    Models.Attempt unknown(Models.Receipt source) {
        app.getBean(FixtureReviewControls.class)
                .setOutcome(new Models.FixtureOutcomeControl("UNKNOWN"));
        var receipt =
                service()
                        .create(
                                source.explorationId(),
                                new Models.CreateRequestCommand("frozen uncertain", null, 1L, 1L),
                                "unknown-create-key");
        app.getBean(GenerationRunner.class).tick();
        var attempt = service().get(receipt.attemptId());
        assertEquals("OUTCOME_UNKNOWN", attempt.outcome().code());
        return attempt;
    }

    Models.Attempt awaitTerminal(String id) throws Exception {
        long end = System.nanoTime() + TimeUnit.SECONDS.toNanos(8);
        while (System.nanoTime() < end) {
            var a = service().get(id);
            if (Set.of("FAILED", "SUCCEEDED").contains(a.state())) return a;
            Thread.sleep(25);
        }
        throw new AssertionError("Recovery did not finish");
    }

    @Test
    void acknowledgementRollsBackWithAcceptanceAndReplaysAfterReminderClears() throws Exception {
        var source = source("ack-original-key");
        var unresolved = unknown(source);
        assertNull(service().savedHandle(unresolved.id()));
        assertEquals(
                1,
                jdbc().sql("SELECT COUNT(*) FROM fixture_operation").query(Integer.class).single());
        var noAck = new Models.CreateRequestCommand("next exact words", null, 1L, 2L);
        assertEquals(
                "REMOTE_OUTCOME_UNACKNOWLEDGED",
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        service()
                                                .create(
                                                        source.explorationId(),
                                                        noAck,
                                                        "missing-ack-key"))
                        .code());
        var body =
                new Models.CreateRequestCommand("next exact words", null, 1L, 2L, unresolved.id());
        jdbc().sql(
                        "CREATE FUNCTION refuse_ack_consumption() RETURNS trigger LANGUAGE plpgsql"
                            + " AS $$ BEGIN IF NEW.draft_text='' THEN RETURN NULL; END IF; RETURN"
                            + " NEW; END $$")
                .update();
        jdbc().sql(
                        "CREATE TRIGGER refuse_ack_consumption BEFORE UPDATE ON exploration FOR"
                                + " EACH ROW EXECUTE FUNCTION refuse_ack_consumption()")
                .update();
        assertEquals(
                "STALE_DRAFT",
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        service()
                                                .create(
                                                        source.explorationId(),
                                                        body,
                                                        "acknowledged-create"))
                        .code());
        assertEquals(unresolved.id(), service().unknownOutcome().attemptId());
        assertEquals(
                0,
                jdbc().sql(
                                "SELECT COUNT(*) FROM command_receipt WHERE"
                                        + " command_key='acknowledged-create'")
                        .query(Integer.class)
                        .single());
        jdbc().sql("DROP TRIGGER refuse_ack_consumption ON exploration").update();
        var accepted = service().create(source.explorationId(), body, "acknowledged-create");
        assertNull(service().unknownOutcome());
        assertTrue(
                service().create(source.explorationId(), body, "acknowledged-create").replayed());
        assertEquals(
                accepted.attemptId(),
                jdbc().sql("SELECT acknowledged_by_attempt_id FROM generation_attempt WHERE id=?")
                        .param(unresolved.id())
                        .query(String.class)
                        .single());
        assertEquals("OUTCOME_UNKNOWN", service().get(unresolved.id()).outcome().code());
        app.getBean(GenerationRunner.class).tick();
        assertEquals("SUCCEEDED", service().get(accepted.attemptId()).state());
        String legacy =
                "CREATE_REQUEST:"
                        + source.explorationId()
                        + "|"
                        + "frozen uncertain".length()
                        + ":frozen uncertain"
                        + "-1:"
                        + "1|1";
        String hash =
                HexFormat.of()
                        .formatHex(
                                java.security.MessageDigest.getInstance("SHA-256")
                                        .digest(legacy.getBytes(StandardCharsets.UTF_8)));
        assertEquals(
                hash,
                jdbc().sql(
                                "SELECT fingerprint FROM command_receipt WHERE"
                                        + " command_key='unknown-create-key'")
                        .query(String.class)
                        .single());
    }

    @Test
    void acknowledgedUnknownRetryKeepsDraftAndFrozenRequest() throws Exception {
        var source = source("unknown-retry-source");
        var previous = unknown(source);
        var draft =
                explorations()
                        .saveDraft(
                                source.explorationId(),
                                new Models.DraftUpdate("next notes must survive", null, 2L));
        assertEquals(
                "REMOTE_OUTCOME_UNACKNOWLEDGED",
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        service()
                                                .retry(
                                                        previous.requestId(),
                                                        new Models.RetryCommand(previous.id()),
                                                        "retry-without-ack"))
                        .code());
        var body = new Models.RetryCommand(previous.id(), previous.id());
        var retry = service().retry(previous.requestId(), body, "acknowledged-retry");
        assertNull(service().unknownOutcome());
        assertEquals(draft, explorations().get(source.explorationId()).draft());
        assertTrue(service().retry(previous.requestId(), body, "acknowledged-retry").replayed());
        app.getBean(GenerationRunner.class).tick();
        var done = service().get(retry.attemptId());
        assertEquals("SUCCEEDED", done.state());
        assertEquals(previous.request(), done.request());
        assertEquals(draft, explorations().get(source.explorationId()).draft());
    }

    @Test
    void operationWithoutSavedHandleIsUnknownAndNeverRedispatched() throws Exception {
        var source = source("missing-handle-source");
        var accepted =
                service()
                        .create(
                                source.explorationId(),
                                new Models.CreateRequestCommand("possibly submitted", null, 1L, 1L),
                                "missing-handle-request");
        var owner = service().claimNext();
        app.getBean(FixtureOperations.class).submit(owner, 0, "SUCCESS");
        app.close();
        app = launch("FIXTURE");
        var recovered = service().get(accepted.attemptId());
        assertEquals("OUTCOME_UNKNOWN", recovered.outcome().code());
        assertNull(service().savedHandle(recovered.id()));
        assertEquals(
                1,
                jdbc().sql("SELECT COUNT(*) FROM fixture_operation").query(Integer.class).single());
        assertEquals(0, app.getBean(ScriptedAdapter.class).executionCount());
        assertEquals(recovered.id(), service().unknownOutcome().attemptId());
    }

    @Test
    void savedHandleResumesObservationInUnconfiguredModeWithoutSubmission() throws Exception {
        var source = source("known-handle-source");
        var accepted =
                service()
                        .create(
                                source.explorationId(),
                                new Models.CreateRequestCommand("submitted once", null, 1L, 1L),
                                "known-handle-request");
        var owner = service().claimNext();
        var operation = app.getBean(FixtureOperations.class).submit(owner, 0, "SUCCESS");
        service().saveHandle(owner, operation.id());
        app.close();
        app = launch("UNCONFIGURED");
        var completed = awaitTerminal(accepted.attemptId());
        assertEquals("SUCCEEDED", completed.state());
        assertEquals(
                1,
                jdbc().sql("SELECT COUNT(*) FROM fixture_operation").query(Integer.class).single());
        assertEquals(0, app.getBean(ScriptedAdapter.class).executionCount());
    }

    @Test
    void journalRecoveryKeepsIdentityAndRejectsStaleFailureAndPublication() throws Exception {
        var source = source("journal-source");
        String oldStorage = explorations().storageKey(source.originalRecordingId());
        explorations()
                .replaceSource(
                        source.explorationId(),
                        new MockMultipartFile(
                                "file",
                                "new.wav",
                                "audio/wav",
                                BackendIntegrationTest.wave(48000, 1, 16, 1)),
                        1,
                        "journal-replacement");
        var accepted =
                service()
                        .create(
                                source.explorationId(),
                                new Models.CreateRequestCommand(
                                        "recover linked audio", null, 2L, 2L),
                                "journal-request");
        var submitting = service().claimNext();
        var operation = app.getBean(FixtureOperations.class).submit(submitting, 0, "SUCCESS");
        var saving = service().beginSaving(service().saveHandle(submitting, operation.id()));
        var media = app.getBean(MediaStore.class);
        String temporary = "result-" + UUID.randomUUID() + ".tmp",
                storage = UUID.randomUUID() + ".audio",
                recording = "rec_" + UUID.randomUUID();
        try (var in =
                new org.springframework.core.io.ClassPathResource("fixtures/audio/sample-warm.wav")
                        .getInputStream()) {
            Files.copy(in, media.temporaryPath(temporary));
        }
        var validated = media.validateFile(media.temporaryPath(temporary), "sample-warm.wav");
        media.link(validated.path(), storage);
        jdbc().sql(
                        "INSERT INTO"
                            + " publication_journal(attempt_id,attempt_version,phase,recording_id,temporary_name,storage_key,file_name,content_type,byte_size,sha256,duration_ms,sample_rate,channels,codec,created_at)"
                            + " VALUES(?,?,'LINKED',?,?,?,?,?,?,?,?,?,?,?,?)")
                .params(
                        saving.id(),
                        saving.version(),
                        recording,
                        temporary,
                        storage,
                        validated.fileName(),
                        validated.contentType(),
                        validated.bytes(),
                        validated.hash(),
                        validated.durationMs(),
                        validated.sampleRate(),
                        validated.channels(),
                        validated.codec(),
                        Timestamp.from(Instant.now()))
                .update();
        Path orphan = media.path(UUID.randomUUID() + ".audio");
        Files.writeString(orphan, "unreferenced");
        Path tempOrphan = root.resolve("temporary/upload-orphan.tmp");
        Files.writeString(tempOrphan, "unreferenced");
        var publications = app.getBean(PublicationStore.class);
        publications.cleanup();
        assertFalse(Files.exists(orphan));
        assertFalse(Files.exists(tempOrphan));
        assertTrue(Files.exists(media.path(oldStorage)));
        assertTrue(Files.exists(media.path(storage)));
        assertTrue(Files.exists(validated.path()));
        service().recoverInterrupted();
        var recovery = service().get(saving.id());
        assertEquals("RECONCILING", recovery.state());
        assertFalse(service().fail(saving, "GENERATION_FAILED", "stale callback"));
        assertThrows(StaleAttemptOwner.class, () -> publications.publish(saving, operation));
        var newOwner = service().beginSaving(recovery);
        publications.publish(newOwner, operation);
        assertEquals("SUCCEEDED", service().get(accepted.attemptId()).state());
        assertEquals(recording, service().get(accepted.attemptId()).resultRecordingId());
        assertEquals(storage, explorations().storageKey(recording));
        assertEquals(validated.hash(), explorations().recording(recording).sha256());
        assertFalse(service().fail(newOwner, "GENERATION_FAILED", "late callback"));
        assertThrows(StaleAttemptOwner.class, () -> publications.publish(newOwner, operation));
        assertTrue(Files.exists(media.path(storage)));
        assertEquals(
                0,
                jdbc().sql("SELECT COUNT(*) FROM publication_journal")
                        .query(Integer.class)
                        .single());
    }
}
