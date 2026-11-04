package com.djound;

import static org.junit.jupiter.api.Assertions.*;

import com.djound.availability.*;
import com.djound.generation.*;

import org.junit.jupiter.api.*;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.ConfigurableApplicationContext;
import org.springframework.context.annotation.*;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.mock.web.MockMultipartFile;

import java.nio.file.Path;
import java.sql.*;
import java.time.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicReference;

class AvailabilityIntegrationTest {
    static class MutableClock extends Clock {
        private final AtomicReference<Instant> now =
                new AtomicReference<>(Instant.parse("2026-11-03T12:00:00Z"));

        public ZoneId getZone() {
            return ZoneOffset.UTC;
        }

        public Clock withZone(ZoneId zone) {
            return this;
        }

        public Instant instant() {
            return now.get();
        }

        void advance(long millis) {
            now.updateAndGet(value -> value.plusMillis(millis));
        }
    }

    @TestConfiguration
    static class TimeConfiguration {
        @Bean
        @Primary
        MutableClock controlledClock() {
            return new MutableClock();
        }
    }

    @TempDir Path root;
    String schema, url;
    ConfigurableApplicationContext app;

    @BeforeEach
    void start() throws Exception {
        schema = "availability_" + UUID.randomUUID().toString().replace("-", "");
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
        app = launch();
    }

    ConfigurableApplicationContext launch() {
        var application = new SpringApplication(DjoundApplication.class, TimeConfiguration.class);
        var context =
                application.run(
                        "--server.port=0",
                        "--spring.datasource.url=" + url,
                        "--spring.datasource.username=" + BackendIntegrationTest.USER,
                        "--spring.datasource.password=" + BackendIntegrationTest.PASSWORD,
                        "--djound.data-dir=" + root,
                        "--djound.mode=FIXTURE",
                        "--djound.fixture-review-controls=true",
                        "--djound.fixture-delay-ms=0",
                        "--djound.fixture-cooldown-ms=1000",
                        "--spring.main.banner-mode=off");
        context.getBean(org.springframework.scheduling.concurrent.ThreadPoolTaskScheduler.class)
                .shutdown();
        return context;
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

    AvailabilityService capacity() {
        return app.getBean(AvailabilityService.class);
    }

    MutableClock clock() {
        return app.getBean(MutableClock.class);
    }

    JdbcClient jdbc() {
        return app.getBean(JdbcClient.class);
    }

    GenerationService generations() {
        return app.getBean(GenerationService.class);
    }

    Explorations explorations() {
        return app.getBean(Explorations.class);
    }

    FixtureReviewControls controls() {
        return app.getBean(FixtureReviewControls.class);
    }

    Models.Receipt source(String key) throws Exception {
        return explorations()
                .create(
                        new MockMultipartFile(
                                "file",
                                "capacity.wav",
                                "audio/wav",
                                BackendIntegrationTest.wave(44100, 1, 16, 1)),
                        "saved words",
                        key);
    }

    Models.CreateRequestCommand command(String text, long draft) {
        return new Models.CreateRequestCommand(text, null, 1L, draft);
    }

    void run() {
        app.getBean(GenerationRunner.class).tick();
    }

    @Test
    void actualCapacityFailureBlocksWithoutConsumingLaterDraftAndExpiresWithoutWrites()
            throws Exception {
        var source = source("capacity-source");
        controls().setOutcome(new Models.FixtureOutcomeControl("CAPACITY_UNAVAILABLE"));
        var body = command("frozen capacity probe", 1);
        var accepted = generations().create(source.explorationId(), body, "capacity-request");
        run();
        var failed = generations().get(accepted.attemptId());
        assertEquals("CAPACITY_UNAVAILABLE", failed.outcome().code());
        assertTrue(failed.canRetry());
        assertEquals("BLOCKED", capacity().view().eligibility());
        assertFalse(capacity().view().allowsSubmission());
        assertNull(capacity().view().providerResetAt());
        assertTrue(
                generations().create(source.explorationId(), body, "capacity-request").replayed());
        var draft =
                explorations()
                        .saveDraft(
                                source.explorationId(),
                                new Models.DraftUpdate("next draft", null, 2L));
        controls().setOutcome(new Models.FixtureOutcomeControl("FAILURE"));
        assertEquals(
                "CAPACITY_UNAVAILABLE",
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        generations()
                                                .retry(
                                                        failed.requestId(),
                                                        new Models.RetryCommand(failed.id()),
                                                        "blocked-retry"))
                        .code());
        assertEquals(draft, explorations().get(source.explorationId()).draft());
        assertEquals("FAILURE", controls().get().nextOutcome());
        var stored = jdbc().sql("SELECT * FROM workspace").query().singleRow();
        clock().advance(1000);
        assertEquals("UNKNOWN", capacity().view().eligibility());
        assertTrue(capacity().view().allowsSubmission());
        assertFalse(capacity().view().supportsStatusCheck());
        assertEquals(stored, jdbc().sql("SELECT * FROM workspace").query().singleRow());
        capacity().recheck();
        assertEquals(stored, jdbc().sql("SELECT * FROM workspace").query().singleRow());
        assertEquals(0, app.getBean(FixtureAvailabilityProbe.class).checkCount());
        var retry =
                generations()
                        .retry(
                                failed.requestId(),
                                new Models.RetryCommand(failed.id()),
                                "deliberate-retry");
        assertEquals(draft, explorations().get(source.explorationId()).draft());
        run();
        assertEquals("GENERATION_FAILED", generations().get(retry.attemptId()).outcome().code());
    }

    @Test
    void statusSuccessMalformedAndTimeoutRemainNonGeneratingAndRespectCooldown() {
        capacity().setScenario("STATUS_SUCCESS");
        clock().advance(1000);
        assertEquals("UNKNOWN", capacity().view().eligibility());
        assertFalse(capacity().view().allowsSubmission());
        assertTrue(capacity().view().canRecheck());
        var successful = capacity().recheck();
        assertEquals("ELIGIBLE", successful.eligibility());
        assertTrue(successful.allowsSubmission());
        assertFalse(successful.canRecheck());
        assertEquals("STATUS_CONFIRMED", successful.reasonCode());
        for (String scenario : List.of("STATUS_MALFORMED", "STATUS_TIMEOUT")) {
            capacity().setScenario(scenario);
            clock().advance(1000);
            var result = capacity().recheck();
            assertEquals("UNKNOWN", result.eligibility());
            assertEquals(scenario, result.reasonCode());
            assertFalse(result.allowsSubmission());
            assertFalse(result.canRecheck());
            assertEquals(
                    "RECHECK_TOO_SOON",
                    assertThrows(ApiProblem.class, () -> capacity().recheck()).code());
            clock().advance(1000);
            assertTrue(capacity().view().canRecheck());
            assertFalse(capacity().view().allowsSubmission());
        }
        assertEquals(0, app.getBean(ScriptedAdapter.class).executionCount());
        assertEquals(
                0,
                jdbc().sql("SELECT COUNT(*) FROM fixture_operation").query(Integer.class).single());
    }

    @Test
    void persistedBlockSurvivesRestartAndDoesNotPreventUploadingAnotherSource() throws Exception {
        capacity().setScenario("BLOCKED_NO_STATUS");
        var original = capacity().view();
        source("still-can-upload");
        app.close();
        app = launch();
        assertEquals(original, capacity().view());
        assertEquals(1, explorations().list().size());
        assertFalse(capacity().view().allowsSubmission());
    }

    @Test
    void staleRecheckCannotOverwriteAnOwnedCapacityFailure() throws Exception {
        var source = source("late-status-source");
        controls().setOutcome(new Models.FixtureOutcomeControl("CAPACITY_UNAVAILABLE"));
        var accepted =
                generations()
                        .create(
                                source.explorationId(),
                                command("accepted before status", 1),
                                "late-status-request");
        capacity().setScenario("STATUS_SUCCESS");
        clock().advance(1000);
        var entered = new CountDownLatch(1);
        var release = new CountDownLatch(1);
        var probe =
                new FixtureAvailabilityProbe() {
                    @Override
                    public String observe(String scenario) throws InterruptedException {
                        entered.countDown();
                        release.await();
                        return "ELIGIBLE";
                    }
                };
        var delayed =
                new AvailabilityService(
                        jdbc(),
                        app.getBean(
                                org.springframework.transaction.PlatformTransactionManager.class),
                        app.getBean(GenerationConfiguration.class),
                        clock(),
                        new AvailabilityConfiguration.Cooldown(1000),
                        probe,
                        app.getBean(Installation.class));
        try (var executor = Executors.newVirtualThreadPerTaskExecutor()) {
            var pending = executor.submit(delayed::recheck);
            assertTrue(entered.await(2, TimeUnit.SECONDS));
            assertTimeoutPreemptively(Duration.ofMillis(700), this::run);
            assertEquals(
                    "CAPACITY_UNAVAILABLE",
                    generations().get(accepted.attemptId()).outcome().code());
            release.countDown();
            assertEquals("BLOCKED", pending.get(2, TimeUnit.SECONDS).eligibility());
            assertEquals("CAPACITY_UNAVAILABLE", capacity().view().reasonCode());
        } finally {
            release.countDown();
        }
    }

    @Test
    void capacityRejectionPreservesUnknownAcknowledgementAndOneShotOutcome() throws Exception {
        var source = source("unknown-capacity-source");
        controls().setOutcome(new Models.FixtureOutcomeControl("UNKNOWN"));
        var unknown =
                generations()
                        .create(
                                source.explorationId(),
                                command("uncertain", 1),
                                "unknown-capacity-request");
        run();
        capacity().setScenario("BLOCKED_NO_STATUS");
        controls().setOutcome(new Models.FixtureOutcomeControl("SUCCESS"));
        var body =
                new Models.CreateRequestCommand(
                        "acknowledged probe", null, 1L, 2L, unknown.attemptId());
        assertEquals(
                "CAPACITY_UNAVAILABLE",
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        generations()
                                                .create(
                                                        source.explorationId(),
                                                        body,
                                                        "ack-capacity-request"))
                        .code());
        assertEquals(unknown.attemptId(), generations().unknownOutcome().attemptId());
        assertEquals("SUCCESS", controls().get().nextOutcome());
        assertEquals(
                0,
                jdbc().sql(
                                "SELECT COUNT(*) FROM command_receipt WHERE"
                                    + " command_key='ack-capacity-request'")
                        .query(Integer.class)
                        .single());
        assertEquals(
                false,
                app.getBean(ApiController.class).workspace().get("canSubmitWithAcknowledgement"));
        clock().advance(1000);
        assertEquals(
                true,
                app.getBean(ApiController.class).workspace().get("canSubmitWithAcknowledgement"));
        var accepted = generations().create(source.explorationId(), body, "ack-capacity-request");
        assertNull(generations().unknownOutcome());
        assertTrue(
                generations()
                        .create(source.explorationId(), body, "ack-capacity-request")
                        .replayed());
        run();
        assertEquals("SUCCEEDED", generations().get(accepted.attemptId()).state());
    }

    @Test
    void testCooldownOverrideIsRejectedOutsideFixtureMode() {
        assertThrows(
                IllegalArgumentException.class,
                () ->
                        new AvailabilityConfiguration()
                                .availabilityCooldown(
                                        "1000", new GenerationConfiguration("UNCONFIGURED", 0)));
    }
}
