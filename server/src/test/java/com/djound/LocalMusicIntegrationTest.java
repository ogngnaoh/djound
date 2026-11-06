package com.djound;

import static org.junit.jupiter.api.Assertions.*;

import com.djound.generation.*;
import com.djound.local.*;
import com.djound.local.LocalModels.*;

import org.junit.jupiter.api.*;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.boot.SpringApplication;
import org.springframework.context.ConfigurableApplicationContext;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.mock.web.MockMultipartFile;

import java.nio.file.*;
import java.sql.*;
import java.util.*;
import java.util.concurrent.TimeUnit;

/**
 * Real DB/process/publication boundaries with an explicit fake worker; no model
 * downloads/inference.
 */
class LocalMusicIntegrationTest {
    @TempDir Path root;
    String schema, url;
    ConfigurableApplicationContext app;
    Path script;

    @BeforeEach
    void start() throws Exception {
        schema = "local_test_" + UUID.randomUUID().toString().replace("-", "");
        try (var c =
                        DriverManager.getConnection(
                                BackendIntegrationTest.BASE,
                                BackendIntegrationTest.USER,
                                BackendIntegrationTest.PASSWORD);
                var st = c.createStatement()) {
            st.execute("CREATE SCHEMA " + schema);
        }
        url =
                BackendIntegrationTest.BASE
                        + (BackendIntegrationTest.BASE.contains("?") ? "&" : "?")
                        + "currentSchema="
                        + schema;
        script = root.resolve("fake-worker.py");
        Files.writeString(
                script,
"""
import argparse,json,pathlib,sys,wave,math,struct,time,subprocess,os
p=argparse.ArgumentParser();p.add_argument('--check',action='store_true');p.add_argument('--request');p.add_argument('--output');a=p.parse_args()
if a.check: print(json.dumps({'schemaVersion':1,'ready':True}));sys.exit()
r=json.loads(pathlib.Path(a.request).read_text());out=pathlib.Path(a.output);out.mkdir();op=r['operation'];base=pathlib.Path(os.environ['DJOUND_LOCAL_DATA_ROOT'])
if (base/('fail-'+op)).exists():
 (out/'result.json').write_text(json.dumps({'schemaVersion':1,'status':'failed','operation':op,'error':{'code':'LOCAL_ENGINE_FAILED','message':'Explicit worker failure test'}}));sys.exit(2)
if (base/('pause-'+op)).exists():
 child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
 (base/'child.pid').write_text(str(child.pid));(base/'started').write_text(op);time.sleep(60)
result={'schemaVersion':1,'status':'succeeded','operation':op,'provenance':{'engine':'explicit-test-double'}}
if op=='plan':
 recipe=dict(r['parentRecipe']);recipe['instrument']='electric_piano'
 result.update(recipe=recipe,explanation='Switch the confirmed melody to electric piano.')
 if (base/'plan-result.json').exists(): result.update(json.loads((base/'plan-result.json').read_text()))
else:
 path=out/'audio.wav'
 with wave.open(str(path),'wb') as w:
  w.setparams((1,2,44100,0,'NONE','not compressed'));w.writeframes(b''.join(struct.pack('<h',int(8000*math.sin(i*2*math.pi*440/44100))) for i in range(44100)))
 result.update(audioPath=str(path),duration=1.0,notes=[{'start':0.1,'end':0.8,'pitch':69,'velocity':80}])
(out/'result.json').write_text(json.dumps(result))
""");
        app = launch();
    }

    ConfigurableApplicationContext launch() {
        return SpringApplication.run(
                DjoundApplication.class,
                "--server.port=0",
                "--spring.datasource.url=" + url,
                "--spring.datasource.username=" + BackendIntegrationTest.USER,
                "--spring.datasource.password=" + BackendIntegrationTest.PASSWORD,
                "--djound.data-dir=" + root,
                "--djound.mode=LOCAL",
                "--djound.local-python=/usr/bin/python3",
                "--djound.local-worker=" + script,
                "--spring.main.banner-mode=off");
    }

    @AfterEach
    void close() throws Exception {
        if (app != null) app.close();
        try (var c =
                        DriverManager.getConnection(
                                BackendIntegrationTest.BASE,
                                BackendIntegrationTest.USER,
                                BackendIntegrationTest.PASSWORD);
                var st = c.createStatement()) {
            st.execute("DROP SCHEMA " + schema + " CASCADE");
        }
    }

    LocalMusic local() {
        return app.getBean(LocalMusic.class);
    }

    GenerationService gen() {
        return app.getBean(GenerationService.class);
    }

    Explorations ex() {
        return app.getBean(Explorations.class);
    }

    JdbcClient db() {
        return app.getBean(JdbcClient.class);
    }

    String source() throws Exception {
        return ex().create(
                        new MockMultipartFile(
                                "file",
                                "melody.wav",
                                "audio/wav",
                                BackendIntegrationTest.wave(44100, 1, 16, 1)),
                        "electric piano",
                        "local-source-key")
                .explorationId();
    }

    Job completed(Job j) throws Exception {
        long until = System.nanoTime() + TimeUnit.SECONDS.toNanos(12);
        while (System.nanoTime() < until) {
            var current = local().job(j.id());
            if (Set.of("SUCCEEDED", "FAILED").contains(current.state())) return current;
            Thread.sleep(30);
        }
        throw new AssertionError("Preparation did not complete");
    }

    Models.Attempt finished(String id) throws Exception {
        long until = System.nanoTime() + TimeUnit.SECONDS.toNanos(12);
        while (System.nanoTime() < until) {
            var a = gen().get(id);
            if (Set.of("SUCCEEDED", "FAILED").contains(a.state())) return a;
            Thread.sleep(30);
        }
        throw new AssertionError("Creation did not complete");
    }

    Notes confirmed(String id) throws Exception {
        assertEquals("SUCCEEDED", completed(local().transcribe(id, new SourceCommand(1L))).state());
        var n = local().state(id).notes();
        assertNotNull(n.previewUrl());
        return local().saveNotes(id, new NotesCommand(1L, n.revision(), n.notes(), true));
    }

    Plan planned(String id, Notes n) throws Exception {
        assertEquals(
                "SUCCEEDED",
                completed(
                                local().propose(
                                                id,
                                                new PlanCommand(
                                                        1L,
                                                        n.revision(),
                                                        1L,
                                                        "electric piano",
                                                        null)))
                        .state());
        return local().state(id).plan();
    }

    Models.CreateRequestCommand command(Plan p) {
        return new Models.CreateRequestCommand(
                p.text(),
                p.basisRecordingId(),
                p.sourceRevision(),
                p.draftRevision(),
                null,
                p.id());
    }

    @Test
    void mixedRecordingKeepsFrozenNotesAndControlsWhenTheNextVersionReturnsToUnity()
            throws Exception {
        String id = source();
        var notes = confirmed(id);
        var direct =
                local().manual(
                                id,
                                new PlanCommand(1L, notes.revision(), 1L, "electric piano", null));
        var unchanged =
                new CorrectionCommand(
                        1L, notes.revision(), 1L, direct.text(), null, direct.recipe());
        assertEquals(direct, local().correct(direct.id(), unchanged));
        var recipe = new LinkedHashMap<>(direct.recipe());
        var lead = Map.of("gainDb", -4.5, "pan", -0.25, "muted", false);
        var neutral = Map.of("gainDb", 0, "pan", 0, "muted", false);
        recipe.put("bass", true);
        recipe.put("mix", Map.of("lead", lead, "bass", neutral));
        var correction =
                new CorrectionCommand(1L, notes.revision(), 1L, direct.text(), null, recipe);
        var mixed = local().correct(direct.id(), correction);
        assertEquals(Map.of("lead", lead), mixed.recipe().get("mix"));
        assertEquals(mixed.id(), local().correct(mixed.id(), correction).id());
        assertFalse(local().plan(direct.id()).recipe().containsKey("mix"));
        var render = local().renderRequest(mixed.id(), id);
        assertEquals(notes.notes(), render.get("notes"));
        assertEquals(mixed.recipe(), render.get("recipe"));
        var created = finished(gen().create(id, command(mixed), "mixed-version-key").attemptId());
        assertEquals("SUCCEEDED", created.state());
        var recording = ex().recording(created.resultRecordingId());
        assertEquals(mixed.recipe(), recording.localRecipe());
        assertTrue(recording.direction().contains("lead -4.5 dB, 25% left, unmuted"));
        assertEquals(
                "STALE_DRAFT",
                assertThrows(ApiProblem.class, () -> local().correct(mixed.id(), correction))
                        .code());

        var draft =
                ex().saveDraft(
                                id,
                                new Models.DraftUpdate(
                                        "Center the lead",
                                        recording.id(),
                                        ex().get(id).draft().revision()));
        var next =
                local().manual(
                                id,
                                new PlanCommand(
                                        1L,
                                        notes.revision(),
                                        draft.revision(),
                                        draft.text(),
                                        recording.id()));
        assertEquals(mixed.recipe(), next.basisRecipe());
        var reset = new LinkedHashMap<>(next.recipe());
        reset.remove("mix");
        var resetCommand =
                new CorrectionCommand(
                        1L,
                        notes.revision(),
                        draft.revision(),
                        draft.text(),
                        recording.id(),
                        reset);
        var restored = local().correct(next.id(), resetCommand);
        assertNotEquals(next.id(), restored.id());
        assertFalse(restored.recipe().containsKey("mix"));
        assertEquals(restored, local().correct(restored.id(), resetCommand));
        assertEquals(4, db().sql("SELECT count(*) FROM local_plan").query(Integer.class).single());
        assertTrue(restored.summary().contains("lead reset to 0 dB"));
        assertEquals(
                mixed.recipe(),
                ((Map<?, ?>) restored.model().get("correction")).get("editedFromRecipe"));
        var child = finished(gen().create(id, command(restored), "unity-version-key").attemptId());
        assertEquals("SUCCEEDED", child.state());
        assertTrue(
                child.request().direction().contains("lead reset to 0 dB, centered and unmuted"));
        assertEquals(restored.recipe(), ex().recording(child.resultRecordingId()).localRecipe());
        assertEquals(mixed.recipe(), ex().recording(recording.id()).localRecipe());
    }

    @Test
    void validatesWorkerMixerOutputAndKeepsGuidedCorrectionsDistinctFromModelPredictions()
            throws Exception {
        String id = source();
        var n = confirmed(id);
        var command = new PlanCommand(1L, n.revision(), 1L, "electric piano", null);
        String result =
                """
                {"provenance":{"origin":"GUIDED_COMMANDS","inferenceAttempts":0},"recipe":{
                  "instrument":"piano","bass":false,"drums":false,"bpm":100,"density":"steady",
                  "backingVolume":"normal","mix":{"lead":{"gainDb":-3,"pan":0,"muted":false}}}}
                """;
        var fixture = root.resolve("plan-result.json");
        Files.writeString(fixture, result.replace("\"lead\"", "\"drums\""));
        var rejected = completed(local().propose(id, command));
        assertEquals("FAILED", rejected.state());
        assertEquals("PLAN_INVALID", rejected.outcomeCode());
        Files.writeString(fixture, result);
        assertEquals("SUCCEEDED", completed(local().propose(id, command)).state());
        var guided = local().state(id).plan();
        var recipe = new LinkedHashMap<>(guided.recipe());
        recipe.remove("mix");
        var corrected =
                local().correct(
                                guided.id(),
                                new CorrectionCommand(
                                        1L, n.revision(), 1L, guided.text(), null, recipe));
        var provenance = (Map<?, ?>) corrected.model().get("correction");
        assertEquals(guided.recipe(), provenance.get("commandRecipe"));
        assertFalse(provenance.containsKey("modelSuggestedRecipe"));
        assertEquals(0, corrected.model().get("inferenceAttempts"));
    }

    @Test
    void confirmedPlanCreatesExactlyOneDurableLocalRecordingAndReplaySurvivesRestart()
            throws Exception {
        String id = source();
        var n = confirmed(id);
        var p = planned(id, n);
        var receipt = gen().create(id, command(p), "local-generate-key");
        var done = finished(receipt.attemptId());
        assertEquals("SUCCEEDED", done.state());
        assertEquals("LOCAL", done.request().provenance());
        assertTrue(gen().create(id, command(p), "local-generate-key").replayed());
        assertEquals(1, ex().get(id).renditions().size());
        var recording = ex().setKept(done.resultRecordingId(), new Models.RecordingUpdate(true));
        assertNull(ex().get(id).original().localRecipe());
        assertEquals(p.recipe(), recording.localRecipe());
        assertEquals(p.recipe(), ex().get(id).renditions().getFirst().localRecipe());
        assertEquals(p.recipe(), ex().list().getFirst().keptRecordings().getFirst().localRecipe());
        var savedDraft =
                ex().saveDraft(
                                id,
                                new Models.DraftUpdate(
                                        "Use guitar next",
                                        recording.id(),
                                        ex().get(id).draft().revision()));
        var newPlan =
                local().manual(
                                id,
                                new PlanCommand(
                                        1L,
                                        n.revision(),
                                        savedDraft.revision(),
                                        savedDraft.text(),
                                        recording.id()));
        var edited = new LinkedHashMap<>(newPlan.recipe());
        edited.put("instrument", "guitar");
        local().correct(
                        newPlan.id(),
                        new CorrectionCommand(
                                1L,
                                n.revision(),
                                savedDraft.revision(),
                                savedDraft.text(),
                                recording.id(),
                                edited));
        // A newer, different proposal cannot change a saved rendition's immutable settings.
        assertEquals(p.recipe(), ex().recording(recording.id()).localRecipe());
        String hash = recording.sha256();
        assertEquals(
                0,
                db().sql("SELECT count(*) FROM live_submission_reservation")
                        .query(Integer.class)
                        .single());
        app.close();
        app = launch();
        assertEquals(hash, ex().recording(recording.id()).sha256());
        assertTrue(ex().recording(recording.id()).kept());
        assertEquals(p.recipe(), ex().recording(recording.id()).localRecipe());
        assertEquals("CONFIRMED", local().state(id).notes().status());
    }

    @Test
    void completedPlanningJobKeepsItsOwnProposalAfterAnotherReview() throws Exception {
        String id = source();
        var notes = confirmed(id);
        var draft = ex().get(id).draft();
        var command = new PlanCommand(1L, notes.revision(), draft.revision(), draft.text(), null);
        var job = completed(local().propose(id, command));
        assertEquals("SUCCEEDED", job.state());
        var first = local().state(id).plan();
        var later = local().manual(id, command);
        assertNotEquals(first.id(), later.id());
        assertEquals(later.id(), local().state(id).plan().id());
        assertEquals(first, local().result(job.id()).plan());
        assertEquals(job, local().result(job.id()).job());
    }

    @Test
    void directArrangementWorksAfterPlannerFailureAndInheritsChosenVersion() throws Exception {
        String id = source();
        var n = confirmed(id);
        var draft = ex().get(id).draft();
        var request = new PlanCommand(1L, n.revision(), draft.revision(), draft.text(), null);
        Files.writeString(root.resolve("fail-plan"), "fail all planner calls");
        assertEquals("FAILED", completed(local().propose(id, request)).state());
        int jobs = db().sql("SELECT count(*) FROM local_job").query(Integer.class).single();
        var direct = local().manual(id, request);
        assertEquals("USER_DEFINED", direct.model().get("origin"));
        var recipe = new LinkedHashMap<>(direct.recipe());
        recipe.put("instrument", "guitar");
        recipe.put("bass", true);
        var corrected =
                local().correct(
                                direct.id(),
                                new CorrectionCommand(
                                        1L,
                                        n.revision(),
                                        draft.revision(),
                                        draft.text(),
                                        null,
                                        recipe));
        assertEquals(
                jobs, db().sql("SELECT count(*) FROM local_job").query(Integer.class).single());
        assertFalse(
                ((Map<?, ?>) corrected.model().get("correction"))
                        .containsKey("modelSuggestedRecipe"));
        var revised =
                local().correct(
                                corrected.id(),
                                new CorrectionCommand(
                                        1L,
                                        n.revision(),
                                        draft.revision(),
                                        draft.text(),
                                        null,
                                        recipe));
        var receipt = gen().create(id, command(revised), "manual-creation-key");
        var done = finished(receipt.attemptId());
        assertEquals("SUCCEEDED", done.state());
        var nextDraft =
                ex().saveDraft(
                                id,
                                new Models.DraftUpdate(
                                        "Keep my guitar",
                                        done.resultRecordingId(),
                                        ex().get(id).draft().revision()));
        var next =
                local().manual(
                                id,
                                new PlanCommand(
                                        1L,
                                        n.revision(),
                                        nextDraft.revision(),
                                        nextDraft.text(),
                                        done.resultRecordingId()));
        assertEquals(recipe, next.recipe());
        assertEquals(recipe, next.basisRecipe());
        assertEquals(
                "STALE_DRAFT",
                assertThrows(ApiProblem.class, () -> local().manual(id, request)).code());
        assertEquals(
                "NOTES_CHANGED",
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        local().manual(
                                                        id,
                                                        new PlanCommand(
                                                                1L,
                                                                n.revision() + 1,
                                                                nextDraft.revision(),
                                                                nextDraft.text(),
                                                                null)))
                        .code());
        assertEquals(
                "LOCAL_BASIS_UNSUPPORTED",
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        local().manual(
                                                        id,
                                                        new PlanCommand(
                                                                1L,
                                                                n.revision(),
                                                                nextDraft.revision(),
                                                                nextDraft.text(),
                                                                "missing")))
                        .code());
        assertEquals(
                jobs, db().sql("SELECT count(*) FROM local_job").query(Integer.class).single());
    }

    @Test
    void stalePlansAndUnpreviewedChangesCannotGenerate() throws Exception {
        String id = source();
        var n = confirmed(id);
        var p = planned(id, n);
        assertEquals(
                "NOTES_OVERLAP",
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        local().saveNotes(
                                                        id,
                                                        new NotesCommand(
                                                                1L,
                                                                n.revision(),
                                                                List.of(
                                                                        new Note(.1, .8, 60, 80),
                                                                        new Note(.2, .6, 60, 80)),
                                                                false)))
                        .code());
        var changed = List.of(new Note(.1, .7, 67, 80));
        assertEquals(
                "PREVIEW_REQUIRED",
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        local().saveNotes(
                                                        id,
                                                        new NotesCommand(
                                                                1L, n.revision(), changed, true)))
                        .code());
        var edited = local().saveNotes(id, new NotesCommand(1L, n.revision(), changed, false));
        assertNull(edited.previewUrl());
        assertEquals(
                "NOTES_CHANGED",
                assertThrows(
                                ApiProblem.class,
                                () -> gen().create(id, command(p), "stale-note-plan-key"))
                        .code());
        assertEquals(
                "SUCCEEDED",
                completed(local().preview(id, new RevisionCommand(1L, edited.revision()))).state());
        var previewed = local().state(id).notes();
        var accepted =
                local().saveNotes(
                                id,
                                new NotesCommand(
                                        1L, previewed.revision(), previewed.notes(), true));
        assertNotNull(accepted.previewUrl());
        var latest = planned(id, accepted);
        ex().saveDraft(id, new Models.DraftUpdate("guitar", null, 1L));
        assertEquals(
                "STALE_DRAFT",
                assertThrows(
                                ApiProblem.class,
                                () -> gen().create(id, command(latest), "stale-text-plan-key"))
                        .code());
        assertEquals(0, ex().get(id).renditions().size());
    }

    @Test
    void cancellationStopsPreparationChildrenAndPreventsGenerationPublication() throws Exception {
        String id = source();
        Files.writeString(root.resolve("pause-transcribe"), "1");
        var j = local().transcribe(id, new SourceCommand(1L));
        waitStarted();
        long child = Long.parseLong(Files.readString(root.resolve("child.pid")));
        var cancelled = local().cancel(j.id());
        assertEquals("FAILED", cancelled.state());
        assertEquals("CANCELLED", cancelled.outcomeCode());
        assertFalse(ProcessHandle.of(child).map(ProcessHandle::isAlive).orElse(false));
        assertNull(local().state(id).notes());
        Files.delete(root.resolve("pause-transcribe"));
        Files.delete(root.resolve("started"));
        var n = confirmed(id);
        var p = planned(id, n);
        Files.writeString(root.resolve("pause-render"), "1");
        var receipt = gen().create(id, command(p), "cancel-local-render-key");
        waitStarted();
        child = Long.parseLong(Files.readString(root.resolve("child.pid")));
        var done = gen().cancelLocal(receipt.attemptId());
        assertEquals("FAILED", done.state());
        assertEquals("CANCELLED", done.outcome().code());
        assertFalse(ProcessHandle.of(child).map(ProcessHandle::isAlive).orElse(false));
        assertEquals(0, ex().get(id).renditions().size());
    }

    @Test
    void localRefinementFreezesRecipeWithoutAccumulatingLiteralPromptHistory() throws Exception {
        String id = source();
        var n = confirmed(id);
        var p = planned(id, n);
        var first = finished(gen().create(id, command(p), "initial-local-plan-key").attemptId());
        assertEquals("local-plan-v1", first.request().assemblerVersion());
        assertEquals(p.text(), first.request().text());
        assertEquals(
                "electric piano\n\n"
                    + "Reviewed arrangement: Electric piano; bass off; drums off; steady, normal"
                    + " backing; 100 BPM backing pulse.",
                first.request().direction());
        assertTrue(first.request().assembledInstructions().contains("\"recipe\""));
        db().sql("UPDATE recording SET direction=? WHERE id=?")
                .params("historic instructions ".repeat(1500), first.resultRecordingId())
                .update();
        assertEquals(
                "SUCCEEDED",
                completed(
                                local().propose(
                                                id,
                                                new PlanCommand(
                                                        1L,
                                                        n.revision(),
                                                        2L,
                                                        "Less backing, same electric piano",
                                                        first.resultRecordingId())))
                        .state());
        var next = local().state(id).plan();
        var second =
                finished(gen().create(id, command(next), "refined-local-plan-key").attemptId());
        assertEquals("SUCCEEDED", second.state());
        assertEquals("", second.request().inheritedDirection());
        assertEquals(next.text(), second.request().text());
        assertEquals(
                "Less backing, same electric piano\n\n"
                    + "Reviewed arrangement: Electric piano; bass off; drums off; steady, normal"
                    + " backing; 100 BPM backing pulse.",
                second.request().direction());
        assertTrue(second.request().assembledInstructions().length() < 1000);
        db().sql("UPDATE recording SET provenance='FIXTURE' WHERE id=?")
                .param(first.resultRecordingId())
                .update();
        assertEquals(
                "LOCAL_BASIS_UNSUPPORTED",
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        local().propose(
                                                        id,
                                                        new PlanCommand(
                                                                1L,
                                                                n.revision(),
                                                                3L,
                                                                "Guitar",
                                                                first.resultRecordingId())))
                        .code());
    }

    @Test
    void workerFailureRequiresDeliberateFrozenRetryWithoutPublishingPartialAudio()
            throws Exception {
        String id = source();
        var n = confirmed(id);
        var p = planned(id, n);
        Files.writeString(root.resolve("fail-render"), "1");
        var receipt = gen().create(id, command(p), "worker-failure-key");
        var failed = finished(receipt.attemptId());
        assertEquals("FAILED", failed.state());
        assertEquals("LOCAL_ENGINE_FAILED", failed.outcome().code());
        assertTrue(failed.canRetry());
        assertEquals(0, ex().get(id).renditions().size());
        Files.delete(root.resolve("fail-render"));
        var retry =
                gen().retry(
                                receipt.requestId(),
                                new Models.RetryCommand(failed.id()),
                                "deliberate-worker-retry");
        assertEquals("SUCCEEDED", finished(retry.attemptId()).state());
        assertEquals(p.id(), gen().localPlanId(receipt.requestId()));
        assertEquals(1, ex().get(id).renditions().size());
    }

    @Test
    void startupFailsInterruptedLocalJobsWithoutReexecutingOrCreatingRemoteDebt() throws Exception {
        String id = source();
        var n = confirmed(id);
        var p = planned(id, n);
        var receipt = gen().create(id, command(p), "before-interruption-key");
        assertEquals("SUCCEEDED", finished(receipt.attemptId()).state());
        String interrupted = "attempt_" + UUID.randomUUID();
        db().sql(
                        "INSERT INTO"
                            + " generation_attempt(id,request_id,exploration_id,state,execution_count,created_at,updated_at)"
                            + " VALUES(?,?,?,'RUNNING',1,now(),now())")
                .params(interrupted, receipt.requestId(), id)
                .update();
        db().sql("UPDATE workspace SET active_attempt_id=? WHERE id=1").param(interrupted).update();
        String job = "local_" + UUID.randomUUID();
        db().sql(
                        "INSERT INTO"
                            + " local_job(id,exploration_id,kind,state,source_revision,payload_json)"
                            + " VALUES(?,?,'PLAN','RUNNING',1,'{}')")
                .params(job, id)
                .update();
        app.close();
        app = launch();
        assertEquals("PROCESS_INTERRUPTED", gen().get(interrupted).outcome().code());
        assertEquals("PROCESS_INTERRUPTED", local().job(job).outcomeCode());
        assertNull(gen().unknownOutcome());
        assertEquals(1, ex().get(id).renditions().size());
        assertEquals(
                1,
                db().sql("SELECT execution_count FROM generation_attempt WHERE id=?")
                        .param(interrupted)
                        .query(Integer.class)
                        .single());
        assertFalse(Files.exists(root.resolve("local-jobs").resolve(interrupted)));
    }

    @Test
    void userCorrectionIsImmutableAuditableAndDoesNotRerunTheModel() throws Exception {
        String id = source();
        var n = confirmed(id);
        var original = planned(id, n);
        assertEquals(
                original,
                local().correct(
                                original.id(),
                                new CorrectionCommand(
                                        1L,
                                        n.revision(),
                                        1L,
                                        original.text(),
                                        original.basisRecordingId(),
                                        original.recipe())));
        Map<String, Object> guitar = new LinkedHashMap<>(original.recipe());
        guitar.put("instrument", "guitar");
        var first =
                local().correct(
                                original.id(),
                                new CorrectionCommand(
                                        1L,
                                        n.revision(),
                                        1L,
                                        original.text(),
                                        original.basisRecordingId(),
                                        guitar));
        Map<String, Object> bass = new LinkedHashMap<>(guitar);
        bass.put("bass", true);
        var second =
                local().correct(
                                first.id(),
                                new CorrectionCommand(
                                        1L,
                                        n.revision(),
                                        1L,
                                        first.text(),
                                        first.basisRecordingId(),
                                        bass));
        assertNotEquals(original.id(), first.id());
        assertNotEquals(first.id(), second.id());
        assertEquals(original.recipe(), local().plan(original.id()).recipe());
        assertEquals(guitar, local().plan(first.id()).recipe());
        var provenance = (Map<?, ?>) second.model().get("correction");
        assertEquals("USER_EDIT", provenance.get("kind"));
        assertEquals(first.id(), provenance.get("parentPlanId"));
        assertEquals(original.id(), provenance.get("originalPlanId"));
        assertEquals(original.recipe(), provenance.get("modelSuggestedRecipe"));
        assertEquals(
                1,
                db().sql("SELECT count(*) FROM local_job WHERE kind='PLAN'")
                        .query(Integer.class)
                        .single());
        assertEquals(n.revision(), local().state(id).notes().revision());
        Map<String, Object> invalid = new LinkedHashMap<>(bass);
        invalid.put("instrument", "violin");
        assertEquals(
                "PLAN_INVALID",
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        local().correct(
                                                        second.id(),
                                                        new CorrectionCommand(
                                                                1L,
                                                                n.revision(),
                                                                1L,
                                                                second.text(),
                                                                second.basisRecordingId(),
                                                                invalid)))
                        .code());
        assertEquals(
                "NOTES_CHANGED",
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        local().correct(
                                                        second.id(),
                                                        new CorrectionCommand(
                                                                1L,
                                                                n.revision() + 1,
                                                                1L,
                                                                second.text(),
                                                                second.basisRecordingId(),
                                                                bass)))
                        .code());
        var receipt = gen().create(id, command(second), "corrected-local-plan-key");
        var rendered = finished(receipt.attemptId());
        assertEquals("SUCCEEDED", rendered.state());
        assertEquals(original.text(), rendered.request().text());
        assertEquals(
                "electric piano\n\n"
                    + "Reviewed arrangement: Guitar; bass on; drums off; steady, normal backing;"
                    + " 100 BPM backing pulse.",
                rendered.request().direction());
        assertEquals(
                rendered.request().direction(),
                ex().recording(rendered.resultRecordingId()).direction());
        assertEquals(second.id(), gen().localPlanId(receipt.requestId()));
        assertTrue(
                Files.readString(
                                root.resolve("local-jobs")
                                        .resolve(receipt.attemptId())
                                        .resolve("request.json"))
                        .contains("guitar"));
        assertEquals(
                "STALE_DRAFT",
                assertThrows(
                                ApiProblem.class,
                                () ->
                                        local().correct(
                                                        second.id(),
                                                        new CorrectionCommand(
                                                                1L,
                                                                n.revision(),
                                                                1L,
                                                                second.text(),
                                                                second.basisRecordingId(),
                                                                bass)))
                        .code());
    }

    void waitStarted() throws Exception {
        long until = System.nanoTime() + TimeUnit.SECONDS.toNanos(10);
        while (!Files.exists(root.resolve("started")) && System.nanoTime() < until)
            Thread.sleep(30);
        assertTrue(Files.exists(root.resolve("started")));
    }
}
