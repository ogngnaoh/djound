package com.djound;

import static org.junit.jupiter.api.Assertions.*;

import org.junit.jupiter.api.*;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.boot.SpringApplication;
import org.springframework.context.ConfigurableApplicationContext;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.mock.web.MockMultipartFile;

import java.nio.*;
import java.nio.file.*;
import java.sql.*;
import java.util.*;
import java.util.concurrent.*;

/**
 * Real PostgreSQL, real ffprobe, isolated schema and temporary installation. No H2 or mocked
 * persistence.
 */
class BackendIntegrationTest {
    @TempDir Path root;
    static final String BASE =
            System.getenv()
                    .getOrDefault("DJOUND_TEST_DB_URL", "jdbc:postgresql://127.0.0.1:55432/djound");
    static final String USER = System.getenv().getOrDefault("DJOUND_DB_USER", "djound"),
            PASSWORD = System.getenv().getOrDefault("DJOUND_DB_PASSWORD", "djound");
    String schema;
    ConfigurableApplicationContext app;

    @BeforeEach
    void start() throws Exception {
        schema = "test_" + UUID.randomUUID().toString().replace("-", "");
        try (var c = DriverManager.getConnection(BASE, USER, PASSWORD);
                var s = c.createStatement()) {
            s.execute("CREATE SCHEMA " + schema);
        }
        app = launch(root);
    }

    ConfigurableApplicationContext launch(Path dir) {
        return SpringApplication.run(
                DjoundApplication.class,
                "--server.port=0",
                "--spring.datasource.url="
                        + BASE
                        + (BASE.contains("?") ? "&" : "?")
                        + "currentSchema="
                        + schema,
                "--spring.datasource.username=" + USER,
                "--spring.datasource.password=" + PASSWORD,
                "--djound.data-dir=" + dir,
                "--spring.main.banner-mode=off");
    }

    @AfterEach
    void stop() throws Exception {
        if (app != null) app.close();
        try (var c = DriverManager.getConnection(BASE, USER, PASSWORD);
                var s = c.createStatement()) {
            s.execute("DROP SCHEMA " + schema + " CASCADE");
        }
    }

    MockMultipartFile wav(String name, int rate, int channels, int bits, int seconds) {
        return new MockMultipartFile(
                "file", name, "application/octet-stream", wave(rate, channels, bits, seconds));
    }

    static byte[] wave(int rate, int channels, int bits, int seconds) {
        int size = rate * channels * (bits / 8) * seconds;
        ByteBuffer b = ByteBuffer.allocate(44 + size).order(ByteOrder.LITTLE_ENDIAN);
        b.put("RIFF".getBytes())
                .putInt(size + 36)
                .put("WAVEfmt ".getBytes())
                .putInt(16)
                .putShort((short) 1)
                .putShort((short) channels)
                .putInt(rate)
                .putInt(rate * channels * bits / 8)
                .putShort((short) (channels * bits / 8))
                .putShort((short) bits)
                .put("data".getBytes())
                .putInt(size);
        for (int n = 0; n < size / 2; n++)
            b.putShort((short) (Math.sin(n * 440 * 2 * Math.PI / rate) * 2000));
        return b.array();
    }

    @Test
    void uploadReceiptSurvivesRestartAndKeepsExactBytes() throws Exception {
        var service = app.getBean(Explorations.class);
        var file = wav("Study.wav", 44100, 1, 16, 2);
        var first = service.create(file, "Keep my exact words 🎶", "upload-key-001");
        assertFalse(first.replayed());
        var detail = service.get(first.explorationId());
        assertEquals(2000, detail.original().durationMs());
        assertEquals("Keep my exact words 🎶", detail.draft().text());
        assertEquals("ORIGINAL", detail.original().provenance());
        assertArrayEquals(
                file.getBytes(),
                Files.readAllBytes(
                        app.getBean(MediaStore.class)
                                .path(service.storageKey(first.originalRecordingId()))));
        app.close();
        app = launch(root);
        service = app.getBean(Explorations.class);
        var replay = service.create(file, "Keep my exact words 🎶", "upload-key-001");
        assertTrue(replay.replayed());
        assertEquals(first.explorationId(), replay.explorationId());
        assertEquals(1, service.list().size());
        Explorations current = service;
        assertEquals(
                "IDEMPOTENCY_CONFLICT",
                assertThrows(
                                ApiProblem.class,
                                () -> current.create(file, "different", "upload-key-001"))
                        .code);
        try (var files = Files.list(root.resolve("temporary"))) {
            assertEquals(0, files.count());
        }
    }

    @Test
    void simultaneousReplayCreatesOnlyOneExploration() throws Exception {
        var service = app.getBean(Explorations.class);
        try (var executor = Executors.newVirtualThreadPerTaskExecutor()) {
            var a =
                    executor.submit(
                            () ->
                                    service.create(
                                            wav("same.wav", 48000, 2, 16, 1),
                                            "hello",
                                            "concurrent-key"));
            var b =
                    executor.submit(
                            () ->
                                    service.create(
                                            wav("same.wav", 48000, 2, 16, 1),
                                            "hello",
                                            "concurrent-key"));
            var x = a.get();
            var y = b.get();
            assertEquals(x.explorationId(), y.explorationId());
            assertNotEquals(x.replayed(), y.replayed());
            assertEquals(1, service.list().size());
        }
    }

    @Test
    void activeWorkspaceGuardAndInvalidMediaLeaveNoEntries() throws Exception {
        var service = app.getBean(Explorations.class);
        var bad = new MockMultipartFile("file", "fake.wav", "audio/wav", "not audio".getBytes());
        assertEquals(
                415,
                assertThrows(ApiProblem.class, () -> service.create(bad, "notes", "bad-file-key"))
                        .status);
        assertEquals(
                422,
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        service.create(
                                                wav("rate.wav", 22050, 1, 16, 1),
                                                "notes",
                                                "bad-rate-key"))
                        .status);
        assertEquals(
                415,
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        service.create(
                                                wav("codec.wav", 44100, 1, 24, 1),
                                                "notes",
                                                "bad-codec-key"))
                        .status);
        byte[] corrupt = Arrays.copyOf(wave(44100, 1, 16, 2), 100);
        assertThrows(
                ApiProblem.class,
                () ->
                        service.create(
                                new MockMultipartFile("file", "broken.wav", "audio/wav", corrupt),
                                "notes",
                                "bad-short-key"));
        app.getBean(JdbcClient.class)
                .sql("UPDATE workspace SET active_attempt_id='attempt_busy' WHERE id=1")
                .update();
        assertEquals(
                "CREATION_ACTIVE",
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        service.create(
                                                wav("good.wav", 44100, 1, 16, 1),
                                                "notes",
                                                "busy-file-key"))
                        .code);
        assertTrue(service.list().isEmpty());
        try (var files = Files.list(root.resolve("temporary"))) {
            assertEquals(0, files.count());
        }
    }

    @Test
    void acceptsRealMp3AndRejectsTruncatedWave() throws Exception {
        Path source = root.resolve("input.wav"), mp3 = root.resolve("input.mp3");
        Files.write(source, wave(48000, 2, 16, 2));
        Process encoder =
                new ProcessBuilder(
                                "ffmpeg",
                                "-v",
                                "error",
                                "-i",
                                source.toString(),
                                "-codec:a",
                                "libmp3lame",
                                mp3.toString())
                        .redirectErrorStream(true)
                        .start();
        assertTrue(encoder.waitFor(10, TimeUnit.SECONDS));
        assertEquals(0, encoder.exitValue());
        var service = app.getBean(Explorations.class);
        var receipt =
                service.create(
                        new MockMultipartFile(
                                "file", "music.mp3", "text/plain", Files.readAllBytes(mp3)),
                        "",
                        "mp3-upload-key");
        var original = service.get(receipt.explorationId()).original();
        assertEquals("mp3", original.codec());
        assertEquals("audio/mpeg", original.contentType());
        assertEquals(48000, original.sampleRate());
        byte[] truncated = Arrays.copyOf(Files.readAllBytes(source), 200000);
        assertEquals(
                422,
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        service.create(
                                                new MockMultipartFile(
                                                        "file",
                                                        "truncated.wav",
                                                        "audio/wav",
                                                        truncated),
                                                "",
                                                "truncated-key"))
                        .status);
    }

    @Test
    void streamLimitAndProbeOutputAreBoundedAndCleaned() throws Exception {
        var store = app.getBean(MediaStore.class);
        var huge =
                new MockMultipartFile("file", "huge.wav", "audio/wav", new byte[0]) {
                    @Override
                    public java.io.InputStream getInputStream() {
                        return new java.io.InputStream() {
                            long remaining = MediaStore.MAX_BYTES + 1;

                            public int read() {
                                return remaining-- > 0 ? 0 : -1;
                            }

                            public int read(byte[] b, int off, int len) {
                                if (remaining <= 0) return -1;
                                int count = (int) Math.min(remaining, len);
                                Arrays.fill(b, off, off + count, (byte) 0);
                                remaining -= count;
                                return count;
                            }
                        };
                    }
                };
        assertEquals(413, assertThrows(ApiProblem.class, () -> store.ingest(huge)).status);
        Path loud = root.resolve("loud-probe.sh");
        Files.writeString(loud, "#!/bin/sh\nexec /usr/bin/yes\n");
        loud.toFile().setExecutable(true);
        var noisy = new MediaStore(app.getBean(Installation.class), loud.toString());
        assertTimeoutPreemptively(
                java.time.Duration.ofSeconds(5),
                () ->
                        assertThrows(
                                ApiProblem.class,
                                () -> noisy.ingest(wav("good.wav", 44100, 1, 16, 1))));
        Path sleeper = root.resolve("slow-probe.sh");
        Files.writeString(sleeper, "#!/bin/sh\nsleep 30\n");
        sleeper.toFile().setExecutable(true);
        var slow = new MediaStore(app.getBean(Installation.class), sleeper.toString());
        assertTimeoutPreemptively(
                java.time.Duration.ofSeconds(13),
                () ->
                        assertThrows(
                                ApiProblem.class,
                                () -> slow.ingest(wav("good.wav", 44100, 1, 16, 1))));
        try (var files = Files.list(root.resolve("temporary"))) {
            assertEquals(0, files.count());
        }
    }

    @Test
    void draftsRejectStaleAndForeignBasisAndSurviveRestart() throws Exception {
        var service = app.getBean(Explorations.class);
        var first = service.create(wav("first.wav", 44100, 1, 16, 1), "initial", "draft-first-key");
        var foreign = service.create(wav("other.wav", 44100, 1, 16, 1), "", "draft-other-key");
        var saved =
                service.saveDraft(
                        first.explorationId(), new Models.DraftUpdate("exact words 🎵", null, 1L));
        assertEquals(2, saved.revision());
        assertEquals(first.originalRecordingId(), saved.basisRecordingId());
        assertEquals(
                "STALE_DRAFT",
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        service.saveDraft(
                                                first.explorationId(),
                                                new Models.DraftUpdate("stale", null, 1L)))
                        .code);
        assertEquals(
                "SOURCE_CHANGED",
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        service.saveDraft(
                                                first.explorationId(),
                                                new Models.DraftUpdate(
                                                        "foreign",
                                                        foreign.originalRecordingId(),
                                                        2L)))
                        .code);
        assertEquals(
                422,
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        service.saveDraft(
                                                first.explorationId(),
                                                new Models.DraftUpdate("a".repeat(4001), null, 2L)))
                        .status);
        app.close();
        app = launch(root);
        assertEquals(saved, app.getBean(Explorations.class).get(first.explorationId()).draft());
    }

    @Test
    void replacementPreservesWordsAndOldMediaAndReplayNeverRestoresOlderDraft() throws Exception {
        var service = app.getBean(Explorations.class);
        var original = wav("first.wav", 44100, 1, 16, 1);
        var first = service.create(original, "initial", "replace-first-key");
        service.saveDraft(
                first.explorationId(), new Models.DraftUpdate("latest before replace", null, 1L));
        var replacement = wav("new source.wav", 48000, 2, 16, 2);
        var accepted =
                service.replaceSource(first.explorationId(), replacement, 1, "replace-source-key");
        var detail = service.get(first.explorationId());
        assertEquals(2, detail.sourceRevision());
        assertEquals(3, detail.draft().revision());
        assertEquals("new source", detail.title());
        assertEquals("latest before replace", detail.draft().text());
        assertEquals(accepted.originalRecordingId(), detail.draft().basisRecordingId());
        assertTrue(detail.canReplaceSource());
        assertArrayEquals(
                original.getBytes(),
                Files.readAllBytes(
                        app.getBean(MediaStore.class)
                                .path(service.storageKey(first.originalRecordingId()))));
        assertEquals(
                "SOURCE_CHANGED",
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        service.saveDraft(
                                                first.explorationId(),
                                                new Models.DraftUpdate(
                                                        "old basis",
                                                        first.originalRecordingId(),
                                                        3L)))
                        .code);
        var newer =
                service.saveDraft(
                        first.explorationId(),
                        new Models.DraftUpdate("newer after replacement", null, 3L));
        app.getBean(JdbcClient.class)
                .sql("UPDATE workspace SET active_attempt_id='busy' WHERE id=1")
                .update();
        var replayed =
                service.replaceSource(first.explorationId(), replacement, 1, "replace-source-key");
        assertTrue(replayed.replayed());
        assertEquals(accepted.originalRecordingId(), replayed.originalRecordingId());
        assertEquals(3, replayed.draftRevision());
        assertEquals(newer, service.get(first.explorationId()).draft());
        assertFalse(service.get(first.explorationId()).canReplaceSource());
        assertEquals(
                "IDEMPOTENCY_CONFLICT",
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        service.replaceSource(
                                                first.explorationId(),
                                                replacement,
                                                2,
                                                "replace-source-key"))
                        .code);
        assertEquals(
                "CREATION_ACTIVE",
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        service.replaceSource(
                                                first.explorationId(),
                                                replacement,
                                                2,
                                                "replace-busy-key"))
                        .code);
    }

    @Test
    void invalidAndConcurrentReplacementsCannotLoseDraftOrSource() throws Exception {
        var service = app.getBean(Explorations.class);
        var first = service.create(wav("first.wav", 44100, 1, 16, 1), "preserve", "race-first-key");
        assertThrows(
                ApiProblem.class,
                () ->
                        service.replaceSource(
                                first.explorationId(),
                                new MockMultipartFile(
                                        "file", "bad.wav", "audio/wav", new byte[] {1, 2}),
                                1,
                                "invalid-replace"));
        assertEquals(
                first.originalRecordingId(), service.get(first.explorationId()).original().id());
        assertEquals("preserve", service.get(first.explorationId()).draft().text());
        try (var executor = Executors.newVirtualThreadPerTaskExecutor()) {
            Callable<String> replace =
                    () -> {
                        try {
                            service.replaceSource(
                                    first.explorationId(),
                                    wav("next.wav", 44100, 1, 16, 1),
                                    1,
                                    "race-" + UUID.randomUUID());
                            return "ACCEPTED";
                        } catch (ApiProblem error) {
                            return error.code;
                        }
                    };
            var a = executor.submit(replace);
            var b = executor.submit(replace);
            assertEquals(Set.of("ACCEPTED", "SOURCE_CHANGED"), Set.of(a.get(), b.get()));
        }
        var current = service.get(first.explorationId());
        assertEquals(2, current.sourceRevision());
        assertEquals("preserve", current.draft().text());
        assertEquals(
                "STALE_DRAFT",
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        service.saveDraft(
                                                first.explorationId(),
                                                new Models.DraftUpdate(
                                                        "delayed save",
                                                        first.originalRecordingId(),
                                                        1L)))
                        .code);
        var jdbc = app.getBean(JdbcClient.class);
        jdbc.sql(
                        "INSERT INTO recording SELECT"
                            + " 'rendition_test',exploration_id,title,'RENDITION',file_name,'unique-test-storage',content_type,byte_size,duration_ms,sample_rate,channels,codec,sha256,'FIXTURE',false,created_at"
                            + " FROM recording WHERE id=?")
                .param(current.original().id())
                .update();
        assertFalse(service.get(first.explorationId()).canReplaceSource());
        assertEquals(
                "SOURCE_REPLACEMENT_UNAVAILABLE",
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        service.replaceSource(
                                                first.explorationId(),
                                                wav("blocked.wav", 44100, 1, 16, 1),
                                                2,
                                                "rendition-blocked"))
                        .code);
    }

    @Test
    void replacementWaitsForAdmissionGuardAndRechecksActiveWork() throws Exception {
        var service = app.getBean(Explorations.class);
        var first =
                service.create(wav("guard.wav", 44100, 1, 16, 1), "saved words", "guard-first-key");
        try (var connection = app.getBean(javax.sql.DataSource.class).getConnection();
                var executor = Executors.newVirtualThreadPerTaskExecutor()) {
            connection.setAutoCommit(false);
            try (var statement = connection.createStatement()) {
                statement.executeQuery("SELECT id FROM workspace WHERE id=1 FOR UPDATE").close();
                var waiting =
                        executor.submit(
                                () ->
                                        service.replaceSource(
                                                first.explorationId(),
                                                wav("replacement.wav", 44100, 1, 16, 1),
                                                1,
                                                "guard-replace-key"));
                assertThrows(TimeoutException.class, () -> waiting.get(300, TimeUnit.MILLISECONDS));
                statement.executeUpdate(
                        "UPDATE workspace SET active_attempt_id='newly_admitted' WHERE id=1");
                connection.commit();
                var failure =
                        assertThrows(
                                ExecutionException.class, () -> waiting.get(5, TimeUnit.SECONDS));
                assertEquals("CREATION_ACTIVE", ((ApiProblem) failure.getCause()).code);
            } finally {
                connection.rollback();
            }
        }
        assertEquals(
                first.originalRecordingId(), service.get(first.explorationId()).original().id());
        assertEquals("saved words", service.get(first.explorationId()).draft().text());
    }

    @Test
    void draftHttpContractReportsTypedConflictsAndMalformedRequests() throws Exception {
        var first =
                app.getBean(Explorations.class)
                        .create(wav("http.wav", 44100, 1, 16, 1), "before", "http-first-key");
        String endpoint =
                "http://127.0.0.1:"
                        + app.getEnvironment().getProperty("local.server.port")
                        + "/api/explorations/"
                        + first.explorationId()
                        + "/draft";
        try (var client = java.net.http.HttpClient.newHttpClient()) {
            var request =
                    java.net.http.HttpRequest.newBuilder(java.net.URI.create(endpoint))
                            .header("Content-Type", "application/json")
                            .PUT(
                                    java.net.http.HttpRequest.BodyPublishers.ofString(
                                            "{\"text\":\"saved through"
                                                + " HTTP\",\"basisRecordingId\":null,\"expectedDraftRevision\":1}"))
                            .build();
            var saved = client.send(request, java.net.http.HttpResponse.BodyHandlers.ofString());
            assertEquals(200, saved.statusCode());
            assertTrue(saved.body().contains("saved through HTTP"));
            var stale = client.send(request, java.net.http.HttpResponse.BodyHandlers.ofString());
            assertEquals(409, stale.statusCode());
            assertTrue(stale.body().contains("STALE_DRAFT"));
            assertTrue(
                    stale.headers()
                            .firstValue("content-type")
                            .orElseThrow()
                            .contains("application/problem+json"));
            var malformed =
                    java.net.http.HttpRequest.newBuilder(java.net.URI.create(endpoint))
                            .header("Content-Type", "application/json")
                            .PUT(java.net.http.HttpRequest.BodyPublishers.ofString("{"))
                            .build();
            assertEquals(
                    400,
                    client.send(malformed, java.net.http.HttpResponse.BodyHandlers.ofString())
                            .statusCode());
        }
    }

    @Test
    void detailAndLibraryReadsNeedOnlyOneDatabaseConnection() throws Exception {
        try (var single = new com.zaxxer.hikari.HikariDataSource()) {
            single.setJdbcUrl(BASE + (BASE.contains("?") ? "&" : "?") + "currentSchema=" + schema);
            single.setUsername(USER);
            single.setPassword(PASSWORD);
            single.setMaximumPoolSize(1);
            single.setMinimumIdle(1);
            var service =
                    new Explorations(
                            JdbcClient.create(single),
                            app.getBean(MediaStore.class),
                            new org.springframework.jdbc.datasource.DataSourceTransactionManager(
                                    single));
            var first =
                    service.create(
                            wav("one connection.wav", 44100, 1, 16, 1), "notes", "single-pool-key");
            assertTimeoutPreemptively(
                    java.time.Duration.ofSeconds(3),
                    () -> {
                        var detail = service.get(first.explorationId());
                        assertEquals(first.originalRecordingId(), detail.original().id());
                        assertEquals("one connection", detail.title());
                        assertEquals(first.explorationId(), detail.id());
                        var summaries = service.list();
                        assertEquals(1, summaries.size());
                        assertEquals(detail.original(), summaries.getFirst().original());
                    });
        }
    }

    @Test
    void refusesAnotherProcessDirectoryAndDatabaseRebinding() throws Exception {
        var ds = app.getBean(javax.sql.DataSource.class);
        assertThrows(IllegalStateException.class, () -> new Installation(ds, root.toString()));
        Path other = Files.createDirectory(root.resolve("other"));
        Files.copy(root.resolve("installation.id"), other.resolve("installation.id"));
        assertThrows(IllegalStateException.class, () -> new Installation(ds, other.toString()));
    }
}
