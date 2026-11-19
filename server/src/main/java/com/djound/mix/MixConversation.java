package com.djound.mix;

import static com.djound.mix.MixModels.*;
import static com.djound.mix.MixStore.*;

import com.djound.ApiProblem;
import com.djound.local.LocalWorker;

import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionTemplate;

import tools.jackson.databind.JsonNode;

import java.util.*;

@Component
class MixConversation {
    private final tools.jackson.databind.ObjectMapper json =
            new tools.jackson.databind.ObjectMapper();
    private final MixStore store;
    private final MixArtifacts files;
    private final LocalWorker worker;
    private final TransactionTemplate tx;

    MixConversation(
            MixStore store, MixArtifacts files, LocalWorker worker, TransactionTemplate tx) {
        this.store = store;
        this.files = files;
        this.worker = worker;
        this.tx = tx;
    }

    private record Plan(
            Project project,
            Passage passage,
            String parent,
            String reference,
            String control,
            double vocals,
            double drums,
            double trim,
            double difference,
            String label,
            Reply clarification) {
        static Plan clarify(String message, List<Take> alternatives) {
            return clarify(message, alternatives, null);
        }

        static Plan clarify(String message, List<Take> alternatives, String control) {
            return answered(new Reply("CLARIFY", message, null, alternatives, control, null, null));
        }

        static Plan answered(Reply immediate) {
            return new Plan(null, null, null, null, null, 0, 0, 0, 0, null, immediate);
        }
    }

    Reply edit(String id, Edit command, String key) throws Exception {
        if (key != null) MixProjects.validateKey(key);
        if (command.text() == null
                || command.text().isBlank()
                || command.text().length() > 1000
                || command.basisTakeId() == null
                || command.startFrame() == null
                || command.endFrame() == null)
            throw new IllegalArgumentException(
                    "A request, playback basis and selected passage are required.");
        String body = json.writeValueAsString(command);
        Plan plan =
                tx.execute(
                        status -> {
                            Project project = store.project(id, true);
                            SavedTake previous = receipt(id, key, body);
                            if (previous != null)
                                return Plan.answered(
                                        new Reply(
                                                "PREVIEW",
                                                previous.label(),
                                                previous.view(),
                                                List.of()));
                            return plan(project, command);
                        });
        if (plan.clarification() != null) return plan.clarification();
        // Render outside the database transaction. A late result adds an alternative, never changes
        // playback or Keep.
        String job = MixProjects.jobId();
        Project project = plan.project();
        Passage passage = plan.passage();
        String sourcePath = project.sourcePath(), sha = project.sourceSha();
        if (!ORIGINAL.equals(passage.baseId())) {
            SavedTake base = store.take(id, passage.baseId());
            sourcePath = base.audioPath();
            sha = base.sha256();
        }
        String prepPath;
        try {
            prepPath = files.existing(passage.prepPath()).toString();
        } catch (ApiProblem e) {
            throw invalidatePreparation(passage);
        }
        Map<String, Object> request =
                new LinkedHashMap<>(
                        Map.of(
                                "schemaVersion",
                                1,
                                "operation",
                                "mix-preview",
                                "sourceAudio",
                                files.existing(sourcePath).toString(),
                                "sourceSha256",
                                sha,
                                "preparationPath",
                                prepPath,
                                "preparationSha256",
                                passage.prepSha(),
                                "startFrame",
                                passage.startFrame(),
                                "endFrame",
                                passage.endFrame(),
                                "gains",
                                Map.of("vocals", plan.vocals(), "drums", plan.drums()),
                                "changedControl",
                                plan.control()));
        request.put("passageTrimDb", plan.trim());
        JsonNode result;
        try {
            result = worker.execute(job, request, () -> false);
        } catch (ApiProblem e) {
            if ("MIX_PREPARATION_INVALID".equals(e.code())) throw invalidatePreparation(passage);
            throw e;
        }
        if ("MIX_WOULD_CLIP".equals(result.path("error").path("code").asText(""))) {
            JsonNode error = result.path("error"), details = error.path("details");
            if (!details.isObject()) details = error;
            return new Reply(
                    "CLIPPING",
                    error.path("message").asText(),
                    null,
                    List.of(),
                    plan.control(),
                    bounded(details.path("suggestedPassageTrimDb"), -12, 0),
                    bounded(details.path("suggestedGainDb"), -12, 12));
        }
        var wave = files.wave(job, result, project.frames());
        SavedTake take =
                tx.execute(
                        status -> {
                            store.project(id, true);
                            SavedTake previous = receipt(id, key, body);
                            if (previous != null) return previous;
                            int number =
                                    store.db
                                            .sql(
                                                    "SELECT COALESCE(MAX(number),0)+1 FROM mix_take"
                                                            + " WHERE project_id=?")
                                            .param(id)
                                            .query(Integer.class)
                                            .single();
                            String takeId = UUID.randomUUID().toString();
                            store.db
                                    .sql(
                                            """
                                            INSERT INTO mix_take(id,project_id,number,passage_id,parent_id,reference_id,vocals_db,drums_db,
                                            passage_trim_db,last_control,last_change_db,request_text,label,audio_path,sha256) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                                            """)
                                    .params(
                                            takeId,
                                            id,
                                            number,
                                            passage.id(),
                                            plan.parent(),
                                            plan.reference(),
                                            plan.vocals(),
                                            plan.drums(),
                                            plan.trim(),
                                            plan.control(),
                                            plan.difference(),
                                            command.text(),
                                            plan.label(),
                                            files.storePath(wave.file().path()),
                                            wave.file().sha())
                                    .update();
                            store.db
                                    .sql("UPDATE mix_project SET revision=revision+1 WHERE id=?")
                                    .param(id)
                                    .update();
                            if (key != null)
                                store.db
                                        .sql(
                                                "INSERT INTO"
                                                    + " mix_edit_receipt(project_id,command_key,request_json,take_id)"
                                                    + " VALUES(?,?,?,?)")
                                        .params(id, key, body, takeId)
                                        .update();
                            return store.take(id, takeId);
                        });
        if (!files.storePath(wave.file().path()).equals(take.audioPath()))
            files.discardDuplicate(wave.file().path());
        return new Reply("PREVIEW", plan.label(), take.view(), List.of());
    }

    private ApiProblem invalidatePreparation(Passage passage) {
        String message =
                "The saved preparation no longer matches this passage. Retry preparation to rebuild"
                        + " it.";
        tx.executeWithoutResult(
                status -> {
                    Project current = store.project(passage.projectId(), true);
                    int changed =
                            store.db
                                    .sql(
                                            """
                                            UPDATE mix_passage SET prep_path=NULL,prep_sha=NULL,cache_key=NULL
                                            WHERE id=? AND project_id=? AND prep_path IS NOT DISTINCT FROM ? AND prep_sha IS NOT DISTINCT FROM ?
                                            """)
                                    .params(
                                            passage.id(),
                                            current.id(),
                                            passage.prepPath(),
                                            passage.prepSha())
                                    .update();
                    if (changed > 0
                            && passage.id().equals(current.passageId())
                            && "READY".equals(current.preparation()))
                        store.db
                                .sql(
                                        "UPDATE mix_project SET"
                                            + " preparation='FAILED',error=?,revision=revision+1"
                                            + " WHERE id=?")
                                .params(message, current.id())
                                .update();
                });
        return new ApiProblem(422, "MIX_PREPARATION_INVALID", message, "RETRY");
    }

    private SavedTake receipt(String id, String key, String body) {
        if (key == null) return null;
        var rows =
                store.db
                        .sql(
                                "SELECT request_json,take_id FROM mix_edit_receipt WHERE"
                                        + " project_id=? AND command_key=?")
                        .params(id, key)
                        .query()
                        .listOfRows();
        if (rows.isEmpty()) return null;
        var row = rows.getFirst();
        if (!body.equals(row.get("request_json"))) throw MixProjects.keyConflict();
        return store.take(id, (String) row.get("take_id"));
    }

    private Plan plan(Project project, Edit command) {
        String id = project.id();
        Passage passage = store.passage(id, project.passageId());
        if (!"READY".equals(project.preparation()) || passage == null)
            throw MixProjects.conflict("Wait for preparation to finish on the selected passage.");
        if (passage.startFrame() != command.startFrame()
                || passage.endFrame() != command.endFrame())
            throw MixProjects.conflict(
                    "The selected passage changed. Try the request on the current selection.");
        String parent = command.basisTakeId();
        // Auditioning choices can change the playback pointer while an explicit copy is pending.
        if (command.referenceTakeId() == null && !project.heardTake().equals(parent))
            throw MixProjects.conflict(
                    "Playback changed. Listen to the take you want to refine and try again.");
        double vocals = 0, drums = 0, trim = 0, lastChange = 0;
        String lastControl = null;
        if (!parent.equals(passage.baseId())) {
            if (ORIGINAL.equals(parent))
                throw MixProjects.conflict(
                        "Listen to the selected passage's base or one of its takes.");
            SavedTake basis = store.take(id, parent);
            if (!basis.heard() || !basis.passageId().equals(passage.id()))
                throw MixProjects.conflict(
                        "Listen to a take from the selected passage before refining it.");
            vocals = basis.vocalsDb();
            drums = basis.drumsDb();
            trim = basis.passageTrimDb();
            lastControl = basis.lastControl();
            lastChange = basis.lastChangeDb();
        }
        if (command.passageTrimDb() != null) {
            trim = command.passageTrimDb();
            if (!Double.isFinite(trim) || trim < -12 || trim > 0)
                throw new IllegalArgumentException("Choose a passage trim from -12 to 0 dB.");
        }
        var decision =
                MixInterpreter.interpret(command.text(), lastControl, vocals, drums, lastChange);
        if ("CLARIFY".equals(decision.kind())) return Plan.clarify(decision.message(), List.of());
        String control = decision.control(), reference = null;
        if (!Set.of("vocals", "drums").contains(control))
            throw new IllegalStateException("Unexpected edit target");
        double gain = decision.gainDb();
        String label = decision.message();
        if ("COPY".equals(decision.kind())) {
            List<SavedTake> previous =
                    store.takes(id).stream()
                            .filter(
                                    t ->
                                            t.heard()
                                                    && t.passageId().equals(passage.id())
                                                    && !t.id().equals(parent))
                            .toList()
                            .reversed();
            MixTakeReference mentioned = MixTakeReference.parse(command.text());
            if (mentioned.unclear())
                return Plan.clarify(
                        "Name one take by its number, such as Take 2.", List.of(), control);
            if (!mentioned.named().isEmpty())
                previous =
                        previous.stream()
                                .filter(t -> mentioned.named().contains(t.number()))
                                .toList();
            SavedTake chosen;
            if (command.referenceTakeId() != null) {
                chosen =
                        previous.stream()
                                .filter(t -> t.id().equals(command.referenceTakeId()))
                                .findFirst()
                                .orElse(null);
            } else if (!mentioned.named().isEmpty()) {
                if (mentioned.named().size() > 1)
                    return Plan.clarify(
                            "Which take's " + control + " setting should I use?",
                            previous.stream().map(SavedTake::view).toList(),
                            control);
                chosen = previous.stream().findFirst().orElse(null);
            } else {
                // One playable example per distinct setting, newest first. Prefer takes that
                // actually edited this control.
                List<SavedTake> matching =
                        previous.stream().filter(t -> t.lastControl().equals(control)).toList();
                if (matching.isEmpty()) matching = previous;
                Map<Double, SavedTake> choices = new LinkedHashMap<>();
                matching.forEach(t -> choices.putIfAbsent(t.gain(control), t));
                if (choices.size() > 1)
                    return Plan.clarify(
                            "Which take's " + control + " setting should I use?",
                            choices.values().stream().map(SavedTake::view).toList(),
                            control);
                chosen = choices.values().stream().findFirst().orElse(null);
            }
            if (chosen == null)
                return Plan.clarify(
                        "Choose a different take you have heard on this passage.",
                        List.of(),
                        control);
            reference = chosen.id();
            gain = chosen.gain(control);
            label = gainLabel(control, gain) + " from Take " + chosen.number();
        } else if (!"SET".equals(decision.kind()) || command.referenceTakeId() != null)
            throw new IllegalArgumentException("Invalid operation or take reference.");
        if (!Double.isFinite(gain) || gain < -12 || gain > 12)
            return Plan.clarify("Choose a gain from −12 to +12 dB.", List.of());
        double before = "vocals".equals(control) ? vocals : drums;
        if ("vocals".equals(control)) vocals = gain;
        else drums = gain;
        return new Plan(
                project,
                passage,
                parent,
                reference,
                control,
                vocals,
                drums,
                trim,
                gain - before,
                label,
                null);
    }

    private static Double bounded(JsonNode value, double min, double max) {
        double n = value.asDouble(Double.NaN);
        return value.isNumber() && Double.isFinite(n) && min <= n && n <= max ? n : null;
    }

    static String gainLabel(String control, double gain) {
        String amount = java.math.BigDecimal.valueOf(gain).stripTrailingZeros().toPlainString();
        return ("vocals".equals(control) ? "Vocals" : "Drums")
                + " "
                + (gain > 0 ? "+" : "")
                + amount
                + " dB";
    }
}
