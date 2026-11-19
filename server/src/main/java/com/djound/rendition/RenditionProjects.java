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
import java.util.*;
import java.util.concurrent.*;

@Service
public final class RenditionProjects implements AutoCloseable {
    static final String ORIGINAL = "original";
    private static final Logger log = LoggerFactory.getLogger(RenditionProjects.class);
    private final JdbcClient db;
    private final TransactionTemplate tx;
    private final MediaStore media;
    private final RenditionAudio audio;
    private final GenerationConfiguration mode;
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final BlockingQueue<Boolean> wake = new ArrayBlockingQueue<>(1);
    private volatile boolean closing;

    public record Index(String id, String title) {}

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
            String error) {}

    public record Command(
            String requestId,
            String parentId,
            String prompt,
            Double startSeconds,
            Double endSeconds) {}

    record Project(
            String id,
            String title,
            String uploadSha,
            String sourceSha,
            int frames,
            String favoriteId) {}

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
            String arrangementPlan) {}

    record Earlier(String id, String request, String label, String caption) {}

    record ScoredAncestor(
            String id,
            String source,
            int frames,
            JsonNode plan,
            List<int[]> editedFrames,
            String scoreSource,
            String scoreSourceTakeId) {}

    private static final String PROJECT_FIELDS =
            "id,title,upload_sha,source_sha,frames,favorite_id";
    private static final String JOB_FIELDS =
            "id,project_id,parent_id,request_id,label,prompt,caption,state,frames,start_seconds,end_seconds,applied_start_seconds,applied_end_seconds,sha256,error,song_time_map::text"
                + " AS song_time_map,arrangement_plan::text AS arrangement_plan";

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
        return db.sql("SELECT id,title FROM rendition_project ORDER BY created_at DESC,id")
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
                job.error());
    }

    private Project project(String id) {
        return db.sql("SELECT " + PROJECT_FIELDS + " FROM rendition_project WHERE id=?")
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
                            Project p =
                                    db.sql(
                                                    "SELECT "
                                                            + PROJECT_FIELDS
                                                            + " FROM rendition_project WHERE id=?"
                                                            + " FOR UPDATE")
                                            .param(id)
                                            .query(Project.class)
                                            .optional()
                                            .orElseThrow(
                                                    () ->
                                                            new ApiProblem(
                                                                    404,
                                                                    "RENDITION_PROJECT_MISSING",
                                                                    "The recording project could"
                                                                            + " not be found."));
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
                                        || !Objects.equals(j.endSeconds(), command.endSeconds()))
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
                                                + " rendition_take(id,project_id,parent_id,request_id,prompt,start_seconds,end_seconds,state,label)"
                                                + " VALUES(?,?,?,?,?,?,?,'queued','New version')")
                                    .params(
                                            UUID.randomUUID().toString(),
                                            id,
                                            parent,
                                            command.requestId(),
                                            command.prompt(),
                                            command.startSeconds(),
                                            command.endSeconds())
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
        readyJob(id, takeId);
        db.sql("UPDATE rendition_project SET favorite_id=? WHERE id=?").params(takeId, id).update();
        return state(id);
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
        if (take.frames() != frames
                || plan == null
                || plan.path("version").asInt(0) != 1
                || !"score".equals(plan.path("realization").asString(""))
                || !plan.path("roles").isArray()
                || !plan.path("fromUpload").isBoolean()) return null;
        boolean hybrid = plan.has("retainedPassageFrames");
        if (hybrid) edited.addAll(retainedFrames(plan.get("retainedPassageFrames"), frames));
        if (edited.isEmpty()) return null;
        edited.sort(Comparator.comparingInt(pair -> pair[0]));
        var merged = new ArrayList<int[]>();
        for (int[] interval : edited) {
            if (merged.isEmpty() || merged.getLast()[1] < interval[0]) merged.add(interval);
            else merged.getLast()[1] = Math.max(merged.getLast()[1], interval[1]);
        }
        // Whole scores can be transcribed from the upload or another take. A hybrid's
        // immediate parent is already arranged, so use its saved, server-owned source ID.
        String inputId =
                plan.path("fromUpload").asBoolean()
                        ? ORIGINAL
                        : hybrid ? plan.path("scoreSourceTakeId").asString(null) : take.parentId();
        if (inputId == null) return null;
        Path input = audio.file(project.id());
        if (!ORIGINAL.equals(inputId)) {
            var scoreInput = readyJob(project.id(), inputId);
            input = audio.file(scoreInput.id());
            if (!verifiedScoreAudio(input, scoreInput)) return null;
        }
        Path ancestor = audio.file(take.id());
        if (!verifiedScoreAudio(ancestor, take)) return null;
        return new ScoredAncestor(
                take.id(), ancestor.toString(), frames, plan, merged, input.toString(), inputId);
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
                finishError(
                        job.id(),
                        state,
                        result.path("error")
                                .asString("Please give another direction and try again."));
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
        String detail =
                message == null || message.isBlank()
                        ? "The worker could not finish this version."
                        : message.substring(0, Math.min(2000, message.length()));
        db.sql("UPDATE rendition_take SET state=?,error=? WHERE id=? AND state='running'")
                .params(state, detail, id)
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
