package com.djound;

import static org.junit.jupiter.api.Assertions.*;

import com.djound.generation.*;

import org.junit.jupiter.api.*;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.boot.SpringApplication;
import org.springframework.context.ConfigurableApplicationContext;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.mock.web.MockMultipartFile;

import java.nio.file.*;
import java.sql.*;
import java.util.*;
import java.util.concurrent.*;

class GenerationIntegrationTest {
    @TempDir Path root;
    String schema, url;
    ConfigurableApplicationContext app;

    @BeforeEach
    void start() throws Exception {
        schema = "generation_" + UUID.randomUUID().toString().replace("-", "");
        try (var connection =
                        DriverManager.getConnection(
                                BackendIntegrationTest.BASE,
                                BackendIntegrationTest.USER,
                                BackendIntegrationTest.PASSWORD);
                var stmt = connection.createStatement()) {
            stmt.execute("CREATE SCHEMA " + schema);
        }
        url =
                BackendIntegrationTest.BASE
                        + (BackendIntegrationTest.BASE.contains("?") ? "&" : "?")
                        + "currentSchema="
                        + schema;
        app = launch("FIXTURE");
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
                "--djound.fixture-delay-ms=800",
                "--spring.main.banner-mode=off");
    }

    @AfterEach
    void stop() throws Exception {
        if (app != null) app.close();
        try (var connection =
                        DriverManager.getConnection(
                                BackendIntegrationTest.BASE,
                                BackendIntegrationTest.USER,
                                BackendIntegrationTest.PASSWORD);
                var stmt = connection.createStatement()) {
            stmt.execute("DROP SCHEMA " + schema + " CASCADE");
        }
    }

    Explorations explorations() {
        return app.getBean(Explorations.class);
    }

    GenerationService generations() {
        return app.getBean(GenerationService.class);
    }

    JdbcClient jdbc() {
        return app.getBean(JdbcClient.class);
    }

    Models.Receipt original(String key) throws Exception {
        return explorations()
                .create(
                        new MockMultipartFile(
                                "file",
                                "my source.wav",
                                "audio/wav",
                                BackendIntegrationTest.wave(44100, 1, 16, 1)),
                        "saved pre-click words",
                        key);
    }

    Models.CreateRequestCommand command(String text, long draft) {
        return new Models.CreateRequestCommand(text, null, 1L, draft);
    }

    Models.Attempt finished(String id) throws Exception {
        long end = System.nanoTime() + TimeUnit.SECONDS.toNanos(8);
        while (System.nanoTime() < end) {
            var attempt = generations().get(id);
            if (Set.of("FAILED", "SUCCEEDED").contains(attempt.state())) return attempt;
            Thread.sleep(30);
        }
        fail("Attempt did not become terminal");
        return null;
    }

    @Test
    void acceptedSnapshotProducesOneValidatedFixtureAndDoesNotEraseLaterNotes() throws Exception {
        var source = original("fixture-original-key");
        var before = explorations().get(source.explorationId()).original();
        var receipt =
                generations()
                        .create(
                                source.explorationId(),
                                command("  Exact click words 🎵\n", 1),
                                "fixture-request-key");
        assertEquals(1, receipt.consumedDraftRevision());
        assertEquals(2, receipt.nextDraftRevision());
        var draft = explorations().get(source.explorationId()).draft();
        assertEquals("", draft.text());
        explorations()
                .saveDraft(
                        source.explorationId(),
                        new Models.DraftUpdate("typing after click", null, 2L));
        assertEquals(
                "CREATION_ACTIVE",
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        explorations()
                                                .replaceSource(
                                                        source.explorationId(),
                                                        new MockMultipartFile(
                                                                "file",
                                                                "other.wav",
                                                                "audio/wav",
                                                                BackendIntegrationTest.wave(
                                                                        44100, 1, 16, 1)),
                                                        1,
                                                        "replace-while-active"))
                        .code());
        var completed = finished(receipt.attemptId());
        assertEquals("SUCCEEDED", completed.state());
        assertEquals("  Exact click words 🎵\n", completed.request().text());
        assertEquals(before.id(), completed.request().sourceRecordingId());
        assertEquals("FIXTURE", completed.request().provenance());
        assertEquals("literal-v1", completed.request().assemblerVersion());
        var detail = explorations().get(source.explorationId());
        assertEquals(1, detail.renditions().size());
        var result = detail.renditions().getFirst();
        assertEquals("FIXTURE", result.provenance());
        assertEquals(receipt.requestId(), result.requestId());
        assertEquals(receipt.attemptId(), result.attemptId());
        assertEquals(
                "90ad8f914e506d9ea2a76b006e950dacfa255785ecf6ff165a024d90b65e5fbc",
                result.sha256());
        assertEquals(before, detail.original());
        assertEquals("typing after click", detail.draft().text());
        assertEquals(3, detail.draft().revision());
        assertEquals(1, app.getBean(ScriptedAdapter.class).executionCount());
        assertEquals(
                1,
                jdbc().sql("SELECT execution_count FROM generation_attempt WHERE id=?")
                        .param(receipt.attemptId())
                        .query(Integer.class)
                        .single());
        assertNull(generations().activeAttemptId());
        assertEquals(1, explorations().list().getFirst().renditionCount());
    }

    @Test
    void concurrentExplorationsAdmitAndExecuteOnlyOneCommand() throws Exception {
        var first = original("race-first-original");
        var second = original("race-second-original");
        try (var pool = Executors.newVirtualThreadPerTaskExecutor()) {
            var gate = new CountDownLatch(1);
            Callable<String> a =
                    () -> {
                        gate.await();
                        try {
                            return generations()
                                    .create(
                                            first.explorationId(),
                                            command("first", 1),
                                            "race-first-request")
                                    .attemptId();
                        } catch (ApiProblem error) {
                            return error.code();
                        }
                    };
            Callable<String> b =
                    () -> {
                        gate.await();
                        try {
                            return generations()
                                    .create(
                                            second.explorationId(),
                                            command("second", 1),
                                            "race-second-request")
                                    .attemptId();
                        } catch (ApiProblem error) {
                            return error.code();
                        }
                    };
            var x = pool.submit(a);
            var y = pool.submit(b);
            gate.countDown();
            var outcomes = List.of(x.get(), y.get());
            assertEquals(1, outcomes.stream().filter("CREATION_ACTIVE"::equals).count());
            String id =
                    outcomes.stream()
                            .filter(value -> value.startsWith("attempt_"))
                            .findFirst()
                            .orElseThrow();
            assertEquals("SUCCEEDED", finished(id).state());
            assertEquals(1, app.getBean(ScriptedAdapter.class).executionCount());
            assertEquals(
                    1,
                    jdbc().sql("SELECT COUNT(*) FROM generation_request")
                            .query(Integer.class)
                            .single());
        }
    }

    @Test
    void replayPrecedesModeAndDraftChecksAndChangedBodyConflicts() throws Exception {
        var source = original("replay-original-key");
        var body = command("frozen words", 1);
        var accepted = generations().create(source.explorationId(), body, "replay-request-key");
        var replay = generations().create(source.explorationId(), body, "replay-request-key");
        assertTrue(replay.replayed());
        assertEquals(accepted.attemptId(), replay.attemptId());
        assertEquals(
                "IDEMPOTENCY_CONFLICT",
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        generations()
                                                .create(
                                                        source.explorationId(),
                                                        command("changed words", 1),
                                                        "replay-request-key"))
                        .code());
        finished(accepted.attemptId());
        app.close();
        app = launch("UNCONFIGURED");
        assertTrue(
                generations()
                        .create(source.explorationId(), body, "replay-request-key")
                        .replayed());
        assertEquals(
                "PROVIDER_NOT_CONFIGURED",
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        generations()
                                                .create(
                                                        source.explorationId(),
                                                        command("new request", 2),
                                                        "unconfigured-request"))
                        .code());
        assertEquals(0, app.getBean(ScriptedAdapter.class).executionCount());
    }

    @Test
    void failedConditionalDraftConsumptionRollsBackEveryAcceptanceRow() throws Exception {
        var source = original("rollback-original-key");
        jdbc().sql(
                        "CREATE FUNCTION refuse_consumption() RETURNS trigger LANGUAGE plpgsql AS"
                            + " $$ BEGIN IF NEW.draft_text='' THEN RETURN NULL; END IF; RETURN NEW;"
                            + " END $$")
                .update();
        jdbc().sql(
                        "CREATE TRIGGER refuse_consumption BEFORE UPDATE ON exploration FOR EACH"
                                + " ROW EXECUTE FUNCTION refuse_consumption()")
                .update();
        assertEquals(
                "STALE_DRAFT",
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        generations()
                                                .create(
                                                        source.explorationId(),
                                                        command("frozen", 1),
                                                        "rollback-request-key"))
                        .code());
        assertEquals(
                0,
                jdbc().sql("SELECT COUNT(*) FROM generation_request")
                        .query(Integer.class)
                        .single());
        assertEquals(
                0,
                jdbc().sql("SELECT COUNT(*) FROM generation_attempt")
                        .query(Integer.class)
                        .single());
        assertEquals(
                0,
                jdbc().sql(
                                "SELECT COUNT(*) FROM command_receipt WHERE"
                                        + " command_key='rollback-request-key'")
                        .query(Integer.class)
                        .single());
        assertNull(generations().activeAttemptId());
        assertEquals(
                "saved pre-click words", explorations().get(source.explorationId()).draft().text());
    }

    @Test
    void databaseConstraintsEnforceOneActiveAttemptAndOneOutputPerAttempt() throws Exception {
        var source = original("constraint-original");
        var accepted =
                generations()
                        .create(
                                source.explorationId(),
                                command("one output", 1),
                                "constraint-request");
        assertThrows(
                org.springframework.dao.DataIntegrityViolationException.class,
                () ->
                        jdbc().sql(
                                        "INSERT INTO"
                                            + " generation_attempt(id,request_id,exploration_id,state,created_at,updated_at)"
                                            + " SELECT"
                                            + " 'second_active',request_id,exploration_id,'ACCEPTED',created_at,updated_at"
                                            + " FROM generation_attempt WHERE id=?")
                                .param(accepted.attemptId())
                                .update());
        assertEquals("SUCCEEDED", finished(accepted.attemptId()).state());
        assertThrows(
                org.springframework.dao.DataIntegrityViolationException.class,
                () ->
                        jdbc().sql("UPDATE recording SET attempt_id=? WHERE id=?")
                                .params(accepted.attemptId(), source.originalRecordingId())
                                .update());
        assertNull(explorations().recording(source.originalRecordingId()).attemptId());
    }

    @Test
    void uncertainSubmissionFailureReleasesWorkspaceWithAcknowledgementRequired() throws Exception {
        app.getBean(org.springframework.scheduling.concurrent.ThreadPoolTaskScheduler.class)
                .shutdown();
        var source = original("failure-original-key");
        var accepted =
                generations()
                        .create(
                                source.explorationId(),
                                command("frozen before failure", 1),
                                "failure-request-key");
        var failing =
                new FixtureOperations(
                        jdbc(),
                        app.getBean(GenerationConfiguration.class),
                        app.getBean(FixtureExecutionSettings.class),
                        app.getBean(ScriptedAdapter.class)) {
                    @Override
                    public Operation submit(Models.Attempt attempt, long ordinal, String outcome) {
                        throw new IllegalStateException("submission response lost");
                    }
                };
        new GenerationRunner(
                        generations(),
                        failing,
                        app.getBean(PublicationStore.class),
                        app.getBean(FixtureCheckpoints.class),
                        app.getBean(LiveOperations.class),
                        app.getBean(com.djound.local.LocalMusic.class),
                        app.getBean(com.djound.local.LocalWorker.class))
                .tick();
        var attempt = finished(accepted.attemptId());
        assertEquals("FAILED", attempt.state());
        assertEquals("OUTCOME_UNKNOWN", attempt.outcome().code());
        assertEquals(attempt.id(), generations().unknownOutcome().attemptId());
        assertTrue(attempt.canRetry());
        assertNull(generations().activeAttemptId());
        assertTrue(explorations().get(source.explorationId()).renditions().isEmpty());
        assertEquals("frozen before failure", attempt.request().text());
    }

    @Test
    void literalAssemblerPreservesInstructionsAndEnforcesBounds() {
        var assembler = new PromptAssembler();
        var assembled = assembler.assemble("inherited\nDo not paraphrase", " newest 🎵 ");
        assertTrue(assembled.instructions().contains("inherited\nDo not paraphrase"));
        assertTrue(assembled.instructions().endsWith(" newest 🎵 "));
        assertThrows(ApiProblem.class, () -> assembler.assemble("", " "));
        assertThrows(ApiProblem.class, () -> assembler.assemble("", "a".repeat(4001)));
        assertThrows(ApiProblem.class, () -> assembler.assemble("b".repeat(15999), "new"));
        assertThrows(
                IllegalArgumentException.class,
                () -> new GenerationConfiguration("UNSUPPORTED", 2000));
    }

    @Test
    void keepIsExplicitIdempotentAndReplaysTheSameStoredAudioWithoutExecution() throws Exception {
        var source = original("keep-original-key");
        var accepted =
                generations()
                        .create(
                                source.explorationId(),
                                command("warm direction", 1),
                                "keep-request-key");
        var completed = finished(accepted.attemptId());
        var originalResult = explorations().recording(completed.resultRecordingId());
        String base = "http://127.0.0.1:" + app.getEnvironment().getProperty("local.server.port");
        try (var client = java.net.http.HttpClient.newHttpClient()) {
            for (String invalid : List.of("{}", "{\"kept\":null}")) {
                var response = patch(client, base, originalResult.id(), invalid);
                assertEquals(400, response.statusCode());
                assertTrue(response.body().contains("INVALID_REQUEST"));
            }
            var originalRejected =
                    patch(client, base, source.originalRecordingId(), "{\"kept\":true}");
            assertEquals(422, originalRejected.statusCode());
            assertTrue(originalRejected.body().contains("ORIGINAL_NOT_KEEPABLE"));
            var kept = patch(client, base, originalResult.id(), "{\"kept\":true}");
            assertEquals(200, kept.statusCode());
            var repeated = patch(client, base, originalResult.id(), "{\"kept\":true}");
            assertEquals(kept.body(), repeated.body());
            var saved = explorations().recording(originalResult.id());
            assertTrue(saved.kept());
            var summary = explorations().list().getFirst();
            assertEquals(1, summary.renditionCount());
            assertEquals(List.of(saved), summary.keptRecordings());
            var bytes =
                    client.send(
                            java.net.http.HttpRequest.newBuilder(
                                            java.net.URI.create(base + saved.mediaUrl()))
                                    .GET()
                                    .build(),
                            java.net.http.HttpResponse.BodyHandlers.ofByteArray());
            assertEquals(200, bytes.statusCode());
            assertEquals(
                    originalResult.sha256(),
                    HexFormat.of()
                            .formatHex(
                                    java.security.MessageDigest.getInstance("SHA-256")
                                            .digest(bytes.body())));
            assertArrayEquals(
                    Files.readAllBytes(
                            app.getBean(MediaStore.class)
                                    .path(explorations().storageKey(originalResult.id()))),
                    bytes.body());
            assertTrue(
                    generations()
                            .create(
                                    source.explorationId(),
                                    command("warm direction", 1),
                                    "keep-request-key")
                            .replayed());
            assertEquals(
                    200, patch(client, base, originalResult.id(), "{\"kept\":false}").statusCode());
            assertEquals(originalResult, explorations().recording(originalResult.id()));
            assertTrue(explorations().list().getFirst().keptRecordings().isEmpty());
            assertEquals(1, explorations().get(source.explorationId()).renditions().size());
            assertEquals(1, app.getBean(ScriptedAdapter.class).executionCount());
        }
    }

    private java.net.http.HttpResponse<String> patch(
            java.net.http.HttpClient client, String base, String id, String body) throws Exception {
        return client.send(
                java.net.http.HttpRequest.newBuilder(
                                java.net.URI.create(base + "/api/recordings/" + id))
                        .header("Content-Type", "application/json")
                        .method("PATCH", java.net.http.HttpRequest.BodyPublishers.ofString(body))
                        .build(),
                java.net.http.HttpResponse.BodyHandlers.ofString());
    }

    @Test
    void refinementSnapshotStillUsesOriginalAndSelectsSecondPredefinedFile() throws Exception {
        var source = original("basis-original-key");
        var first =
                generations()
                        .create(
                                source.explorationId(),
                                command("first exact direction", 1),
                                "basis-request-one");
        var result = finished(first.attemptId());
        explorations()
                .saveDraft(
                        source.explorationId(),
                        new Models.DraftUpdate(
                                "working refinement", result.resultRecordingId(), 2L));
        var second =
                generations()
                        .create(
                                source.explorationId(),
                                new Models.CreateRequestCommand(
                                        "then quieter", result.resultRecordingId(), 1L, 3L),
                                "basis-request-two");
        var pinned =
                explorations()
                        .saveDraft(
                                source.explorationId(),
                                new Models.DraftUpdate(
                                        "notes pinned to the first version",
                                        result.resultRecordingId(),
                                        4L));
        var completed = finished(second.attemptId());
        assertEquals(pinned, explorations().get(source.explorationId()).draft());
        assertFalse(explorations().get(source.explorationId()).canReplaceSource());
        assertEquals(source.originalRecordingId(), completed.request().sourceRecordingId());
        assertEquals("first exact direction", completed.request().inheritedDirection());
        assertEquals(
                "c98118d80a0e98da65019b0bd0aa5fa0fbca186d37a61389c945ed63dd1b0793",
                explorations().recording(completed.resultRecordingId()).sha256());
        assertEquals(2, app.getBean(ScriptedAdapter.class).executionCount());
    }
}
