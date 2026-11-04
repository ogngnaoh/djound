package com.djound.availability;

import com.djound.*;
import com.djound.generation.GenerationConfiguration;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

import java.sql.Timestamp;
import java.time.*;
import java.util.*;
import java.util.concurrent.*;

@Service
public class AvailabilityService {
    public record View(
            String eligibility,
            String reasonCode,
            Instant observedAt,
            Instant providerResetAt,
            Instant nextProbeAt,
            boolean supportsStatusCheck,
            boolean canRecheck,
            boolean allowsSubmission) {}

    private record Reservation(long version, String scenario) {}

    private final JdbcClient jdbc;
    private final TransactionTemplate transactions;
    private final GenerationConfiguration configuration;
    private final Clock clock;
    private final AvailabilityConfiguration.Cooldown cooldown;
    private final FixtureAvailabilityProbe probe;

    public AvailabilityService(
            JdbcClient jdbc,
            PlatformTransactionManager manager,
            GenerationConfiguration configuration,
            Clock clock,
            AvailabilityConfiguration.Cooldown cooldown,
            FixtureAvailabilityProbe probe,
            Installation installation) {
        this.jdbc = jdbc;
        this.transactions = new TransactionTemplate(manager);
        this.configuration = configuration;
        this.clock = clock;
        this.cooldown = cooldown;
        this.probe = probe;
    }

    public View view() {
        return policy(
                jdbc.sql(
                                "SELECT * FROM "
                                        + (configuration.live() ? "live_availability" : "workspace")
                                        + " WHERE id=1")
                        .query()
                        .singleRow());
    }

    private View policy(Map<String, Object> row) {
        if (!configuration.configured())
            return new View(
                    "UNKNOWN", "PROVIDER_NOT_CONFIGURED", null, null, null, false, false, false);
        Instant now = clock.instant(),
                observed = instant(row.get("availability_observed_at")),
                reset = instant(row.get("availability_provider_reset_at")),
                next = instant(row.get("availability_next_probe_at"));
        boolean supported = (Boolean) row.get("availability_supports_status_check"),
                elapsed = next == null || !now.isBefore(next);
        String state = (String) row.get("availability_state"),
                reason = (String) row.get("availability_reason_code");
        if (state.equals("BLOCKED") && elapsed) {
            state = "UNKNOWN";
            reason = "CAPACITY_UNCONFIRMED";
        }
        boolean allows =
                state.equals("ELIGIBLE") || (state.equals("UNKNOWN") && !supported && elapsed);
        return new View(
                state, reason, observed, reset, next, supported, supported && elapsed, allows);
    }

    private static Instant instant(Object value) {
        return value == null ? null : ((Timestamp) value).toInstant();
    }

    /** Admission already holds the workspace guard. Reads never alter availability. */
    public void requireAvailable() {
        View state = view();
        if (!state.allowsSubmission())
            throw new ApiProblem(
                    503,
                    "CAPACITY_UNAVAILABLE",
                    state.supportsStatusCheck()
                            ? "Creation availability is unconfirmed or blocked. Check availability"
                                    + " when the local cooldown ends."
                            : "Free capacity is currently unavailable. Your source and notes remain"
                                    + " saved; try a deliberate request after the local cooldown.",
                    state.canRecheck() ? "RECHECK_AVAILABILITY" : "WAIT",
                    "EDIT_TEXT");
    }

    /**
     * Only called inside a successful owned attempt-failure transaction under the workspace guard.
     */
    public void observeCapacityFailure() {
        observeCapacityFailure(configuration.live() ? "LIVE" : "FIXTURE");
    }

    public void observeCapacityFailure(String provenance) {
        Instant now = clock.instant();
        jdbc.sql(
                        "UPDATE "
                                + (provenance.equals("LIVE") ? "live_availability" : "workspace")
                                + " SET availability_state='BLOCKED',availability_reason_code='CAPACITY_UNAVAILABLE',availability_observed_at=?,availability_provider_reset_at=NULL,availability_next_probe_at=?,availability_version=availability_version+1"
                                + " WHERE id=1")
                .params(Timestamp.from(now), Timestamp.from(now.plusMillis(cooldown.millis())))
                .update();
    }

    public void setScenario(String scenario) {
        if (!configuration.controlsEnabled())
            throw new ApiProblem(
                    404, "NOT_FOUND", "This resource could not be found.", "RETURN_TO_LIBRARY");
        if (scenario == null
                || !Set.of(
                                "READY",
                                "BLOCKED_NO_STATUS",
                                "STATUS_SUCCESS",
                                "STATUS_MALFORMED",
                                "STATUS_TIMEOUT")
                        .contains(scenario))
            throw new ApiProblem(
                    400,
                    "INVALID_REQUEST",
                    "Choose a supported availability review scenario.",
                    "RETRY");
        transactions.executeWithoutResult(
                status -> {
                    jdbc.sql("SELECT id FROM workspace WHERE id=1 FOR UPDATE")
                            .query(Integer.class)
                            .single();
                    Instant now = clock.instant();
                    boolean ready = scenario.equals("READY");
                    jdbc.sql(
                                    "UPDATE workspace SET"
                                        + " availability_state=?,availability_reason_code=?,availability_observed_at=?,availability_provider_reset_at=NULL,availability_next_probe_at=?,availability_supports_status_check=?,availability_version=availability_version+1,fixture_status_scenario=?"
                                        + " WHERE id=1")
                            .param(ready ? "ELIGIBLE" : "BLOCKED")
                            .param(ready ? "FIXTURE_READY" : "CAPACITY_UNAVAILABLE")
                            .param(Timestamp.from(now))
                            .param(
                                    new org.springframework.jdbc.core.SqlParameterValue(
                                            java.sql.Types.TIMESTAMP,
                                            ready
                                                    ? null
                                                    : Timestamp.from(
                                                            now.plusMillis(cooldown.millis()))))
                            .param(scenario.startsWith("STATUS_"))
                            .param(scenario)
                            .update();
                });
    }

    public View recheck() {
        if (configuration.live()) return view();
        if (!configuration.fixture())
            throw new ApiProblem(
                    503, "PROVIDER_NOT_CONFIGURED", "Creation is not configured.", "EDIT_TEXT");
        Reservation reservation =
                transactions.execute(
                        status -> {
                            var row =
                                    jdbc.sql("SELECT * FROM workspace WHERE id=1 FOR UPDATE")
                                            .query()
                                            .singleRow();
                            View current = policy(row);
                            if (!current.supportsStatusCheck()) return null;
                            if (!current.canRecheck())
                                throw new ApiProblem(
                                        409,
                                        "RECHECK_TOO_SOON",
                                        "Wait until the local availability-check cooldown has"
                                                + " elapsed.",
                                        "WAIT");
                            long version =
                                    ((Number) row.get("availability_version")).longValue() + 1;
                            jdbc.sql(
                                            "UPDATE workspace SET"
                                                + " availability_state='UNKNOWN',availability_reason_code='STATUS_CHECK_PENDING',availability_next_probe_at=?,availability_version=?"
                                                + " WHERE id=1")
                                    .params(
                                            Timestamp.from(
                                                    clock.instant().plusMillis(cooldown.millis())),
                                            version)
                                    .update();
                            return new Reservation(
                                    version, (String) row.get("fixture_status_scenario"));
                        });
        if (reservation == null) return view();
        String state = "UNKNOWN", reason = "STATUS_MALFORMED";
        ExecutorService worker = Executors.newVirtualThreadPerTaskExecutor();
        Future<String> observation = worker.submit(() -> probe.observe(reservation.scenario()));
        try {
            if (observation.get(1, TimeUnit.SECONDS).equals("ELIGIBLE")) {
                state = "ELIGIBLE";
                reason = "STATUS_CONFIRMED";
            }
        } catch (TimeoutException error) {
            reason = "STATUS_TIMEOUT";
            observation.cancel(true);
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
            reason = "STATUS_TIMEOUT";
            observation.cancel(true);
        } catch (ExecutionException error) {
            reason = "STATUS_MALFORMED";
        } finally {
            worker.shutdownNow();
        }
        String observedState = state, observedReason = reason;
        transactions.executeWithoutResult(
                status -> {
                    jdbc.sql("SELECT id FROM workspace WHERE id=1 FOR UPDATE")
                            .query(Integer.class)
                            .single();
                    Instant now = clock.instant();
                    jdbc.sql(
                                    "UPDATE workspace SET"
                                        + " availability_state=?,availability_reason_code=?,availability_observed_at=?,availability_provider_reset_at=NULL,availability_next_probe_at=?"
                                        + " WHERE id=1 AND availability_version=?")
                            .params(
                                    observedState,
                                    observedReason,
                                    Timestamp.from(now),
                                    Timestamp.from(now.plusMillis(cooldown.millis())),
                                    reservation.version())
                            .update();
                });
        return view();
    }
}
