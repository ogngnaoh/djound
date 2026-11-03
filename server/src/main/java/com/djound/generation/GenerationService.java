package com.djound.generation;

import static com.djound.Models.*;

import com.djound.*;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.*;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.sql.*;
import java.time.Instant;
import java.util.*;

@Service
public class GenerationService {
    private final JdbcClient jdbc;
    private final TransactionTemplate transactions;
    private final GenerationConfiguration configuration;
    private final MediaStore media;
    private final FixtureReviewControls controls;
    private final PromptAssembler assembler = new PromptAssembler();

    public GenerationService(
            JdbcClient jdbc,
            PlatformTransactionManager manager,
            GenerationConfiguration configuration,
            MediaStore media,
            FixtureReviewControls controls) {
        this.jdbc = jdbc;
        this.transactions = new TransactionTemplate(manager);
        this.configuration = configuration;
        this.media = media;
        this.controls = controls;
    }

    public GenerationConfiguration configuration() {
        return configuration;
    }

    public String activeAttemptId() {
        return (String)
                jdbc.sql("SELECT active_attempt_id FROM workspace WHERE id=1")
                        .query()
                        .singleRow()
                        .get("active_attempt_id");
    }

    public CreationReceipt create(String explorationId, CreateRequestCommand command, String key) {
        if (key == null || !key.matches("[A-Za-z0-9._:-]{8,128}"))
            throw new ApiProblem(
                    400,
                    "INVALID_IDEMPOTENCY_KEY",
                    "Provide a unique Idempotency-Key.",
                    "EDIT_TEXT");
        if (command == null
                || command.text() == null
                || command.expectedSourceRevision() == null
                || command.expectedDraftRevision() == null
                || command.expectedSourceRevision() < 1
                || command.expectedDraftRevision() < 1)
            throw new ApiProblem(
                    400,
                    "INVALID_REQUEST",
                    "Provide the direction and current source and draft revisions.",
                    "EDIT_TEXT");
        String operation = "CREATE_REQUEST:" + explorationId;
        // Null basis is encoded distinctly from an explicitly selected original; replay compares
        // exact command semantics.
        String payload =
                operation
                        + "|"
                        + field(command.text())
                        + field(command.basisRecordingId())
                        + command.expectedSourceRevision()
                        + "|"
                        + command.expectedDraftRevision();
        String fingerprint;
        try {
            fingerprint =
                    HexFormat.of()
                            .formatHex(
                                    java.security.MessageDigest.getInstance("SHA-256")
                                            .digest(payload.getBytes(StandardCharsets.UTF_8)));
        } catch (java.security.NoSuchAlgorithmException impossible) {
            throw new IllegalStateException(impossible);
        }
        return transactions.execute(
                status -> {
                    var workspace =
                            jdbc.sql(
                                            "SELECT active_attempt_id FROM workspace WHERE id=1 FOR"
                                                    + " UPDATE")
                                    .query()
                                    .singleRow();
                    var old =
                            jdbc
                                    .sql("SELECT * FROM command_receipt WHERE command_key=?")
                                    .param(key)
                                    .query()
                                    .listOfRows()
                                    .stream()
                                    .findFirst();
                    if (old.isPresent()) {
                        var receipt = old.get();
                        if (!operation.equals(receipt.get("operation"))
                                || !fingerprint.equals(receipt.get("fingerprint")))
                            throw new ApiProblem(
                                    409,
                                    "IDEMPOTENCY_CONFLICT",
                                    "This key already belongs to a different command.",
                                    "EDIT_TEXT");
                        return receipt(
                                (String) receipt.get("request_id"),
                                (String) receipt.get("attempt_id"),
                                ((Number) receipt.get("consumed_draft_revision")).longValue(),
                                ((Number) receipt.get("draft_revision")).longValue(),
                                true);
                    }
                    if (!configuration.fixture())
                        throw new ApiProblem(
                                503,
                                "PROVIDER_NOT_CONFIGURED",
                                "Music creation is not configured. Your source and direction remain"
                                        + " available.",
                                "EDIT_TEXT");
                    if (workspace.get("active_attempt_id") != null)
                        throw new ApiProblem(
                                409,
                                "CREATION_ACTIVE",
                                "Another creation is already in progress.",
                                "WAIT");
                    var exp =
                            jdbc
                                    .sql("SELECT * FROM exploration WHERE id=? FOR UPDATE")
                                    .param(explorationId)
                                    .query()
                                    .listOfRows()
                                    .stream()
                                    .findFirst()
                                    .orElseThrow(GenerationService::missing);
                    long source = ((Number) exp.get("source_revision")).longValue(),
                            draft = ((Number) exp.get("draft_revision")).longValue();
                    if (source != command.expectedSourceRevision())
                        throw new ApiProblem(
                                409,
                                "SOURCE_CHANGED",
                                "The original source has changed. Review it before creating a"
                                        + " version.",
                                "REFRESH_EXPLORATION");
                    if (draft != command.expectedDraftRevision())
                        throw new ApiProblem(
                                409,
                                "STALE_DRAFT",
                                "The saved direction changed elsewhere. Keep your words and review"
                                        + " the current draft.",
                                "REFRESH_DRAFT");
                    String original = (String) exp.get("original_id"),
                            basis =
                                    command.basisRecordingId() == null
                                            ? original
                                            : command.basisRecordingId();
                    var basisRow =
                            jdbc
                                    .sql(
                                            "SELECT * FROM recording WHERE id=? AND"
                                                    + " exploration_id=? AND (id=? OR"
                                                    + " kind='RENDITION')")
                                    .params(basis, explorationId, original)
                                    .query()
                                    .listOfRows()
                                    .stream()
                                    .findFirst()
                                    .orElseThrow(
                                            () ->
                                                    new ApiProblem(
                                                            409,
                                                            "SOURCE_CHANGED",
                                                            "The selected direction basis is"
                                                                    + " unavailable or belongs to"
                                                                    + " another exploration.",
                                                            "REFRESH_EXPLORATION"));
                    String inherited =
                            Optional.ofNullable((String) basisRow.get("direction")).orElse("");
                    var assembled = assembler.assemble(inherited, command.text());
                    String request = "req_" + UUID.randomUUID(),
                            attempt = "attempt_" + UUID.randomUUID();
                    Timestamp now = Timestamp.from(Instant.now());
                    jdbc.sql(
                                    """
                                    INSERT INTO generation_request(id,exploration_id,source_recording_id,source_revision,basis_recording_id,submitted_text,inherited_direction,direction,assembled_instructions,assembler_version,adapter_configuration_id,provenance,created_at)
                                    VALUES(?,?,?,?,?,?,?,?,?,?,?,'FIXTURE',?)
                                    """)
                            .params(
                                    request,
                                    explorationId,
                                    original,
                                    source,
                                    basis,
                                    command.text(),
                                    inherited,
                                    assembled.direction(),
                                    assembled.instructions(),
                                    PromptAssembler.VERSION,
                                    configuration.adapterId(),
                                    now)
                            .update();
                    jdbc.sql(
                                    "INSERT INTO"
                                        + " generation_attempt(id,request_id,exploration_id,state,created_at,updated_at,fixture_outcome)"
                                        + " VALUES(?,?,?,'ACCEPTED',?,?,?)")
                            .params(
                                    attempt,
                                    request,
                                    explorationId,
                                    now,
                                    now,
                                    controls.consumeOutcome())
                            .update();
                    jdbc.sql(
                                    "INSERT INTO"
                                        + " command_receipt(command_key,operation,fingerprint,exploration_id,recording_id,source_revision,draft_revision,created_at,request_id,attempt_id,consumed_draft_revision)"
                                        + " VALUES(?,?,?,?,?,?,?,?,?,?,?)")
                            .params(
                                    key,
                                    operation,
                                    fingerprint,
                                    explorationId,
                                    original,
                                    source,
                                    draft + 1,
                                    now,
                                    request,
                                    attempt,
                                    draft)
                            .update();
                    // An update count other than one must roll back the request, attempt AND
                    // receipt.
                    int consumed =
                            jdbc.sql(
                                            "UPDATE exploration SET"
                                                + " draft_text='',draft_basis_id=?,draft_revision=draft_revision+1,updated_at=?,latest_attempt_id=?"
                                                + " WHERE id=? AND source_revision=? AND"
                                                + " draft_revision=?")
                                    .params(basis, now, attempt, explorationId, source, draft)
                                    .update();
                    if (consumed != 1)
                        throw new ApiProblem(
                                409,
                                "STALE_DRAFT",
                                "The draft could not be consumed. Your words remain available.",
                                "REFRESH_DRAFT");
                    jdbc.sql("UPDATE workspace SET active_attempt_id=? WHERE id=1")
                            .param(attempt)
                            .update();
                    return receipt(request, attempt, draft, draft + 1, false);
                });
    }

    public RetryReceipt retry(String requestId, RetryCommand command, String key) {
        if (key == null || !key.matches("[A-Za-z0-9._:-]{8,128}"))
            throw new ApiProblem(
                    400, "INVALID_IDEMPOTENCY_KEY", "Provide a unique Idempotency-Key.", "RETRY");
        if (command == null
                || command.expectedPreviousAttemptId() == null
                || command.expectedPreviousAttemptId().isBlank())
            throw new ApiProblem(
                    400, "INVALID_REQUEST", "Provide the previous attempt being retried.", "RETRY");
        String operation = "RETRY_REQUEST:" + requestId;
        String fingerprint;
        try {
            fingerprint =
                    HexFormat.of()
                            .formatHex(
                                    java.security.MessageDigest.getInstance("SHA-256")
                                            .digest(
                                                    (operation
                                                                    + "|"
                                                                    + field(
                                                                            command
                                                                                    .expectedPreviousAttemptId()))
                                                            .getBytes(StandardCharsets.UTF_8)));
        } catch (java.security.NoSuchAlgorithmException impossible) {
            throw new IllegalStateException(impossible);
        }
        return transactions.execute(
                status -> {
                    var workspace =
                            jdbc.sql(
                                            "SELECT active_attempt_id FROM workspace WHERE id=1 FOR"
                                                    + " UPDATE")
                                    .query()
                                    .singleRow();
                    var receipt =
                            jdbc
                                    .sql("SELECT * FROM command_receipt WHERE command_key=?")
                                    .param(key)
                                    .query()
                                    .listOfRows()
                                    .stream()
                                    .findFirst();
                    if (receipt.isPresent()) {
                        var row = receipt.get();
                        if (!operation.equals(row.get("operation"))
                                || !fingerprint.equals(row.get("fingerprint")))
                            throw new ApiProblem(
                                    409,
                                    "IDEMPOTENCY_CONFLICT",
                                    "This key already belongs to a different command.",
                                    "RETRY");
                        return retryReceipt(requestId, (String) row.get("attempt_id"), true);
                    }
                    if (!configuration.fixture())
                        throw new ApiProblem(
                                503,
                                "PROVIDER_NOT_CONFIGURED",
                                "Music creation is not configured. The fixed request remains"
                                        + " saved.",
                                "EDIT_TEXT");
                    var requestRow =
                            jdbc.sql("SELECT exploration_id FROM generation_request WHERE id=?")
                                    .param(requestId)
                                    .query(String.class)
                                    .optional()
                                    .orElseThrow(GenerationService::missing);
                    var exploration =
                            jdbc.sql("SELECT * FROM exploration WHERE id=? FOR UPDATE")
                                    .param(requestRow)
                                    .query()
                                    .singleRow();
                    String previousId = command.expectedPreviousAttemptId();
                    if (!previousId.equals(exploration.get("latest_attempt_id")))
                        throw new ApiProblem(
                                409,
                                "STALE_ATTEMPT",
                                "A newer creation has replaced this Retry action.",
                                "REFRESH_EXPLORATION");
                    Attempt previous = get(previousId);
                    if (!previous.requestId().equals(requestId)
                            || !previous.state().equals("FAILED")
                            || previous.outcome() == null
                            || !Set.of(
                                            "GENERATION_FAILED",
                                            "OUTPUT_INVALID",
                                            "MEDIA_STORAGE_FAILED",
                                            "PROCESS_INTERRUPTED")
                                    .contains(previous.outcome().code())) {
                        throw new ApiProblem(
                                409,
                                "STALE_ATTEMPT",
                                "This attempt is not available for Retry.",
                                "REFRESH_EXPLORATION");
                    }
                    var frozen = previous.request();
                    if (!frozen.sourceRecordingId().equals(exploration.get("original_id"))
                            || frozen.sourceRevision()
                                    != ((Number) exploration.get("source_revision")).longValue())
                        throw new ApiProblem(
                                409,
                                "SOURCE_CHANGED",
                                "The source has changed since this request. Create a new version"
                                        + " from the current source.",
                                "EDIT_TEXT");
                    if (!frozen.adapterConfigurationId().equals(configuration.adapterId())
                            || !frozen.provenance().equals("FIXTURE"))
                        throw new ApiProblem(
                                409,
                                "RETRY_CONTEXT_UNAVAILABLE",
                                "The saved request uses an incompatible creation configuration.",
                                "EDIT_TEXT");
                    if (workspace.get("active_attempt_id") != null)
                        throw new ApiProblem(
                                409,
                                "CREATION_ACTIVE",
                                "Another creation is already in progress.",
                                "WAIT");
                    String attempt = "attempt_" + UUID.randomUUID();
                    Timestamp now = Timestamp.from(Instant.now());
                    jdbc.sql(
                                    "INSERT INTO"
                                        + " generation_attempt(id,request_id,exploration_id,state,created_at,updated_at,previous_attempt_id,fixture_outcome)"
                                        + " VALUES(?,?,?,'ACCEPTED',?,?,?,?)")
                            .params(
                                    attempt,
                                    requestId,
                                    requestRow,
                                    now,
                                    now,
                                    previousId,
                                    controls.consumeOutcome())
                            .update();
                    // Retry records authority without consuming, clearing, rebasing, or advancing
                    // the current draft.
                    jdbc.sql("UPDATE exploration SET latest_attempt_id=?,updated_at=? WHERE id=?")
                            .params(attempt, now, requestRow)
                            .update();
                    jdbc.sql("UPDATE workspace SET active_attempt_id=? WHERE id=1")
                            .param(attempt)
                            .update();
                    jdbc.sql(
                                    "INSERT INTO"
                                        + " command_receipt(command_key,operation,fingerprint,exploration_id,recording_id,source_revision,draft_revision,created_at,request_id,attempt_id)"
                                        + " VALUES(?,?,?,?,?,?,?,?,?,?)")
                            .params(
                                    key,
                                    operation,
                                    fingerprint,
                                    requestRow,
                                    frozen.sourceRecordingId(),
                                    frozen.sourceRevision(),
                                    ((Number) exploration.get("draft_revision")).longValue(),
                                    now,
                                    requestId,
                                    attempt)
                            .update();
                    return retryReceipt(requestId, attempt, false);
                });
    }

    private static RetryReceipt retryReceipt(String requestId, String attemptId, boolean replayed) {
        return new RetryReceipt(requestId, attemptId, "/api/attempts/" + attemptId, replayed);
    }

    public String fixtureOutcome(String attemptId) {
        return jdbc.sql("SELECT fixture_outcome FROM generation_attempt WHERE id=?")
                .param(attemptId)
                .query(String.class)
                .single();
    }

    private static String field(String value) {
        return value == null ? "-1:" : value.length() + ":" + value;
    }

    private static CreationReceipt receipt(
            String req, String attempt, long consumed, long next, boolean replayed) {
        return new CreationReceipt(
                req, attempt, "/api/attempts/" + attempt, consumed, next, replayed);
    }

    public Attempt latest(String explorationId) {
        var id =
                jdbc.sql("SELECT latest_attempt_id FROM exploration WHERE id=?")
                        .param(explorationId)
                        .query(String.class)
                        .optional();
        return id.map(this::get).orElse(null);
    }

    public Attempt get(String id) {
        return jdbc.sql(
                        """
                        SELECT a.id AS attempt_key,a.state,a.version,a.outcome_code,a.outcome_detail,a.result_recording_id,a.previous_attempt_id,
                               a.created_at AS attempt_created_at,a.updated_at,r.*,
                               (? AND a.state='FAILED' AND a.outcome_code IN ('GENERATION_FAILED','OUTPUT_INVALID','MEDIA_STORAGE_FAILED','PROCESS_INTERRUPTED')
                                AND e.latest_attempt_id=a.id AND e.original_id=r.source_recording_id AND e.source_revision=r.source_revision
                                AND r.adapter_configuration_id=? AND r.provenance='FIXTURE'
                                AND w.active_attempt_id IS NULL) AS can_retry
                        FROM generation_attempt a JOIN generation_request r ON r.id=a.request_id
                        JOIN exploration e ON e.id=a.exploration_id CROSS JOIN workspace w WHERE w.id=1 AND a.id=?
                        """)
                .params(configuration.fixture(), configuration.adapterId(), id)
                .query(
                        (r, n) -> {
                            var request =
                                    new GenerationRequest(
                                            r.getString("id"),
                                            r.getString("exploration_id"),
                                            r.getString("source_recording_id"),
                                            r.getLong("source_revision"),
                                            r.getString("basis_recording_id"),
                                            r.getString("submitted_text"),
                                            r.getString("inherited_direction"),
                                            r.getString("direction"),
                                            r.getString("assembled_instructions"),
                                            r.getString("assembler_version"),
                                            r.getString("adapter_configuration_id"),
                                            r.getString("provenance"),
                                            r.getTimestamp("created_at").toInstant());
                            String state = r.getString("state"), code = r.getString("outcome_code");
                            return new Attempt(
                                    r.getString("attempt_key"),
                                    request.explorationId(),
                                    request.id(),
                                    state,
                                    r.getLong("version"),
                                    request,
                                    code == null
                                            ? null
                                            : new AttemptOutcome(
                                                    code, r.getString("outcome_detail")),
                                    r.getString("result_recording_id"),
                                    r.getString("previous_attempt_id"),
                                    r.getBoolean("can_retry"),
                                    state.equals("SUCCEEDED")
                                            ? List.of("OPEN_RESULT")
                                            : state.equals("FAILED")
                                                    ? r.getBoolean("can_retry")
                                                            ? List.of("RETRY", "EDIT_TEXT")
                                                            : List.of("EDIT_TEXT")
                                                    : List.of("WAIT"),
                                    r.getTimestamp("attempt_created_at").toInstant(),
                                    r.getTimestamp("updated_at").toInstant());
                        })
                .optional()
                .orElseThrow(GenerationService::missing);
    }

    /**
     * Called once before the scheduler is enabled. No fixture output is automatically resubmitted.
     */
    public void recoverInterrupted() {
        transactions.executeWithoutResult(
                status -> {
                    jdbc.sql("SELECT id FROM workspace WHERE id=1 FOR UPDATE")
                            .query(Integer.class)
                            .single();
                    jdbc.sql(
                                    "UPDATE generation_attempt SET"
                                        + " state='FAILED',version=version+1,outcome_code='PROCESS_INTERRUPTED',outcome_detail='Creation"
                                        + " was interrupted. Your source and direction are still"
                                        + " saved.',updated_at=? WHERE state IN"
                                        + " ('SUBMITTING','RUNNING','SAVING','RECONCILING') OR"
                                        + " (state='ACCEPTED' AND ?=false)")
                            .params(Timestamp.from(Instant.now()), configuration.fixture())
                            .update();
                    jdbc.sql(
                                    "UPDATE workspace SET active_attempt_id=NULL WHERE"
                                        + " active_attempt_id IN (SELECT id FROM generation_attempt"
                                        + " WHERE state IN ('FAILED','SUCCEEDED'))")
                            .update();
                });
    }

    public Attempt claimNext() {
        if (!configuration.fixture()) return null;
        return transactions.execute(
                status -> {
                    var workspace =
                            jdbc.sql(
                                            "SELECT active_attempt_id FROM workspace WHERE id=1 FOR"
                                                    + " UPDATE")
                                    .query()
                                    .singleRow();
                    String id = (String) workspace.get("active_attempt_id");
                    if (id == null) return null;
                    Attempt current = get(id);
                    if (!current.state().equals("ACCEPTED")) return null;
                    int changed =
                            jdbc.sql(
                                            "UPDATE generation_attempt SET"
                                                + " state='SUBMITTING',version=version+1,execution_count=execution_count+1,updated_at=?"
                                                + " WHERE id=? AND state='ACCEPTED' AND version=?")
                                    .params(Timestamp.from(Instant.now()), id, current.version())
                                    .update();
                    return changed == 1 ? get(id) : null;
                });
    }

    public Attempt transition(Attempt current, String next) {
        int changed =
                jdbc.sql(
                                "UPDATE generation_attempt SET"
                                        + " state=?,version=version+1,updated_at=? WHERE id=? AND"
                                        + " state=? AND version=?")
                        .params(
                                next,
                                Timestamp.from(Instant.now()),
                                current.id(),
                                current.state(),
                                current.version())
                        .update();
        if (changed != 1) throw new IllegalStateException("Attempt phase changed concurrently");
        return get(current.id());
    }

    public long outputOrdinal(Attempt attempt) {
        return jdbc.sql(
                        "SELECT COUNT(*) FROM recording WHERE exploration_id=? AND"
                                + " kind='RENDITION'")
                .param(attempt.explorationId())
                .query(Long.class)
                .single();
    }

    public void complete(Attempt saving, MediaStore.Upload upload) throws IOException {
        final String storage = media.publish(upload);
        transactions.executeWithoutResult(
                status -> {
                    TransactionSynchronizationManager.registerSynchronization(
                            new TransactionSynchronization() {
                                public void afterCompletion(int completion) {
                                    // Preserve the file if commit outcome is unknown; only
                                    // confirmed rollback permits cleanup.
                                    if (completion == STATUS_ROLLED_BACK) {
                                        try {
                                            Files.deleteIfExists(media.path(storage));
                                        } catch (IOException ignored) {
                                            /* Conservative orphan reconciliation can reclaim it later. */
                                        }
                                    }
                                }
                            });
                    jdbc.sql("SELECT id FROM workspace WHERE id=1 FOR UPDATE")
                            .query(Integer.class)
                            .single();
                    var current = get(saving.id());
                    if (!current.state().equals("SAVING") || current.version() != saving.version())
                        throw new IllegalStateException("Attempt no longer owns publication");
                    String recording = "rec_" + UUID.randomUUID();
                    Timestamp now = Timestamp.from(Instant.now());
                    String title = "Sample version " + (outputOrdinal(saving) + 1);
                    jdbc.sql(
                                    """
                                    INSERT INTO recording(id,exploration_id,title,kind,file_name,storage_key,content_type,byte_size,duration_ms,sample_rate,channels,codec,sha256,provenance,created_at,direction,request_id,attempt_id)
                                    VALUES(?,?,?,'RENDITION',?,?,?,?,?,?,?,?,?,'FIXTURE',?,?,?,?)
                                    """)
                            .params(
                                    recording,
                                    saving.explorationId(),
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
                                    now,
                                    saving.request().direction(),
                                    saving.requestId(),
                                    saving.id())
                            .update();
                    int changed =
                            jdbc.sql(
                                            "UPDATE generation_attempt SET"
                                                + " state='SUCCEEDED',version=version+1,result_recording_id=?,updated_at=?"
                                                + " WHERE id=? AND state='SAVING' AND version=?")
                                    .params(recording, now, saving.id(), saving.version())
                                    .update();
                    if (changed != 1)
                        throw new IllegalStateException("Attempt no longer owns result");
                    jdbc.sql("UPDATE exploration SET updated_at=? WHERE id=?")
                            .params(now, saving.explorationId())
                            .update();
                    jdbc.sql(
                                    "UPDATE workspace SET active_attempt_id=NULL WHERE id=1 AND"
                                            + " active_attempt_id=?")
                            .param(saving.id())
                            .update();
                });
    }

    public void fail(String id, String code, String detail) {
        transactions.executeWithoutResult(
                status -> {
                    jdbc.sql("SELECT id FROM workspace WHERE id=1 FOR UPDATE")
                            .query(Integer.class)
                            .single();
                    var attempt = get(id);
                    if (attempt.state().equals("SUCCEEDED") || attempt.state().equals("FAILED"))
                        return;
                    int changed =
                            jdbc.sql(
                                            "UPDATE generation_attempt SET"
                                                + " state='FAILED',version=version+1,outcome_code=?,outcome_detail=?,updated_at=?"
                                                + " WHERE id=? AND version=?")
                                    .params(
                                            code,
                                            detail,
                                            Timestamp.from(Instant.now()),
                                            id,
                                            attempt.version())
                                    .update();
                    if (changed != 1)
                        throw new IllegalStateException(
                                "Attempt changed before failure could be committed");
                    jdbc.sql(
                                    "UPDATE workspace SET active_attempt_id=NULL WHERE id=1 AND"
                                            + " active_attempt_id=?")
                            .param(id)
                            .update();
                });
    }

    private static ApiProblem missing() {
        return new ApiProblem(
                404, "NOT_FOUND", "This creation could not be found.", "RETURN_TO_LIBRARY");
    }
}
