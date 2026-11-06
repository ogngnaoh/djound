package com.djound.generation;

import com.djound.ApiProblem;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.io.*;
import java.net.*;
import java.net.http.*;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.*;

/** Read-only Gradio protocol for acknowledged historical results. */
public final class MelodyFlowTransport {
    private static final Logger log = LoggerFactory.getLogger(MelodyFlowTransport.class);
    private static final int ERROR_DIAGNOSTIC_LIMIT = 2048;
    private static final java.util.regex.Pattern QUOTA_EXHAUSTED =
            java.util.regex.Pattern.compile(
                    "^You have exceeded your ZeroGPU quota \\([0-9]+s requested vs\\. 0s"
                        + " left\\)\\.");
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

    public String observe(String eventId) throws Exception {
        if (!eventId.matches("[A-Za-z0-9_-]{1,128}")) throw unknown("Invalid saved event handle.");
        long started = System.nanoTime();
        try (var client = client()) {
            var response =
                    client.send(
                            HttpRequest.newBuilder(
                                            origin.resolve(
                                                    "/gradio_api/queue/data?session_hash="
                                                            + eventId))
                                    .timeout(observationDeadline)
                                    .header("Accept", "text/event-stream")
                                    .GET()
                                    .build(),
                            HttpResponse.BodyHandlers.ofInputStream());
            try (var in = response.body()) {
                if (response.statusCode() != 200)
                    throw unknown("The saved event could not be observed.");
                return bounded(
                        in, remaining(started, observationDeadline), () -> readEvent(in, eventId));
            } finally {
                client.shutdownNow();
            }
        }
    }

    private String readEvent(InputStream in, String eventId) throws Exception {
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
                if (data.isEmpty()) continue;
                JsonNode message = json.readTree(data.toString());
                data.setLength(0);
                String kind = message.path("msg").asString("");
                if (kind.equals("unexpected_error")) {
                    logProviderError(
                            eventId, message.path("message").asString("No diagnostic supplied"));
                    throw unknown(
                            "The provider reported an error without reliable execution evidence.");
                }
                if (kind.equals("close_stream"))
                    throw unknown("The event stream closed without durable result evidence.");
                if (kind.equals("process_completed") || kind.equals("process_generating")) {
                    if (!eventId.equals(message.path("event_id").asString("")))
                        throw unknown("The observed event did not match its saved handle.");
                    if (!message.path("success").isBoolean())
                        throw unknown("The event omitted its completion status.");
                    if (!message.path("success").asBoolean()) {
                        JsonNode diagnostic = message.path("output").path("error");
                        logProviderError(
                                eventId,
                                diagnostic.isTextual()
                                        ? diagnostic.asString()
                                        : diagnostic.toString());
                        if (diagnostic.isTextual()
                                && QUOTA_EXHAUSTED.matcher(diagnostic.asString()).find())
                            throw new ApiProblem(
                                    503,
                                    "CAPACITY_UNAVAILABLE",
                                    "MelodyFlow's anonymous GPU quota is exhausted. Your recordings"
                                        + " and draft remain saved. No reliable reset time was"
                                        + " supplied.",
                                    "WAIT");
                        throw unknown(
                                "The provider reported an error without reliable execution"
                                    + " evidence.");
                    }
                    if (kind.equals("process_completed")) {
                        JsonNode result = message.path("output").path("data");
                        if (!result.isArray() || result.size() != 3)
                            throw unknown("The completed event did not contain three variations.");
                        JsonNode first = result.get(0);
                        if (!first.path("meta").path("_type").asString().equals("gradio.FileData"))
                            throw unknown("The first variation was not FileData.");
                        String url = first.path("url").asString("");
                        validateResult(url, origin);
                        return url;
                    }
                }
            } else if (value.startsWith("data:")) {
                if (!data.isEmpty()) data.append('\n');
                data.append(value.substring(5).stripLeading());
            }
        }
    }

    /**
     * Local diagnostics only: never attach upstream data to public errors or log request bodies.
     */
    private static void logProviderError(String eventId, String data) {
        StringBuilder diagnostic = new StringBuilder();
        data.codePoints()
                .limit(ERROR_DIAGNOSTIC_LIMIT)
                .forEach(
                        codePoint -> {
                            int type = Character.getType(codePoint);
                            diagnostic.appendCodePoint(
                                    Character.isISOControl(codePoint)
                                                    || type == Character.FORMAT
                                                    || type == Character.LINE_SEPARATOR
                                                    || type == Character.PARAGRAPH_SEPARATOR
                                            ? ' '
                                            : codePoint);
                        });
        log.warn(
                "MelodyFlow event error: eventId={} payload={} truncated={}",
                eventId,
                diagnostic.toString(),
                data.codePointCount(0, data.length()) > ERROR_DIAGNOSTIC_LIMIT);
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
