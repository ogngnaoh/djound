package com.djound.generation;

import static org.junit.jupiter.api.Assertions.*;

import com.djound.*;
import com.sun.net.httpserver.HttpServer;

import org.junit.jupiter.api.*;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.boot.SpringApplication;
import org.springframework.context.ConfigurableApplicationContext;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.transaction.PlatformTransactionManager;

import java.net.*;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.sql.*;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.atomic.AtomicInteger;

/** End-to-end durable operations and publication with a loopback-only protocol peer. */
class LiveOperationsIntegrationTest {
    @TempDir Path root;
    String schema, url;
    ConfigurableApplicationContext app;
    HttpServer peer;
    URI origin;
    AtomicInteger posts = new AtomicInteger(),
            observations = new AtomicInteger(),
            downloads = new AtomicInteger();
    byte[] output;
    static final String BASE =
            System.getenv()
                    .getOrDefault("DJOUND_TEST_DB_URL", "jdbc:postgresql://127.0.0.1:55432/djound");
    static final String USER = System.getenv().getOrDefault("DJOUND_DB_USER", "djound"),
            PASSWORD = System.getenv().getOrDefault("DJOUND_DB_PASSWORD", "djound");

    @BeforeEach
    void start() throws Exception {
        schema = "live_ops_" + UUID.randomUUID().toString().replace("-", "");
        try (var c = DriverManager.getConnection(BASE, USER, PASSWORD);
                var s = c.createStatement()) {
            s.execute("CREATE SCHEMA " + schema);
        }
        url = BASE + (BASE.contains("?") ? "&" : "?") + "currentSchema=" + schema;
        output = Files.readAllBytes(Path.of("../fixtures/audio/sample-warm.wav"));
        peer = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        origin = URI.create("http://127.0.0.1:" + peer.getAddress().getPort());
        peer.createContext(
                "/",
                exchange -> {
                    String path = exchange.getRequestURI().getPath();
                    exchange.getRequestBody().readAllBytes();
                    byte[] response;
                    if (!exchange.getRequestMethod().equals("GET")) {
                        posts.incrementAndGet();
                        exchange.sendResponseHeaders(405, -1);
                        exchange.close();
                        return;
                    }
                    if (path.equals("/gradio_api/queue/data")) {
                        assertEquals(
                                "session_hash=test-event", exchange.getRequestURI().getRawQuery());
                        observations.incrementAndGet();
                        String item =
                                "{\"url\":\""
                                        + origin
                                        + "/gradio_api/file=/tmp/gradio/result.wav\",\"meta\":{\"_type\":\"gradio.FileData\"}}";
                        response =
                                ("data:"
                                     + " {\"msg\":\"process_completed\",\"event_id\":\"test-event\",\"success\":true,\"output\":{\"data\":["
                                                + item
                                                + ","
                                                + item
                                                + ","
                                                + item
                                                + "]}}\n\n")
                                        .getBytes(StandardCharsets.UTF_8);
                        exchange.getResponseHeaders().add("Content-Type", "text/event-stream");
                    } else {
                        downloads.incrementAndGet();
                        response = output;
                    }
                    exchange.sendResponseHeaders(200, response.length);
                    try (var body = exchange.getResponseBody()) {
                        body.write(response);
                    }
                });
        peer.start();
        launch("FIXTURE");
        jdbc().sql(
                        "INSERT INTO live_submission_allowance(installation_id,submission_limit)"
                                + " SELECT installation_id,2 FROM workspace WHERE id=1")
                .update();
    }

    void launch(String mode) {
        app =
                SpringApplication.run(
                        DjoundApplication.class,
                        "--server.port=0",
                        "--spring.datasource.url=" + url,
                        "--spring.datasource.username=" + USER,
                        "--spring.datasource.password=" + PASSWORD,
                        "--djound.data-dir=" + root,
                        "--djound.mode=" + mode,
                        "--spring.main.banner-mode=off");
        app.getBean(org.springframework.scheduling.concurrent.ThreadPoolTaskScheduler.class)
                .shutdown();
    }

    @AfterEach
    void stop() throws Exception {
        if (app != null) app.close();
        if (peer != null) peer.stop(0);
        try (var c = DriverManager.getConnection(BASE, USER, PASSWORD);
                var s = c.createStatement()) {
            s.execute("DROP SCHEMA " + schema + " CASCADE");
        }
    }

    JdbcClient jdbc() {
        return app.getBean(JdbcClient.class);
    }

    GenerationService service() {
        return app.getBean(GenerationService.class);
    }

    LiveOperations operations() {
        return new LiveOperations(jdbc(), new MelodyFlowTransport(origin, Duration.ofSeconds(2)));
    }

    PublicationStore publication() {
        return new PublicationStore(
                jdbc(),
                app.getBean(PlatformTransactionManager.class),
                app.getBean(MediaStore.class),
                new FixtureResultRetriever(app.getBean(FixtureCheckpoints.class), origin),
                app.getBean(FixtureCheckpoints.class));
    }

    Models.CreationReceipt accept() throws Exception {
        var source =
                app.getBean(Explorations.class)
                        .create(
                                new MockMultipartFile(
                                        "file",
                                        "source.wav",
                                        "audio/wav",
                                        Files.readAllBytes(
                                                Path.of("../fixtures/audio/source-study.wav"))),
                                "draft",
                                "live-local-source");
        var receipt =
                service()
                        .create(
                                source.explorationId(),
                                new Models.CreateRequestCommand("warm strings", null, 1L, 1L),
                                "live-local-request");
        // An older installation admitted this request before retirement.
        jdbc().sql(
                        "UPDATE generation_request SET provenance='LIVE',"
                            + " adapter_configuration_id='melodyflow-anonymous-v1' WHERE id=?")
                .param(receipt.requestId())
                .update();
        return receipt;
    }

    Models.Attempt acknowledged() {
        var owner = service().claimNext();
        app.getBean(LiveAllowance.class).reserve(owner);
        jdbc().sql(
                        "INSERT INTO"
                            + " generation_operation(id,attempt_id,adapter_configuration_id,provenance,event_id)"
                            + " VALUES('historical-event',?,'melodyflow-anonymous-v1','LIVE','test-event')")
                .param(owner.id())
                .update();
        return service().saveHandle(owner, "historical-event");
    }

    @Test
    void durableResultLocatorRecoversPublicationWithoutAnotherSubmissionOrEventRead()
            throws Exception {
        var receipt = accept();
        var original =
                app.getBean(Explorations.class)
                        .get(service().get(receipt.attemptId()).explorationId())
                        .original();
        var owner = acknowledged();
        var operation = operations();
        assertEquals("RUNNING", owner.state());
        String handle = service().savedHandle(owner.id());
        operation.observe(handle);
        assertNotNull(
                jdbc().sql("SELECT result_url FROM generation_operation WHERE id=?")
                        .param(handle)
                        .query(String.class)
                        .single());
        app.close();
        launch("UNCONFIGURED");
        owner = service().active();
        var saved = operations().observe(handle);
        owner = service().beginSaving(owner);
        publication().publish(owner, saved);
        var finished = service().get(receipt.attemptId());
        assertEquals("SUCCEEDED", finished.state());
        var recording =
                app.getBean(Explorations.class)
                        .get(finished.explorationId())
                        .renditions()
                        .getFirst();
        assertEquals("LIVE", recording.provenance());
        assertEquals(
                java.util.HexFormat.of()
                        .formatHex(
                                java.security.MessageDigest.getInstance("SHA-256").digest(output)),
                recording.sha256());
        assertEquals(
                original.sha256(),
                app.getBean(Explorations.class).get(finished.explorationId()).original().sha256());
        String originalKey =
                jdbc().sql("SELECT storage_key FROM recording WHERE id=?")
                        .param(original.id())
                        .query(String.class)
                        .single();
        assertArrayEquals(
                Files.readAllBytes(Path.of("../fixtures/audio/source-study.wav")),
                Files.readAllBytes(app.getBean(MediaStore.class).path(originalKey)));
        assertEquals("Version 1", recording.title());
        assertEquals(0, posts.get());
        assertEquals(1, observations.get());
        assertEquals(1, downloads.get());
        assertEquals(1L, app.getBean(LiveAllowance.class).view().get("used"));
        assertTrue(
                service()
                        .create(
                                finished.explorationId(),
                                new Models.CreateRequestCommand("warm strings", null, 1L, 1L),
                                "live-local-request")
                        .replayed());
        assertEquals(0, posts.get());
    }

    @org.junit.jupiter.params.ParameterizedTest
    @org.junit.jupiter.params.provider.ValueSource(ints = {0, 1})
    void exactThirtySecondOutputPublishesButOneSampleOverIsRejected(int extraSamples)
            throws Exception {
        int frames = 44100 * 30 + extraSamples;
        byte[] wave = new byte[44 + frames * 2];
        ByteBuffer b = ByteBuffer.wrap(wave).order(ByteOrder.LITTLE_ENDIAN);
        b.put("RIFF".getBytes())
                .putInt(wave.length - 8)
                .put("WAVEfmt ".getBytes())
                .putInt(16)
                .putShort((short) 1)
                .putShort((short) 1)
                .putInt(44100)
                .putInt(88200)
                .putShort((short) 2)
                .putShort((short) 16)
                .put("data".getBytes())
                .putInt(frames * 2);
        output = wave;
        var receipt = accept();
        acknowledged();
        new GenerationRunner(
                        service(),
                        app.getBean(FixtureOperations.class),
                        publication(),
                        app.getBean(FixtureCheckpoints.class),
                        operations(),
                        app.getBean(com.djound.local.LocalMusic.class),
                        app.getBean(com.djound.local.LocalWorker.class))
                .tick();
        var failed = service().get(receipt.attemptId());
        if (extraSamples == 0) {
            assertEquals("SUCCEEDED", failed.state());
            assertEquals(
                    30000,
                    app.getBean(Explorations.class)
                            .get(failed.explorationId())
                            .renditions()
                            .getFirst()
                            .durationMs());
        } else {
            assertEquals("FAILED", failed.state());
            assertEquals("OUTPUT_INVALID", failed.outcome().code());
            assertTrue(
                    app.getBean(Explorations.class)
                            .get(failed.explorationId())
                            .renditions()
                            .isEmpty());
        }
        assertEquals(0, posts.get());
        assertEquals(1L, app.getBean(LiveAllowance.class).view().get("used"));
    }

    @Test
    void historicalAcceptedWorkIsClosedWithoutSubmittingOrSpendingAllowance() throws Exception {
        var receipt = accept();
        app.getBean(GenerationRunner.class).tick();
        var closed = service().get(receipt.attemptId());
        assertEquals("FAILED", closed.state());
        assertEquals("RETRY_CONTEXT_UNAVAILABLE", closed.outcome().code());
        assertFalse(closed.canRetry());
        assertEquals(0, posts.get());
        assertEquals(0L, app.getBean(LiveAllowance.class).view().get("used"));
        assertEquals(
                0L,
                jdbc().sql("SELECT count(*) FROM generation_operation").query(Long.class).single());
    }
}
