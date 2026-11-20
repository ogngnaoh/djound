package com.djound.rendition;

import com.djound.ApiProblem;
import com.djound.MediaStore;
import com.djound.generation.GenerationConfiguration;
import com.fasterxml.jackson.annotation.JsonInclude;

import jakarta.annotation.PostConstruct;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.multipart.MultipartFile;

import tools.jackson.databind.JsonNode;

import java.io.*;
import java.nio.file.*;
import java.time.OffsetDateTime;
import java.util.*;
import java.util.concurrent.*;

@Service
public final class RenditionProjects implements AutoCloseable {
    static final String ORIGINAL = "original";
    private static final String RETAINED_PASSAGE = "retained_passage";
    private static final Logger log = LoggerFactory.getLogger(RenditionProjects.class);
    private final JdbcClient db;
    private final TransactionTemplate tx;
    private final MediaStore media;
    private final RenditionAudio audio;
    private final GenerationConfiguration mode;
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final BlockingQueue<Boolean> wake = new ArrayBlockingQueue<>(1);
    private volatile boolean closing;

    public record Index(
            String id,
            String title,
            double durationSeconds,
            OffsetDateTime createdAt,
            OffsetDateTime lastActivityAt,
            long readyTakeCount,
            long inFlightCount,
            String favoriteId,
            String favoriteLabel,
            Double favoriteDurationSeconds,
            OffsetDateTime trashedAt) {}

    public record State(
            String id,
            String title,
            double durationSeconds,
            String originalUrl,
            String favoriteId,
            List<Take> takes) {}

    public record Take(
            String id,
            String parentId,
            String requestId,
            String label,
            String request,
            String caption,
            String state,
            @JsonInclude(JsonInclude.Include.NON_NULL) String progress,
            @JsonInclude(JsonInclude.Include.NON_NULL) int[][] songTimeMap,
            String audioUrl,
            String exportUrl,
            Double durationSeconds,
            Double startSeconds,
            Double endSeconds,
            Double appliedStartSeconds,
            Double appliedEndSeconds,
            String sha256,
            String error,
            @JsonInclude(JsonInclude.Include.NON_NULL) String clarificationKind,
            @JsonInclude(JsonInclude.Include.NON_NULL) String answerTo,
            @JsonInclude(JsonInclude.Include.NON_NULL) String passageChoice) {}

    public record Command(
            String requestId,
            String parentId,
            String prompt,
            Double startSeconds,
            Double endSeconds) {}

    public record Answer(String requestId, String choice) {}

    record Project(
            String id,
            String title,
            String uploadSha,
            String sourceSha,
            int frames,
            String favoriteId,
            OffsetDateTime trashedAt) {
        Project active() {
            if (trashedAt != null)
                throw new ApiProblem(
                        410,
                        "RENDITION_PROJECT_TRASHED",
                        "This recording is in Trash. Restore it to open it again.",
                        "RESTORE_RECORDING");
            return this;
        }
    }

    record Job(
            String id,
            String projectId,
            String parentId,
            String requestId,
            String label,
            String prompt,
            String caption,
            String state,
            Integer frames,
            Double startSeconds,
            Double endSeconds,
            Double appliedStartSeconds,
            Double appliedEndSeconds,
            String sha256,
            String error,
            String songTimeMap,
            String arrangementPlan,
            String clarificationKind,
            String answerTo,
            String passageChoice) {}

    record Earlier(String id, String request, String label, String caption) {}

    record ScoreInput(String id, String source) {}

    record ScoredAncestor(
            String id,
            String source,
            int frames,
            JsonNode plan,
            List<int[]> editedFrames,
            String scoreSource,
            String scoreSourceTakeId) {}

    private static final String PROJECT_FIELDS =
            "id,title,upload_sha,source_sha,frames,favorite_id,trashed_at";
    private static final String JOB_FIELDS =
            "id,project_id,parent_id,request_id,label,prompt,caption,state,frames,start_seconds,end_seconds,applied_start_seconds,applied_end_seconds,sha256,error,song_time_map::text"
                + " AS song_time_map,arrangement_plan::text AS"
                + " arrangement_plan,clarification_kind,answer_to,passage_choice";

    RenditionProjects(
            JdbcClient db,
            TransactionTemplate tx,
            MediaStore media,
            RenditionAudio audio,
            GenerationConfiguration mode) {
        this.db = db;
        this.tx = tx;
        this.media = media;
        this.audio = audio;
        this.mode = mode;
    }

    @PostConstruct
    public void recover() {
        db.sql(
                        "UPDATE rendition_take SET state='failed',error='Generation was"
                            + " interrupted. Your recording and earlier takes are saved. Submit it"
                            + " again to retry.' WHERE state='running' RETURNING id")
                .query(String.class)
                .list()
                .forEach(audio::clearProgress);
        if (mode.local()) {
            executor.submit(this::consume);
            wake.offer(true);
        }
    }

    public List<Index> list() {
        return list(false);
    }

    public List<Index> list(boolean trash) {
        return db.sql(
                        """
                        SELECT p.id, p.title, p.frames / 44100.0 AS duration_seconds,
                               p.created_at, p.last_activity_at,
                               coalesce(t.ready_count, 0) AS ready_take_count,
                               coalesce(t.working_count, 0) AS in_flight_count,
                               f.id AS favorite_id, f.label AS favorite_label,
                               f.frames / 44100.0 AS favorite_duration_seconds, p.trashed_at
                        FROM rendition_project p
                        LEFT JOIN (
                            SELECT project_id,
                                   count(*) FILTER (WHERE state = 'ready') AS ready_count,
                                   count(*) FILTER (WHERE state IN ('queued', 'running')) AS working_count
                            FROM rendition_take GROUP BY project_id
                        ) t ON t.project_id = p.id
                        LEFT JOIN rendition_take f ON f.project_id = p.id AND f.id = p.favorite_id
                        WHERE (p.trashed_at IS NOT NULL) = ?
                        ORDER BY p.last_activity_at DESC, p.id
                        """)
                .param(trash)
                .query(Index.class)
                .list();
    }

    public State state(String id) {
        Project project = project(id);
        var takes =
                db
                        .sql(
                                "SELECT "
                                        + JOB_FIELDS
                                        + " FROM rendition_take WHERE project_id=? ORDER BY"
                                        + " created_at,id")
                        .param(id)
                        .query(Job.class)
                        .list()
                        .stream()
                        .map(this::view)
                        .toList();
        return new State(
                id,
                project.title(),
                seconds(project.frames()),
                url(id, ORIGINAL, "audio"),
                project.favoriteId(),
                takes);
    }

    public State create(MultipartFile file, String key) throws IOException, InterruptedException {
        if (key != null) validKey(key);
        try (var upload = media.ingestRecording(file)) {
            if (key != null) {
                var previous = byKey(key);
                if (previous.isPresent()) return state(checkImport(previous.get(), upload));
            }
            Path temporary = audio.temporary();
            String id = UUID.randomUUID().toString();
            boolean[] published = {false};
            try {
                audio.decode(upload.path(), temporary);
                var wave = audio.verify(temporary);
                String winner =
                        tx.execute(
                                status -> {
                                    // Serialize the short publication step, including contenders
                                    // that decoded in parallel.
                                    db.sql("SELECT id FROM workspace WHERE id=1 FOR UPDATE")
                                            .query(Integer.class)
                                            .single();
                                    if (key != null) {
                                        var previous = byKey(key);
                                        if (previous.isPresent())
                                            return checkImport(previous.get(), upload);
                                    }
                                    try {
                                        Files.move(
                                                temporary,
                                                audio.file(id),
                                                StandardCopyOption.ATOMIC_MOVE);
                                        published[0] = true;
                                    } catch (IOException e) {
                                        throw new UncheckedIOException(e);
                                    }
                                    db.sql(
                                                    "INSERT INTO"
                                                        + " rendition_project(id,title,import_key,upload_sha,source_sha,frames)"
                                                        + " VALUES(?,?,?,?,?,?)")
                                            .params(
                                                    id,
                                                    upload.fileName(),
                                                    key,
                                                    upload.hash(),
                                                    wave.hash(),
                                                    wave.frames())
                                            .update();
                                    return id;
                                });
                return state(winner);
            } catch (RuntimeException e) {
                if (published[0]
                        && !db.sql("SELECT EXISTS(SELECT 1 FROM rendition_project WHERE id=?)")
                                .param(id)
                                .query(Boolean.class)
                                .single()) Files.deleteIfExists(audio.file(id));
                throw e;
            } finally {
                Files.deleteIfExists(temporary);
            }
        }
    }

    private Optional<Project> byKey(String key) {
        return db.sql("SELECT " + PROJECT_FIELDS + " FROM rendition_project WHERE import_key=?")
                .param(key)
                .query(Project.class)
                .optional();
    }

    private String checkImport(Project project, MediaStore.Upload upload) {
        if (!project.uploadSha().equals(upload.hash())
                || !project.title().equals(upload.fileName())) throw conflictKey();
        return project.id();
    }

    private static void validKey(String key) {
        if (key == null
                || key.isBlank()
                || key.length() > 160
                || key.chars().anyMatch(Character::isISOControl))
            throw new ApiProblem(
                    400, "RENDITION_KEY_INVALID", "Use a valid submission identifier.");
    }

    private static ApiProblem conflictKey() {
        return new ApiProblem(
                409,
                "RENDITION_REQUEST_CONFLICT",
                "That submission identifier already belongs to a different request.");
    }

    private static double seconds(int frames) {
        return frames / (double) RenditionAudio.RATE;
    }

    private static String url(String id, String take, String operation) {
        return "/api/renditions/" + id + "/takes/" + take + "/" + operation;
    }

    private Take view(Job job) {
        boolean ready = job.state().equals("ready");
        return new Take(
                job.id(),
                job.parentId() == null ? ORIGINAL : job.parentId(),
                job.requestId(),
                job.label(),
                job.prompt(),
                job.caption(),
                job.state(),
                "running".equals(job.state()) ? audio.progress(job.id()) : null,
                RenditionTimeline.storedMap(job.songTimeMap()),
                ready ? url(job.projectId(), job.id(), "audio") : null,
                ready ? url(job.projectId(), job.id(), "export") : null,
                job.frames() == null ? null : seconds(job.frames()),
                job.startSeconds(),
                job.endSeconds(),
                job.appliedStartSeconds(),
                job.appliedEndSeconds(),
                job.sha256(),
                job.error(),
                job.clarificationKind(),
                job.answerTo(),
                job.passageChoice());
    }

    private Project project(String id) {
        return loadProject(id, false).active();
    }

    private Project loadProject(String id, boolean lock) {
        return db.sql(
                        "SELECT "
                                + PROJECT_FIELDS
                                + " FROM rendition_project WHERE id=?"
                                + (lock ? " FOR UPDATE" : ""))
                .param(id)
                .query(Project.class)
                .optional()
                .orElseThrow(
                        () ->
                                new ApiProblem(
                                        404,
                                        "RENDITION_PROJECT_MISSING",
                                        "The recording project could not be found."));
    }

    private Job job(String project, String id) {
        return db.sql("SELECT " + JOB_FIELDS + " FROM rendition_take WHERE project_id=? AND id=?")
                .params(project, id)
                .query(Job.class)
                .optional()
                .orElseThrow(
                        () ->
                                new ApiProblem(
                                        404,
                                        "RENDITION_TAKE_MISSING",
                                        "That version is not in this recording project."));
    }

    private Job readyJob(String project, String id) {
        Job j = job(project, id);
        if (!j.state().equals("ready"))
            throw new ApiProblem(
                    409,
                    "RENDITION_TAKE_UNAVAILABLE",
                    "Choose a version that has finished generating.");
        return j;
    }

    public State submit(String id, Command command) {
        return enqueue(id, command, null, null);
    }

    public State answer(String id, String takeId, Answer answer) {
        project(id);
        if (!"keep".equals(answer.choice()) && !"develop".equals(answer.choice()))
            throw new ApiProblem(
                    422,
                    "RENDITION_ANSWER_INVALID",
                    "Choose whether to keep or change the edited passage.");
        Job question = job(id, takeId);
        if (!"clarify".equals(question.state())
                || !RETAINED_PASSAGE.equals(question.clarificationKind()))
            throw new ApiProblem(
                    409,
                    "RENDITION_ANSWER_UNAVAILABLE",
                    "This take has no saved-passage question to answer.");
        Command original =
                new Command(
                        answer.requestId(),
                        question.parentId() == null ? ORIGINAL : question.parentId(),
                        question.prompt(),
                        question.startSeconds(),
                        question.endSeconds());
        return enqueue(id, original, question.id(), answer.choice());
    }

    private State enqueue(String id, Command command, String answerTo, String passageChoice) {
        validKey(command.requestId());
        if (command.prompt() == null
                || command.prompt().isBlank()
                || command.prompt().length() > 1000)
            throw new ApiProblem(
                    422, "RENDITION_REQUEST_INVALID", "Describe the change in 1–1,000 characters.");
        if (command.parentId() == null || command.parentId().isBlank())
            throw new ApiProblem(
                    422, "RENDITION_PARENT_REQUIRED", "Choose the version you want to change.");
        String parent = ORIGINAL.equals(command.parentId()) ? null : command.parentId();
        Boolean inserted =
                tx.execute(
                        status -> {
                            Project p = loadProject(id, true).active();
                            var existing =
                                    db.sql(
                                                    "SELECT "
                                                            + JOB_FIELDS
                                                            + " FROM rendition_take WHERE"
                                                            + " project_id=? AND request_id=?")
                                            .params(id, command.requestId())
                                            .query(Job.class)
                                            .optional();
                            if (existing.isPresent()) {
                                Job j = existing.get();
                                if (!Objects.equals(j.parentId(), parent)
                                        || !Objects.equals(j.prompt(), command.prompt())
                                        || !Objects.equals(j.startSeconds(), command.startSeconds())
                                        || !Objects.equals(j.endSeconds(), command.endSeconds())
                                        || !Objects.equals(j.answerTo(), answerTo)
                                        || !Objects.equals(j.passageChoice(), passageChoice))
                                    throw conflictKey();
                                return false;
                            }
                            int frames =
                                    parent == null ? p.frames() : readyJob(id, parent).frames();
                            validateScope(
                                    command.startSeconds(), command.endSeconds(), seconds(frames));
                            if (!mode.local())
                                throw new ApiProblem(
                                        503,
                                        "RENDITION_LOCAL_REQUIRED",
                                        "Start Djound in LOCAL mode to make a rendition. Saved"
                                                + " versions are available.",
                                        "USE_EXISTING_AUDIO");
                            db.sql(
                                            "INSERT INTO"
                                                + " rendition_take(id,project_id,parent_id,request_id,prompt,start_seconds,end_seconds,answer_to,passage_choice,state,label)"
                                                + " VALUES(?,?,?,?,?,?,?,?,?,'queued','New"
                                                + " version')")
                                    .params(
                                            UUID.randomUUID().toString(),
                                            id,
                                            parent,
                                            command.requestId(),
                                            command.prompt(),
                                            command.startSeconds(),
                                            command.endSeconds(),
                                            answerTo,
                                            passageChoice)
                                    .update();
                            return true;
                        });
        if (Boolean.TRUE.equals(inserted)) wake.offer(true);
        return state(id);
    }

    private static void validateScope(Double start, Double end, double duration) {
        if (start == null && end == null) return;
        if (start == null
                || end == null
                || !Double.isFinite(start)
                || !Double.isFinite(end)
                || start < 0
                || end <= start
                || end > duration + 1e-8)
            throw new ApiProblem(
                    422,
                    "RENDITION_RANGE_INVALID",
                    "Choose a start and end inside the selected version.");
    }

    public State favorite(String id, String takeId) {
        return tx.execute(
                status -> {
                    loadProject(id, true).active();
                    readyJob(id, takeId);
                    db.sql(
                                    """
                                    UPDATE rendition_project SET favorite_id = ?,
                                        last_activity_at = greatest(last_activity_at, statement_timestamp())
                                    WHERE id = ? AND favorite_id IS DISTINCT FROM ?
                                    """)
                            .params(takeId, id, takeId)
                            .update();
                    return state(id);
                });
    }

    public void setTrashed(String id, boolean trash) {
        tx.executeWithoutResult(
                status -> {
                    Project p = loadProject(id, true);
                    if ((p.trashedAt() != null) == trash) return;
                    if (trash
                            && db.sql(
                                            """
                                            SELECT EXISTS(SELECT 1 FROM rendition_take
                                                          WHERE project_id = ? AND state IN ('queued','running'))
                                            """)
                                    .param(id)
                                    .query(Boolean.class)
                                    .single())
                        throw new ApiProblem(
                                409,
                                "RENDITION_PROJECT_BUSY",
                                "Wait for this recording’s current requests to finish, then move it"
                                    + " to Trash.");
                    db.sql(
                                    """
                                    UPDATE rendition_project SET trashed_at = CASE WHEN ? THEN statement_timestamp() END,
                                        last_activity_at = greatest(last_activity_at, statement_timestamp())
                                    WHERE id = ?
                                    """)
                            .params(trash, id)
                            .update();
                });
    }

    Path path(String id, String takeId) {
        Project p = project(id);
        String fileId = ORIGINAL.equals(takeId) ? p.id() : readyJob(id, takeId).id();
        Path path = audio.file(fileId);
        if (!Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS))
            throw new ApiProblem(
                    410, "RENDITION_FILE_LOST", "The saved audio is missing from local storage.");
        return path;
    }

    private void consume() {
        // The database is the durable queue. A single coalesced signal also covers submissions
        // arriving between the last empty database read and the next blocking wait.
        while (!closing) {
            try {
                wake.take();
                drain();
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                return;
            } catch (RuntimeException e) {
                log.error("Could not drain the rendition queue", e);
                if (!closing) {
                    try {
                        Thread.sleep(1000);
                    } catch (InterruptedException interrupted) {
                        Thread.currentThread().interrupt();
                        return;
                    }
                    wake.offer(true);
                }
            }
        }
    }

    private void drain() {
        while (!closing) {
            var next =
                    db.sql(
                                    "SELECT "
                                            + JOB_FIELDS
                                            + " FROM rendition_take WHERE state='queued' ORDER BY"
                                            + " created_at,id LIMIT 1")
                            .query(Job.class)
                            .optional();
            if (next.isEmpty()) return;
            var j = next.get();
            if (db.sql("UPDATE rendition_take SET state='running' WHERE id=? AND state='queued'")
                            .param(j.id())
                            .update()
                    == 1) run(j);
        }
    }

    private List<Earlier> history(Job job) {
        var rows = new ArrayList<Earlier>();
        String ancestor = job.parentId();
        while (ancestor != null && rows.size() < 4) {
            Job j = readyJob(job.projectId(), ancestor);
            rows.add(new Earlier(j.id(), j.prompt(), j.label(), j.caption()));
            ancestor = j.parentId();
        }
        return rows.reversed();
    }

    private static List<int[]> retainedFrames(JsonNode ranges, int frames) throws IOException {
        if (!ranges.isArray()
                || ranges.isEmpty()
                || ranges.size() > RenditionTimeline.MAX_WORKER_POINTS)
            throw new IOException("The retained passage list is invalid.");
        var result = new ArrayList<int[]>();
        int previous = -1;
        for (var range : ranges) {
            if (!range.isArray()
                    || range.size() != 2
                    || !range.get(0).isIntegralNumber()
                    || !range.get(0).canConvertToInt()
                    || !range.get(1).isIntegralNumber()
                    || !range.get(1).canConvertToInt())
                throw new IOException("The retained passage needs two frame positions.");
            int start = range.get(0).asInt(), end = range.get(1).asInt();
            if (start <= previous || start < 0 || end <= start || end > frames)
                throw new IOException("The retained passages must be merged and within the take.");
            result.add(new int[] {start, end});
            previous = end;
        }
        return result;
    }

    private ScoredAncestor scoredAncestor(Project project, Job take, int frames)
            throws IOException {
        if (take == null) return null;
        var edited = new ArrayList<int[]>();
        var seen = new HashSet<String>();
        while (take.appliedStartSeconds() != null || take.appliedEndSeconds() != null) {
            if (take.parentId() == null || !seen.add(take.id()) || take.frames() != frames)
                return null;
            Double first = take.appliedStartSeconds(), last = take.appliedEndSeconds();
            if (first == null
                    || last == null
                    || !Double.isFinite(first)
                    || !Double.isFinite(last)
                    || first < 0
                    || last > seconds(frames) + 1e-8) return null;
            int start = (int) Math.round(first * RenditionAudio.RATE);
            int end = (int) Math.round(last * RenditionAudio.RATE);
            if (end <= start || end > frames) return null;
            edited.add(new int[] {start, end});
            var earlier = readyJob(project.id(), take.parentId());
            if (!Objects.equals(take.songTimeMap(), earlier.songTimeMap())) return null;
            take = earlier;
        }
        var plan = RenditionTimeline.storedPlan(take.arrangementPlan());
        if (take.frames() != frames || !isSavedScore(plan)) return null;
        boolean hybrid = plan.has("retainedPassageFrames");
        if (hybrid) edited.addAll(retainedFrames(plan.get("retainedPassageFrames"), frames));
        if (edited.isEmpty()) return null;
        edited.sort(Comparator.comparingInt(pair -> pair[0]));
        var merged = new ArrayList<int[]>();
        for (int[] interval : edited) {
            if (merged.isEmpty() || merged.getLast()[1] < interval[0]) merged.add(interval);
            else merged.getLast()[1] = Math.max(merged.getLast()[1], interval[1]);
        }
        var input = scoreInput(project, take, plan);
        if (input.source() == null) return null;
        Path ancestor = audio.file(take.id());
        if (!verifiedScoreAudio(ancestor, take)) return null;
        return new ScoredAncestor(
                take.id(), ancestor.toString(), frames, plan, merged, input.source(), input.id());
    }

    private static boolean isSavedScore(JsonNode plan) {
        return plan != null
                && plan.path("version").asInt(0) == 1
                && "score".equals(plan.path("realization").asString(""))
                && plan.path("roles").isArray()
                && plan.path("fromUpload").isBoolean();
    }

    private ScoreInput scoreInput(Project project, Job take, JsonNode plan) {
        if (plan.path("fromUpload").asBoolean())
            return new ScoreInput(ORIGINAL, audio.file(project.id()).toString());
        // Before source IDs were saved, whole scores were always transcribed from
        // their immediate parent. A retained passage already required an explicit ID.
        String id = plan.path("scoreSourceTakeId").asString(null);
        if (id == null && !plan.has("retainedPassageFrames")) id = take.parentId();
        if (id == null || ORIGINAL.equals(id)) return new ScoreInput(id, null);
        var input = readyJob(project.id(), id);
        var file = audio.file(input.id());
        return new ScoreInput(id, verifiedScoreAudio(file, input) ? file.toString() : null);
    }

    private boolean verifiedScoreAudio(Path file, Job take) {
        try {
            var verified = audio.verifyTake(file);
            return verified.frames() == take.frames() && verified.hash().equals(take.sha256());
        } catch (IOException absent) {
            return false;
        }
    }

    private void run(Job job) {
        Path temp = null;
        try {
            var project = project(job.projectId());
            var parent = job.parentId() == null ? null : readyJob(project.id(), job.parentId());
            Path original = audio.file(project.id()),
                    source = parent == null ? original : audio.file(parent.id());
            String expectedSha = parent == null ? project.sourceSha() : parent.sha256();
            var basis = parent == null ? audio.verify(source) : audio.verifyTake(source);
            if (!expectedSha.equals(basis.hash())
                    || parent != null && !RenditionAudio.hash(original).equals(project.sourceSha()))
                throw new IOException(
                        "The saved source recording changed on disk. Import it again.");
            temp = audio.pending(job.id());
            var request = new LinkedHashMap<String, Object>();
            request.put("source", source.toString());
            request.put("original", original.toString());
            request.put("output", temp.toString());
            request.put("parentId", parent == null ? ORIGINAL : parent.id());
            var parentMap =
                    parent == null ? null : RenditionTimeline.storedMap(parent.songTimeMap());
            var parentPlan =
                    parent == null ? null : RenditionTimeline.storedPlan(parent.arrangementPlan());
            request.put("parentSongTimeMap", parentMap);
            request.put("originalFrames", project.frames());
            request.put("supportsArrangedScore", true);
            request.put("parentPlan", parentPlan);
            var ancestor =
                    job.startSeconds() == null
                            ? scoredAncestor(project, parent, basis.frames())
                            : null;
            if (ancestor != null) request.put("scoredAncestor", ancestor);
            ScoreInput directInput = null;
            if (job.startSeconds() == null
                    && isSavedScore(parentPlan)
                    && !parentPlan.path("fromUpload").asBoolean()
                    && !parentPlan.has("retainedPassageFrames")
                    && parent.appliedStartSeconds() == null
                    && parent.appliedEndSeconds() == null) {
                directInput = scoreInput(project, parent, parentPlan);
                request.put("parentScoreInput", directInput);
            }
            if (job.passageChoice() != null) {
                if (ancestor == null)
                    throw new IOException(
                            "The saved passage could not be verified. Choose the earlier take"
                                + " before retrying.");
                request.put("passagePolicy", job.passageChoice());
            }
            request.put("prompt", job.prompt());
            request.put("parentCaption", parent == null ? "" : parent.caption());
            request.put("history", history(job));
            request.put("durationSeconds", seconds(basis.frames()));
            if (job.startSeconds() != null) {
                request.put("startSeconds", job.startSeconds());
                request.put("endSeconds", job.endSeconds());
            }
            JsonNode result = audio.generate(request, job.id());
            String state = result.path("state").asString("");
            if ("failed".equals(state) || "clarify".equals(state)) {
                String kind =
                        "clarify".equals(state)
                                        && ancestor != null
                                        && job.answerTo() == null
                                        && RETAINED_PASSAGE.equals(
                                                result.path("clarificationKind").asString(""))
                                ? RETAINED_PASSAGE
                                : null;
                finishError(
                        job.id(),
                        state,
                        result.path("error")
                                .asString("Please give another direction and try again."),
                        kind);
                return;
            }
            if (!"ready".equals(state))
                throw new IOException("The local engine did not return a finished version.");
            String label = result.path("label").asString("").strip(),
                    caption = result.path("caption").asString("").strip();
            if (label.isBlank()
                    || label.length() > 200
                    || caption.isBlank()
                    || caption.length() > 4000)
                throw new IOException(
                        "The local engine returned an incomplete rendition description.");
            var output = audio.verifyTake(temp);
            double reported = result.path("durationSeconds").asDouble(Double.NaN);
            if (!Double.isFinite(reported) || Math.abs(reported - seconds(output.frames())) > .05)
                throw new IOException("The local engine returned a mismatched recording duration.");
            Double start = numberOr(result, "appliedStartSeconds", job.startSeconds()),
                    end = numberOr(result, "appliedEndSeconds", job.endSeconds());
            validateScope(start, end, seconds(basis.frames()));
            if (job.startSeconds() != null
                    && (Math.round(start * RenditionAudio.RATE)
                                    != Math.round(job.startSeconds() * RenditionAudio.RATE)
                            || Math.round(end * RenditionAudio.RATE)
                                    != Math.round(job.endSeconds() * RenditionAudio.RATE)))
                throw new IOException("The local engine did not honor the selected range.");
            boolean scoped = start != null;
            int[][] mapping =
                    RenditionTimeline.workerMap(
                            result.path("parentTimeMap"), output.frames(), basis.frames(), scoped);
            String songTimeMap =
                    RenditionTimeline.encoded(RenditionTimeline.toOriginal(mapping, parentMap));
            var nextPlan = result.path("arrangementPlan");
            if (!scoped && nextPlan instanceof tools.jackson.databind.node.ObjectNode object) {
                object.remove("scoreSourceTakeId");
                if (object.has("retainedPassageFrames")) {
                    var ranges =
                            retainedFrames(object.get("retainedPassageFrames"), output.frames());
                    if (ancestor == null
                            || mapping != null
                            || output.frames() != ancestor.frames()
                            || !Arrays.deepEquals(
                                    ranges.toArray(), ancestor.editedFrames().toArray())
                            || !object.path("fromUpload").isBoolean()
                            || object.path("fromUpload").asBoolean()
                                    != ORIGINAL.equals(ancestor.scoreSourceTakeId())
                            || !"score".equals(object.path("realization").asString("")))
                        throw new IOException(
                                "The retained passages could not be matched to this take.");
                    object.put("scoreSourceTakeId", ancestor.scoreSourceTakeId());
                } else if (isSavedScore(object) && !object.path("fromUpload").asBoolean()) {
                    if (result.path("inheritedScoreInput").asBoolean(false)) {
                        if (directInput == null || directInput.source() == null)
                            throw new IOException("The earlier score input could not be verified.");
                        object.put("scoreSourceTakeId", directInput.id());
                    } else if (parent != null) object.put("scoreSourceTakeId", parent.id());
                }
            }
            if (scoped && (nextPlan.isMissingNode() || nextPlan.isNull()) && parentPlan != null)
                nextPlan = parentPlan;
            if (scoped
                    && nextPlan instanceof tools.jackson.databind.node.ObjectNode object
                    && object.has("fromUpload")) object.put("fromUpload", false);
            String plan = RenditionTimeline.plan(nextPlan);
            audio.checkChangedAudio(source, temp, basis.frames(), output.frames(), start, end);
            Files.move(temp, audio.file(job.id()), StandardCopyOption.ATOMIC_MOVE);
            db.sql(
                            "UPDATE rendition_take SET"
                                + " state='ready',label=?,caption=?,frames=?,sha256=?,applied_start_seconds=?,applied_end_seconds=?,song_time_map=CAST(?"
                                + " AS jsonb),arrangement_plan=CAST(? AS jsonb) WHERE id=? AND"
                                + " state='running'")
                    .params(
                            label,
                            caption,
                            output.frames(),
                            output.hash(),
                            start,
                            end,
                            songTimeMap,
                            plan,
                            job.id())
                    .update();
        } catch (Exception e) {
            if (e instanceof InterruptedException) Thread.currentThread().interrupt();
            log.warn("Rendition {} failed", job.id(), e);
            finishError(
                    job.id(),
                    "failed",
                    e instanceof IOException || e instanceof ApiProblem
                            ? e.getMessage()
                            : "The local rendition was interrupted. Submit it again to retry.");
        } finally {
            if (temp != null)
                try {
                    Files.deleteIfExists(temp);
                } catch (IOException e) {
                    log.warn("Could not remove pending rendition file", e);
                }
        }
    }

    private static Double numberOr(JsonNode result, String key, Double fallback)
            throws IOException {
        JsonNode field = result.path(key);
        if (field.isNull() || field.isMissingNode()) return fallback;
        if (!field.isNumber())
            throw new IOException("Invalid render scope returned from local worker.");
        return field.asDouble();
    }

    private void finishError(String id, String state, String message) {
        finishError(id, state, message, null);
    }

    private void finishError(String id, String state, String message, String clarificationKind) {
        String detail =
                message == null || message.isBlank()
                        ? "The worker could not finish this version."
                        : message.substring(0, Math.min(2000, message.length()));
        db.sql(
                        "UPDATE rendition_take SET state=?,error=?,clarification_kind=? WHERE id=?"
                            + " AND state='running'")
                .params(state, detail, clarificationKind, id)
                .update();
    }

    public void close() {
        closing = true;
        executor.shutdownNow();
        try {
            executor.awaitTermination(5, TimeUnit.SECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }
}
