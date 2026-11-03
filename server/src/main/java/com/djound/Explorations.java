package com.djound;

import static com.djound.Models.*;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.util.*;

@Service
public class Explorations {
    private final JdbcClient jdbc;
    private final MediaStore media;
    private final TransactionTemplate transactions;

    public Explorations(JdbcClient jdbc, MediaStore media, PlatformTransactionManager manager) {
        this.jdbc = jdbc;
        this.media = media;
        this.transactions = new TransactionTemplate(manager);
    }

    public Receipt create(MultipartFile file, String text, String key) throws IOException {
        if (key == null || !key.matches("[A-Za-z0-9._:-]{8,128}"))
            throw new ApiProblem(
                    400,
                    "INVALID_IDEMPOTENCY_KEY",
                    "Provide a unique Idempotency-Key of 8–128 letters, digits or . _ : -.",
                    "RETRY_UPLOAD");
        if (text.codePointCount(0, text.length()) > 4000)
            throw new ApiProblem(
                    422,
                    "TEXT_TOO_LONG",
                    "Direction must be at most 4,000 characters.",
                    "EDIT_TEXT");
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
                        String storage;
                        try {
                            storage = media.publish(upload);
                        } catch (IOException e) {
                            throw new ApiProblem(
                                    500,
                                    "MEDIA_STORAGE_FAILED",
                                    "The recording could not be saved. Your direction is"
                                        + " unchanged.",
                                    "RETRY_UPLOAD");
                        }
                        final String publishedKey = storage;
                        org.springframework.transaction.support.TransactionSynchronizationManager
                                .registerSynchronization(
                                        new org.springframework.transaction.support
                                                .TransactionSynchronization() {
                                            public void afterCompletion(int completion) {
                                                if (completion == STATUS_ROLLED_BACK)
                                                    try {
                                                        java.nio.file.Files.deleteIfExists(
                                                                media.path(publishedKey));
                                                    } catch (IOException ignored) {
                                                        /* Startup orphan reconciliation can reclaim this unreferenced file. */
                                                    }
                                            }
                                        });
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

    public Exploration get(String id) {
        return jdbc.sql("SELECT * FROM exploration WHERE id=?")
                .param(id)
                .query(
                        (rs, n) ->
                                new Exploration(
                                        rs.getString("id"),
                                        rs.getString("title"),
                                        rs.getLong("source_revision"),
                                        recording(rs.getString("original_id")),
                                        List.of(),
                                        new Draft(
                                                rs.getString("draft_text"),
                                                rs.getString("draft_basis_id"),
                                                rs.getLong("draft_revision")),
                                        rs.getTimestamp("created_at").toInstant(),
                                        rs.getTimestamp("updated_at").toInstant()))
                .optional()
                .orElseThrow(() -> missing());
    }

    public List<Summary> list() {
        return jdbc.sql("SELECT * FROM exploration ORDER BY updated_at DESC,id")
                .query(
                        (rs, n) ->
                                new Summary(
                                        rs.getString("id"),
                                        rs.getString("title"),
                                        recording(rs.getString("original_id")),
                                        0,
                                        List.of(),
                                        rs.getTimestamp("updated_at").toInstant()))
                .list();
    }

    public Recording recording(String id) {
        return jdbc.sql("SELECT * FROM recording WHERE id=?")
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
                r.getTimestamp("created_at").toInstant());
    }
}
