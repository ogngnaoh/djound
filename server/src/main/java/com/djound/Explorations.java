package com.djound;

import static com.djound.Models.*;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.multipart.MultipartFile;

import tools.jackson.databind.ObjectMapper;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.util.*;

@Service
public class Explorations {
    private static final ObjectMapper JSON = new ObjectMapper();
    private static final String RECORDING_SELECT =
            "SELECT r.*, p.recipe_json AS local_recipe_json, p.note_revision AS local_note_revision FROM recording r "
                    + "LEFT JOIN generation_request q ON q.id=r.request_id "
                    + "LEFT JOIN local_plan p ON p.id=q.local_plan_id AND r.provenance='LOCAL' ";
    private final JdbcClient jdbc;
    private final MediaStore media;
    private final TransactionTemplate transactions;
    private final com.djound.generation.GenerationService generations;

    public Explorations(
            JdbcClient jdbc,
            MediaStore media,
            PlatformTransactionManager manager,
            com.djound.generation.GenerationService generations) {
        this.generations = generations;
        this.jdbc = jdbc;
        this.media = media;
        this.transactions = new TransactionTemplate(manager);
    }

    public Receipt create(MultipartFile file, String text, String key) throws IOException {
        validateKey(key);
        validateText(text);
        try (var upload = media.ingest(file)) {
            // Length-prefixed fields prevent ambiguous command fingerprints; original name is part
            // of the accepted command.
            String body =
                    upload.hash()
                            + "|"
                            + upload.fileName().length()
                            + ":"
                            + upload.fileName()
                            + "|"
                            + text.length()
                            + ":"
                            + text;
            String fingerprint =
                    HexFormat.of()
                            .formatHex(
                                    MediaStore.sha().digest(body.getBytes(StandardCharsets.UTF_8)));
            return transactions.execute(
                    status -> {
                        var workspace =
                                jdbc.sql(
                                                "SELECT active_attempt_id FROM workspace WHERE id=1"
                                                        + " FOR UPDATE")
                                        .query()
                                        .singleRow();
                        var previous =
                                jdbc
                                        .sql("SELECT * FROM command_receipt WHERE command_key=?")
                                        .param(key)
                                        .query()
                                        .listOfRows()
                                        .stream()
                                        .findFirst();
                        if (previous.isPresent()) {
                            var p = previous.get();
                            if (!p.get("operation").equals("UPLOAD")
                                    || !p.get("fingerprint").equals(fingerprint))
                                throw new ApiProblem(
                                        409,
                                        "IDEMPOTENCY_CONFLICT",
                                        "This upload key already belongs to a different command.",
                                        "CHOOSE_FILE");
                            return new Receipt(
                                    (String) p.get("exploration_id"),
                                    (String) p.get("recording_id"),
                                    ((Number) p.get("source_revision")).longValue(),
                                    ((Number) p.get("draft_revision")).longValue(),
                                    "/api/explorations/" + p.get("exploration_id"),
                                    true);
                        }
                        generations.requireLiveDuration(upload);
                        generations.requireNoLocalPreparation();
                        if (workspace.get("active_attempt_id") != null)
                            throw new ApiProblem(
                                    409,
                                    "CREATION_ACTIVE",
                                    "Wait for the current creation to finish before adding another"
                                            + " source.",
                                    "WAIT");
                        String exp = "exp_" + UUID.randomUUID(), rec = "rec_" + UUID.randomUUID();
                        Instant now = Instant.now();
                        String title = upload.fileName().replaceFirst("(?i)\\.(wav|mp3)$", "");
                        if (title.isBlank()) title = "Recording";
                        String storage = publish(upload);
                        jdbc.sql(
                                        "INSERT INTO"
                                            + " exploration(id,title,original_id,draft_text,draft_basis_id,created_at,updated_at)"
                                            + " VALUES(?,?,?,?,?,?,?)")
                                .params(
                                        exp,
                                        title,
                                        rec,
                                        text,
                                        rec,
                                        java.sql.Timestamp.from(now),
                                        java.sql.Timestamp.from(now))
                                .update();
                        insertOriginal(rec, exp, title, upload, storage, now);
                        jdbc.sql(
                                        "INSERT INTO"
                                            + " command_receipt(command_key,operation,fingerprint,exploration_id,recording_id,source_revision,draft_revision,created_at)"
                                            + " VALUES(?,'UPLOAD',?,?,?,1,1,?)")
                                .params(key, fingerprint, exp, rec, java.sql.Timestamp.from(now))
                                .update();
                        return new Receipt(exp, rec, 1, 1, "/api/explorations/" + exp, false);
                    });
        }
    }

    public Draft saveDraft(String id, DraftUpdate command) {
        if (command == null
                || command.text() == null
                || command.expectedDraftRevision() == null
                || command.expectedDraftRevision() < 1) {
            throw new ApiProblem(
                    400,
                    "INVALID_REQUEST",
                    "Provide text and the current draft revision.",
                    "EDIT_TEXT");
        }
        validateText(command.text());
        return transactions.execute(
                status -> {
                    var row =
                            jdbc
                                    .sql("SELECT * FROM exploration WHERE id=? FOR UPDATE")
                                    .param(id)
                                    .query()
                                    .listOfRows()
                                    .stream()
                                    .findFirst()
                                    .orElseThrow(Explorations::missing);
                    long revision = ((Number) row.get("draft_revision")).longValue();
                    if (revision != command.expectedDraftRevision()) {
                        throw new ApiProblem(
                                409,
                                "STALE_DRAFT",
                                "This direction changed elsewhere. Review the saved version before"
                                        + " saving your words.",
                                "REFRESH_DRAFT");
                    }
                    String basis =
                            command.basisRecordingId() == null
                                    ? (String) row.get("original_id")
                                    : command.basisRecordingId();
                    boolean validBasis =
                            jdbc.sql(
                                            "SELECT EXISTS(SELECT 1 FROM recording WHERE id=? AND"
                                                    + " exploration_id=? AND (id=? OR"
                                                    + " kind='RENDITION'))")
                                    .params(basis, id, row.get("original_id"))
                                    .query(Boolean.class)
                                    .single();
                    if (!validBasis) {
                        throw new ApiProblem(
                                409,
                                "SOURCE_CHANGED",
                                "This direction refers to an unavailable or replaced recording.",
                                "REFRESH_DRAFT");
                    }
                    int changed =
                            jdbc.sql(
                                            "UPDATE exploration SET"
                                                + " draft_text=?,draft_basis_id=?,draft_revision=draft_revision+1,updated_at=?"
                                                + " WHERE id=? AND draft_revision=?")
                                    .params(
                                            command.text(),
                                            basis,
                                            java.sql.Timestamp.from(Instant.now()),
                                            id,
                                            revision)
                                    .update();
                    if (changed != 1)
                        throw new IllegalStateException(
                                "Locked draft revision changed unexpectedly");
                    return new Draft(command.text(), basis, revision + 1);
                });
    }

    public Receipt replaceSource(String id, MultipartFile file, long expectedRevision, String key)
            throws IOException {
        validateKey(key);
        if (expectedRevision < 1)
            throw new ApiProblem(
                    400, "INVALID_REQUEST", "Provide the current source revision.", "RETRY_UPLOAD");
        try (var upload = media.ingest(file)) {
            String operation = "REPLACE_SOURCE:" + id;
            String body =
                    operation
                            + "|"
                            + expectedRevision
                            + "|"
                            + upload.hash()
                            + "|"
                            + upload.fileName().length()
                            + ":"
                            + upload.fileName();
            String fingerprint =
                    HexFormat.of()
                            .formatHex(
                                    MediaStore.sha().digest(body.getBytes(StandardCharsets.UTF_8)));
            return transactions.execute(
                    status -> {
                        var workspace =
                                jdbc.sql(
                                                "SELECT active_attempt_id FROM workspace WHERE id=1"
                                                        + " FOR UPDATE")
                                        .query()
                                        .singleRow();
                        Receipt accepted = replay(key, operation, fingerprint);
                        if (accepted != null) return accepted;
                        var row =
                                jdbc
                                        .sql("SELECT * FROM exploration WHERE id=? FOR UPDATE")
                                        .param(id)
                                        .query()
                                        .listOfRows()
                                        .stream()
                                        .findFirst()
                                        .orElseThrow(Explorations::missing);
                        generations.requireLiveDuration(upload);
                        generations.requireNoLocalPreparation();
                        if (workspace.get("active_attempt_id") != null) {
                            throw new ApiProblem(
                                    409,
                                    "CREATION_ACTIVE",
                                    "Wait for the current creation to finish before replacing the"
                                            + " source.",
                                    "WAIT");
                        }
                        if (jdbc.sql(
                                        "SELECT EXISTS(SELECT 1 FROM recording WHERE"
                                                + " exploration_id=? AND kind='RENDITION')")
                                .param(id)
                                .query(Boolean.class)
                                .single()) {
                            throw new ApiProblem(
                                    409,
                                    "SOURCE_REPLACEMENT_UNAVAILABLE",
                                    "This exploration already has renditions. Start a new remix to"
                                            + " use another source.",
                                    "NEW_REMIX");
                        }
                        long revision = ((Number) row.get("source_revision")).longValue();
                        if (revision != expectedRevision) {
                            throw new ApiProblem(
                                    409,
                                    "SOURCE_CHANGED",
                                    "The source has changed. Review the current recording before"
                                            + " replacing it.",
                                    "REFRESH_EXPLORATION");
                        }
                        long draftRevision = ((Number) row.get("draft_revision")).longValue() + 1;
                        String rec = "rec_" + UUID.randomUUID();
                        Instant now = Instant.now();
                        String title = upload.fileName().replaceFirst("(?i)\\.(wav|mp3)$", "");
                        if (title.isBlank()) title = "Recording";
                        String storage = publish(upload);
                        insertOriginal(rec, id, title, upload, storage, now);
                        // Deliberately do not write draft_text: the latest saved words survive
                        // replacement.
                        jdbc.sql(
                                        "UPDATE exploration SET"
                                            + " title=?,original_id=?,source_revision=source_revision+1,draft_basis_id=?,draft_revision=draft_revision+1,updated_at=?"
                                            + " WHERE id=?")
                                .params(title, rec, rec, java.sql.Timestamp.from(now), id)
                                .update();
                        jdbc.sql(
                                        "INSERT INTO"
                                            + " command_receipt(command_key,operation,fingerprint,exploration_id,recording_id,source_revision,draft_revision,created_at)"
                                            + " VALUES(?,?,?,?,?,?,?,?)")
                                .params(
                                        key,
                                        operation,
                                        fingerprint,
                                        id,
                                        rec,
                                        revision + 1,
                                        draftRevision,
                                        java.sql.Timestamp.from(now))
                                .update();
                        return new Receipt(
                                id,
                                rec,
                                revision + 1,
                                draftRevision,
                                "/api/explorations/" + id,
                                false);
                    });
        }
    }

    private Receipt replay(String key, String operation, String fingerprint) {
        var previous =
                jdbc
                        .sql("SELECT * FROM command_receipt WHERE command_key=?")
                        .param(key)
                        .query()
                        .listOfRows()
                        .stream()
                        .findFirst();
        if (previous.isEmpty()) return null;
        var row = previous.get();
        if (!operation.equals(row.get("operation"))
                || !fingerprint.equals(row.get("fingerprint"))) {
            throw new ApiProblem(
                    409,
                    "IDEMPOTENCY_CONFLICT",
                    "This key already belongs to a different command.",
                    "CHOOSE_FILE");
        }
        return new Receipt(
                (String) row.get("exploration_id"),
                (String) row.get("recording_id"),
                ((Number) row.get("source_revision")).longValue(),
                ((Number) row.get("draft_revision")).longValue(),
                "/api/explorations/" + row.get("exploration_id"),
                true);
    }

    private String publish(MediaStore.Upload upload) {
        final String storage;
        try {
            storage = media.publish(upload);
        } catch (IOException error) {
            throw new ApiProblem(
                    500,
                    "MEDIA_STORAGE_FAILED",
                    "The recording could not be saved. Your direction is unchanged.",
                    "RETRY_UPLOAD");
        }
        org.springframework.transaction.support.TransactionSynchronizationManager
                .registerSynchronization(
                        new org.springframework.transaction.support.TransactionSynchronization() {
                            public void afterCompletion(int completion) {
                                if (completion == STATUS_ROLLED_BACK) {
                                    try {
                                        java.nio.file.Files.deleteIfExists(media.path(storage));
                                    } catch (IOException ignored) {
                                        /* Unreferenced files can be reclaimed during startup reconciliation. */
                                    }
                                }
                            }
                        });
        return storage;
    }

    private void insertOriginal(
            String rec,
            String exp,
            String title,
            MediaStore.Upload upload,
            String storage,
            Instant now) {
        jdbc.sql(
                        "INSERT INTO"
                            + " recording(id,exploration_id,title,kind,file_name,storage_key,content_type,byte_size,duration_ms,sample_rate,channels,codec,sha256,provenance,created_at)"
                            + " VALUES(?,?,?,'ORIGINAL',?,?,?,?,?,?,?,?,?,'ORIGINAL',?)")
                .params(
                        rec,
                        exp,
                        title,
                        upload.fileName(),
                        storage,
                        upload.contentType(),
                        upload.bytes(),
                        upload.durationMs(),
                        upload.sampleRate(),
                        upload.channels(),
                        upload.codec(),
                        upload.hash(),
                        java.sql.Timestamp.from(now))
                .update();
    }

    private static void validateKey(String key) {
        if (key == null || !key.matches("[A-Za-z0-9._:-]{8,128}")) {
            throw new ApiProblem(
                    400,
                    "INVALID_IDEMPOTENCY_KEY",
                    "Provide a unique Idempotency-Key of 8–128 letters, digits or . _ : -.",
                    "RETRY_UPLOAD");
        }
    }

    private static void validateText(String text) {
        if (text.codePointCount(0, text.length()) > 4000) {
            throw new ApiProblem(
                    422,
                    "TEXT_TOO_LONG",
                    "Direction must be at most 4,000 characters.",
                    "EDIT_TEXT");
        }
    }

    public Exploration get(String id) {
        Exploration core =
                jdbc.sql(
                                """
                                SELECT e.id AS exploration_key, e.title AS exploration_title,
                                       e.source_revision, e.draft_text, e.draft_basis_id, e.draft_revision,
                                       e.created_at AS exploration_created_at, e.updated_at,
                                       ((SELECT active_attempt_id IS NULL FROM workspace WHERE id=1)
                                        AND NOT EXISTS(SELECT 1 FROM recording rendition
                                                       WHERE rendition.exploration_id=e.id AND rendition.kind='RENDITION'))
                                        AS can_replace_source, original.*, NULL::bigint AS local_note_revision, NULL AS local_recipe_json
                                FROM exploration e JOIN recording original ON original.id=e.original_id
                                WHERE e.id=?
                                """)
                        .param(id)
                        .query(
                                (rs, n) ->
                                        new Exploration(
                                                rs.getString("exploration_key"),
                                                rs.getString("exploration_title"),
                                                rs.getLong("source_revision"),
                                                rs.getBoolean("can_replace_source"),
                                                mapRecording(rs, n),
                                                List.of(),
                                                new Draft(
                                                        rs.getString("draft_text"),
                                                        rs.getString("draft_basis_id"),
                                                        rs.getLong("draft_revision")),
                                                null,
                                                rs.getTimestamp("exploration_created_at")
                                                        .toInstant(),
                                                rs.getTimestamp("updated_at").toInstant()))
                        .optional()
                        .orElseThrow(Explorations::missing);
        var renditions =
                jdbc.sql(
                                RECORDING_SELECT
                                        + "WHERE r.exploration_id=? AND r.kind='RENDITION'"
                                        + " ORDER BY r.created_at,r.id")
                        .param(id)
                        .query(Explorations::mapRecording)
                        .list();
        return new Exploration(
                core.id(),
                core.title(),
                core.sourceRevision(),
                core.canReplaceSource(),
                core.original(),
                renditions,
                core.draft(),
                generations.latest(id),
                core.createdAt(),
                core.updatedAt());
    }

    public List<Summary> list() {
        var summaries =
                jdbc.sql(
                                """
                                SELECT e.id AS exploration_key, e.title AS exploration_title, e.updated_at, original.*, NULL::bigint AS local_note_revision, NULL AS local_recipe_json,
                                       (SELECT COUNT(*) FROM recording result WHERE result.exploration_id=e.id AND result.kind='RENDITION') AS rendition_count
                                FROM exploration e JOIN recording original ON original.id=e.original_id
                                ORDER BY e.updated_at DESC,e.id
                                """)
                        .query(
                                (rs, n) ->
                                        new Summary(
                                                rs.getString("exploration_key"),
                                                rs.getString("exploration_title"),
                                                mapRecording(rs, n),
                                                rs.getInt("rendition_count"),
                                                List.of(),
                                                rs.getTimestamp("updated_at").toInstant()))
                        .list();
        // Complete both reads before mapping; no extra connection is acquired inside a RowMapper.
        var kept =
                jdbc
                        .sql(
                                RECORDING_SELECT
                                        + "WHERE r.kind='RENDITION' AND r.kept=true"
                                        + " ORDER BY r.created_at,r.id")
                        .query(Explorations::mapRecording)
                        .list()
                        .stream()
                        .collect(java.util.stream.Collectors.groupingBy(Recording::explorationId));
        return summaries.stream()
                .map(
                        summary ->
                                new Summary(
                                        summary.id(),
                                        summary.title(),
                                        summary.original(),
                                        summary.renditionCount(),
                                        kept.getOrDefault(summary.id(), List.of()),
                                        summary.updatedAt()))
                .toList();
    }

    public Recording setKept(String id, RecordingUpdate command) {
        if (command == null || command.kept() == null) {
            throw new ApiProblem(
                    400,
                    "INVALID_REQUEST",
                    "Provide an explicit kept value of true or false.",
                    "RETRY");
        }
        var changed =
                jdbc.sql("UPDATE recording SET kept=? WHERE id=? AND kind='RENDITION' RETURNING id")
                        .params(command.kept(), id)
                        .query(String.class)
                        .optional();
        if (changed.isPresent()) return recording(changed.get());
        recording(id); // Distinguish a missing recording from an original.
        throw new ApiProblem(
                422,
                "ORIGINAL_NOT_KEEPABLE",
                "The original is always retained. Keep is available for renditions.",
                "OPEN_RENDITION");
    }

    public Recording recording(String id) {
        return jdbc.sql(RECORDING_SELECT + "WHERE r.id=?")
                .param(id)
                .query(Explorations::mapRecording)
                .optional()
                .orElseThrow(() -> missing());
    }

    public String storageKey(String id) {
        return jdbc.sql("SELECT storage_key FROM recording WHERE id=?")
                .param(id)
                .query(String.class)
                .optional()
                .orElseThrow(() -> missing());
    }

    public boolean canCreate() {
        return jdbc.sql("SELECT active_attempt_id IS NULL FROM workspace WHERE id=1")
                .query(Boolean.class)
                .single();
    }

    private static ApiProblem missing() {
        return new ApiProblem(
                404,
                "NOT_FOUND",
                "This recording or exploration could not be found.",
                "RETURN_TO_LIBRARY");
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> localRecipe(String serialized) {
        return serialized == null ? null : JSON.readValue(serialized, Map.class);
    }

    private static Recording mapRecording(ResultSet r, int n) throws SQLException {
        return new Recording(
                r.getString("id"),
                r.getString("exploration_id"),
                r.getString("kind"),
                r.getString("title"),
                r.getString("file_name"),
                r.getString("content_type"),
                r.getLong("byte_size"),
                r.getInt("duration_ms"),
                r.getInt("sample_rate"),
                r.getInt("channels"),
                r.getString("codec"),
                r.getString("sha256"),
                "/api/media/" + r.getString("id"),
                r.getString("provenance"),
                r.getBoolean("kept"),
                r.getString("direction"),
                r.getString("request_id"),
                r.getString("attempt_id"),
                r.getTimestamp("created_at").toInstant(),
                localRecipe(r.getString("local_recipe_json")),
                r.getObject("local_note_revision", Long.class));
    }
}
