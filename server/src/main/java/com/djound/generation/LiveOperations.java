package com.djound.generation;

import com.djound.*;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;

import java.sql.Timestamp;
import java.time.Instant;

@Component
public class LiveOperations {
    private final JdbcClient jdbc;
    private final MelodyFlowTransport transport;

    @org.springframework.beans.factory.annotation.Autowired
    public LiveOperations(JdbcClient jdbc) {
        this(jdbc, new MelodyFlowTransport());
    }

    LiveOperations(JdbcClient jdbc, MelodyFlowTransport transport) {
        this.jdbc = jdbc;
        this.transport = transport;
    }

    /** Observe an acknowledged historical operation. There is no submission path. */
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
