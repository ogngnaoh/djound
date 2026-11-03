package com.djound.generation;

import com.djound.*;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.concurrent.atomic.AtomicBoolean;

@Component
@EnableScheduling
public class GenerationRunner {
    private static final Logger log = LoggerFactory.getLogger(GenerationRunner.class);
    private final GenerationService service;
    private final ScriptedAdapter adapter;
    private final MediaStore media;
    private final AtomicBoolean running = new AtomicBoolean();

    public GenerationRunner(GenerationService service, ScriptedAdapter adapter, MediaStore media) {
        this.service = service;
        this.adapter = adapter;
        this.media = media;
        service.recoverInterrupted();
    }

    @Scheduled(fixedDelay = 250, initialDelay = 250)
    public void tick() {
        if (!running.compareAndSet(false, true)) return;
        Models.Attempt attempt = null;
        try {
            attempt = service.claimNext();
            if (attempt == null) return;
            // claimNext committed SUBMITTING before the adapter can execute.
            attempt = service.transition(attempt, "RUNNING");
            var output = adapter.execute(service.outputOrdinal(attempt));
            attempt = service.transition(attempt, "SAVING");
            try (var upload = media.ingest(output)) {
                service.complete(attempt, upload);
            }
        } catch (Exception error) {
            log.error("Fixture creation failed", error);
            if (attempt != null) {
                String code =
                        error instanceof ApiProblem
                                ? "OUTPUT_INVALID"
                                : error instanceof java.io.IOException
                                        ? "MEDIA_STORAGE_FAILED"
                                        : "GENERATION_FAILED";
                service.fail(
                        attempt.id(),
                        code,
                        "Sample creation could not finish. Your source, saved notes, and earlier"
                                + " recordings remain available.");
            }
            if (error instanceof InterruptedException) Thread.currentThread().interrupt();
        } finally {
            running.set(false);
        }
    }
}
