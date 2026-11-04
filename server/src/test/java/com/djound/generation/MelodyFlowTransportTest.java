package com.djound.generation;

import static org.junit.jupiter.api.Assertions.*;

import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;

import com.djound.ApiProblem;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.slf4j.LoggerFactory;

import tools.jackson.databind.ObjectMapper;

import java.io.*;
import java.net.*;
import java.net.http.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;

/** Actual loopback HTTP exchanges only. No test can select the production origin. */
class MelodyFlowTransportTest {
    @TempDir Path temporary;
    private final ObjectMapper json = new ObjectMapper();

    private static String fixture(String name) throws IOException {
        Path directory = Path.of("fixtures/provider/melodyflow");
        if (!Files.isDirectory(directory)) directory = Path.of("../fixtures/provider/melodyflow");
        return Files.readString(directory.resolve(name));
    }

    private record Received(
            String method, String path, Map<String, List<String>> headers, byte[] body) {}

    @FunctionalInterface
    private interface Handler {
        void handle(HttpExchange exchange, byte[] body) throws Exception;
    }

    private static final class LocalServer implements AutoCloseable {
        final HttpServer server;
        final ExecutorService executor = Executors.newVirtualThreadPerTaskExecutor();
        final List<Received> requests = new CopyOnWriteArrayList<>();
        final AtomicReference<Throwable> failure = new AtomicReference<>();
        final URI origin;

        LocalServer(Handler handler) throws IOException {
            server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            origin = URI.create("http://127.0.0.1:" + server.getAddress().getPort());
            server.setExecutor(executor);
            server.createContext(
                    "/",
                    exchange -> {
                        try {
                            byte[] body = exchange.getRequestBody().readAllBytes();
                            requests.add(
                                    new Received(
                                            exchange.getRequestMethod(),
                                            exchange.getRequestURI().getPath(),
                                            new TreeMap<>(exchange.getRequestHeaders()),
                                            body));
                            assertEquals("HTTP/1.1", exchange.getProtocol());
                            assertNull(exchange.getRequestHeaders().getFirst("Authorization"));
                            assertNull(exchange.getRequestHeaders().getFirst("Cookie"));
                            assertNull(
                                    exchange.getRequestHeaders().getFirst("Proxy-Authorization"));
                            handler.handle(exchange, body);
                        } catch (InterruptedException expectedDuringClose) {
                            Thread.currentThread().interrupt();
                        } catch (Throwable error) {
                            failure.compareAndSet(null, error);
                        } finally {
                            exchange.close();
                        }
                    });
            server.start();
        }

        public void close() {
            server.stop(0);
            executor.shutdownNow();
            assertNull(failure.get(), () -> "Local endpoint failed: " + failure.get());
        }
    }

    private static void reply(HttpExchange exchange, int status, String body) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, bytes.length == 0 ? -1 : bytes.length);
        if (bytes.length > 0) exchange.getResponseBody().write(bytes);
    }

    @Test
    void uploadAndPredictionMatchVerifiedShapeAndFragmentedUtf8SseSelectsOnlyVariationOne()
            throws Exception {
        byte[] audio = new byte[] {'R', 'I', 'F', 'F', 0, 1, 2, (byte) 255, '\r', '\n', 0};
        Path source = temporary.resolve("source.wav");
        Files.write(source, audio);
        AtomicReference<URI> origin = new AtomicReference<>();
        try (var endpoint =
                new LocalServer(
                        (exchange, body) -> {
                            switch (exchange.getRequestURI().getPath()) {
                                case "/gradio_api/upload" ->
                                        reply(exchange, 200, fixture("upload-response.json"));
                                case "/gradio_api/call/predict" ->
                                        reply(exchange, 200, fixture("predict-response.json"));
                                case "/gradio_api/queue/data" -> {
                                    assertEquals(
                                            "session_hash=fixture-event-one",
                                            exchange.getRequestURI().getRawQuery());
                                    assertEquals(
                                            "text/event-stream",
                                            exchange.getRequestHeaders().getFirst("Accept"));
                                    exchange.getResponseHeaders()
                                            .set(
                                                    "Content-Type",
                                                    "text/event-stream; charset=utf-8");
                                    exchange.sendResponseHeaders(200, 0);
                                    String frames =
                                            fixture("queue-heartbeat.sse")
                                                    + fixture("queue-complete.sse")
                                                            .replace(
                                                                    "{{origin}}",
                                                                    origin.get().toString())
                                                            .replace(
                                                                    "fixture-one/audio.wav",
                                                                    "fixture-one/café.wav");
                                    byte[] bytes = frames.getBytes(StandardCharsets.UTF_8);
                                    for (int offset = 0; offset < bytes.length; offset += 3) {
                                        exchange.getResponseBody()
                                                .write(
                                                        bytes,
                                                        offset,
                                                        Math.min(3, bytes.length - offset));
                                        exchange.getResponseBody().flush();
                                    }
                                }
                                default ->
                                        throw new AssertionError(
                                                "Unexpected request " + exchange.getRequestURI());
                            }
                        })) {
            origin.set(endpoint.origin);
            var transport = new MelodyFlowTransport(endpoint.origin, Duration.ofSeconds(2));
            String uploaded = transport.upload(source, "ignored\r\nInjected: yes.wav", "audio/wav");
            assertEquals("/tmp/gradio/fixture-upload/source.wav", uploaded);
            String instructions = "Warm guitar.\nKeep the melody: café.";
            String event =
                    transport.submit(uploaded, "source.wav", "audio/wav", instructions, 12.0);
            assertEquals("fixture-event-one", event);
            assertEquals(
                    endpoint.origin + "/gradio_api/file=/tmp/gradio/fixture-one/café.wav",
                    transport.observe(event));
            assertEquals(3, endpoint.requests.size());
            var upload = endpoint.requests.get(0);
            assertEquals("POST", upload.method());
            String multipart = new String(upload.body(), StandardCharsets.ISO_8859_1);
            assertTrue(multipart.contains("name=\"files\"; filename=\"source.wav\""));
            assertFalse(multipart.contains("Injected"));
            int offset = multipart.indexOf("\r\n\r\n") + 4;
            assertTrue(offset > 3);
            assertArrayEquals(
                    audio, Arrays.copyOfRange(upload.body(), offset, offset + audio.length));
            var prediction = json.readTree(endpoint.requests.get(1).body()).path("data");
            var expected =
                    json.readTree(
                                    fixture("predict-request.json")
                                            .replace("{{instructions}}", "unused"))
                            .path("data");
            assertEquals(9, prediction.size());
            assertEquals(instructions, prediction.get(1).asString());
            for (int i : new int[] {0, 2, 3, 4, 5, 6, 7, 8})
                assertEquals(expected.get(i), prediction.get(i));
            assertEquals("GET", endpoint.requests.get(2).method());
            assertTrue(
                    endpoint.requests.stream()
                            .noneMatch(r -> r.path().startsWith("/gradio_api/file=")));
        }
    }

    @Test
    void providerErrorDiagnosticIsBoundedSanitizedAndPrivateWithoutRetrying() throws Exception {
        Logger logger = (Logger) LoggerFactory.getLogger(MelodyFlowTransport.class);
        ListAppender<ILoggingEvent> capture = new ListAppender<>();
        capture.start();
        logger.addAppender(capture);
        try {
            for (String data :
                    List.of(
                            "null",
                            "Provider failure\nline\t\u001b\u202e\u2028" + "🎵".repeat(2200))) {
                String frame =
                        "data: "
                                + json.writeValueAsString(
                                        Map.of(
                                                "msg", "process_completed",
                                                "event_id", "diagnostic-error-event",
                                                "success", false,
                                                "output", Map.of("error", data)))
                                + "\n\n";
                try (var endpoint =
                        new LocalServer((exchange, body) -> reply(exchange, 200, frame))) {
                    var transport = new MelodyFlowTransport(endpoint.origin, Duration.ofSeconds(1));
                    ApiProblem problem =
                            assertThrows(
                                    ApiProblem.class,
                                    () -> transport.observe("diagnostic-error-event"));
                    assertEquals("OUTCOME_UNKNOWN", problem.code());
                    assertEquals(
                            "The provider reported an error without reliable execution evidence.",
                            problem.getMessage());
                    assertEquals(1, endpoint.requests.size());
                    assertEquals("GET", endpoint.requests.getFirst().method());
                    assertEquals(
                            "/gradio_api/queue/data",
                            endpoint.requests.getFirst().path());
                }
            }
            assertEquals(2, capture.list.size());
            assertEquals("diagnostic-error-event", capture.list.getFirst().getArgumentArray()[0]);
            assertEquals("null", capture.list.getFirst().getArgumentArray()[1]);
            assertEquals(false, capture.list.getFirst().getArgumentArray()[2]);
            Object[] arguments = capture.list.getLast().getArgumentArray();
            assertEquals("diagnostic-error-event", arguments[0]);
            String diagnostic = (String) arguments[1];
            assertTrue(diagnostic.startsWith("Provider failure line    "));
            assertEquals(2048, diagnostic.codePointCount(0, diagnostic.length()));
            assertFalse(
                    diagnostic
                            .codePoints()
                            .anyMatch(
                                    codePoint ->
                                            Character.isISOControl(codePoint)
                                                    || Character.getType(codePoint)
                                                            == Character.FORMAT
                                                    || Character.getType(codePoint)
                                                            == Character.LINE_SEPARATOR
                                                    || Character.getType(codePoint)
                                                            == Character.PARAGRAPH_SEPARATOR));
            assertEquals(true, arguments[2]);
        } finally {
            logger.detachAppender(capture);
            capture.stop();
        }
    }

    @Test
    void errorMalformedAndDrainedStreamsCannotInventResultEvidence() throws Exception {
        for (String frame :
                List.of(
                        fixture("queue-error.sse"),
                        fixture("queue-complete.sse").replace("fixture-event-one", "wrong-event"),
                        fixture("queue-complete.sse").replace("\"success\":true,", ""),
                        fixture("queue-complete.sse").replace("\"success\":true", "\"success\":false"),
                        "data: {\"msg\":\"close_stream\"}\n\n",
                        "data: {\"msg\":\"unexpected_error\",\"message\":\"Queue stopped\"}\n\n",
                        "data: {\"msg\":\"process_completed\",\"event_id\":\"fixture-event-one\",\"success\":true,\"output\":{\"data\":[]}}\n\n",
                        "data: not-json\n\n",
                        fixture("queue-heartbeat.sse"))) {
            try (var endpoint = new LocalServer((exchange, body) -> reply(exchange, 200, frame))) {
                var transport = new MelodyFlowTransport(endpoint.origin, Duration.ofMillis(300));
                assertThrows(Exception.class, () -> transport.observe("fixture-event-one"));
                assertEquals(
                        1, endpoint.requests.size(), "Observation never retries a consumed stream");
            }
        }
        try (var endpoint =
                new LocalServer(
                        (exchange, body) -> {
                            exchange.getResponseHeaders().set("Content-Type", "text/event-stream");
                            exchange.sendResponseHeaders(200, 0);
                            exchange.getResponseBody()
                                    .write(
                                            fixture("queue-heartbeat.sse")
                                                    .getBytes(StandardCharsets.UTF_8));
                            exchange.getResponseBody().flush();
                            Thread.sleep(5000);
                        })) {
            var transport = new MelodyFlowTransport(endpoint.origin, Duration.ofMillis(200));
            assertTimeoutPreemptively(
                    Duration.ofSeconds(3),
                    () -> {
                        ApiProblem problem =
                                assertThrows(
                                        ApiProblem.class, () -> transport.observe("drained-event"));
                        assertEquals("OUTCOME_UNKNOWN", problem.code());
                    });
            assertEquals(1, endpoint.requests.size());
        }
    }

    @Test
    void malformedAcknowledgementsAndRedirectsAreNotRetriedOrFollowed() throws Exception {
        for (String response : List.of("{}", "{\"event_id\":\"../elsewhere\"}", "[]")) {
            try (var endpoint =
                    new LocalServer((exchange, body) -> reply(exchange, 200, response))) {
                ApiProblem problem =
                        assertThrows(
                                ApiProblem.class,
                                () ->
                                        new MelodyFlowTransport(
                                                        endpoint.origin, Duration.ofSeconds(1))
                                                .submit(
                                                        "/tmp/gradio/source.wav",
                                                        "source.wav",
                                                        "audio/wav",
                                                        "Exact words",
                                                        12));
                assertEquals("OUTCOME_UNKNOWN", problem.code());
                assertEquals(1, endpoint.requests.size());
            }
        }
        try (var endpoint =
                new LocalServer(
                        (exchange, body) -> {
                            exchange.getResponseHeaders().set("Location", "/redirect-target");
                            reply(exchange, 302, "");
                        })) {
            ApiProblem problem =
                    assertThrows(
                            ApiProblem.class,
                            () ->
                                    new MelodyFlowTransport(endpoint.origin, Duration.ofSeconds(1))
                                            .submit(
                                                    "/tmp/gradio/source.wav",
                                                    "source.wav",
                                                    "audio/wav",
                                                    "Exact words",
                                                    12));
            assertEquals("OUTCOME_UNKNOWN", problem.code());
            assertEquals(1, endpoint.requests.size());
            assertEquals("/gradio_api/call/predict", endpoint.requests.getFirst().path());
        }
    }

    @Test
    void resultLocatorsRequireExactOriginAndFileRoute() throws Exception {
        URI origin = URI.create("http://127.0.0.1:12345");
        String valid = origin + "/gradio_api/file=/tmp/gradio/fixture-one/audio.wav";
        assertEquals(URI.create(valid), MelodyFlowTransport.validateResult(valid, origin));
        for (String invalid :
                List.of(
                        "",
                        "/relative",
                        "https://example.invalid/gradio_api/file=x",
                        "http://127.0.0.1:12346/gradio_api/file=x",
                        "http://user@127.0.0.1:12345/gradio_api/file=x",
                        origin + "/other/file=x",
                        valid + "?redirect=elsewhere",
                        valid + "#fragment",
                        origin + "/gradio_api/file=")) {
            ApiProblem problem =
                    assertThrows(
                            ApiProblem.class,
                            () -> MelodyFlowTransport.validateResult(invalid, origin),
                            invalid);
            assertEquals("OUTPUT_INVALID", problem.code());
        }
        try (var endpoint =
                new LocalServer(
                        (exchange, body) ->
                                reply(
                                        exchange,
                                        200,
                                        fixture("queue-complete.sse")
                                                .replace("fixture-event-one", "foreign-result")
                                                .replace(
                                                        "{{origin}}",
                                                        "https://example.invalid")))) {
            ApiProblem problem =
                    assertThrows(
                            ApiProblem.class,
                            () ->
                                    new MelodyFlowTransport(endpoint.origin, Duration.ofSeconds(1))
                                            .observe("foreign-result"));
            assertEquals("OUTPUT_INVALID", problem.code());
            assertEquals(1, endpoint.requests.size());
        }
    }

    @Test
    void fullBodyThenFinOrResetNeverRepeatsGenerationPostAndNegativeControlDetectsReplay()
            throws Exception {
        assertEquals(1, droppedPosts(false, false));
        assertEquals(1, droppedPosts(true, false));
        assertTrue(
                droppedPosts(false, true) > 1, "The offline negative control must detect a replay");
    }

    private int droppedPosts(boolean reset, boolean retryEnabled) throws Exception {
        AtomicInteger posts = new AtomicInteger();
        AtomicReference<Throwable> failure = new AtomicReference<>();
        List<byte[]> bodies = new CopyOnWriteArrayList<>();
        try (ServerSocket socket = new ServerSocket(0, 10, InetAddress.getByName("127.0.0.1"))) {
            Thread receiver =
                    Thread.ofPlatform()
                            .start(
                                    () -> {
                                        try {
                                            while (!socket.isClosed()) {
                                                try (Socket connection = socket.accept()) {
                                                    connection.setSoTimeout(3000);
                                                    InputStream in = connection.getInputStream();
                                                    ByteArrayOutputStream header =
                                                            new ByteArrayOutputStream();
                                                    int state = 0;
                                                    while (state < 4 && header.size() < 16384) {
                                                        int b = in.read();
                                                        if (b < 0) throw new EOFException();
                                                        header.write(b);
                                                        state =
                                                                b == (state % 2 == 0 ? 13 : 10)
                                                                        ? state + 1
                                                                        : 0;
                                                    }
                                                    String text =
                                                            header.toString(
                                                                    StandardCharsets.US_ASCII);
                                                    assertTrue(
                                                            text.startsWith(
                                                                    "POST /gradio_api/call/predict"
                                                                            + " HTTP/1.1"));
                                                    int length =
                                                            Arrays.stream(text.split("\r\n"))
                                                                    .filter(
                                                                            line ->
                                                                                    line.toLowerCase(
                                                                                                    Locale
                                                                                                            .ROOT)
                                                                                            .startsWith(
                                                                                                    "content-length:"))
                                                                    .mapToInt(
                                                                            line ->
                                                                                    Integer
                                                                                            .parseInt(
                                                                                                    line.substring(
                                                                                                                    line
                                                                                                                                    .indexOf(
                                                                                                                                            ':')
                                                                                                                            + 1)
                                                                                                            .trim()))
                                                                    .findFirst()
                                                                    .orElseThrow();
                                                    byte[] body = in.readNBytes(length);
                                                    assertEquals(length, body.length);
                                                    assertTrue(
                                                            new String(body, StandardCharsets.UTF_8)
                                                                    .contains(
                                                                            "generation-body-fully-consumed"));
                                                    bodies.add(body);
                                                    posts.incrementAndGet();
                                                    if (reset) connection.setSoLinger(true, 0);
                                                }
                                            }
                                        } catch (SocketException closed) {
                                            if (!socket.isClosed()) failure.set(closed);
                                        } catch (Throwable error) {
                                            failure.set(error);
                                        }
                                    });
            Path output = temporary.resolve("drop-" + reset + "-" + retryEnabled + ".log");
            List<String> command =
                    List.of(
                            Path.of(System.getProperty("java.home"), "bin/java").toString(),
                            "-Djdk.httpclient.redirects.retrylimit=" + (retryEnabled ? "5" : "1"),
                            "-Djdk.httpclient.disableRetryConnect=true",
                            "-Djdk.httpclient.enableAllMethodRetry=" + retryEnabled,
                            "-cp",
                            System.getProperty(
                                    "surefire.test.class.path",
                                    System.getProperty("java.class.path")),
                            DropPostProbe.class.getName(),
                            "http://127.0.0.1:" + socket.getLocalPort());
            Process child =
                    new ProcessBuilder(command)
                            .redirectErrorStream(true)
                            .redirectOutput(output.toFile())
                            .start();
            try {
                assertTrue(child.waitFor(12, TimeUnit.SECONDS), "Drop probe exceeded its bound");
                assertEquals(0, child.exitValue(), Files.readString(output));
            } finally {
                if (child.isAlive()) child.destroyForcibly().waitFor();
                socket.close();
                receiver.join(4000);
            }
            assertFalse(receiver.isAlive());
            assertNull(failure.get(), () -> "Drop endpoint failed: " + failure.get());
            assertFalse(bodies.isEmpty());
            for (byte[] body : bodies) assertArrayEquals(bodies.getFirst(), body);
            return posts.get();
        }
    }

    /** A fresh JVM makes the documented launch-time retry properties effective and testable. */
    public static class DropPostProbe {
        public static void main(String[] args) throws Exception {
            URI origin = URI.create(args[0]);
            if (!"127.0.0.1".equals(origin.getHost()))
                throw new IllegalArgumentException("Loopback only");
            try {
                if (Boolean.getBoolean("jdk.httpclient.enableAllMethodRetry")) {
                    // Intentionally bypass the production safety guard for this offline negative
                    // control.
                    try (var client =
                            HttpClient.newBuilder()
                                    .version(HttpClient.Version.HTTP_1_1)
                                    .followRedirects(HttpClient.Redirect.NEVER)
                                    .proxy(
                                            new ProxySelector() {
                                                public List<Proxy> select(URI uri) {
                                                    return List.of(Proxy.NO_PROXY);
                                                }

                                                public void connectFailed(
                                                        URI uri,
                                                        SocketAddress address,
                                                        IOException error) {}
                                            })
                                    .build()) {
                        client.send(
                                HttpRequest.newBuilder(origin.resolve("/gradio_api/call/predict"))
                                        .timeout(Duration.ofSeconds(3))
                                        .POST(
                                                HttpRequest.BodyPublishers.ofString(
                                                        "{\"data\":[\"generation-body-fully-consumed\"]}"))
                                        .build(),
                                HttpResponse.BodyHandlers.discarding());
                    }
                } else {
                    new MelodyFlowTransport(origin, Duration.ofSeconds(1))
                            .submit(
                                    "/tmp/gradio/source.wav",
                                    "source.wav",
                                    "audio/wav",
                                    "generation-body-fully-consumed",
                                    12);
                }
                throw new AssertionError("The endpoint deliberately sends no response");
            } catch (IOException expected) {
                System.out.println(
                        "Expected dropped response: " + expected.getClass().getSimpleName());
            }
        }
    }
}
