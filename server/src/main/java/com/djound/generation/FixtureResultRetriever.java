package com.djound.generation;

import com.djound.*;

import org.springframework.core.io.ClassPathResource;
import org.springframework.stereotype.Component;

import java.io.*;
import java.net.*;
import java.net.http.*;
import java.nio.file.*;
import java.time.Duration;
import java.util.List;
import java.util.concurrent.*;

@Component
public class FixtureResultRetriever {
    private final FixtureCheckpoints checkpoints;
    private final URI liveOrigin;

    @org.springframework.beans.factory.annotation.Autowired
    public FixtureResultRetriever(FixtureCheckpoints checkpoints) {
        this(checkpoints, MelodyFlowTransport.ORIGIN);
    }

    FixtureResultRetriever(FixtureCheckpoints checkpoints, URI liveOrigin) {
        this.checkpoints = checkpoints;
        this.liveOrigin = liveOrigin;
    }

    public void retrieve(
            FixtureOperations.Operation operation, Path destination, Models.Attempt attempt)
            throws Exception {
        if (operation.resultUrl() == null || operation.resultUrl().isEmpty()) {
            var resource = new ClassPathResource("fixtures/audio/" + operation.fileName());
            try (var input = resource.getInputStream();
                    var output =
                            Files.newOutputStream(destination, StandardOpenOption.CREATE_NEW)) {
                copy(input, output, resource.contentLength(), attempt);
            }
            return;
        }
        URI uri =
                attempt.request().provenance().equals("LIVE")
                        ? MelodyFlowTransport.validateResult(operation.resultUrl(), liveOrigin)
                        : FixtureExecutionSettings.validateUrl(operation.resultUrl());
        var client =
                HttpClient.newBuilder()
                        .version(HttpClient.Version.HTTP_1_1)
                        .connectTimeout(Duration.ofSeconds(2))
                        .followRedirects(HttpClient.Redirect.NEVER)
                        .proxy(
                                new ProxySelector() {
                                    public List<Proxy> select(URI ignored) {
                                        return List.of(Proxy.NO_PROXY);
                                    }

                                    public void connectFailed(
                                            URI ignored,
                                            SocketAddress address,
                                            IOException error) {}
                                })
                        .build();
        HttpResponse<InputStream> response;
        try {
            response =
                    client.send(
                            HttpRequest.newBuilder(uri)
                                    .timeout(Duration.ofSeconds(5))
                                    .GET()
                                    .build(),
                            HttpResponse.BodyHandlers.ofInputStream());
        } catch (IOException error) {
            client.shutdownNow();
            throw invalid("The local result endpoint could not return a complete response.");
        }
        try (var input = response.body()) {
            if (response.statusCode() != 200)
                throw invalid(
                        "The local result endpoint did not return a complete successful response.");
            if (!response.headers()
                    .firstValue("Content-Encoding")
                    .orElse("identity")
                    .equalsIgnoreCase("identity"))
                throw invalid("Encoded result bodies are not supported.");
            long declared;
            try {
                declared = response.headers().firstValueAsLong("Content-Length").orElse(-1);
            } catch (NumberFormatException error) {
                throw invalid("The result declared an invalid byte length.");
            }
            if (declared > MediaStore.MAX_BYTES || declared < -1)
                throw invalid("The result exceeds the allowed byte limit.");
            ExecutorService worker = Executors.newVirtualThreadPerTaskExecutor();
            try (var output = Files.newOutputStream(destination, StandardOpenOption.CREATE_NEW)) {
                Future<?> copying =
                        worker.submit(
                                () -> {
                                    try {
                                        copy(input, output, declared, attempt);
                                    } catch (Exception error) {
                                        throw new CompletionException(error);
                                    }
                                });
                try {
                    copying.get(10, TimeUnit.SECONDS);
                } catch (TimeoutException error) {
                    input.close();
                    copying.cancel(true);
                    throw invalid("The result body exceeded its ten-second download deadline.");
                } catch (ExecutionException error) {
                    Throwable cause = error.getCause();
                    if (cause instanceof CompletionException && cause.getCause() != null)
                        cause = cause.getCause();
                    if (cause instanceof Exception exception) throw exception;
                    throw new IllegalStateException(cause);
                } finally {
                    worker.shutdownNow();
                }
            }
        } finally {
            client.shutdownNow();
        }
    }

    private void copy(InputStream input, OutputStream output, long declared, Models.Attempt attempt)
            throws Exception {
        long count = 0;
        boolean marked = false;
        byte[] buffer = new byte[32768];
        while (true) {
            int n;
            try {
                n = input.read(buffer);
            } catch (IOException error) {
                throw invalid("The result transfer ended before its complete body arrived.");
            }
            if (n < 0) break;
            if (Thread.currentThread().isInterrupted())
                throw new InterruptedException("Download interrupted");
            count += n;
            if (count > MediaStore.MAX_BYTES)
                throw invalid("The result exceeds the 64 MiB byte limit.");
            output.write(buffer, 0, n);
            if (!marked && n > 0) {
                marked = true;
                output.flush();
                checkpoints.reach("DURING_DOWNLOAD", attempt);
            }
        }
        if (declared >= 0 && count != declared)
            throw invalid("The received result length did not match its declared length.");
        if (count == 0) throw invalid("The result body was empty.");
    }

    private static ApiProblem invalid(String detail) {
        return new ApiProblem(422, "OUTPUT_INVALID", detail, "RETRY");
    }
}
