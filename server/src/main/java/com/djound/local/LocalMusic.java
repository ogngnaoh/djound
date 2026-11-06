package com.djound.local;

import static com.djound.local.LocalModels.*;

import com.djound.*;
import com.djound.generation.GenerationConfiguration;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.nio.file.*;
import java.util.*;
import java.util.concurrent.atomic.AtomicBoolean;

@Service
public class LocalMusic {
    private final JdbcClient db;
    private final TransactionTemplate tx;
    private final LocalWorker worker;
    private final MediaStore media;
    private final GenerationConfiguration config;
    private final ObjectMapper json = new ObjectMapper();
    private final AtomicBoolean running = new AtomicBoolean();

    public LocalMusic(
            JdbcClient db,
            PlatformTransactionManager manager,
            LocalWorker worker,
            MediaStore media,
            GenerationConfiguration config,
            Installation installation) {
        this.db = db;
        this.tx = new TransactionTemplate(manager);
        this.worker = worker;
        this.media = media;
        this.config = config;
        db.sql(
                        "UPDATE local_job SET"
                            + " state='FAILED',version=version+1,outcome_code='PROCESS_INTERRUPTED',outcome_detail='Local"
                            + " preparation was interrupted. Review your saved input and"
                            + " deliberately try again.',updated_at=now() WHERE state IN"
                            + " ('ACCEPTED','RUNNING')")
                .update();
    }

    private void enabled() {
        if (!config.local())
            throw problem("LOCAL_ENGINE_UNAVAILABLE", "Local music mode is not enabled.");
    }

    private void guard() {
        db.sql("SELECT id FROM workspace WHERE id=1 FOR UPDATE").query(Integer.class).single();
    }

    public void requireIdle() {
        if (db.sql("SELECT EXISTS(SELECT 1 FROM local_job WHERE state IN ('ACCEPTED','RUNNING'))")
                .query(Boolean.class)
                .single())
            throw problem("CREATION_ACTIVE", "Another local preparation step is in progress.");
    }

    public boolean busy() {
        return db.sql(
                        "SELECT EXISTS(SELECT 1 FROM local_job WHERE state IN"
                                + " ('ACCEPTED','RUNNING'))")
                .query(Boolean.class)
                .single();
    }

    private void idle() {
        requireIdle();
        if (db.sql("SELECT active_attempt_id IS NOT NULL FROM workspace WHERE id=1")
                .query(Boolean.class)
                .single())
            throw problem("CREATION_ACTIVE", "Wait for the current creation to finish.");
    }

    private Map<String, Object> exploration(String id, Long revision) {
        var rows = db.sql("SELECT * FROM exploration WHERE id=?").param(id).query().listOfRows();
        if (rows.isEmpty())
            throw new ApiProblem(
                    404, "NOT_FOUND", "This exploration is unavailable.", "RETURN_TO_LIBRARY");
        var row = rows.getFirst();
        if (revision != null && number(row, "source_revision") != revision)
            throw problem("SOURCE_CHANGED", "The source changed. Review the current recording.");
        return row;
    }

    private long nextRevision(String id) {
        return db.sql(
                        "SELECT COALESCE(MAX(revision),0)+1 FROM local_note_revision WHERE"
                                + " exploration_id=?")
                .param(id)
                .query(Long.class)
                .single();
    }

    public Notes notes(String id, long source) {
        var rows =
                db.sql(
                                "SELECT * FROM local_note_revision WHERE exploration_id=? AND"
                                        + " source_revision=? ORDER BY revision DESC LIMIT 1")
                        .params(id, source)
                        .query()
                        .listOfRows();
        if (rows.isEmpty()) return null;
        var r = rows.getFirst();
        String preview = (String) r.get("preview_job_id");
        return new Notes(
                number(r, "revision"),
                source,
                (String) r.get("status"),
                readNotes((String) r.get("notes_json")),
                ((Number) r.get("duration_seconds")).doubleValue(),
                preview == null ? null : "/api/local/jobs/" + preview + "/audio");
    }

    private Notes currentNotes(String id, long source, Long revision, boolean confirmed) {
        Notes n = notes(id, source);
        if (n == null || revision == null || n.revision() != revision)
            throw problem("NOTES_CHANGED", "The note revision changed. Review the current notes.");
        if (confirmed && !n.status().equals("CONFIRMED"))
            throw problem(
                    "NOTES_NOT_CONFIRMED",
                    "Listen to and explicitly confirm the melody notes first.");
        return n;
    }

    public State state(String id) {
        enabled();
        var exp = exploration(id, null);
        long source = number(exp, "source_revision");
        var active =
                db.sql(
                                "SELECT id FROM local_job WHERE exploration_id=? AND state IN"
                                    + " ('ACCEPTED','RUNNING') ORDER BY created_at DESC LIMIT 1")
                        .param(id)
                        .query(String.class)
                        .optional();
        var latest =
                db.sql(
                                "SELECT id FROM local_job WHERE exploration_id=? ORDER BY"
                                        + " created_at DESC LIMIT 1")
                        .param(id)
                        .query(String.class)
                        .optional();
        Notes n = notes(id, source);
        Plan p = null;
        if (n != null) {
            var plans =
                    db.sql(
                                    "SELECT id FROM local_plan WHERE exploration_id=? AND"
                                            + " source_revision=? AND note_revision=? ORDER BY"
                                            + " created_at DESC LIMIT 1")
                            .params(id, source, n.revision())
                            .query(String.class)
                            .list();
            if (!plans.isEmpty()) p = plan(plans.getFirst());
        }
        var capabilities = worker.readiness();
        return new State(
                capabilities.ready(),
                capabilities.ready() ? null : "Set up the local music engine before continuing.",
                capabilities.freeformAvailable(),
                capabilities.freeformUnavailableReason(),
                active.map(this::job).orElse(null),
                latest.map(this::job).orElse(null),
                n,
                p);
    }

    public Job job(String id) {
        return db.sql("SELECT * FROM local_job WHERE id=?")
                .param(id)
                .query(
                        (r, n) ->
                                new Job(
                                        r.getString("id"),
                                        r.getString("kind"),
                                        r.getString("state"),
                                        r.getLong("source_revision"),
                                        (Long) r.getObject("note_revision"),
                                        r.getString("outcome_code"),
                                        r.getString("outcome_detail"),
                                        r.getTimestamp("updated_at").toInstant()))
                .optional()
                .orElseThrow(() -> problem("NOT_FOUND", "This preparation step is unavailable."));
    }

    public JobResult result(String id) {
        Job j = job(id);
        Plan p = null;
        if (j.kind().equals("PLAN") && j.state().equals("SUCCEEDED")) {
            p =
                    db.sql("SELECT id FROM local_plan WHERE preparation_job_id=?")
                            .param(id)
                            .query(String.class)
                            .optional()
                            .map(this::plan)
                            .orElse(null);
        }
        return new JobResult(j, p);
    }

    private Job accept(
            String exp, String kind, long source, Long note, Map<String, Object> payload) {
        String id = "local_" + UUID.randomUUID();
        db.sql(
                        "INSERT INTO"
                            + " local_job(id,exploration_id,kind,state,source_revision,note_revision,payload_json)"
                            + " VALUES(?,?,?,'ACCEPTED',?,?,?)")
                .params(id, exp, kind, source, note, write(payload))
                .update();
        return job(id);
    }

    public Job transcribe(String id, SourceCommand command) {
        enabled();
        if (command == null || command.expectedSourceRevision() == null)
            throw problem("INVALID_REQUEST", "Provide the source revision.");
        return tx.execute(
                s -> {
                    guard();
                    idle();
                    var e = exploration(id, command.expectedSourceRevision());
                    var r =
                            db.sql("SELECT storage_key,duration_ms FROM recording WHERE id=?")
                                    .param(e.get("original_id"))
                                    .query()
                                    .singleRow();
                    if (number(r, "duration_ms") > 12000)
                        throw problem(
                                "PROVIDER_DURATION_UNSUPPORTED",
                                "Local melody work supports clips up to 12 seconds. Audio is never"
                                        + " cropped.");
                    Map<String, Object> payload = new LinkedHashMap<>();
                    payload.put("schemaVersion", 1);
                    payload.put("operation", "transcribe");
                    payload.put(
                            "sourceAudio",
                            media.path((String) r.get("storage_key")).toAbsolutePath().toString());
                    payload.put("sourceRecordingId", e.get("original_id"));
                    return accept(
                            id, "TRANSCRIBE", command.expectedSourceRevision(), null, payload);
                });
    }

    public Notes saveNotes(String id, NotesCommand command) {
        enabled();
        if (command == null || command.expectedSourceRevision() == null)
            throw problem("INVALID_REQUEST", "Provide source and note revisions.");
        return tx.execute(
                s -> {
                    guard();
                    idle();
                    var e = exploration(id, command.expectedSourceRevision());
                    Notes previous =
                            currentNotes(
                                    id,
                                    command.expectedSourceRevision(),
                                    command.expectedNoteRevision(),
                                    false);
                    validateNotes(command.notes(), previous.durationSeconds());
                    boolean unchanged = previous.notes().equals(command.notes());
                    if (command.confirm() && (!unchanged || previous.previewUrl() == null))
                        throw problem(
                                "PREVIEW_REQUIRED",
                                "Save and listen to a preview of these exact notes before"
                                        + " confirming them.");
                    if (unchanged && !command.confirm()) return previous;
                    String preview =
                            unchanged
                                    ? db.sql(
                                                    "SELECT preview_job_id FROM local_note_revision"
                                                        + " WHERE exploration_id=? AND revision=?")
                                            .params(id, previous.revision())
                                            .query(String.class)
                                            .optional()
                                            .orElse(null)
                                    : null;
                    long revision = nextRevision(id);
                    db.sql(
                                    "INSERT INTO"
                                        + " local_note_revision(exploration_id,revision,source_recording_id,source_revision,status,notes_json,duration_seconds,preview_job_id)"
                                        + " VALUES(?,?,?,?,?,?,?,?)")
                            .params(
                                    id,
                                    revision,
                                    e.get("original_id"),
                                    command.expectedSourceRevision(),
                                    command.confirm() ? "CONFIRMED" : "PROPOSED",
                                    write(command.notes()),
                                    previous.durationSeconds(),
                                    preview)
                            .update();
                    return notes(id, command.expectedSourceRevision());
                });
    }

    public Job preview(String id, RevisionCommand command) {
        enabled();
        if (command == null || command.expectedSourceRevision() == null)
            throw problem("INVALID_REQUEST", "Provide revisions.");
        return tx.execute(
                s -> {
                    guard();
                    idle();
                    exploration(id, command.expectedSourceRevision());
                    Notes n =
                            currentNotes(
                                    id,
                                    command.expectedSourceRevision(),
                                    command.expectedNoteRevision(),
                                    false);
                    validateNotes(n.notes(), n.durationSeconds());
                    return accept(
                            id,
                            "PREVIEW",
                            command.expectedSourceRevision(),
                            n.revision(),
                            Map.of(
                                    "schemaVersion",
                                    1,
                                    "operation",
                                    "preview",
                                    "notes",
                                    n.notes(),
                                    "duration",
                                    n.durationSeconds()));
                });
    }

    private Map<String, Object> planningPayload(String id, PlanCommand command) {
        if (command == null
                || command.expectedSourceRevision() == null
                || command.expectedDraftRevision() == null
                || command.text() == null
                || command.text().isBlank()
                || command.text().codePointCount(0, command.text().length()) > 1000)
            throw problem("INVALID_REQUEST", "Provide a direction of at most 1000 characters.");
        var e = exploration(id, command.expectedSourceRevision());
        var confirmed =
                currentNotes(
                        id, command.expectedSourceRevision(), command.expectedNoteRevision(), true);
        if (number(e, "draft_revision") != command.expectedDraftRevision())
            throw problem("STALE_DRAFT", "The direction changed. Review the current draft.");
        String basis =
                command.basisRecordingId() == null
                        ? (String) e.get("original_id")
                        : command.basisRecordingId();
        if (!db.sql(
                        "SELECT EXISTS(SELECT 1 FROM recording WHERE id=? AND exploration_id=? AND"
                                + " (id=? OR (kind='RENDITION' AND provenance='LOCAL')))")
                .params(basis, id, e.get("original_id"))
                .query(Boolean.class)
                .single())
            throw problem(
                    "LOCAL_BASIS_UNSUPPORTED",
                    "Choose the original or a local arrangement as the basis.");
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("schemaVersion", 1);
        payload.put("operation", "plan");
        payload.put("prompt", command.text());
        var inherited = basisRecipe(basis);
        validateArrangementTiming(inherited, confirmed.durationSeconds());
        payload.put("parentRecipe", inherited);
        payload.put("notes", confirmed.notes());
        payload.put("duration", confirmed.durationSeconds());
        payload.put("draftRevision", command.expectedDraftRevision());
        payload.put("basisRecordingId", basis);
        payload.put("sourceRecordingId", e.get("original_id"));
        return payload;
    }

    public Job propose(String id, PlanCommand command) {
        enabled();
        return tx.execute(
                s -> {
                    guard();
                    idle();
                    var payload = planningPayload(id, command);
                    return accept(
                            id,
                            "PLAN",
                            command.expectedSourceRevision(),
                            command.expectedNoteRevision(),
                            payload);
                });
    }

    /** Copy the chosen basis into a reviewable plan without any model execution. */
    public Plan manual(String id, PlanCommand command) {
        enabled();
        return tx.execute(
                s -> {
                    guard();
                    idle();
                    var payload = planningPayload(id, command);
                    String planId = "plan_" + UUID.randomUUID();
                    db.sql(
                                    "INSERT INTO"
                                        + " local_plan(id,exploration_id,source_recording_id,source_revision,note_revision,draft_revision,basis_recording_id,submitted_text,recipe_json,basis_recipe_json,summary,warnings_json,can_generate,model_json)"
                                        + " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,true,?)")
                            .params(
                                    planId,
                                    id,
                                    payload.get("sourceRecordingId"),
                                    command.expectedSourceRevision(),
                                    command.expectedNoteRevision(),
                                    command.expectedDraftRevision(),
                                    payload.get("basisRecordingId"),
                                    command.text(),
                                    write(payload.get("parentRecipe")),
                                    write(payload.get("parentRecipe")),
                                    "These arrangement settings come from the selected version.",
                                    write(
                                            List.of(
                                                    "Direct controls use your selected settings. No"
                                                        + " language model was called.")),
                                    write(Map.of("origin", "USER_DEFINED")))
                            .update();
                    return plan(planId);
                });
    }

    private Map<String, Object> basisRecipe(String basis) {
        var values =
                db.sql(
                                "SELECT p.recipe_json FROM recording r JOIN generation_request q ON"
                                    + " q.id=r.request_id JOIN local_plan p ON p.id=q.local_plan_id"
                                    + " WHERE r.id=?")
                        .param(basis)
                        .query(String.class)
                        .list();
        return values.isEmpty()
                ? Map.of(
                        "instrument",
                        "piano",
                        "bass",
                        false,
                        "drums",
                        false,
                        "density",
                        "steady",
                        "backingVolume",
                        "normal",
                        "bpm",
                        100)
                : map(values.getFirst());
    }

    public Plan plan(String id) {
        var r =
                db
                        .sql("SELECT * FROM local_plan WHERE id=?")
                        .param(id)
                        .query()
                        .listOfRows()
                        .stream()
                        .findFirst()
                        .orElseThrow(
                                () ->
                                        problem(
                                                "PLAN_CHANGED",
                                                "The proposed changes are unavailable."));
        return new Plan(
                id,
                number(r, "note_revision"),
                number(r, "source_revision"),
                number(r, "draft_revision"),
                (String) r.get("submitted_text"),
                (String) r.get("basis_recording_id"),
                map((String) r.get("recipe_json")),
                map((String) r.get("basis_recipe_json")),
                (String) r.get("summary"),
                (Boolean) r.get("can_generate"),
                Arrays.asList(json.readValue((String) r.get("warnings_json"), String[].class)),
                map((String) r.get("model_json")));
    }

    /** Human corrections create a new immutable plan; the model suggestion is never rewritten. */
    public Plan correct(String id, CorrectionCommand command) {
        enabled();
        if (command == null
                || command.expectedSourceRevision() == null
                || command.expectedNoteRevision() == null
                || command.expectedDraftRevision() == null
                || command.text() == null)
            throw problem(
                    "INVALID_REQUEST",
                    "Provide the current source, melody, draft and arrangement controls.");
        var recipe = validateRecipe(command.recipe());
        return tx.execute(
                status -> {
                    guard();
                    idle();
                    var parent = plan(id);
                    String explorationId =
                            db.sql("SELECT exploration_id FROM local_plan WHERE id=?")
                                    .param(id)
                                    .query(String.class)
                                    .single();
                    var exploration = exploration(explorationId, command.expectedSourceRevision());
                    if (number(exploration, "draft_revision") != command.expectedDraftRevision())
                        throw problem(
                                "STALE_DRAFT",
                                "The direction changed. Review it before saving arrangement"
                                        + " corrections.");
                    if (parent.noteRevision() != command.expectedNoteRevision())
                        throw problem(
                                "NOTES_CHANGED",
                                "The melody changed. Review the current confirmed notes.");
                    var confirmed =
                            requirePlanNotes(
                                    explorationId,
                                    new Models.CreateRequestCommand(
                                            command.text(),
                                            command.basisRecordingId(),
                                            command.expectedSourceRevision(),
                                            command.expectedDraftRevision(),
                                            null,
                                            id),
                                    parent);
                    validateArrangementTiming(recipe, confirmed.durationSeconds());
                    if (recipe.equals(parent.recipe())) return parent;
                    Map<String, Object> provenance = new LinkedHashMap<>(parent.model());
                    String originalRecipeKey =
                            switch (String.valueOf(parent.model().get("origin"))) {
                                case "USER_DEFINED" -> "initialRecipe";
                                case "GUIDED_COMMANDS" -> "commandRecipe";
                                default -> "modelSuggestedRecipe";
                            };
                    Object originalId = parent.id(), originalRecipe = parent.recipe();
                    if (parent.model().get("correction") instanceof Map<?, ?> previous) {
                        originalId = previous.get("originalPlanId");
                        originalRecipe = previous.get(originalRecipeKey);
                    }
                    provenance.put(
                            "correction",
                            Map.of(
                                    "kind",
                                    "USER_EDIT",
                                    "parentPlanId",
                                    parent.id(),
                                    "originalPlanId",
                                    originalId,
                                    originalRecipeKey,
                                    originalRecipe,
                                    "editedFromRecipe",
                                    parent.recipe()));
                    String corrected = "plan_" + UUID.randomUUID();
                    db.sql(
                                    "INSERT INTO"
                                        + " local_plan(id,exploration_id,source_recording_id,source_revision,note_revision,draft_revision,basis_recording_id,submitted_text,recipe_json,basis_recipe_json,summary,warnings_json,can_generate,model_json)"
                                        + " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,true,?)")
                            .params(
                                    corrected,
                                    explorationId,
                                    exploration.get("original_id"),
                                    parent.sourceRevision(),
                                    parent.noteRevision(),
                                    parent.draftRevision(),
                                    parent.basisRecordingId(),
                                    parent.text(),
                                    write(recipe),
                                    write(parent.basisRecipe()),
                                    "Your arrangement choices. The confirmed melody notes stay"
                                            + " unchanged."
                                            + grooveDescription(recipe, parent.recipe())
                                            + LocalHarmony.describe(recipe, parent.recipe())
                                            + LocalMix.describe(recipe, parent.recipe()),
                                    write(
                                            List.of(
                                                    switch (originalRecipeKey) {
                                                        case "initialRecipe" ->
                                                                "Direct arrangement choices; no"
                                                                    + " language model was called.";
                                                        case "commandRecipe" ->
                                                                "You edited the guided command's"
                                                                    + " settings before rendering.";
                                                        default ->
                                                                "You edited these controls; they"
                                                                    + " may differ from the model's"
                                                                    + " interpretation of your"
                                                                    + " words.";
                                                    })),
                                    write(provenance))
                            .update();
                    return plan(corrected);
                });
    }

    /** Caller owns workspace/exploration guard. Immutable plan is the render contract. */
    public Plan requirePlan(String explorationId, Models.CreateRequestCommand command) {
        requireIdle();
        if (command.localPlanId() == null)
            throw problem(
                    "PLAN_REQUIRED",
                    "Preview and confirm the proposed changes before creating a version.");
        Plan p = plan(command.localPlanId());
        var confirmed = requirePlanNotes(explorationId, command, p);
        validateArrangementTiming(p.recipe(), confirmed.durationSeconds());
        return p;
    }

    private Notes requirePlanNotes(
            String explorationId, Models.CreateRequestCommand command, Plan p) {
        var e = exploration(explorationId, command.expectedSourceRevision());
        boolean owned =
                db.sql("SELECT EXISTS(SELECT 1 FROM local_plan WHERE id=? AND exploration_id=?)")
                        .params(p.id(), explorationId)
                        .query(Boolean.class)
                        .single();
        String basis =
                command.basisRecordingId() == null
                        ? (String) e.get("original_id")
                        : command.basisRecordingId();
        if (!owned
                || !p.canGenerate()
                || p.sourceRevision() != command.expectedSourceRevision()
                || p.draftRevision() != command.expectedDraftRevision()
                || !p.text().equals(command.text())
                || !p.basisRecordingId().equals(basis))
            throw problem(
                    "PLAN_CHANGED",
                    "The source, direction or selected basis changed. Preview the proposed changes"
                            + " again.");
        return currentNotes(explorationId, p.sourceRevision(), p.noteRevision(), true);
    }

    public Map<String, Object> renderRequest(String planId, String exploration) {
        Plan p = plan(planId);
        var source =
                db.sql(
                                """
                                SELECT n.notes_json, n.duration_seconds, r.storage_key, r.sha256
                                FROM local_note_revision n
                                JOIN recording r ON r.id=n.source_recording_id AND r.exploration_id=n.exploration_id
                                JOIN local_plan p ON p.exploration_id=n.exploration_id AND p.note_revision=n.revision
                                    AND p.source_recording_id=r.id AND p.source_revision=n.source_revision
                                WHERE p.id=? AND p.exploration_id=? AND n.status='CONFIRMED'
                                """)
                        .params(planId, exploration)
                        .query()
                        .listOfRows()
                        .stream()
                        .findFirst()
                        .orElseThrow(() -> problem("PLAN_CHANGED", "The reviewed melody and recording do not match."));
        validateArrangementTiming(p.recipe(), ((Number) source.get("duration_seconds")).doubleValue());
        Map<String, Object> request = new LinkedHashMap<>();
        request.put("schemaVersion", 1);
        request.put("operation", "render");
        request.put("notes", readNotes((String) source.get("notes_json")));
        request.put("duration", source.get("duration_seconds"));
        request.put("recipe", p.recipe());
        if ("recorded".equals(p.recipe().get("instrument"))) {
            request.put("sourceAudio", media.path((String) source.get("storage_key")).toAbsolutePath().toString());
            request.put("sourceSha256", source.get("sha256"));
        }
        return request;
    }

    public String reviewedDirection(Plan plan) {
        var recipe = plan.recipe();
        String instrument =
                switch ((String) recipe.get("instrument")) {
                    case "piano" -> "Piano";
                    case "recorded" -> "Uploaded recording";
                    case "electric_piano" -> "Electric piano";
                    case "guitar" -> "Guitar";
                    default ->
                            throw problem(
                                    "PLAN_INVALID", "The reviewed instrument is unsupported.");
                };
        return plan.text()
                + "\n\nReviewed arrangement: "
                + instrument
                + "; bass "
                + (Boolean.TRUE.equals(recipe.get("bass")) ? "on" : "off")
                + "; drums "
                + (Boolean.TRUE.equals(recipe.get("drums")) ? "on" : "off")
                + "; "
                + recipe.get("density")
                + ", "
                + recipe.get("backingVolume")
                + " backing; "
                + recipe.get("bpm")
                + " BPM backing pulse."
                + grooveDescription(recipe, plan.basisRecipe())
                + LocalHarmony.describe(recipe, plan.basisRecipe())
                + LocalMix.describe(recipe, plan.basisRecipe());
    }

    public String frozenInstructions(Plan plan) {
        Map<String, Object> instruction = new TreeMap<>();
        instruction.put("schemaVersion", 1);
        instruction.put("planId", plan.id());
        instruction.put("noteRevision", plan.noteRevision());
        instruction.put("sourceRevision", plan.sourceRevision());
        instruction.put("recipe", new TreeMap<>(plan.recipe()));
        return write(instruction);
    }

    public Job cancel(String id) {
        tx.executeWithoutResult(
                s -> {
                    guard();
                    db.sql(
                                    "UPDATE local_job SET cancel_requested=true WHERE id=? AND"
                                            + " state IN ('ACCEPTED','RUNNING')")
                            .param(id)
                            .update();
                });
        worker.stop(id);
        db.sql(
                        "UPDATE local_job SET"
                            + " state='FAILED',version=version+1,outcome_code='CANCELLED',outcome_detail='Local"
                            + " preparation was cancelled.',updated_at=now() WHERE id=? AND state"
                            + " IN ('ACCEPTED','RUNNING')")
                .param(id)
                .update();
        return job(id);
    }

    public Path previewAudio(String id) throws Exception {
        var r =
                db.sql(
                                "SELECT result_json FROM local_job WHERE id=? AND state='SUCCEEDED'"
                                        + " AND kind IN ('TRANSCRIBE','PREVIEW')")
                        .param(id)
                        .query(String.class)
                        .optional()
                        .orElseThrow(
                                () -> problem("MEDIA_UNAVAILABLE", "The preview is unavailable."));
        return worker.audio(id, json.readTree(r).path("audioPath").asText());
    }

    @Scheduled(fixedDelay = 300, initialDelay = 500)
    public void tick() {
        if (!config.local() || !running.compareAndSet(false, true)) return;
        String id = null;
        try {
            var selected =
                    tx.execute(
                            s -> {
                                guard();
                                var ids =
                                        db.sql(
                                                        "SELECT id FROM local_job WHERE"
                                                                + " state='ACCEPTED' AND NOT"
                                                                + " cancel_requested ORDER BY"
                                                                + " created_at LIMIT 1")
                                                .query(String.class)
                                                .list();
                                if (ids.isEmpty()) return null;
                                String key = ids.getFirst();
                                db.sql(
                                                "UPDATE local_job SET"
                                                    + " state='RUNNING',version=version+1,updated_at=now()"
                                                    + " WHERE id=?")
                                        .param(key)
                                        .update();
                                return key;
                            });
            if (selected == null) return;
            id = selected;
            final String key = id;
            var row = db.sql("SELECT * FROM local_job WHERE id=?").param(id).query().singleRow();
            Map<String, Object> payload = map((String) row.get("payload_json"));
            Map<String, Object> input = new LinkedHashMap<>(payload);
            input.keySet()
                    .retainAll(
                            Set.of(
                                    "schemaVersion",
                                    "operation",
                                    "sourceAudio",
                                    "notes",
                                    "duration",
                                    "prompt",
                                    "parentRecipe",
                                    "recipe"));
            JsonNode result = worker.execute(id, input, () -> cancelled(key));
            String kind = (String) row.get("kind");
            if (!kind.equals("PLAN")) {
                Path audio = worker.audio(id, result.path("audioPath").asText());
                var valid = media.validateFile(audio, "preview.wav");
                if (valid.preciseSeconds() > 12.0)
                    throw problem("OUTPUT_INVALID", "The local preview exceeds 12 seconds.");
            }
            final String jobId = id;
            tx.executeWithoutResult(
                    s -> {
                        guard();
                        if (cancelled(jobId)) return;
                        String exploration = (String) row.get("exploration_id");
                        long source = number(row, "source_revision");
                        var e = exploration(exploration, source);
                        if (kind.equals("TRANSCRIBE")) {
                            List<Note> proposal =
                                    Arrays.asList(
                                            json.treeToValue(result.path("notes"), Note[].class));
                            double duration = result.path("duration").asDouble();
                            validateProposal(proposal, duration);
                            db.sql(
                                            "INSERT INTO"
                                                + " local_note_revision(exploration_id,revision,source_recording_id,source_revision,status,notes_json,duration_seconds,preview_job_id)"
                                                + " VALUES(?,?,?,?,'PROPOSED',?,?,?)")
                                    .params(
                                            exploration,
                                            nextRevision(exploration),
                                            e.get("original_id"),
                                            source,
                                            write(proposal),
                                            duration,
                                            jobId)
                                    .update();
                        } else if (kind.equals("PREVIEW")) {
                            Notes n =
                                    currentNotes(
                                            exploration,
                                            source,
                                            number(row, "note_revision"),
                                            false);
                            db.sql(
                                            "UPDATE local_note_revision SET preview_job_id=? WHERE"
                                                    + " exploration_id=? AND revision=?")
                                    .params(jobId, exploration, n.revision())
                                    .update();
                        } else {
                            var confirmed =
                                    currentNotes(
                                            exploration,
                                            source,
                                            number(row, "note_revision"),
                                            true);
                            if (number(e, "draft_revision")
                                    != ((Number) payload.get("draftRevision")).longValue())
                                throw problem(
                                        "STALE_DRAFT",
                                        "The direction changed while it was being interpreted."
                                                + " Review it and try again.");
                            Map<String, Object> recipe =
                                    validateRecipe(map(result.path("recipe").toString()));
                            validateArrangementTiming(recipe, confirmed.durationSeconds());
                            db.sql(
                                            "INSERT INTO"
                                                + " local_plan(id,exploration_id,source_recording_id,source_revision,note_revision,draft_revision,basis_recording_id,submitted_text,recipe_json,basis_recipe_json,summary,warnings_json,can_generate,model_json,preparation_job_id)"
                                                + " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,true,?,?)")
                                    .params(
                                            "plan_" + UUID.randomUUID(),
                                            exploration,
                                            e.get("original_id"),
                                            source,
                                            row.get("note_revision"),
                                            payload.get("draftRevision"),
                                            payload.get("basisRecordingId"),
                                            payload.get("prompt"),
                                            write(recipe),
                                            write(payload.get("parentRecipe")),
                                            result.path("explanation")
                                                    .asText(
                                                            "Review these explicit instrument and"
                                                                    + " backing changes."),
                                            "[]",
                                            result.path("provenance").isObject()
                                                    ? result.path("provenance").toString()
                                                    : "{}",
                                            jobId)
                                    .update();
                        }
                        db.sql(
                                        "UPDATE local_job SET"
                                            + " state='SUCCEEDED',version=version+1,result_json=?,updated_at=now()"
                                            + " WHERE id=? AND state='RUNNING' AND NOT"
                                            + " cancel_requested")
                                .params(result.toString(), jobId)
                                .update();
                    });
        } catch (Exception error) {
            if (id != null)
                db.sql(
                                "UPDATE local_job SET"
                                    + " state='FAILED',version=version+1,outcome_code=?,outcome_detail=?,updated_at=now()"
                                    + " WHERE id=? AND state IN ('ACCEPTED','RUNNING')")
                        .params(
                                error instanceof ApiProblem p ? p.code() : "LOCAL_WORKER_FAILED",
                                error instanceof ApiProblem
                                        ? error.getMessage()
                                        : "The local step could not finish. Your source and saved"
                                                + " notes remain available.",
                                id)
                        .update();
        } finally {
            running.set(false);
        }
    }

    private boolean cancelled(String id) {
        return db.sql("SELECT cancel_requested OR state<>'RUNNING' FROM local_job WHERE id=?")
                .param(id)
                .query(Boolean.class)
                .single();
    }

    public static void validateProposal(List<Note> notes, double duration) {
        if (notes == null
                || notes.isEmpty()
                || notes.size() > 256
                || !Double.isFinite(duration)
                || duration <= 0
                || duration > 12)
            throw problem("NOTES_INVALID", "Use 1–256 notes in a clip up to 12 seconds.");
        for (Note n : notes)
            if (n == null
                    || !Double.isFinite(n.start())
                    || !Double.isFinite(n.end())
                    || n.start() < 0
                    || n.end() <= n.start()
                    || n.end() > duration
                    || n.pitch() < 0
                    || n.pitch() > 127
                    || n.velocity() < 1
                    || n.velocity() > 127)
                throw problem(
                        "NOTES_INVALID", "Check each note's pitch, velocity and start/end times.");
    }

    public static void validateNotes(List<Note> notes, double duration) {
        validateProposal(notes, duration);
        var sorted = notes.stream().sorted(Comparator.comparingDouble(Note::start)).toList();
        Map<Integer, Long> ends = new HashMap<>();
        for (Note n : sorted) {
            long start = (long) Math.rint(n.start() * 44100),
                    end = (long) Math.rint(n.end() * 44100);
            if (end <= start)
                throw problem("NOTES_INVALID", "Each note must last at least one audio sample.");
            if (start < ends.getOrDefault(n.pitch(), -1L))
                throw problem(
                        "NOTES_OVERLAP",
                        "Review overlapping notes at the same pitch. Different pitches may"
                                + " overlap.");
            ends.put(n.pitch(), end);
        }
    }

    /** Validate a complete proposal and return its canonical representation for persistence. */
    public static Map<String, Object> validateRecipe(Map<String, Object> input) {
        Map<String, Object> r = new LinkedHashMap<>(input == null ? Map.of() : input);
        r.remove("mix");
        Object grid = r.remove("groove");
        Object harmony = r.remove("harmony");
        if (!r.keySet()
                        .equals(
                                Set.of(
                                        "instrument",
                                        "bass",
                                        "drums",
                                        "density",
                                        "backingVolume",
                                        "bpm"))
                || !Set.of("piano", "electric_piano", "guitar", "recorded")
                        .contains(String.valueOf(r.get("instrument")))
                || !(r.get("bass") instanceof Boolean)
                || !(r.get("drums") instanceof Boolean)
                || !Set.of("sparse", "steady").contains(String.valueOf(r.get("density")))
                || !Set.of("soft", "normal").contains(String.valueOf(r.get("backingVolume")))
                || !(r.get("bpm") instanceof Integer tempo)
                || tempo < 40
                || tempo > 200
                || (grid == null && tempo != 100))
            throw problem(
                    "PLAN_INVALID",
                    "The proposed changes are outside the supported instrument/backing controls.");
        if (input.containsKey("groove")) {
            if (!(grid instanceof Map<?, ?> fields)
                    || !fields.keySet().equals(Set.of("pattern", "downbeatMs"))
                    || !Set.of("swing_ride", "four_on_the_floor")
                            .contains(String.valueOf(fields.get("pattern")))
                    || !(fields.get("downbeatMs") instanceof Integer firstBeat)
                    || firstBeat < 0
                    || firstBeat > 11990)
                throw problem(
                        "PLAN_INVALID",
                        "Choose a supported drum pattern and a first beat in milliseconds.");
            Map<String, Object> groove = new TreeMap<>();
            groove.put("pattern", fields.get("pattern"));
            groove.put("downbeatMs", firstBeat);
            r.put("groove", groove);
        }
        if (input.containsKey("harmony")) r.put("harmony", LocalHarmony.canonical(harmony));
        var mix = LocalMix.canonical(input);
        if (!mix.isEmpty()) r.put("mix", mix);
        return r;
    }

    private static void validateArrangementTiming(Map<String, Object> recipe, double seconds) {
        LocalHarmony.validateTiming(recipe, seconds);
        if (recipe.get("groove") instanceof Map<?, ?> grid
                && (Integer) grid.get("downbeatMs") + 10L > seconds * 1000 + 1e-7)
            throw problem(
                    "PLAN_INVALID",
                    "Place the first beat inside the recording with at least 10 ms left to play.");
    }

    private static String grooveDescription(
            Map<String, Object> recipe, Map<String, Object> previous) {
        if (recipe.get("groove") instanceof Map<?, ?> grid)
            return " Drum pattern: "
                    + grid.get("pattern").toString().replace('_', ' ')
                    + ", "
                    + recipe.get("bpm")
                    + " BPM, first beat at "
                    + grid.get("downbeatMs")
                    + " ms.";
        return previous.containsKey("groove") ? " Restored the original 100 BPM pulse." : "";
    }

    private List<Note> readNotes(String value) {
        return Arrays.asList(json.readValue(value, Note[].class));
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> map(String value) {
        return json.readValue(value, Map.class);
    }

    private String write(Object value) {
        return json.writeValueAsString(value);
    }

    private static long number(Map<String, Object> row, String key) {
        return ((Number) row.get(key)).longValue();
    }

    private static ApiProblem problem(String code, String detail) {
        return new ApiProblem(409, code, detail, "EDIT_TEXT");
    }
}
