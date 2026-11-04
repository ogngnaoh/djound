package com.djound.generation;

import static com.djound.Models.*;

import com.djound.*;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * Local opt-in demonstration controls. They never enqueue work, alter audio, or call a provider.
 */
@Service
public class FixtureReviewControls {
    private final JdbcClient jdbc;
    private final GenerationConfiguration configuration;
    private final TransactionTemplate transactions;

    public FixtureReviewControls(
            JdbcClient jdbc,
            GenerationConfiguration configuration,
            PlatformTransactionManager manager,
            Installation installation) {
        this.jdbc = jdbc;
        this.configuration = configuration;
        this.transactions = new TransactionTemplate(manager);
    }

    public boolean enabled() {
        return configuration.controlsEnabled();
    }

    private void requireEnabled() {
        if (!enabled())
            throw new ApiProblem(
                    404, "NOT_FOUND", "This resource could not be found.", "RETURN_TO_LIBRARY");
    }

    public FixtureControls get() {
        requireEnabled();
        return read();
    }

    private FixtureControls read() {
        return jdbc.sql(
                        "SELECT fixture_next_outcome,fixture_unavailable_recording_id FROM"
                                + " workspace WHERE id=1")
                .query(
                        (r, n) ->
                                new FixtureControls(
                                        r.getString("fixture_next_outcome"),
                                        r.getString("fixture_unavailable_recording_id")))
                .single();
    }

    public FixtureControls setOutcome(FixtureOutcomeControl command) {
        requireEnabled();
        if (command == null
                || !("SUCCESS".equals(command.outcome())
                        || "FAILURE".equals(command.outcome())
                        || "UNKNOWN".equals(command.outcome())
                        || "CAPACITY_UNAVAILABLE".equals(command.outcome())))
            throw new ApiProblem(
                    400,
                    "INVALID_REQUEST",
                    "Choose SUCCESS, FAILURE, UNKNOWN or CAPACITY_UNAVAILABLE for the next sample"
                            + " attempt.",
                    "RETRY");
        return transactions.execute(
                status -> {
                    jdbc.sql("SELECT id FROM workspace WHERE id=1 FOR UPDATE")
                            .query(Integer.class)
                            .single();
                    jdbc.sql("UPDATE workspace SET fixture_next_outcome=? WHERE id=1")
                            .param(command.outcome())
                            .update();
                    return read();
                });
    }

    public FixtureControls setPlayback(FixturePlaybackControl command) {
        requireEnabled();
        if (command == null
                || command.recordingId() == null
                || command.recordingId().isBlank()
                || command.unavailable() == null)
            throw new ApiProblem(
                    400,
                    "INVALID_REQUEST",
                    "Provide a recording and an explicit unavailable value.",
                    "RETRY");
        return transactions.execute(
                status -> {
                    jdbc.sql("SELECT id FROM workspace WHERE id=1 FOR UPDATE")
                            .query(Integer.class)
                            .single();
                    var record =
                            jdbc
                                    .sql("SELECT kind,provenance FROM recording WHERE id=?")
                                    .param(command.recordingId())
                                    .query()
                                    .listOfRows()
                                    .stream()
                                    .findFirst()
                                    .orElseThrow(
                                            () ->
                                                    new ApiProblem(
                                                            404,
                                                            "NOT_FOUND",
                                                            "This recording could not be found.",
                                                            "RETURN_TO_LIBRARY"));
                    if (!record.get("kind").equals("RENDITION")
                            || !record.get("provenance").equals("FIXTURE"))
                        throw new ApiProblem(
                                422,
                                "FIXTURE_RECORDING_REQUIRED",
                                "Playback review controls apply only to stored sample renditions.",
                                "OPEN_RENDITION");
                    if (command.unavailable())
                        jdbc.sql(
                                        "UPDATE workspace SET fixture_unavailable_recording_id=?"
                                                + " WHERE id=1")
                                .param(command.recordingId())
                                .update();
                    else
                        jdbc.sql(
                                        "UPDATE workspace SET fixture_unavailable_recording_id=NULL"
                                            + " WHERE id=1 AND fixture_unavailable_recording_id=?")
                                .param(command.recordingId())
                                .update();
                    return read();
                });
    }

    /**
     * Caller already owns the workspace guard and admission transaction. Rollback restores the
     * pending control.
     */
    public String consumeOutcome() {
        if (!enabled()) return "SUCCESS";
        String outcome =
                jdbc.sql("SELECT fixture_next_outcome FROM workspace WHERE id=1")
                        .query(String.class)
                        .single();
        jdbc.sql("UPDATE workspace SET fixture_next_outcome='SUCCESS' WHERE id=1").update();
        return outcome;
    }

    public void requirePlayable(String recordingId) {
        if (enabled()
                && jdbc.sql(
                                "SELECT EXISTS(SELECT 1 FROM workspace WHERE id=1 AND"
                                        + " fixture_unavailable_recording_id=?)")
                        .param(recordingId)
                        .query(Boolean.class)
                        .single()) {
            throw new ApiProblem(
                    503,
                    "MEDIA_UNAVAILABLE",
                    "This sample is temporarily unavailable in playback review. Restore playback"
                            + " and try again.",
                    "RETRY_PLAYBACK",
                    "OPEN_ANOTHER_RECORDING");
        }
    }
}
