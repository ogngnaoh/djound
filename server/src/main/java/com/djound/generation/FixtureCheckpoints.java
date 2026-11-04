package com.djound.generation;

import com.djound.Installation;
import com.djound.Models.Attempt;

import org.springframework.stereotype.Component;

import tools.jackson.databind.ObjectMapper;

import java.nio.file.*;
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;

@Component
public class FixtureCheckpoints {
    private final FixtureExecutionSettings settings;
    private final Path directory;
    private final AtomicBoolean reached = new AtomicBoolean();

    public FixtureCheckpoints(FixtureExecutionSettings settings, Installation installation)
            throws java.io.IOException {
        this.settings = settings;
        this.directory = installation.rootPath().resolve("checkpoints");
        if (!settings.checkpoint().isEmpty()) {
            Files.createDirectories(directory);
            Files.deleteIfExists(directory.resolve("reached.json"));
        }
    }

    public void reach(String checkpoint, Attempt attempt) throws Exception {
        if (!settings.checkpoint().equals(checkpoint) || !reached.compareAndSet(false, true))
            return;
        Path temporary = Files.createTempFile(directory, "marker-", ".tmp");
        Files.writeString(
                temporary,
                new ObjectMapper()
                        .writeValueAsString(
                                Map.of(
                                        "schemaVersion",
                                        1,
                                        "processId",
                                        ProcessHandle.current().pid(),
                                        "checkpoint",
                                        checkpoint,
                                        "attemptId",
                                        attempt.id(),
                                        "attemptVersion",
                                        attempt.version())));
        Files.move(
                temporary,
                directory.resolve("reached.json"),
                StandardCopyOption.ATOMIC_MOVE,
                StandardCopyOption.REPLACE_EXISTING);
        Thread.sleep(60000);
    }
}
