package com.djound.generation;

import com.djound.*;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;

import java.sql.Timestamp;
import java.time.Instant;
import java.util.UUID;

@Component
public class LiveOperations {
    private final JdbcClient jdbc;
    private final MediaStore media;
    private final LiveAllowance allowance;
    private final GenerationConfiguration configuration;
    private final MelodyFlowTransport transport;
    private final org.springframework.transaction.support.TransactionTemplate transactions;

    @org.springframework.beans.factory.annotation.Autowired
    public LiveOperations(
            JdbcClient jdbc,
            MediaStore media,
            LiveAllowance allowance,
            GenerationConfiguration configuration,
            org.springframework.transaction.PlatformTransactionManager manager) {
        this(jdbc, media, allowance, configuration, new MelodyFlowTransport(), manager);
    }

    LiveOperations(
            JdbcClient jdbc,
            MediaStore media,
            LiveAllowance allowance,
            GenerationConfiguration configuration,
            MelodyFlowTransport transport,
            org.springframework.transaction.PlatformTransactionManager manager) {
        this.transactions =
                new org.springframework.transaction.support.TransactionTemplate(manager);
        this.jdbc = jdbc;
        this.media = media;
        this.allowance = allowance;
        this.configuration = configuration;
        this.transport = transport;
    }

    public String submit(Models.Attempt owner) throws Exception {
        if (!configuration.live() || !owner.request().provenance().equals("LIVE"))
            throw new IllegalStateException("Live dispatch is disabled");
        var row =
                jdbc.sql("SELECT storage_key,file_name FROM recording WHERE id=?")
                        .param(owner.request().sourceRecordingId())
                        .query()
                        .singleRow();
        var source =
                media.validateFile(
                        media.path((String) row.get("storage_key")), (String) row.get("file_name"));
        if (source.preciseSeconds() > 30
                || owner.request()
                                .assembledInstructions()
                                .codePointCount(0, owner.request().assembledInstructions().length())
                        > 500)
            throw new ApiProblem(
                    422,
                    "PROVIDER_DURATION_UNSUPPORTED",
                    "The frozen request is incompatible with the live adapter.",
                    "EDIT_TEXT");
        allowance.reserve(owner);
        String handle = "live_op_" + UUID.randomUUID();
        jdbc.sql(
                        "INSERT INTO"
                            + " generation_operation(id,attempt_id,adapter_configuration_id,provenance)"
                            + " VALUES(?,?,?,'LIVE')")
                .params(handle, owner.id(), owner.request().adapterConfigurationId())
                .update();
        String uploaded = transport.upload(source.path(), source.fileName(), source.contentType());
        jdbc.sql("UPDATE generation_operation SET upload_path=? WHERE id=?")
                .params(uploaded, handle)
                .update();
        String event =
                transport.submit(
                        uploaded,
                        source.fileName(),
                        source.contentType(),
                        owner.request().assembledInstructions(),
                        source.preciseSeconds());
        // Persist the receipt before returning to the runner; no other outbound request intervenes.
        transactions.executeWithoutResult(
                status -> {
                    jdbc.sql("UPDATE generation_operation SET event_id=? WHERE id=?")
                            .params(event, handle)
                            .update();
                    if (jdbc.sql(
                                            "UPDATE generation_attempt SET"
                                                + " operation_handle=?,state='RUNNING',version=version+1,updated_at=?"
                                                + " WHERE id=? AND version=? AND"
                                                + " state='SUBMITTING'")
                                    .params(
                                            handle,
                                            Timestamp.from(Instant.now()),
                                            owner.id(),
                                            owner.version())
                                    .update()
                            != 1) throw new StaleAttemptOwner();
                });
        return handle;
    }

    public FixtureOperations.Operation observe(String handle) throws Exception {
        var row =
                jdbc.sql("SELECT * FROM generation_operation WHERE id=? AND provenance='LIVE'")
                        .param(handle)
                        .query()
                        .singleRow();
        String url = (String) row.get("result_url");
        if (url == null) {
            if (row.get("event_id") == null || row.get("observation_started_at") != null)
                throw unknown();
            // CAS consumes permission to observe this non-replayable event, even if the process
            // dies.
            if (jdbc.sql(
                                    "UPDATE generation_operation SET observation_started_at=? WHERE"
                                        + " id=? AND observation_started_at IS NULL AND result_url"
                                        + " IS NULL")
                            .params(Timestamp.from(Instant.now()), handle)
                            .update()
                    != 1) throw unknown();
            url = transport.observe((String) row.get("event_id"));
            jdbc.sql(
                            "UPDATE generation_operation SET result_url=? WHERE id=? AND result_url"
                                    + " IS NULL")
                    .params(url, handle)
                    .update();
        }
        return new FixtureOperations.Operation(
                handle, "SUCCESS", "melodyflow-variation-1.wav", url, Instant.now());
    }

    private static ApiProblem unknown() {
        return new ApiProblem(
                503,
                "OUTCOME_UNKNOWN",
                "The saved event stream may already have been consumed. It will not be submitted or"
                        + " observed again.",
                "ACKNOWLEDGE_UNKNOWN_OUTCOME");
    }
}
