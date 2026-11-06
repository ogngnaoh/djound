package com.djound.generation;

import static org.junit.jupiter.api.Assertions.*;

import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;

import com.djound.ApiProblem;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;

import tools.jackson.databind.ObjectMapper;

import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;

/** Actual loopback HTTP exchanges only. No test can select the production origin. */
class MelodyFlowTransportTest {
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
    void fragmentedUtf8SseSelectsOnlyTheFirstExistingVariation() throws Exception {
        AtomicReference<URI> origin = new AtomicReference<>();
        try (var endpoint =
                new LocalServer(
                        (exchange, body) -> {
                            switch (exchange.getRequestURI().getPath()) {
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
            assertEquals(
                    endpoint.origin + "/gradio_api/file=/tmp/gradio/fixture-one/café.wav",
                    transport.observe("fixture-event-one"));
            assertEquals(1, endpoint.requests.size());
            assertEquals("GET", endpoint.requests.getFirst().method());
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
                                                "msg",
                                                "process_completed",
                                                "event_id",
                                                "diagnostic-error-event",
                                                "success",
                                                false,
                                                "output",
                                                Map.of("error", data)))
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
                    assertEquals("/gradio_api/queue/data", endpoint.requests.getFirst().path());
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
    void confirmedZeroGpuExhaustionIsCapacityWithoutInventingAResetOrRetry() throws Exception {
        try (var endpoint =
                new LocalServer(
                        (exchange, body) ->
                                reply(exchange, 200, fixture("queue-quota-exhausted.sse")))) {
            var transport = new MelodyFlowTransport(endpoint.origin, Duration.ofSeconds(1));
            ApiProblem problem =
                    assertThrows(ApiProblem.class, () -> transport.observe("fixture-event-one"));
            assertEquals("CAPACITY_UNAVAILABLE", problem.code());
            assertTrue(problem.getMessage().contains("anonymous GPU quota is exhausted"));
            assertFalse(problem.getMessage().contains("0:00:00"));
            assertFalse(problem.getMessage().contains("token"));
            assertEquals(1, endpoint.requests.size());
            assertEquals("GET", endpoint.requests.getFirst().method());
        }
        // A vaguely quota-related model failure is still uncertain.
        try (var endpoint =
                new LocalServer(
                        (exchange, body) ->
                                reply(
                                        exchange,
                                        200,
                                        fixture("queue-quota-exhausted.sse")
                                                .replace(
                                                        "You have exceeded your ZeroGPU quota",
                                                        "Model failed while checking quota")))) {
            ApiProblem problem =
                    assertThrows(
                            ApiProblem.class,
                            () ->
                                    new MelodyFlowTransport(endpoint.origin, Duration.ofSeconds(1))
                                            .observe("fixture-event-one"));
            assertEquals("OUTCOME_UNKNOWN", problem.code());
            assertEquals(1, endpoint.requests.size());
        }
    }

    @Test
    void errorMalformedAndDrainedStreamsCannotInventResultEvidence() throws Exception {
        for (String frame :
                List.of(
                        fixture("queue-error.sse"),
                        fixture("queue-complete.sse").replace("fixture-event-one", "wrong-event"),
                        fixture("queue-complete.sse").replace("\"success\":true,", ""),
                        fixture("queue-complete.sse")
                                .replace("\"success\":true", "\"success\":false"),
                        "data: {\"msg\":\"close_stream\"}\n\n",
                        "data: {\"msg\":\"unexpected_error\",\"message\":\"Queue stopped\"}\n\n",
                        "data:"
                            + " {\"msg\":\"process_completed\",\"event_id\":\"fixture-event-one\",\"success\":true,\"output\":{\"data\":[]}}\n\n",
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
    void observationRedirectsAreNotRetriedOrFollowed() throws Exception {
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
                                            .observe("saved-event"));
            assertEquals("OUTCOME_UNKNOWN", problem.code());
            assertEquals(1, endpoint.requests.size());
            assertEquals("/gradio_api/queue/data", endpoint.requests.getFirst().path());
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
}
