package com.djound.generation;

import com.djound.Models.Attempt;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;

import java.sql.Timestamp;
import java.time.Instant;
import java.util.UUID;

@Component
public class FixtureOperations {
    public record Operation(
            String id, String outcome, String fileName, String resultUrl, Instant readyAt) {}

    private final JdbcClient jdbc;
    private final GenerationConfiguration configuration;
    private final FixtureExecutionSettings settings;
    private final ScriptedAdapter adapter;

    public FixtureOperations(
            JdbcClient jdbc,
            GenerationConfiguration configuration,
            FixtureExecutionSettings settings,
            ScriptedAdapter adapter) {
        this.jdbc = jdbc;
        this.configuration = configuration;
        this.settings = settings;
        this.adapter = adapter;
    }

    /**
     * Separate durable submission commit. A lost handle is deliberately not reconstructed by
     * attempt ID.
     */
    public Operation submit(Attempt attempt, long ordinal, String outcome) {
        if (!configuration.fixture())
            throw new IllegalStateException("New fixture dispatch is disabled");
        String id = "fixture_op_" + UUID.randomUUID(),
                name = ordinal % 2 == 0 ? "sample-warm.wav" : "sample-pulse.wav";
        Instant now = Instant.now(), ready = now.plusMillis(configuration.delayMs());
        jdbc.sql(
                        "INSERT INTO"
                            + " fixture_operation(id,attempt_id,fixture_outcome,fixture_name,result_url,ready_at,created_at)"
                            + " VALUES(?,?,?,?,?,?,?)")
                .params(
                        id,
                        attempt.id(),
                        outcome,
                        name,
                        settings.resultUrl(),
                        Timestamp.from(ready),
                        Timestamp.from(now))
                .update();
        jdbc.sql(
                        "INSERT INTO"
                            + " generation_operation(id,attempt_id,adapter_configuration_id,provenance,result_url)"
                            + " VALUES(?,?,?,'FIXTURE',?)")
                .params(
                        id,
                        attempt.id(),
                        attempt.request().adapterConfigurationId(),
                        settings.resultUrl())
                .update();
        adapter.recordSubmission();
        return new Operation(id, outcome, name, settings.resultUrl(), ready);
    }

    public Operation observe(String savedHandle) {
        return jdbc.sql("SELECT * FROM fixture_operation WHERE id=?")
                .param(savedHandle)
                .query(
                        (r, n) ->
                                new Operation(
                                        r.getString("id"),
                                        r.getString("fixture_outcome"),
                                        r.getString("fixture_name"),
                                        r.getString("result_url"),
                                        r.getTimestamp("ready_at").toInstant()))
                .optional()
                .orElseThrow(
                        () ->
                                new IllegalStateException(
                                        "Saved fixture operation cannot be observed"));
    }
}
