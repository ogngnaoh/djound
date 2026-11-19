package com.djound;

import static org.junit.jupiter.api.Assertions.*;

import com.djound.rendition.RenditionProjects;

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

    ConfigurableApplicationContext launch(Path dir, String... options) {
        var args =
                new ArrayList<>(
                        List.of(
                                "--server.port=0",
                                "--spring.datasource.url="
                                        + BASE
                                        + (BASE.contains("?") ? "&" : "?")
                                        + "currentSchema="
                                        + schema,
                                "--spring.datasource.username=" + USER,
                                "--spring.datasource.password=" + PASSWORD,
                                "--djound.data-dir=" + dir,
                                "--spring.main.banner-mode=off"));
        args.addAll(List.of(options));
        return SpringApplication.run(DjoundApplication.class, args.toArray(String[]::new));
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
    void renditionVoiceMemoHistoryIsDurableAndBoundToTheSelectedParent() throws Exception {
        app.close();
        Path python = Path.of("/usr/bin/python3");
        Path worker = root.resolve("test-worker.py");
        Files.writeString(
                worker,
                """
                import json, os, sys, wave
                q=json.load(sys.stdin)
                assert not os.path.exists(q["output"])
                if q['prompt']=='question':
                    print(json.dumps(dict(state='clarify',error='Which instrument?')))
                else:
                    with wave.open(q['source'],'rb') as orig:
                        fmt=orig.getparams()
                        pcm=bytearray(orig.readframes(fmt.nframes))
                    pos=round(q.get('startSeconds',0)*fmt.framerate)*4
                    if q['prompt'] != 'unchanged': pcm[pos]^=1
                    with wave.open(q['output'],'wb') as out:
                        out.setparams(fmt);out.writeframes(pcm)
                    print(json.dumps(dict(state='ready',caption='Parent: '+q['parentCaption']+' / '+str(len(q['history'])),
                                          label='Test output',durationSeconds=fmt.nframes/fmt.framerate)))
                """);
        String[] settings = {
            "--djound.mode=LOCAL",
            "--djound.rendition-worker=" + worker,
            "--djound.local-python=" + python
        };
        app = launch(root, settings);
        var projects = app.getBean(com.djound.rendition.RenditionProjects.class);
        Path input = root.resolve("memo.wav"), memo = root.resolve("memo.m4a");
        Files.write(input, wave(32000, 1, 16, 2));
        assertEquals(
                0,
                new ProcessBuilder(
                                "ffmpeg",
                                "-loglevel",
                                "error",
                                "-i",
                                input.toString(),
                                "-c:a",
                                "aac",
                                memo.toString())
                        .inheritIO()
                        .start()
                        .waitFor());
        var upload =
                new MockMultipartFile("file", "memo.m4a", "audio/mp4", Files.readAllBytes(memo));
        var session = projects.create(upload, "import");
        String id = session.id();
        assertEquals(id, projects.create(upload, "import").id());
        assertEquals("original", session.originalUrl().split("/")[5]);
        assertEquals(2, session.durationSeconds(), .05);
        var conflict =
                new MockMultipartFile("file", "other.m4a", "audio/mp4", Files.readAllBytes(memo));
        assertEquals(
                "RENDITION_REQUEST_CONFLICT",
                assertThrows(ApiProblem.class, () -> projects.create(conflict, "import")).code());
        var first =
                new com.djound.rendition.RenditionProjects.Command(
                        "first", "original", "jazz", null, null);
        projects.submit(id, first);
        var take = awaitRendition(projects, id, "first");
        assertEquals("ready", take.state(), take.error());
        assertNull(take.progress());
        var json = new tools.jackson.databind.ObjectMapper();
        assertFalse(json.readTree(json.writeValueAsBytes(take)).has("progress"));
        assertEquals("Parent:  / 0", take.caption());
        assertEquals(take.id(), projects.submit(id, first).takes().getFirst().id());
        assertThrows(
                ApiProblem.class,
                () ->
                        projects.submit(
                                id,
                                new com.djound.rendition.RenditionProjects.Command(
                                        "first", "original", "different", null, null)));
        projects.submit(
                id,
                new com.djound.rendition.RenditionProjects.Command(
                        "next", take.id(), "quieter", .25, .75));
        var second = awaitRendition(projects, id, "next");
        assertEquals("ready", second.state(), second.error());
        assertEquals(take.id(), second.parentId());
        assertEquals("Parent: " + take.caption() + " / 1", second.caption());
        assertEquals(.25, second.appliedStartSeconds());
        assertNotEquals(take.sha256(), second.sha256());
        String url = "http://127.0.0.1:" + app.getEnvironment().getProperty("local.server.port");
        try (var http = java.net.http.HttpClient.newHttpClient()) {
            var exported =
                    http.send(
                            java.net.http.HttpRequest.newBuilder(
                                            java.net.URI.create(url + second.exportUrl()))
                                    .build(),
                            java.net.http.HttpResponse.BodyHandlers.ofByteArray());
            assertEquals(200, exported.statusCode());
            assertEquals(
                    second.sha256(),
                    HexFormat.of()
                            .formatHex(
                                    java.security.MessageDigest.getInstance("SHA-256")
                                            .digest(exported.body())));
            var partial =
                    http.send(
                            java.net.http.HttpRequest.newBuilder(
                                            java.net.URI.create(url + session.originalUrl()))
                                    .header("Range", "bytes=0-43")
                                    .build(),
                            java.net.http.HttpResponse.BodyHandlers.ofByteArray());
            assertEquals(206, partial.statusCode());
            assertEquals(44, partial.body().length);
        }
        assertEquals(second.id(), projects.favorite(id, second.id()).favoriteId());
        projects.submit(
                id,
                new com.djound.rendition.RenditionProjects.Command(
                        "ask", take.id(), "question", null, null));
        assertEquals("clarify", awaitRendition(projects, id, "ask").state());
        projects.submit(
                id,
                new com.djound.rendition.RenditionProjects.Command(
                        "same", take.id(), "unchanged", null, null));
        var unchanged = awaitRendition(projects, id, "same");
        assertEquals("failed", unchanged.state());
        var db = app.getBean(JdbcClient.class);
        db.sql("UPDATE rendition_take SET state='running' WHERE project_id=? AND request_id='same'")
                .param(id)
                .update();
        app.close();
        Path abandonedStage = root.resolve("renditions").resolve(unchanged.id() + ".stage");
        Files.writeString(abandonedStage, "rendering");
        app = launch(root);
        var restored = app.getBean(com.djound.rendition.RenditionProjects.class);
        assertEquals(1, restored.list().size());
        assertEquals(second.id(), restored.state(id).favoriteId());
        assertEquals(second.sha256(), restored.state(id).takes().get(1).sha256());
        assertEquals("failed", awaitRendition(restored, id, "same").state());
        assertFalse(Files.exists(abandonedStage));
        assertThrows(
                ApiProblem.class,
                () ->
                        restored.submit(
                                id,
                                new com.djound.rendition.RenditionProjects.Command(
                                        "offline", take.id(), "another", null, null)));
    }

    @Test
    void longerArrangementsKeepSongTimeAcrossPassagesAndRestart() throws Exception {
        app.close();
        Path worker = root.resolve("arrangement-worker.py");
        Files.writeString(
                worker,
                """
                import json,sys,wave
                q=json.load(sys.stdin)
                assert q['supportsArrangedScore'] and q['originalFrames']==88200
                prompt=q['prompt']
                with wave.open(q['source'],'rb') as f:
                    fmt=f.getparams()
                    audio=bytearray(f.readframes(fmt.nframes))
                landmarks=[[0,0],[44100,22050],[132300,88200]]
                result=dict(state='ready',label='Test arrangement',caption='Test performance')
                if prompt=='longer':
                    assert q['parentSongTimeMap'] is None and q['parentPlan'] is None
                    audio.extend(audio[:44100*4])
                    result.update(parentTimeMap=landmarks,arrangementPlan=dict(version=1,fromUpload=True,ensemble='trio'))
                else:
                    assert q['parentSongTimeMap']==landmarks and q['parentPlan']['ensemble']=='trio'
                    assert q['parentPlan']['fromUpload']==(prompt!='next arrangement')
                    position=round(q.get('startSeconds',0)*44100)*4
                    audio[position]^=1
                    if prompt=='outside': audio[0]^=1
                    if prompt=='resize': audio.extend(audio[:4])
                with wave.open(q['output'],'wb') as f:
                    f.setparams(fmt)
                    f.writeframes(audio)
                result['durationSeconds']=len(audio)/(44100*4)
                print(json.dumps(result))
                """);
        String[] settings = {
            "--djound.mode=LOCAL",
            "--djound.rendition-worker=" + worker,
            "--djound.local-python=/usr/bin/python3"
        };
        app = launch(root, settings);
        var projects = app.getBean(RenditionProjects.class);
        String id = projects.create(wav("tune.wav", 44100, 2, 16, 2), null).id();
        projects.submit(
                id, new RenditionProjects.Command("first", "original", "longer", null, null));
        var first = awaitRendition(projects, id, "first");
        assertEquals("ready", first.state(), first.error());
        assertEquals(3., first.durationSeconds());
        int[][] expected = {{0, 0}, {44100, 22050}, {132300, 88200}};
        assertArrayEquals(expected, first.songTimeMap());
        for (String invalid : List.of("outside", "resize")) {
            projects.submit(
                    id, new RenditionProjects.Command(invalid, first.id(), invalid, 2.25, 2.75));
            var rejected = awaitRendition(projects, id, invalid);
            assertEquals("failed", rejected.state());
            assertNull(rejected.audioUrl());
        }
        projects.submit(
                id, new RenditionProjects.Command("inside", first.id(), "inside", 2.25, 2.75));
        var passage = awaitRendition(projects, id, "inside");
        assertEquals("ready", passage.state(), passage.error());
        assertArrayEquals(expected, passage.songTimeMap());
        assertEquals(2.25, passage.appliedStartSeconds());
        assertEquals(2.75, passage.appliedEndSeconds());
        app.close();
        app = launch(root, settings);
        var reopened = app.getBean(RenditionProjects.class);
        reopened.submit(
                id,
                new RenditionProjects.Command(
                        "followup", passage.id(), "next arrangement", null, null));
        var followup = awaitRendition(reopened, id, "followup");
        assertEquals("ready", followup.state(), followup.error());
        assertArrayEquals(expected, followup.songTimeMap());
    }

    @Test
    void concurrentRenditionRequestsAreSerializedAndQueuedWorkSurvivesShutdown() throws Exception {
        app.close();
        var worker = root.resolve("blocking-worker.py");
        var pidFile = root.resolve("worker-child");
        Files.copy(
                Path.of(
                        System.getProperty("basedir"),
                        "..",
                        "local-engine",
                        "rendition_progress.py"),
                root.resolve("rendition_progress.py"));
        Files.writeString(
                worker,
                """
                import json,os,subprocess,sys,time
                from pathlib import Path
                from rendition_progress import report_stage
                request=json.load(sys.stdin)
                if request['prompt']=='block':
                    report_stage('melody')
                    child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(45)'])
                    Path(__file__).with_name('worker-child').write_text(str(child.pid))
                    while not Path(__file__).with_name('arrange').exists():
                        time.sleep(.02)
                    report_stage('arranging')
                    child.wait()
                print(json.dumps(dict(state='clarify',error='Choose a direction.')))
                """);
        String[] settings = {
            "--djound.mode=LOCAL",
            "--djound.rendition-worker=" + worker,
            "--djound.local-python=/usr/bin/python3"
        };
        app = launch(root, settings);
        var projects = app.getBean(com.djound.rendition.RenditionProjects.class);
        var a = projects.create(wav("one.wav", 44100, 1, 16, 1), null);
        var b = projects.create(wav("two.wav", 44100, 1, 16, 1), null);
        projects.submit(
                a.id(),
                new com.djound.rendition.RenditionProjects.Command(
                        "long", "original", "block", null, null));
        for (int i = 0; i < 120 && !Files.exists(pidFile); i++) Thread.sleep(50);
        long childPid = Long.parseLong(Files.readString(pidFile));
        var playing = projects.state(a.id()).takes().getFirst();
        assertEquals("Learning the tune…", playing.progress());
        var json = new tools.jackson.databind.ObjectMapper();
        assertEquals(
                "Learning the tune…",
                json.readTree(json.writeValueAsBytes(playing)).path("progress").asString());
        Path stageFile = root.resolve("renditions").resolve(playing.id() + ".stage");
        Files.createFile(root.resolve("arrange"));
        String progress = null;
        for (int i = 0; i < 100; i++) {
            progress = projects.state(a.id()).takes().getFirst().progress();
            if ("Arranging the instruments…".equals(progress)) break;
            Thread.sleep(30);
        }
        assertEquals("Arranging the instruments…", progress);
        try (var submitters = Executors.newFixedThreadPool(3)) {
            var futures = new ArrayList<Future<?>>();
            for (int i = 0; i < 6; i++) {
                final int n = i;
                futures.add(
                        submitters.submit(
                                () ->
                                        projects.submit(
                                                n % 2 == 0 ? a.id() : b.id(),
                                                new com.djound.rendition.RenditionProjects.Command(
                                                        "q" + n, "original", "next", null, null))));
            }
            for (var future : futures) future.get(5, TimeUnit.SECONDS);
        }
        var db = app.getBean(JdbcClient.class);
        assertTrue(projects.state(b.id()).takes().stream().allMatch(t -> t.progress() == null));
        assertEquals(
                1,
                db.sql("SELECT count(*) FROM rendition_take WHERE state='running'")
                        .query(Integer.class)
                        .single());
        assertEquals(
                6,
                db.sql("SELECT count(*) FROM rendition_take WHERE state='queued'")
                        .query(Integer.class)
                        .single());
        app.close();
        assertFalse(Files.exists(stageFile));
        for (int i = 0;
                i < 100 && ProcessHandle.of(childPid).map(ProcessHandle::isAlive).orElse(false);
                i++) Thread.sleep(30);
        assertFalse(
                ProcessHandle.of(childPid).map(ProcessHandle::isAlive).orElse(false),
                "render subprocess survived graceful shutdown");
        app = launch(root, settings);
        var resumed = app.getBean(com.djound.rendition.RenditionProjects.class);
        var interrupted = awaitRendition(resumed, a.id(), "long");
        assertEquals("failed", interrupted.state());
        assertNull(interrupted.progress());
        for (int i = 0; i < 6; i++)
            assertEquals(
                    "clarify",
                    awaitRendition(resumed, i % 2 == 0 ? a.id() : b.id(), "q" + i).state());
    }

    private static com.djound.rendition.RenditionProjects.Take awaitRendition(
            com.djound.rendition.RenditionProjects projects, String id, String requestId)
            throws InterruptedException {
        for (int i = 0; i < 130; i++) {
            var take =
                    projects.state(id).takes().stream()
                            .filter(t -> t.requestId().equals(requestId))
                            .findFirst()
                            .orElseThrow();
            if (!Set.of("running", "queued").contains(take.state())) return take;
            Thread.sleep(50);
        }
        throw new AssertionError("Rendition did not reach a terminal state.");
    }

    @Test
    void keepsTheAuditionedMixAcrossLaterPreviewsRestartAndReceiptReplay() throws Exception {
        var mix = app.getBean(com.djound.mix.MixProjects.class);
        var db = app.getBean(JdbcClient.class);
        String id = UUID.randomUUID().toString(), passage = UUID.randomUUID().toString();
        byte[] original = wave(44100, 2, 16, 1);
        Files.write(root.resolve("original.wav"), original);
        Files.write(root.resolve("prepared.npz"), original);
        String originalHash =
                HexFormat.of()
                        .formatHex(
                                java.security.MessageDigest.getInstance("SHA-256")
                                        .digest(original));
        db.sql(
                        """
                        INSERT INTO mix_project(id,title,import_sha,duration_ms,frames,source_path,source_sha,preparation)
                        VALUES(?,'song.mp3',?,1000,44100,'original.wav',?,'READY')
                        """)
                .params(id, originalHash, originalHash)
                .update();
        db.sql(
                        "INSERT INTO"
                            + " mix_passage(id,project_id,base_id,start_frame,end_frame,prep_path,prep_sha)"
                            + " VALUES(?,?,'original',100,200,'prepared.npz',?)")
                .params(passage, id, originalHash)
                .update();
        db.sql("UPDATE mix_project SET passage_id=? WHERE id=?").params(passage, id).update();
        String first = null, hash = null;
        for (int n = 1; n <= 2; n++) {
            String take = UUID.randomUUID().toString(), file = take + ".wav";
            byte[] preview = original.clone();
            preview[44 + 4 * 150] = (byte) n;
            Files.write(root.resolve(file), preview);
            String sha =
                    HexFormat.of()
                            .formatHex(
                                    java.security.MessageDigest.getInstance("SHA-256")
                                            .digest(preview));
            db.sql(
                            """
                            INSERT INTO mix_take(id,project_id,number,passage_id,parent_id,vocals_db,drums_db,last_control,
                            last_change_db,request_text,label,audio_path,sha256)
                            VALUES(?,?,?,?,'original',?,0,'vocals',1,'lift the voice','Vocal level',?,?)
                            """)
                    .params(take, id, n, passage, n, file, sha)
                    .update();
            mix.heard(id, take);
            if (n == 1) {
                first = take;
                hash = sha;
            }
        }
        var command = new com.djound.mix.MixModels.Keep(first, hash);
        var edit =
                new com.djound.mix.MixModels.Edit(
                        "lift the voice", "original", 100, 200, null, null);
        db.sql(
                        "INSERT INTO mix_edit_receipt(project_id,command_key,request_json,take_id)"
                                + " VALUES(?,?,?,?)")
                .params(
                        id,
                        "preview-key",
                        new tools.jackson.databind.ObjectMapper().writeValueAsString(edit),
                        first)
                .update();
        String key = "keep-heard-version";
        assertEquals(first, mix.keep(id, command, key).keptTakeId());
        assertEquals(
                hash,
                HexFormat.of()
                        .formatHex(
                                java.security.MessageDigest.getInstance("SHA-256")
                                        .digest(
                                                Files.readAllBytes(
                                                        mix.takePath(id, first, true)))));
        app.close();
        app = launch(root);
        var restored = app.getBean(com.djound.mix.MixProjects.class);
        var replay = restored.keep(id, command, key);
        assertEquals(first, replay.keptTakeId());
        assertEquals(first, restored.edit(id, edit, "preview-key").take().id());
        assertEquals(
                "IDEMPOTENCY_CONFLICT",
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        restored.edit(
                                                id,
                                                new com.djound.mix.MixModels.Edit(
                                                        "lift the drums",
                                                        "original",
                                                        100,
                                                        200,
                                                        null,
                                                        null),
                                                "preview-key"))
                        .code());
        assertEquals(
                1, replay.takes().stream().filter(com.djound.mix.MixModels.Take::kept).count());
        var undone = restored.undo(id, first);
        assertEquals("original", undone.keptTakeId());
        assertEquals(replay.heardTakeId(), undone.heardTakeId());
        restored.heard(id, "original");
        var missingTake =
                restored.edit(
                        id,
                        new com.djound.mix.MixModels.Edit(
                                "Use drums from version 99", "original", 100, 200, null, null),
                        "absent-take");
        assertEquals("CLARIFY", missingTake.status());
        assertEquals("drums", missingTake.control());
        assertEquals(2, restored.state(id).takes().size());
        assertEquals("original", restored.keep(id, command, key).keptTakeId());
        assertEquals(
                "IDEMPOTENCY_CONFLICT",
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        restored.keep(
                                                id,
                                                new com.djound.mix.MixModels.Keep(
                                                        command.takeId(), "different"),
                                                key))
                        .code());
        Files.delete(root.resolve("prepared.npz"));
        assertEquals(
                "MIX_PREPARATION_INVALID",
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        restored.edit(
                                                id,
                                                new com.djound.mix.MixModels.Edit(
                                                        "Bring the vocal forward",
                                                        "original",
                                                        100,
                                                        200,
                                                        null,
                                                        null),
                                                null))
                        .code());
        assertEquals("FAILED", restored.state(id).preparation());
        assertTrue(Files.isRegularFile(restored.takePath(id, first, true)));
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
                        "INSERT INTO"
                            + " recording(id,exploration_id,title,kind,file_name,storage_key,content_type,byte_size,duration_ms,sample_rate,channels,codec,sha256,provenance,kept,created_at)"
                            + " SELECT"
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
                                    single),
                            app.getBean(com.djound.generation.GenerationService.class));
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
    void emptyInitialDraftIsAcceptedThroughRealMultipartHttp() throws Exception {
        String endpoint =
                "http://127.0.0.1:"
                        + app.getEnvironment().getProperty("local.server.port")
                        + "/api/explorations";
        String boundary = "djound-empty-draft";
        byte[] audio = wave(44100, 1, 16, 1);
        String fileHeader =
                "--"
                        + boundary
                        + "\r\n"
                        + "Content-Disposition: form-data; name=\"file\"; filename=\"empty"
                        + " draft.wav\"\r\n"
                        + "Content-Type: audio/wav\r\n\r\n";
        String emptyField =
                "--"
                        + boundary
                        + "\r\nContent-Disposition: form-data; name=\"initialDraft\"\r\n\r\n\r\n";
        String ending = "\r\n--" + boundary + "--\r\n";
        try (var client = java.net.http.HttpClient.newHttpClient()) {
            var payload =
                    java.net.http.HttpRequest.BodyPublishers.concat(
                            java.net.http.HttpRequest.BodyPublishers.ofString(
                                    emptyField + fileHeader),
                            java.net.http.HttpRequest.BodyPublishers.ofByteArray(audio),
                            java.net.http.HttpRequest.BodyPublishers.ofString(ending));
            var request =
                    java.net.http.HttpRequest.newBuilder(java.net.URI.create(endpoint))
                            .header("Content-Type", "multipart/form-data; boundary=" + boundary)
                            .header("Idempotency-Key", "empty-draft-http-key")
                            .POST(payload)
                            .build();
            var response = client.send(request, java.net.http.HttpResponse.BodyHandlers.ofString());
            assertEquals(201, response.statusCode(), response.body());
            var details =
                    app.getBean(Explorations.class)
                            .get(app.getBean(Explorations.class).list().getFirst().id());
            assertEquals("", details.draft().text());
            var missing =
                    java.net.http.HttpRequest.newBuilder(java.net.URI.create(endpoint))
                            .header("Content-Type", "multipart/form-data; boundary=" + boundary)
                            .header("Idempotency-Key", "missing-draft-http-key")
                            .POST(
                                    java.net.http.HttpRequest.BodyPublishers.concat(
                                            java.net.http.HttpRequest.BodyPublishers.ofString(
                                                    fileHeader),
                                            java.net.http.HttpRequest.BodyPublishers.ofByteArray(
                                                    audio),
                                            java.net.http.HttpRequest.BodyPublishers.ofString(
                                                    ending)))
                            .build();
            assertEquals(
                    400,
                    client.send(missing, java.net.http.HttpResponse.BodyHandlers.ofString())
                            .statusCode());
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
