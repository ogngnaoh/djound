package com.djound.generation;

import com.djound.ApiProblem;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

import java.util.Map;

@Component
public class LiveAllowance {
    private final JdbcClient jdbc;
    private final TransactionTemplate transactions;

    public LiveAllowance(JdbcClient jdbc, PlatformTransactionManager manager) {
        this.jdbc = jdbc;
        this.transactions = new TransactionTemplate(manager);
    }

    public Map<String, Object> view() {
        var row =
                jdbc.sql(
                                "SELECT a.submission_limit,(SELECT count(*) FROM"
                                    + " live_submission_reservation r WHERE"
                                    + " r.installation_id=w.installation_id) AS used FROM workspace"
                                    + " w LEFT JOIN live_submission_allowance a ON"
                                    + " a.installation_id=w.installation_id WHERE w.id=1")
                        .query()
                        .singleRow();
        boolean provisioned = row.get("submission_limit") != null;
        long used = ((Number) row.get("used")).longValue();
        return Map.of(
                "limit",
                2,
                "used",
                used,
                "remaining",
                provisioned ? Math.max(0, 2 - used) : 0,
                "provisioned",
                provisioned);
    }

    public void requireAvailable() {
        var view = view();
        if (!(Boolean) view.get("provisioned"))
            throw new ApiProblem(
                    503,
                    "LIVE_ALLOWANCE_UNPROVISIONED",
                    "The installation has no approved live submission allowance.",
                    "EDIT_TEXT");
        if (((Number) view.get("remaining")).longValue() == 0)
            throw new ApiProblem(
                    503,
                    "LIVE_ALLOWANCE_EXHAUSTED",
                    "The two approved live submissions have been consumed. Saved recordings remain"
                            + " available.",
                    "EDIT_TEXT");
    }

    /** Separate commit before any transmission. No failure or restart refunds this reservation. */
    public void reserve(com.djound.Models.Attempt owner) {
        String attemptId = owner.id();
        if (!owner.request().provenance().equals("LIVE"))
            throw new IllegalArgumentException("Only LIVE work consumes the live allowance");
        transactions.executeWithoutResult(
                status -> {
                    jdbc.sql("SELECT id FROM workspace WHERE id=1 FOR UPDATE")
                            .query(Integer.class)
                            .single();
                    if (!jdbc.sql(
                                    "SELECT EXISTS(SELECT 1 FROM generation_attempt WHERE id=? AND"
                                            + " version=? AND state='SUBMITTING')")
                            .params(owner.id(), owner.version())
                            .query(Boolean.class)
                            .single()) throw new StaleAttemptOwner();
                    if (jdbc.sql(
                                    "SELECT EXISTS(SELECT 1 FROM live_submission_reservation WHERE"
                                            + " attempt_id=?)")
                            .param(attemptId)
                            .query(Boolean.class)
                            .single())
                        throw new IllegalStateException(
                                "A reserved live attempt must never be dispatched twice");
                    requireAvailable();
                    jdbc.sql(
                                    "INSERT INTO"
                                        + " live_submission_reservation(attempt_id,installation_id)"
                                        + " SELECT ?,installation_id FROM workspace WHERE id=1")
                            .param(attemptId)
                            .update();
                });
    }
}
