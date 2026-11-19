package com.djound.mix;

import static com.djound.mix.MixModels.*;
import static com.djound.mix.MixStore.*;

import com.djound.ApiProblem;
import com.djound.MediaStore;
import com.djound.local.LocalWorker;

import org.slf4j.*;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.*;
import org.springframework.web.multipart.MultipartFile;

import java.io.*;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;

@Service
public class MixProjects implements AutoCloseable {
    private static final Logger log = LoggerFactory.getLogger(MixProjects.class);
    private final MixStore store;
    private final MixArtifacts files;
    private final MediaStore media;
    private final LocalWorker worker;
    private final TransactionTemplate tx;
    private final MixConversation conversation;
    private final ExecutorService preparing = Executors.newSingleThreadExecutor();
    private final ExecutorService decoding = Executors.newVirtualThreadPerTaskExecutor();
    private volatile boolean closing;

    MixProjects(
            MixStore store,
            MixArtifacts files,
            MediaStore media,
            LocalWorker worker,
            TransactionTemplate tx,
            MixConversation conversation) {
        this.store = store;
        this.files = files;
        this.media = media;
        this.worker = worker;
        this.tx = tx;
        this.conversation = conversation;
        // A server restart cannot transfer ownership of a running subprocess.
        store.db
                .sql(
                        """
                        UPDATE mix_project SET preparation='FAILED',job_id=NULL,revision=revision+1,
                        error='Preparation was interrupted. Retry to continue with the saved recording.'
                        WHERE preparation IN ('DECODING','PREPARING')
                        """)
                .update();
    }

    public List<State> list() {
        return store
                .db
                .sql("SELECT * FROM mix_project ORDER BY created_at DESC")
                .query(Project.class)
                .list()
                .stream()
                .map(store::state)
                .toList();
    }

    public State state(String id) {
        return store.state(store.project(id, false));
    }

    public State create(MultipartFile input, String key) throws IOException {
        if (key != null) validateKey(key);
        try (var upload = media.ingest(input)) {
            if (!upload.codec().equals("mp3") || upload.channels() != 2)
                throw new ApiProblem(
                        422,
                        "MIX_STEREO_MP3_REQUIRED",
                        "Choose one finished stereo MP3.",
                        "CHOOSE_FILE");
            String id =
                    tx.execute(
                            status -> {
                                store.db
                                        .sql("SELECT id FROM workspace WHERE id=1 FOR UPDATE")
                                        .query(Integer.class)
                                        .single();
                                if (key != null) {
                                    var previous =
                                            store.db
                                                    .sql(
                                                            "SELECT * FROM mix_project WHERE"
                                                                    + " import_key=?")
                                                    .param(key)
                                                    .query(Project.class)
                                                    .optional();
                                    if (previous.isPresent()) {
                                        Project p = previous.get();
                                        if (!p.importSha().equals(upload.hash())
                                                || !p.title().equals(upload.fileName()))
                                            throw keyConflict();
                                        return p.id();
                                    }
                                }
                                String project = UUID.randomUUID().toString(), job = jobId();
                                Path path = files.uploaded(project);
                                try {
                                    Files.createLink(path, upload.path());
                                } catch (IOException e) {
                                    throw new UncheckedIOException(e);
                                }
                                TransactionSynchronizationManager.registerSynchronization(
                                        new TransactionSynchronization() {
                                            @Override
                                            public void afterCompletion(int result) {
                                                if (result == STATUS_ROLLED_BACK)
                                                    try {
                                                        Files.deleteIfExists(path);
                                                    } catch (IOException e) {
                                                        log.warn(
                                                                "Could not remove rolled back"
                                                                        + " import {}",
                                                                project,
                                                                e);
                                                    }
                                            }
                                        });
                                store.db
                                        .sql(
                                                """
                                                INSERT INTO mix_project(id,title,import_key,import_sha,duration_ms,preparation,job_id)
                                                VALUES(?,?,?,?,?,'DECODING',?)
                                                """)
                                        .params(
                                                project,
                                                upload.fileName(),
                                                key,
                                                upload.hash(),
                                                upload.durationMs(),
                                                job)
                                        .update();
                                afterCommit(() -> decoding.submit(() -> decode(project, job)));
                                return project;
                            });
            return state(id);
        }
    }

    public State retry(String id) {
        tx.executeWithoutResult(
                status -> {
                    Project project = store.project(id, true);
                    if (!"FAILED".equals(project.preparation())) return;
                    if (project.sourcePath() == null) {
                        String job = jobId();
                        startJob(id, job, "DECODING");
                        afterCommit(() -> decoding.submit(() -> decode(id, job)));
                    } else if (project.passageId() != null) {
                        selectPassage(project, store.passage(id, project.passageId()));
                    }
                });
        return state(id);
    }

    public State select(String id, Select command) {
        tx.executeWithoutResult(
                status -> {
                    Project project = store.project(id, true);
                    if (project.frames() == null)
                        throw conflict("Wait for the decoded song to finish preparing.");
                    if (command.startFrame() == null
                            || command.endFrame() == null
                            || command.startFrame() < 0
                            || command.endFrame() > project.frames()
                            || command.endFrame() <= command.startFrame()
                            || command.endFrame() - command.startFrame() > 30 * MixArtifacts.RATE)
                        throw new ApiProblem(
                                422,
                                "MIX_RANGE_INVALID",
                                "Select a passage up to 30 seconds inside the song.",
                                "SELECT_PASSAGE");
                    Passage current = store.passage(id, project.passageId());
                    String base = project.keptTake();
                    if (command.basisTakeId() != null) {
                        if (ORIGINAL.equals(command.basisTakeId())) base = ORIGINAL;
                        else {
                            SavedTake basis = store.take(id, command.basisTakeId());
                            if (basis.startFrame() == command.startFrame()
                                    && basis.endFrame() == command.endFrame())
                                base = basis.baseId();
                            else base = basis.id();
                        }
                    } else if (current != null
                            && current.startFrame() == command.startFrame()
                            && current.endFrame() == command.endFrame()) {
                        base = current.baseId();
                    }
                    store.db
                            .sql(
                                    """
                                    INSERT INTO mix_passage(id,project_id,base_id,start_frame,end_frame) VALUES(?,?,?,?,?)
                                    ON CONFLICT(project_id,base_id,start_frame,end_frame) DO NOTHING
                                    """)
                            .params(
                                    UUID.randomUUID().toString(),
                                    id,
                                    base,
                                    command.startFrame(),
                                    command.endFrame())
                            .update();
                    Passage chosen =
                            store.db
                                    .sql(
                                            "SELECT * FROM mix_passage WHERE project_id=? AND"
                                                + " base_id=? AND start_frame=? AND end_frame=?")
                                    .params(id, base, command.startFrame(), command.endFrame())
                                    .query(Passage.class)
                                    .single();
                    selectPassage(project, chosen);
                });
        return state(id);
    }

    private void selectPassage(Project project, Passage passage) {
        String id = project.id();
        boolean current = passage.id().equals(project.passageId());
        if (current && "PREPARING".equals(project.preparation())) return;
        boolean ready =
                !"FAILED".equals(project.preparation())
                        && files.matches(passage.prepPath(), passage.prepSha());
        if (current && ready && "READY".equals(project.preparation())) return;
        String job = ready ? null : jobId();
        store.db
                .sql(
                        """
                        UPDATE mix_project SET passage_id=?,preparation=?,job_id=?,error=NULL,revision=revision+1 WHERE id=?
                        """)
                .params(passage.id(), ready ? "READY" : "PREPARING", job, id)
                .update();
        if (!ready) afterCommit(() -> preparing.submit(() -> prepare(id, passage, job)));
    }

    private void startJob(String id, String job, String phase) {
        store.db
                .sql(
                        "UPDATE mix_project SET"
                            + " preparation=?,job_id=?,error=NULL,revision=revision+1 WHERE id=?")
                .params(phase, job, id)
                .update();
    }

    private boolean superseded(String id, String job) {
        return closing
                || !store.db
                        .sql("SELECT EXISTS(SELECT 1 FROM mix_project WHERE id=? AND job_id=?)")
                        .params(id, job)
                        .query(Boolean.class)
                        .single();
    }

    private void decode(String id, String job) {
        try {
            if (superseded(id, job)) return;
            Project project = store.project(id, false);
            var result =
                    worker.execute(
                            job,
                            Map.of(
                                    "schemaVersion",
                                    1,
                                    "operation",
                                    "mix-decode",
                                    "sourceAudio",
                                    files.uploaded(id).toString(),
                                    "sourceSha256",
                                    project.importSha()),
                            () -> superseded(id, job));
            var wave = files.wave(job, result, null);
            store.db
                    .sql(
                            """
                            UPDATE mix_project SET preparation='IDLE',frames=?,duration_ms=?,source_path=?,source_sha=?,
                            job_id=NULL,error=NULL,revision=revision+1 WHERE id=? AND job_id=?
                            """)
                    .params(
                            wave.frames(),
                            Math.round(wave.frames() * 1000.0 / MixArtifacts.RATE),
                            files.storePath(wave.file().path()),
                            wave.file().sha(),
                            id,
                            job)
                    .update();
        } catch (Exception e) {
            failed(id, job, e);
        }
    }

    private void prepare(String id, Passage passage, String job) {
        try {
            if (superseded(id, job)) return;
            Project project = store.project(id, false);
            var base = source(project, passage.baseId());
            var result =
                    worker.execute(
                            job,
                            Map.of(
                                    "schemaVersion",
                                    1,
                                    "operation",
                                    "mix-prepare",
                                    "sourceAudio",
                                    base.path().toString(),
                                    "sourceSha256",
                                    base.sha(),
                                    "startFrame",
                                    passage.startFrame(),
                                    "endFrame",
                                    passage.endFrame()),
                            () -> superseded(id, job));
            var prep = files.verified(job, result, "preparation");
            tx.executeWithoutResult(
                    status -> {
                        if (!job.equals(store.project(id, true).jobId())) return;
                        store.db
                                .sql(
                                        "UPDATE mix_passage SET prep_path=?,prep_sha=?,cache_key=?"
                                                + " WHERE project_id=? AND id=?")
                                .params(
                                        files.storePath(prep.path()),
                                        prep.sha(),
                                        result.path("cacheKey").asText(),
                                        id,
                                        passage.id())
                                .update();
                        store.db
                                .sql(
                                        "UPDATE mix_project SET"
                                            + " preparation='READY',error=NULL,job_id=NULL,revision=revision+1"
                                            + " WHERE id=?")
                                .param(id)
                                .update();
                    });
        } catch (Exception e) {
            failed(id, job, e);
        }
    }

    private MixArtifacts.FileRef source(Project project, String take) {
        if (ORIGINAL.equals(take))
            return new MixArtifacts.FileRef(
                    files.existing(project.sourcePath()), project.sourceSha());
        SavedTake saved = store.take(project.id(), take);
        return new MixArtifacts.FileRef(files.existing(saved.audioPath()), saved.sha256());
    }

    private void failed(String project, String job, Exception e) {
        if (closing) return;
        String message =
                e instanceof ApiProblem
                        ? e.getMessage()
                        : "The local audio worker could not complete this step. Retry preparation.";
        log.warn("Finished mix job {}: {}", job, e.toString());
        store.db
                .sql(
                        "UPDATE mix_project SET"
                            + " preparation='FAILED',error=?,job_id=NULL,revision=revision+1 WHERE"
                            + " id=? AND job_id=?")
                .params(message, project, job)
                .update();
    }

    public Reply edit(String id, Edit edit, String key) throws Exception {
        return conversation.edit(id, edit, key);
    }

    public State heard(String id, String takeId) {
        tx.executeWithoutResult(
                status -> {
                    store.project(id, true);
                    if (!ORIGINAL.equals(takeId)) {
                        store.take(id, takeId);
                        store.db
                                .sql("UPDATE mix_take SET heard=true WHERE project_id=? AND id=?")
                                .params(id, takeId)
                                .update();
                    }
                    store.db
                            .sql(
                                    "UPDATE mix_project SET heard_take=?,revision=revision+1 WHERE"
                                            + " id=?")
                            .params(takeId, id)
                            .update();
                });
        return state(id);
    }

    public State keep(String id, Keep command, String key) {
        validateKey(key);
        tx.executeWithoutResult(
                status -> {
                    store.project(id, true);
                    var receipt =
                            store
                                    .db
                                    .sql(
                                            "SELECT take_id,sha256 FROM mix_keep_receipt WHERE"
                                                    + " project_id=? AND command_key=?")
                                    .params(id, key)
                                    .query()
                                    .listOfRows()
                                    .stream()
                                    .findFirst();
                    if (receipt.isPresent()) {
                        if (!Objects.equals(receipt.get().get("take_id"), command.takeId())
                                || !Objects.equals(receipt.get().get("sha256"), command.sha256()))
                            throw keyConflict();
                        return;
                    }
                    SavedTake take = store.take(id, command.takeId());
                    if (!take.heard()) throw conflict("Play this take before keeping it.");
                    if (!take.sha256().equals(command.sha256()))
                        throw conflict("The audio hash does not match the auditioned take.");
                    verifySaved(take);
                    store.db
                            .sql(
                                    "INSERT INTO"
                                        + " mix_keep_receipt(project_id,command_key,take_id,sha256)"
                                        + " VALUES(?,?,?,?)")
                            .params(id, key, take.id(), take.sha256())
                            .update();
                    store.db
                            .sql("UPDATE mix_take SET kept=true WHERE project_id=? AND id=?")
                            .params(id, take.id())
                            .update();
                    store.db
                            .sql(
                                    "UPDATE mix_project SET kept_take=?,revision=revision+1 WHERE"
                                            + " id=?")
                            .params(take.id(), id)
                            .update();
                });
        return state(id);
    }

    public State undo(String id, String target) {
        tx.executeWithoutResult(
                status -> {
                    Project project = store.project(id, true);
                    if (!Objects.equals(target, project.keptTake()) || ORIGINAL.equals(target))
                        throw conflict("Select the current kept take to undo it.");
                    SavedTake child = store.take(id, target);
                    String parent = child.parentId();
                    if (!ORIGINAL.equals(parent)) {
                        SavedTake prior = store.take(id, parent);
                        store.db
                                .sql("UPDATE mix_take SET kept=true WHERE project_id=? AND id=?")
                                .params(id, parent)
                                .update();
                        selectPassage(project, store.passage(id, prior.passageId()));
                    } else selectPassage(project, store.passage(id, child.passageId()));
                    store.db
                            .sql(
                                    "UPDATE mix_project SET kept_take=?,revision=revision+1 WHERE"
                                        + " id=?")
                            .params(parent, id)
                            .update();
                });
        return state(id);
    }

    public Path uploadPath(String id) {
        store.project(id, false);
        return files.uploaded(id);
    }

    public Path takePath(String id, String takeId, boolean export) {
        Project project = store.project(id, false);
        if (ORIGINAL.equals(takeId)) return files.existing(project.sourcePath());
        SavedTake take = store.take(id, takeId);
        if (export && !take.kept()) throw conflict("Keep this take before exporting its WAV.");
        return export ? verifySaved(take) : files.existing(take.audioPath());
    }

    private Path verifySaved(SavedTake take) {
        Path path = files.existing(take.audioPath());
        try {
            if (!MixArtifacts.sha256(path).equals(take.sha256()))
                throw MixArtifacts.invalid("The saved audio no longer matches its preview hash.");
        } catch (IOException e) {
            throw MixArtifacts.invalid("The saved WAV could not be read.");
        }
        return path;
    }

    private static void afterCommit(Runnable work) {
        TransactionSynchronizationManager.registerSynchronization(
                new TransactionSynchronization() {
                    @Override
                    public void afterCommit() {
                        work.run();
                    }
                });
    }

    static String jobId() {
        return "mix_" + UUID.randomUUID();
    }

    static ApiProblem conflict(String message) {
        return new ApiProblem(409, "MIX_STATE_CHANGED", message, "REFRESH");
    }

    static ApiProblem keyConflict() {
        return new ApiProblem(
                409,
                "IDEMPOTENCY_CONFLICT",
                "This request key already refers to a different action.",
                "REFRESH");
    }

    static void validateKey(String key) {
        if (key == null || key.isBlank() || key.length() > 160)
            throw new IllegalArgumentException("Invalid request key");
    }

    public void close() {
        closing = true;
        preparing.shutdownNow();
        decoding.shutdownNow();
    }
}
