package com.djound;

import static org.junit.jupiter.api.Assertions.*;

import com.djound.generation.*;

import org.junit.jupiter.api.*;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.boot.SpringApplication;
import org.springframework.context.ConfigurableApplicationContext;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.mock.web.MockMultipartFile;

import java.net.URI;
import java.net.http.*;
import java.nio.file.*;
import java.sql.*;
import java.util.*;
import java.util.concurrent.*;

class RecoveryIntegrationTest {
    @TempDir Path root;
    String schema, url;
    ConfigurableApplicationContext app;

    @BeforeEach
    void start() throws Exception {
        schema = "recovery_" + UUID.randomUUID().toString().replace("-", "");
        try (var connection =
                        DriverManager.getConnection(
                                BackendIntegrationTest.BASE,
                                BackendIntegrationTest.USER,
                                BackendIntegrationTest.PASSWORD);
                var statement = connection.createStatement()) {
            statement.execute("CREATE SCHEMA " + schema);
        }
        url =
                BackendIntegrationTest.BASE
                        + (BackendIntegrationTest.BASE.contains("?") ? "&" : "?")
                        + "currentSchema="
                        + schema;
        app = launch("FIXTURE", true);
    }

    ConfigurableApplicationContext launch(String mode, boolean controls) {
        return SpringApplication.run(
                DjoundApplication.class,
                "--server.port=0",
                "--spring.datasource.url=" + url,
                "--spring.datasource.username=" + BackendIntegrationTest.USER,
                "--spring.datasource.password=" + BackendIntegrationTest.PASSWORD,
                "--djound.data-dir=" + root,
                "--djound.mode=" + mode,
                "--djound.fixture-review-controls=" + controls,
                "--djound.fixture-delay-ms=250",
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
                var statement = connection.createStatement()) {
            statement.execute("DROP SCHEMA " + schema + " CASCADE");
        }
    }

    Explorations explorations() {
        return app.getBean(Explorations.class);
    }

    GenerationService generations() {
        return app.getBean(GenerationService.class);
    }

    FixtureReviewControls controls() {
        return app.getBean(FixtureReviewControls.class);
    }

    JdbcClient jdbc() {
        return app.getBean(JdbcClient.class);
    }

    MockMultipartFile audio(String filename) {
        return new MockMultipartFile(
                "file", filename, "audio/wav", BackendIntegrationTest.wave(44100, 1, 16, 1));
    }

    Models.Receipt source(String key) throws Exception {
        return explorations().create(audio("recovery.wav"), "initial words", key);
    }

    Models.CreateRequestCommand command(String text, long revision) {
        return new Models.CreateRequestCommand(text, null, 1L, revision);
    }

    Models.Attempt finished(String id) throws Exception {
        long end = System.nanoTime() + TimeUnit.SECONDS.toNanos(8);
        while (System.nanoTime() < end) {
            var attempt = generations().get(id);
            if (Set.of("SUCCEEDED", "FAILED").contains(attempt.state())) return attempt;
            Thread.sleep(25);
        }
        fail("Attempt did not finish");
        return null;
    }

    Models.Attempt firstFailure(Models.Receipt source, String key) throws Exception {
        controls().setOutcome(new Models.FixtureOutcomeControl("FAILURE"));
        var receipt =
                generations()
                        .create(
                                source.explorationId(),
                                command("frozen failing direction", 1),
                                key);
        var attempt = finished(receipt.attemptId());
        assertEquals("FAILED", attempt.state());
        return attempt;
    }

    HttpResponse<byte[]> request(String path, String method, String body) throws Exception {
        try (var client = HttpClient.newHttpClient()) {
            var request =
                    HttpRequest.newBuilder(
                            URI.create(
                                    "http://127.0.0.1:"
                                            + app.getEnvironment().getProperty("local.server.port")
                                            + path));
            if (body != null) request.header("Content-Type", "application/json");
            return client.send(
                    request.method(
                                    method,
                                    body == null
                                            ? HttpRequest.BodyPublishers.noBody()
                                            : HttpRequest.BodyPublishers.ofString(body))
                            .build(),
                    HttpResponse.BodyHandlers.ofByteArray());
        }
    }

    @Test
    void frozenFailureAndRetryReceiptsNeverConsumeLaterDraftOrPendingControl() throws Exception {
        var source = source("retry-source-key");
        var original = explorations().get(source.explorationId()).original();
        controls().setOutcome(new Models.FixtureOutcomeControl("FAILURE"));
        var body = command("exact initial request", 1);
        var accepted = generations().create(source.explorationId(), body, "retry-create-key");
        assertEquals("SUCCESS", controls().get().nextOutcome());
        controls().setOutcome(new Models.FixtureOutcomeControl("FAILURE"));
        assertTrue(
                generations().create(source.explorationId(), body, "retry-create-key").replayed());
        assertEquals("FAILURE", controls().get().nextOutcome());
        controls().setOutcome(new Models.FixtureOutcomeControl("SUCCESS"));
        var failed = finished(accepted.attemptId());
        assertEquals("GENERATION_FAILED", failed.outcome().code());
        assertTrue(failed.canRetry());
        assertNull(failed.resultRecordingId());
        assertTrue(explorations().get(source.explorationId()).renditions().isEmpty());
        var draft =
                explorations()
                        .saveDraft(
                                source.explorationId(),
                                new Models.DraftUpdate("new words after failure", null, 2L));
        var retryBody = new Models.RetryCommand(failed.id());
        var retry = generations().retry(failed.requestId(), retryBody, "accepted-retry-key");
        controls().setOutcome(new Models.FixtureOutcomeControl("FAILURE"));
        var replay = generations().retry(failed.requestId(), retryBody, "accepted-retry-key");
        assertTrue(replay.replayed());
        assertEquals(retry.attemptId(), replay.attemptId());
        assertEquals("FAILURE", controls().get().nextOutcome());
        var succeeded = finished(retry.attemptId());
        assertEquals("SUCCEEDED", succeeded.state());
        assertEquals(failed.id(), succeeded.previousAttemptId());
        assertEquals(failed.request(), succeeded.request());
        assertEquals(draft, explorations().get(source.explorationId()).draft());
        assertEquals(original, explorations().get(source.explorationId()).original());
        assertFalse(generations().get(failed.id()).canRetry());
        assertEquals(
                "STALE_ATTEMPT",
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        generations()
                                                .retry(
                                                        failed.requestId(),
                                                        retryBody,
                                                        "stale-retry-key"))
                        .code());
        assertEquals(
                "IDEMPOTENCY_CONFLICT",
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        generations()
                                                .retry(
                                                        failed.requestId(),
                                                        new Models.RetryCommand(
                                                                "different-attempt"),
                                                        "accepted-retry-key"))
                        .code());
        assertEquals(2, app.getBean(ScriptedAdapter.class).executionCount());
        app.close();
        app = launch("UNCONFIGURED", true);
        assertTrue(
                generations()
                        .retry(failed.requestId(), retryBody, "accepted-retry-key")
                        .replayed());
        assertEquals(draft, explorations().get(source.explorationId()).draft());
    }

    @Test
    void concurrentRetriesHaveOneAuthorityAndOneExecution() throws Exception {
        var source = source("concurrent-retry-source");
        var failed = firstFailure(source, "concurrent-failure");
        var draft =
                explorations()
                        .saveDraft(
                                source.explorationId(),
                                new Models.DraftUpdate("preserved next notes", null, 2L));
        try (var executor = Executors.newVirtualThreadPerTaskExecutor()) {
            var start = new CountDownLatch(1);
            Callable<String> retry =
                    () -> {
                        start.await();
                        try {
                            return generations()
                                    .retry(
                                            failed.requestId(),
                                            new Models.RetryCommand(failed.id()),
                                            "race-retry-" + UUID.randomUUID())
                                    .attemptId();
                        } catch (ApiProblem error) {
                            return error.code();
                        }
                    };
            var a = executor.submit(retry);
            var b = executor.submit(retry);
            start.countDown();
            var results = List.of(a.get(), b.get());
            assertEquals(1, results.stream().filter("STALE_ATTEMPT"::equals).count());
            var accepted =
                    results.stream()
                            .filter(value -> value.startsWith("attempt_"))
                            .findFirst()
                            .orElseThrow();
            assertEquals("SUCCEEDED", finished(accepted).state());
        }
        assertEquals(draft, explorations().get(source.explorationId()).draft());
        assertEquals(2, app.getBean(ScriptedAdapter.class).executionCount());
        assertEquals(
                2,
                jdbc().sql("SELECT COUNT(*) FROM generation_attempt")
                        .query(Integer.class)
                        .single());
    }

    @Test
    void sourceReplacementAndLaterRequestsRetireOldRetryAuthority() throws Exception {
        var source = source("replacement-source-key");
        var failed = firstFailure(source, "replacement-failure");
        explorations()
                .replaceSource(
                        source.explorationId(), audio("replacement.wav"), 1, "replacement-command");
        assertFalse(generations().get(failed.id()).canRetry());
        assertEquals(
                "SOURCE_CHANGED",
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        generations()
                                                .retry(
                                                        failed.requestId(),
                                                        new Models.RetryCommand(failed.id()),
                                                        "retired-retry-key"))
                        .code());
        var current = explorations().get(source.explorationId());
        var newer =
                generations()
                        .create(
                                source.explorationId(),
                                new Models.CreateRequestCommand(
                                        "new source direction",
                                        null,
                                        2L,
                                        current.draft().revision()),
                                "new-source-request");
        assertEquals("SUCCEEDED", finished(newer.attemptId()).state());
        assertEquals(
                "STALE_ATTEMPT",
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        generations()
                                                .retry(
                                                        failed.requestId(),
                                                        new Models.RetryCommand(failed.id()),
                                                        "newer-retired-key"))
                        .code());
    }

    @Test
    void replacementAndRetryRaceCannotRetargetAFrozenRequest() throws Exception {
        var source = source("race-replacement-source");
        var failed = firstFailure(source, "race-replacement-failure");
        try (var executor = Executors.newVirtualThreadPerTaskExecutor()) {
            var start = new CountDownLatch(1);
            var retry =
                    executor.submit(
                            () -> {
                                start.await();
                                try {
                                    return generations()
                                            .retry(
                                                    failed.requestId(),
                                                    new Models.RetryCommand(failed.id()),
                                                    "race-retry-command")
                                            .attemptId();
                                } catch (ApiProblem error) {
                                    return error.code();
                                }
                            });
            var replace =
                    executor.submit(
                            () -> {
                                start.await();
                                try {
                                    explorations()
                                            .replaceSource(
                                                    source.explorationId(),
                                                    audio("race new.wav"),
                                                    1,
                                                    "race-replace-command");
                                    return "REPLACED";
                                } catch (ApiProblem error) {
                                    return error.code();
                                }
                            });
            start.countDown();
            var retryResult = retry.get();
            var replaceResult = replace.get();
            if (replaceResult.equals("REPLACED")) {
                assertEquals("SOURCE_CHANGED", retryResult);
                assertEquals(2, explorations().get(source.explorationId()).sourceRevision());
            } else {
                assertTrue(
                        Set.of("CREATION_ACTIVE", "SOURCE_REPLACEMENT_UNAVAILABLE")
                                .contains(replaceResult));
                assertTrue(retryResult.startsWith("attempt_"));
                assertEquals("SUCCEEDED", finished(retryResult).state());
                assertEquals(
                        source.originalRecordingId(),
                        generations().get(retryResult).request().sourceRecordingId());
            }
        }
    }

    @Test
    void refinementFailureRetainsEarlierMusicAndRetryUsesFrozenOriginalAndBasis() throws Exception {
        var source = source("refinement-failure-source");
        var initial =
                generations()
                        .create(
                                source.explorationId(),
                                command("first direction", 1),
                                "refinement-first");
        var first = finished(initial.attemptId());
        var earlier = explorations().recording(first.resultRecordingId());
        controls().setOutcome(new Models.FixtureOutcomeControl("FAILURE"));
        var refinement =
                generations()
                        .create(
                                source.explorationId(),
                                new Models.CreateRequestCommand(
                                        "failed refinement", earlier.id(), 1L, 2L),
                                "refinement-failed");
        var nextDraft =
                explorations()
                        .saveDraft(
                                source.explorationId(),
                                new Models.DraftUpdate("keep later draft", earlier.id(), 3L));
        var failed = finished(refinement.attemptId());
        assertEquals("FAILED", failed.state());
        assertTrue(failed.canRetry());
        assertEquals(List.of(earlier), explorations().get(source.explorationId()).renditions());
        assertFalse(explorations().get(source.explorationId()).canReplaceSource());
        var retry =
                generations()
                        .retry(
                                failed.requestId(),
                                new Models.RetryCommand(failed.id()),
                                "refinement-retry");
        var completed = finished(retry.attemptId());
        assertEquals("SUCCEEDED", completed.state());
        assertEquals(failed.request(), completed.request());
        assertEquals(source.originalRecordingId(), completed.request().sourceRecordingId());
        assertEquals(earlier.id(), completed.request().basisRecordingId());
        assertEquals(nextDraft, explorations().get(source.explorationId()).draft());
        assertEquals(2, explorations().get(source.explorationId()).renditions().size());
        assertEquals(3, app.getBean(ScriptedAdapter.class).executionCount());
    }

    @Test
    void playbackFaultIsReadOnlyRestorableAndGatedAwayFromNormalMode() throws Exception {
        var source = source("playback-source-key");
        var accepted =
                generations()
                        .create(
                                source.explorationId(),
                                command("playback sample", 1),
                                "playback-request");
        var completed = finished(accepted.attemptId());
        var recording = explorations().recording(completed.resultRecordingId());
        var before = request(recording.mediaUrl(), "GET", null);
        assertEquals(200, before.statusCode());
        assertEquals("no-store", before.headers().firstValue("cache-control").orElseThrow());
        assertEquals(
                422,
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        controls()
                                                .setPlayback(
                                                        new Models.FixturePlaybackControl(
                                                                source.originalRecordingId(),
                                                                true)))
                        .status);
        controls().setPlayback(new Models.FixturePlaybackControl(recording.id(), true));
        var broken = request(recording.mediaUrl(), "GET", null);
        assertEquals(503, broken.statusCode());
        assertTrue(new String(broken.body()).contains("MEDIA_UNAVAILABLE"));
        assertEquals("no-store", broken.headers().firstValue("cache-control").orElseThrow());
        assertEquals(503, request(recording.mediaUrl(), "HEAD", null).statusCode());
        assertEquals(
                200,
                request("/api/media/" + source.originalRecordingId(), "GET", null).statusCode());
        controls().setPlayback(new Models.FixturePlaybackControl(recording.id(), false));
        assertArrayEquals(before.body(), request(recording.mediaUrl(), "GET", null).body());
        assertEquals(recording, explorations().recording(recording.id()));
        assertEquals(1, app.getBean(ScriptedAdapter.class).executionCount());
        controls().setPlayback(new Models.FixturePlaybackControl(recording.id(), true));
        controls().setOutcome(new Models.FixtureOutcomeControl("FAILURE"));
        app.close();
        app = launch("FIXTURE", false);
        assertEquals(404, request("/api/fixture/controls", "GET", null).statusCode());
        assertEquals(
                404,
                request("/api/fixture/controls/next-attempt", "PUT", "{\"outcome\":\"SUCCESS\"}")
                        .statusCode());
        assertEquals(
                404,
                request(
                                "/api/fixture/controls/playback",
                                "PUT",
                                "{\"recordingId\":\""
                                        + recording.id()
                                        + "\",\"unavailable\":false}")
                        .statusCode());
        var normal = request(recording.mediaUrl(), "GET", null);
        assertEquals(200, normal.statusCode());
        assertArrayEquals(before.body(), normal.body());
        assertTrue(
                normal.headers().firstValue("cache-control").orElseThrow().contains("immutable"));
        var next =
                generations()
                        .create(
                                source.explorationId(),
                                command("normal success ignores review failure", 2),
                                "normal-request");
        assertEquals("SUCCEEDED", finished(next.attemptId()).state());
        app.close();
        app = launch("UNCONFIGURED", true);
        assertEquals(404, request("/api/fixture/controls", "GET", null).statusCode());
        assertEquals(200, request(recording.mediaUrl(), "GET", null).statusCode());
    }
}
