package com.djound.generation;

import com.djound.ApiProblem;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.io.*;
import java.net.*;
import java.net.http.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.*;

/**
 * Concrete anonymous Gradio 5.49.1 protocol. No retry, authentication, or alternate destination.
 */
public final class MelodyFlowTransport {
    public static final URI ORIGIN = URI.create("https://facebook-melodyflow.hf.space");
    private final URI origin;
    private final Duration observationDeadline;
    private final ObjectMapper json = new ObjectMapper();

    public MelodyFlowTransport() {
        this(ORIGIN, Duration.ofSeconds(120));
    }

    // Package-private injection is only for local protocol tests, never a runtime setting.
    MelodyFlowTransport(URI origin, Duration observationDeadline) {
        this.origin = origin;
        this.observationDeadline = observationDeadline;
    }

    public static void requireNoRetryLaunchProperties() {
        // Recovery can run in UNCONFIGURED, but must retain the same one-attempt transport policy.
        if (!"1".equals(System.getProperty("jdk.httpclient.redirects.retrylimit"))
                || !"true".equals(System.getProperty("jdk.httpclient.disableRetryConnect"))
                || !"false".equals(System.getProperty("jdk.httpclient.enableAllMethodRetry")))
            throw new IllegalStateException(
                    "Live transport requires JVM no-retry launch properties");
    }

    public static HttpClient client() {
        requireNoRetryLaunchProperties();
        return HttpClient.newBuilder()
                .version(HttpClient.Version.HTTP_1_1)
                .connectTimeout(Duration.ofSeconds(10))
                .followRedirects(HttpClient.Redirect.NEVER)
                .proxy(
                        new ProxySelector() {
                            public List<Proxy> select(URI uri) {
                                return List.of(Proxy.NO_PROXY);
                            }

                            public void connectFailed(
                                    URI uri, SocketAddress address, IOException failure) {}
                        })
                .build();
    }

    public String upload(Path path, String name, String contentType) throws Exception {
        String boundary = "djound-" + UUID.randomUUID();
        // Fixed multipart filename avoids injecting any user-controlled header bytes.
        byte[] prefix =
                ("--"
                                + boundary
                                + "\r\nContent-Disposition: form-data; name=\"files\"; filename=\""
                                + (contentType.equals("audio/wav") ? "source.wav" : "source.mp3")
                                + "\"\r\nContent-Type: "
                                + contentType
                                + "\r\n\r\n")
                        .getBytes(StandardCharsets.UTF_8);
        var body =
                HttpRequest.BodyPublishers.concat(
                        HttpRequest.BodyPublishers.ofByteArray(prefix),
                        HttpRequest.BodyPublishers.ofFile(path),
                        HttpRequest.BodyPublishers.ofString("\r\n--" + boundary + "--\r\n"));
        var result =
                json.readTree(
                        exchange(
                                "/gradio_api/upload",
                                body,
                                "multipart/form-data; boundary=" + boundary));
        if (!result.isArray() || result.size() != 1 || !result.get(0).isTextual())
            throw unknown("The source upload acknowledgement was malformed.");
        String uploaded = result.get(0).asString();
        if (!uploaded.startsWith("/tmp/gradio/")
                || uploaded.contains("\r")
                || uploaded.contains("\n")
                || uploaded.length() > 4096)
            throw unknown("The source upload locator was invalid.");
        return uploaded;
    }

    public String submit(
            String uploaded, String name, String type, String instructions, double duration)
            throws Exception {
        var file =
                Map.of(
                        "path",
                        uploaded,
                        "orig_name",
                        name,
                        "mime_type",
                        type,
                        "meta",
                        Map.of("_type", "gradio.FileData"));
        var payload =
                Map.of(
                        "data",
                        List.of(
                                "facebook/melodyflow-t24-30secs",
                                instructions,
                                "euler",
                                125,
                                0.0,
                                true,
                                0.2,
                                duration,
                                file));
        JsonNode result =
                json.readTree(
                        exchange(
                                "/gradio_api/call/predict",
                                HttpRequest.BodyPublishers.ofString(
                                        json.writeValueAsString(payload)),
                                "application/json"));
        String event = result.path("event_id").asString("");
        if (!event.matches("[A-Za-z0-9_-]{1,128}"))
            throw unknown("The generation acknowledgement did not contain a usable event handle.");
        return event;
    }

    private String exchange(String path, HttpRequest.BodyPublisher body, String type)
            throws Exception {
        long started = System.nanoTime();
        try (var client = client()) {
            var response =
                    client.send(
                            HttpRequest.newBuilder(origin.resolve(path))
                                    .timeout(Duration.ofSeconds(30))
                                    .header("Content-Type", type)
                                    .POST(body)
                                    .build(),
                            HttpResponse.BodyHandlers.ofInputStream());
            try (var in = response.body()) {
                if (response.statusCode() == 429)
                    throw new ApiProblem(
                            503,
                            "CAPACITY_UNAVAILABLE",
                            "The anonymous provider rejected the request because capacity is"
                                    + " unavailable.",
                            "WAIT");
                if (response.statusCode() != 200)
                    throw unknown("The provider did not acknowledge the request successfully.");
                return bounded(
                        in,
                        remaining(started, Duration.ofSeconds(30)),
                        () -> {
                            byte[] bytes = in.readNBytes(1024 * 1024 + 1);
                            if (bytes.length > 1024 * 1024)
                                throw unknown("The provider response exceeded its byte limit.");
                            return new String(bytes, StandardCharsets.UTF_8);
                        });
            } finally {
                client.shutdownNow();
            }
        }
    }

    /** Event streams are consumed once; caller persists observation-start before entering. */
    public String observe(String eventId) throws Exception {
        if (!eventId.matches("[A-Za-z0-9_-]{1,128}")) throw unknown("Invalid saved event handle.");
        long started = System.nanoTime();
        try (var client = client()) {
            var response =
                    client.send(
                            HttpRequest.newBuilder(
                                            origin.resolve("/gradio_api/call/predict/" + eventId))
                                    .timeout(observationDeadline)
                                    .header("Accept", "text/event-stream")
                                    .GET()
                                    .build(),
                            HttpResponse.BodyHandlers.ofInputStream());
            try (var in = response.body()) {
                if (response.statusCode() != 200)
                    throw unknown("The saved event could not be observed.");
                return bounded(in, remaining(started, observationDeadline), () -> readEvent(in));
            } finally {
                client.shutdownNow();
            }
        }
    }

    private String readEvent(InputStream in) throws Exception {
        String event = "";
        StringBuilder data = new StringBuilder();
        ByteArrayOutputStream line = new ByteArrayOutputStream();
        int count = 0;
        while (true) {
            int b = in.read();
            if (b < 0) throw unknown("The event stream ended without durable result evidence.");
            if (++count > 1024 * 1024) throw unknown("The event stream exceeded its byte limit.");
            if (b != '\n') {
                if (b != '\r') line.write(b);
                continue;
            }
            String value = line.toString(StandardCharsets.UTF_8);
            line.reset();
            if (value.isEmpty()) {
                if (event.equals("error"))
                    throw unknown(
                            "The provider reported an error without reliable execution evidence.");
                if (event.equals("complete")) {
                    JsonNode result = json.readTree(data.toString());
                    if (!result.isArray() || result.size() != 3)
                        throw unknown("The completed event did not contain three variations.");
                    JsonNode first = result.get(0);
                    if (!first.path("meta").path("_type").asString().equals("gradio.FileData"))
                        throw unknown("The first variation was not FileData.");
                    String url = first.path("url").asString("");
                    validateResult(url, origin);
                    return url;
                }
                event = "";
                data.setLength(0);
            } else if (value.startsWith("event:")) event = value.substring(6).strip();
            else if (value.startsWith("data:")) {
                if (!data.isEmpty()) data.append('\n');
                data.append(value.substring(5).stripLeading());
            }
        }
    }

    public static URI validateResult(String value) {
        return validateResult(value, ORIGIN);
    }

    static URI validateResult(String value, URI origin) {
        try {
            if (value == null) throw new IllegalArgumentException();
            URI uri = URI.create(value);
            if (uri.getScheme() == null || uri.getHost() == null)
                throw new IllegalArgumentException();
            int port =
                    uri.getPort() == -1
                            ? (uri.getScheme().equals("https") ? 443 : 80)
                            : uri.getPort();
            int expected =
                    origin.getPort() == -1
                            ? (origin.getScheme().equals("https") ? 443 : 80)
                            : origin.getPort();
            if (!Objects.equals(uri.getScheme(), origin.getScheme())
                    || !Objects.equals(uri.getHost(), origin.getHost())
                    || port != expected
                    || uri.getUserInfo() != null
                    || uri.getFragment() != null
                    || uri.getQuery() != null
                    || !uri.getRawPath().startsWith("/gradio_api/file=")
                    || uri.getRawPath().length() <= 17
                    || value.length() > 8192) throw new IllegalArgumentException();
            return uri;
        } catch (IllegalArgumentException e) {
            throw new ApiProblem(
                    422,
                    "OUTPUT_INVALID",
                    "The result locator is outside the approved provider file origin.",
                    "RETRY");
        }
    }

    private static Duration remaining(long started, Duration limit) {
        return Duration.ofNanos(Math.max(1, limit.toNanos() - (System.nanoTime() - started)));
    }

    private static <T> T bounded(InputStream input, Duration duration, Callable<T> action)
            throws Exception {
        var worker = Executors.newVirtualThreadPerTaskExecutor();
        var future = worker.submit(action);
        try {
            return future.get(duration.toMillis(), TimeUnit.MILLISECONDS);
        } catch (TimeoutException e) {
            input.close();
            future.cancel(true);
            throw unknown("The provider response exceeded its observation deadline.");
        } catch (ExecutionException e) {
            if (e.getCause() instanceof Exception cause) throw cause;
            throw new IllegalStateException(e.getCause());
        } finally {
            worker.shutdownNow();
        }
    }

    private static ApiProblem unknown(String detail) {
        return new ApiProblem(503, "OUTCOME_UNKNOWN", detail, "ACKNOWLEDGE_UNKNOWN_OUTCOME");
    }
}
