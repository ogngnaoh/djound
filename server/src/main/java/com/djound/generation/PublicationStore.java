package com.djound.generation;

import com.djound.*;
import com.djound.Models.Attempt;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

import java.nio.file.*;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.*;

@Component
public class PublicationStore {
    private final JdbcClient jdbc;
    private final TransactionTemplate transactions;
    private final MediaStore media;
    private final FixtureResultRetriever retriever;
    private final FixtureCheckpoints checkpoints;

    public PublicationStore(
            JdbcClient jdbc,
            PlatformTransactionManager manager,
            MediaStore media,
            FixtureResultRetriever retriever,
            FixtureCheckpoints checkpoints) {
        this.jdbc = jdbc;
        this.transactions = new TransactionTemplate(manager);
        this.media = media;
        this.retriever = retriever;
        this.checkpoints = checkpoints;
    }

    private void requireOwned(Attempt owner) {
        boolean valid =
                jdbc.sql(
                                "SELECT EXISTS(SELECT 1 FROM generation_attempt WHERE id=? AND"
                                        + " version=? AND state='SAVING' AND NOT cancel_requested)")
                        .params(owner.id(), owner.version())
                        .query(Boolean.class)
                        .single();
        if (!valid) throw new StaleAttemptOwner();
    }

    public void publish(Attempt owner, FixtureOperations.Operation operation) throws Exception {
        var journal =
                transactions.execute(
                        status -> {
                            requireOwned(owner);
                            jdbc.sql(
                                            "INSERT INTO"
                                                + " publication_journal(attempt_id,attempt_version,phase,recording_id,temporary_name,storage_key,file_name,created_at)"
                                                + " VALUES(?,?,'DOWNLOADING',?,?,?,?,?) ON"
                                                + " CONFLICT(attempt_id) DO NOTHING")
                                    .params(
                                            owner.id(),
                                            owner.version(),
                                            "rec_" + UUID.randomUUID(),
                                            "result-" + UUID.randomUUID() + ".tmp",
                                            UUID.randomUUID() + ".audio",
                                            operation.fileName(),
                                            Timestamp.from(Instant.now()))
                                    .update();
                            var row =
                                    jdbc.sql("SELECT * FROM publication_journal WHERE attempt_id=?")
                                            .param(owner.id())
                                            .query()
                                            .singleRow();
                            if (((Number) row.get("attempt_version")).longValue()
                                    != owner.version()) throw new StaleAttemptOwner();
                            return row;
                        });
        Path temporary = media.temporaryPath((String) journal.get("temporary_name"));
        String storage = (String) journal.get("storage_key");
        Path destination = media.path(storage);
        MediaStore.Upload validated;
        if (journal.get("phase").equals("DOWNLOADING")) {
            // Partial bodies are never resumed as if complete; only observation/download is
            // repeated.
            Files.deleteIfExists(temporary);
            if (owner.request().provenance().equals("LOCAL")) {
                Path local = Path.of(java.net.URI.create(operation.resultUrl()));
                Files.copy(local, temporary);
            } else retriever.retrieve(operation, temporary, owner);
            validated = media.validateFile(temporary, (String) journal.get("file_name"));
            int count =
                    jdbc.sql(
                                    "UPDATE publication_journal SET"
                                        + " phase='VALIDATED',content_type=?,byte_size=?,sha256=?,duration_ms=?,sample_rate=?,channels=?,codec=?"
                                        + " WHERE attempt_id=? AND attempt_version=?")
                            .params(
                                    validated.contentType(),
                                    validated.bytes(),
                                    validated.hash(),
                                    validated.durationMs(),
                                    validated.sampleRate(),
                                    validated.channels(),
                                    validated.codec(),
                                    owner.id(),
                                    owner.version())
                            .update();
            if (count != 1) throw new StaleAttemptOwner();
        } else {
            Path surviving = Files.isRegularFile(destination) ? destination : temporary;
            if (!Files.isRegularFile(surviving))
                throw invalid("The validated result file is no longer available.");
            validated = media.validateFile(surviving, (String) journal.get("file_name"));
            if (validated.bytes() != ((Number) journal.get("byte_size")).longValue()
                    || !validated.hash().equals(journal.get("sha256")))
                throw invalid("The surviving result does not match its validated hash and size.");
        }
        if ((owner.request().provenance().equals("LIVE") && validated.preciseSeconds() > 30.0)
                || (owner.request().provenance().equals("LOCAL")
                        && validated.preciseSeconds() > 12.0))
            throw invalid(
                    "The live result exceeds the supported 30-second bound. Audio is never"
                            + " cropped.");
        requireOwned(owner);
        if (Files.exists(destination)) {
            var existing = media.validateFile(destination, validated.fileName());
            if (existing.bytes() != validated.bytes() || !existing.hash().equals(validated.hash()))
                throw invalid("The immutable result path already contains different bytes.");
        } else media.link(validated.path(), storage);
        checkpoints.reach("AFTER_MEDIA_LINK", owner);
        if (jdbc.sql(
                                "UPDATE publication_journal SET phase='LINKED' WHERE attempt_id=?"
                                        + " AND attempt_version=?")
                        .params(owner.id(), owner.version())
                        .update()
                != 1) throw new StaleAttemptOwner();
        MediaStore.Upload metadata = validated;
        transactions.executeWithoutResult(
                status -> {
                    jdbc.sql("SELECT id FROM workspace WHERE id=1 FOR UPDATE")
                            .query(Integer.class)
                            .single();
                    requireOwned(owner);
                    if (!jdbc.sql(
                                    "SELECT EXISTS(SELECT 1 FROM publication_journal WHERE"
                                            + " attempt_id=? AND attempt_version=?)")
                            .params(owner.id(), owner.version())
                            .query(Boolean.class)
                            .single()) throw new StaleAttemptOwner();
                    String recording = (String) journal.get("recording_id");
                    Timestamp now = Timestamp.from(Instant.now());
                    long ordinal =
                            jdbc.sql(
                                            "SELECT COUNT(*) FROM recording WHERE exploration_id=?"
                                                    + " AND kind='RENDITION'")
                                    .param(owner.explorationId())
                                    .query(Long.class)
                                    .single();
                    jdbc.sql(
                                    """
                                    INSERT INTO recording(id,exploration_id,title,kind,file_name,storage_key,content_type,byte_size,duration_ms,sample_rate,channels,codec,sha256,provenance,created_at,direction,request_id,attempt_id)
                                    VALUES(?,?,?,'RENDITION',?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                                    """)
                            .params(
                                    recording,
                                    owner.explorationId(),
                                    (!owner.request().provenance().equals("FIXTURE")
                                                    ? "Version "
                                                    : "Sample version ")
                                            + (ordinal + 1),
                                    metadata.fileName(),
                                    storage,
                                    metadata.contentType(),
                                    metadata.bytes(),
                                    metadata.durationMs(),
                                    metadata.sampleRate(),
                                    metadata.channels(),
                                    metadata.codec(),
                                    metadata.hash(),
                                    owner.request().provenance(),
                                    now,
                                    owner.request().direction(),
                                    owner.requestId(),
                                    owner.id())
                            .update();
                    if (jdbc.sql(
                                            "UPDATE generation_attempt SET"
                                                + " state='SUCCEEDED',version=version+1,result_recording_id=?,updated_at=?"
                                                + " WHERE id=? AND version=? AND state='SAVING' AND"
                                                + " NOT cancel_requested")
                                    .params(recording, now, owner.id(), owner.version())
                                    .update()
                            != 1) throw new StaleAttemptOwner();
                    jdbc.sql("UPDATE exploration SET updated_at=? WHERE id=?")
                            .params(now, owner.explorationId())
                            .update();
                    jdbc.sql(
                                    "UPDATE workspace SET active_attempt_id=NULL WHERE id=1 AND"
                                            + " active_attempt_id=?")
                            .param(owner.id())
                            .update();
                    jdbc.sql(
                                    "DELETE FROM publication_journal WHERE attempt_id=? AND"
                                            + " attempt_version=?")
                            .params(owner.id(), owner.version())
                            .update();
                    try {
                        checkpoints.reach("BEFORE_PUBLICATION_COMMIT", owner);
                    } catch (Exception error) {
                        throw new IllegalStateException(
                                "Publication checkpoint interrupted", error);
                    }
                });
        Files.deleteIfExists(temporary);
    }

    /** Only a confirmed terminal failure owned by this version authorizes journal removal. */
    public void discardFailed(Attempt owner) throws java.io.IOException {
        var rows =
                jdbc.sql(
                                "DELETE FROM publication_journal j USING generation_attempt a WHERE"
                                    + " j.attempt_id=a.id AND a.id=? AND j.attempt_version=? AND"
                                    + " a.state='FAILED' AND a.version=? AND"
                                    + " a.outcome_code<>'OUTCOME_UNKNOWN' RETURNING j.*")
                        .params(owner.id(), owner.version(), owner.version() + 1)
                        .query()
                        .listOfRows();
        for (var row : rows) removeUnreferenced(row);
    }

    private void removeUnreferenced(Map<String, Object> row) throws java.io.IOException {
        String key = (String) row.get("storage_key");
        if (!jdbc.sql(
                        "SELECT EXISTS(SELECT 1 FROM recording WHERE storage_key=?) OR"
                                + " EXISTS(SELECT 1 FROM publication_journal WHERE storage_key=?)")
                .params(key, key)
                .query(Boolean.class)
                .single()) Files.deleteIfExists(media.path(key));
        String temp = (String) row.get("temporary_name");
        if (!jdbc.sql("SELECT EXISTS(SELECT 1 FROM publication_journal WHERE temporary_name=?)")
                .param(temp)
                .query(Boolean.class)
                .single()) Files.deleteIfExists(media.temporaryPath(temp));
    }

    /**
     * Startup only: discovery precedes sweeping, and every original/rendition row protects its
     * bytes.
     */
    public void cleanup() throws java.io.IOException {
        var retired =
                jdbc.sql(
                                "DELETE FROM publication_journal j USING generation_attempt a WHERE"
                                    + " a.id=j.attempt_id AND (a.state='SUCCEEDED' OR"
                                    + " (a.state='FAILED' AND a.outcome_code<>'OUTCOME_UNKNOWN'))"
                                    + " RETURNING j.*")
                        .query()
                        .listOfRows();
        for (var row : retired) removeUnreferenced(row);
        Set<String> keys =
                new HashSet<>(
                        jdbc.sql(
                                        "SELECT storage_key FROM recording UNION SELECT storage_key"
                                                + " FROM publication_journal")
                                .query(String.class)
                                .list());
        Set<String> temporaries =
                new HashSet<>(
                        jdbc.sql("SELECT temporary_name FROM publication_journal")
                                .query(String.class)
                                .list());
        media.cleanupUnreferenced(keys, temporaries);
    }

    private static ApiProblem invalid(String message) {
        return new ApiProblem(422, "OUTPUT_INVALID", message, "RETRY");
    }
}
