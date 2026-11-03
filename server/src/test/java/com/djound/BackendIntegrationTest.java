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
    void refusesAnotherProcessDirectoryAndDatabaseRebinding() throws Exception {
        var ds = app.getBean(javax.sql.DataSource.class);
        assertThrows(IllegalStateException.class, () -> new Installation(ds, root.toString()));
        Path other = Files.createDirectory(root.resolve("other"));
        Files.copy(root.resolve("installation.id"), other.resolve("installation.id"));
        assertThrows(IllegalStateException.class, () -> new Installation(ds, other.toString()));
    }
}
