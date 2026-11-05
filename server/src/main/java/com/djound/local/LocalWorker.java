package com.djound.local;

import com.djound.ApiProblem;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.function.BooleanSupplier;

/** Only server-owned JSON/files cross this boundary. No shell, downloads or remote fallback. */
@Component
public class LocalWorker implements AutoCloseable {
    private final ObjectMapper json = new ObjectMapper();
    private final Path root, python, script;
    private final Map<String, Process> processes = new ConcurrentHashMap<>();
    private volatile boolean closing;
    private volatile long checkedAt;
    private volatile boolean ready;

    public LocalWorker(
            @Value("${djound.data-dir}") String data,
            @Value("${djound.local-python:.runtime/local-engine/venv/bin/python}") String python,
            @Value("${djound.local-worker:local-engine/worker.py}") String script) {
        this.root = Path.of(data).toAbsolutePath().normalize();
        this.python = Path.of(python).toAbsolutePath().normalize();
        this.script = Path.of(script).toAbsolutePath().normalize();
    }

    public synchronized boolean available() {
        if (closing || !Files.isExecutable(python) || !Files.isRegularFile(script)) return false;
        if (System.nanoTime() - checkedAt < TimeUnit.SECONDS.toNanos(ready ? 300 : 5)) return ready;
        ready = false;
        Process process = null;
        Path output = null;
        try {
            Files.createDirectories(root.resolve("local-jobs"));
            output = Files.createTempFile(root.resolve("local-jobs"), "readiness-", ".json");
            ProcessBuilder builder =
                    new ProcessBuilder(python.toString(), script.toString(), "--check");
            builder.redirectOutput(output.toFile()).redirectError(ProcessBuilder.Redirect.DISCARD);
            process = builder.start();
            if (process.waitFor(10, TimeUnit.SECONDS)
                    && process.exitValue() == 0
                    && Files.size(output) < 65536) {
                var check = json.readTree(Files.readString(output));
                ready = check.path("schemaVersion").asInt() == 1 && check.path("ready").asBoolean();
            }
        } catch (Exception ignored) {
            ready = false;
        } finally {
            if (process != null) stop(process);
            if (output != null)
                try {
                    Files.deleteIfExists(output);
                } catch (java.io.IOException ignored) {
                }
            checkedAt = System.nanoTime();
        }
        return ready;
    }

    public Path directory(String id) {
        if (!id.matches("[A-Za-z0-9_-]+"))
            throw new IllegalArgumentException("Invalid owned job ID");
        return root.resolve("local-jobs").resolve(id);
    }

    public JsonNode execute(String id, Map<String, Object> request, BooleanSupplier cancelled)
            throws Exception {
        if (!available())
            throw new ApiProblem(
                    503,
                    "LOCAL_ENGINE_UNAVAILABLE",
                    "Set up the local music engine before continuing.",
                    "RETRY");
        Path dir = directory(id);
        Files.createDirectories(dir.getParent());
        Files.createDirectory(dir);
        Path input = dir.resolve("request.json"), output = dir.resolve("output");
        Files.writeString(input, json.writeValueAsString(request), StandardOpenOption.CREATE_NEW);
        ProcessBuilder builder =
                new ProcessBuilder(
                        python.toString(),
                        script.toString(),
                        "--request",
                        input.toString(),
                        "--output",
                        output.toString());
        builder.environment().put("DJOUND_LOCAL_DATA_ROOT", root.toString());
        for (String name :
                List.of(
                        "OMP_NUM_THREADS",
                        "OPENBLAS_NUM_THREADS",
                        "VECLIB_MAXIMUM_THREADS",
                        "NUMBA_NUM_THREADS")) builder.environment().put(name, "2");
        builder.redirectErrorStream(true).redirectOutput(dir.resolve("worker.log").toFile());
        Process process = builder.start();
        processes.put(id, process);
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(180);
        try {
            while (!process.waitFor(200, TimeUnit.MILLISECONDS)) {
                if (closing || cancelled.getAsBoolean())
                    throw new ApiProblem(
                            409,
                            "CANCELLED",
                            "Local work was cancelled; your saved audio and notes remain"
                                + " available.",
                            "EDIT_TEXT");
                if (System.nanoTime() > deadline)
                    throw new ApiProblem(
                            503,
                            "LOCAL_TIMEOUT",
                            "The local engine exceeded its time limit.",
                            "RETRY");
            }
            if (cancelled.getAsBoolean())
                throw new ApiProblem(409, "CANCELLED", "Local work was cancelled.", "EDIT_TEXT");
            Path result = output.resolve("result.json");
            if (!Files.isRegularFile(result) || Files.size(result) > 2_000_000)
                throw new ApiProblem(
                        503,
                        "LOCAL_WORKER_FAILED",
                        "The local engine did not return a valid result. Your input is saved.",
                        "RETRY");
            JsonNode response = json.readTree(Files.readString(result));
            if (process.exitValue() != 0 || !response.path("status").asText().equals("succeeded")) {
                String code = response.path("error").path("code").asText("LOCAL_WORKER_FAILED");
                String message =
                        response.path("error")
                                .path("message")
                                .asText("The local engine could not complete this step.");
                throw new ApiProblem(422, code, message, "EDIT_TEXT");
            }
            if (response.path("schemaVersion").asInt() != 1
                    || !response.path("operation").asText().equals(request.get("operation")))
                throw new ApiProblem(
                        422,
                        "OUTPUT_INVALID",
                        "The local result does not match this operation.",
                        "RETRY");
            return response;
        } finally {
            stop(process);
            processes.remove(id, process);
        }
    }

    public Path audio(String id, String value) throws java.io.IOException {
        Path output = directory(id).resolve("output").toRealPath();
        Path path = Path.of(value);
        if (!path.isAbsolute()) path = output.resolve(path);
        path = path.toRealPath();
        if (!path.startsWith(output) || !Files.isRegularFile(path))
            throw new ApiProblem(
                    422,
                    "OUTPUT_INVALID",
                    "The worker returned an invalid audio location.",
                    "RETRY");
        return path;
    }

    public void stop(String id) {
        Process p = processes.get(id);
        if (p != null) stop(p);
    }

    private static void stop(Process p) {
        var descendants = p.descendants().toList();
        p.destroy();
        try {
            if (!p.waitFor(3, TimeUnit.SECONDS)) p.destroyForcibly();
        } catch (InterruptedException e) {
            p.destroyForcibly();
            Thread.currentThread().interrupt();
        }
        for (var child : descendants) if (child.isAlive()) child.destroyForcibly();
        try {
            p.waitFor(3, TimeUnit.SECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    public void close() {
        closing = true;
        processes.values().forEach(LocalWorker::stop);
    }
}
