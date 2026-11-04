package com.djound.generation;

import com.djound.*;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.time.Instant;
import java.util.concurrent.atomic.AtomicBoolean;

@Component
@EnableScheduling
public class GenerationRunner {
    private static final Logger log = LoggerFactory.getLogger(GenerationRunner.class);
    private final GenerationService service;
    private final FixtureOperations operations;
    private final PublicationStore publications;
    private final FixtureCheckpoints checkpoints;
    private final AtomicBoolean running = new AtomicBoolean();

    public GenerationRunner(
            GenerationService service,
            FixtureOperations operations,
            PublicationStore publications,
            FixtureCheckpoints checkpoints)
            throws java.io.IOException {
        this.service = service;
        this.operations = operations;
        this.publications = publications;
        this.checkpoints = checkpoints;
        service.recoverInterrupted();
        publications.cleanup();
    }

    @Scheduled(fixedDelay = 250, initialDelay = 250)
    public void tick() {
        if (!running.compareAndSet(false, true)) return;
        Models.Attempt owner = null;
        try {
            owner = service.active();
            if (owner == null) return;
            FixtureOperations.Operation operation;
            if (owner.state().equals("ACCEPTED")) {
                checkpoints.reach("BEFORE_SUBMIT", owner);
                owner = service.claimNext();
                if (owner == null) return;
                operation =
                        operations.submit(
                                owner,
                                service.outputOrdinal(owner),
                                service.fixtureOutcome(owner.id()));
                checkpoints.reach("AFTER_SUBMITTING", owner);
                if (operation.outcome().equals("UNKNOWN")) {
                    service.fail(
                            owner,
                            "OUTCOME_UNKNOWN",
                            "This sample review submission has an intentionally unknown outcome."
                                + " Acknowledge that it may still finish before another deliberate"
                                + " submission.");
                    return;
                }
                owner = service.saveHandle(owner, operation.id());
                checkpoints.reach("AFTER_HANDLE", owner);
            } else {
                String handle = service.savedHandle(owner.id());
                if (handle == null) {
                    service.fail(
                            owner,
                            "OUTCOME_UNKNOWN",
                            "The earlier submission has no saved observation handle. Its outcome"
                                    + " remains unknown.");
                    return;
                }
                try {
                    operation = operations.observe(handle);
                } catch (RuntimeException error) {
                    service.fail(
                            owner,
                            "OUTCOME_UNKNOWN",
                            "The saved operation could not be observed. Its earlier execution"
                                    + " remains uncertain.");
                    return;
                }
            }
            long remaining = Duration.between(Instant.now(), operation.readyAt()).toMillis();
            if (remaining > 30000) {
                service.fail(
                        owner,
                        "OUTCOME_UNKNOWN",
                        "The saved operation did not provide a bounded observation time.");
                return;
            }
            if (remaining > 0) Thread.sleep(remaining);
            if (operation.outcome().equals("FAILURE")) {
                service.fail(
                        owner,
                        "GENERATION_FAILED",
                        "The sample attempt failed. Your source, notes, and earlier versions remain"
                                + " available.");
                return;
            }
            owner = service.beginSaving(owner);
            publications.publish(owner, operation);
            checkpoints.reach("AFTER_PUBLICATION_COMMIT", service.get(owner.id()));
        } catch (StaleAttemptOwner ignored) {
            // A newer recovery owner or terminal row is authoritative. Never fail or clean up its
            // work.
        } catch (Exception error) {
            log.error("Fixture creation failed", error);
            if (owner != null) {
                String code =
                        owner.state().equals("SUBMITTING")
                                ? "OUTCOME_UNKNOWN"
                                : error instanceof ApiProblem
                                        ? "OUTPUT_INVALID"
                                        : error instanceof java.io.IOException
                                                ? "MEDIA_STORAGE_FAILED"
                                                : "GENERATION_FAILED";
                if (service.fail(
                        owner,
                        code,
                        "Sample creation could not finish. Your source, notes, and earlier"
                                + " recordings remain available.")) {
                    try {
                        publications.discardFailed(owner);
                    } catch (java.io.IOException cleanupError) {
                        log.warn("Deferred unpublished file cleanup", cleanupError);
                    }
                }
            }
            if (error instanceof InterruptedException) Thread.currentThread().interrupt();
        } finally {
            running.set(false);
        }
    }
}
