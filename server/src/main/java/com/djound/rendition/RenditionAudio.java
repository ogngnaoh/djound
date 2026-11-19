package com.djound.rendition;

import com.djound.ApiProblem;
import com.djound.Installation;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.*;
import java.util.*;
import java.util.concurrent.*;

import javax.sound.sampled.*;

@Component
final class RenditionAudio {
    static final int RATE = 44100;
    private static final Logger log = LoggerFactory.getLogger(RenditionAudio.class);
    private static final String PROGRESS_ENV = "DJOUND_RENDITION_PROGRESS";

    record Wave(int frames, String hash) {}

    private final Path directory, python, script;
    private final String ffmpeg;
    private final long timeout;
    private final ObjectMapper json = new ObjectMapper();

    RenditionAudio(
            Installation installation,
            @Value("${djound.local-python}") String python,
            @Value("${djound.rendition-worker}") String script,
            @Value("${djound.ffmpeg}") String ffmpeg,
            @Value("${djound.rendition-timeout-seconds}") long timeout)
            throws IOException {
        this.directory = Files.createDirectories(installation.rootPath().resolve("renditions"));
        this.python = Path.of(python).toAbsolutePath().normalize();
        this.script = Path.of(script).toAbsolutePath().normalize();
        this.ffmpeg = ffmpeg;
        this.timeout = timeout;
    }

    Path file(String id) {
        return directory.resolve(UUID.fromString(id) + ".wav");
    }

    Path pending(String take) {
        // The renderer creates its output exclusively. Each database take owns one fresh path.
        return directory.resolve(UUID.fromString(take) + ".pending.wav");
    }

    private Path progressFile(String id) {
        return directory.resolve(UUID.fromString(id) + ".stage");
    }

    String progress(String id) {
        Path file = progressFile(id);
        if (!Files.isRegularFile(file, LinkOption.NOFOLLOW_LINKS)) return null;
        try (var input = Files.newInputStream(file, LinkOption.NOFOLLOW_LINKS)) {
            byte[] data = input.readNBytes(17);
            if (data.length > 16) return null;
            return switch (new String(data, StandardCharsets.US_ASCII).strip()) {
                case "planning" -> "Planning your take…";
                case "melody" -> "Learning the tune…";
                case "arranging" -> "Arranging the instruments…";
                case "rendering" -> "Rendering the performance…";
                case "finishing" -> "Finishing the take…";
                default -> null;
            };
        } catch (IOException e) {
            return null;
        }
    }

    void clearProgress(String id) {
        try {
            Files.deleteIfExists(progressFile(id));
        } catch (IOException e) {
            log.debug("Could not remove rendition progress for {}", id, e);
        }
    }

    Path temporary() throws IOException {
        return Files.createTempFile(directory, "writing-", ".wav");
    }

    void decode(Path input, Path output) throws IOException, InterruptedException {
        Process process;
        try {
            process =
                    new ProcessBuilder(
                                    ffmpeg,
                                    "-nostdin",
                                    "-loglevel",
                                    "error",
                                    "-y",
                                    "-protocol_whitelist",
                                    "file,pipe",
                                    "-i",
                                    input.toString(),
                                    "-map",
                                    "0:a:0",
                                    "-vn",
                                    "-t",
                                    "301",
                                    "-ar",
                                    "44100",
                                    "-ac",
                                    "2",
                                    "-c:a",
                                    "pcm_s16le",
                                    "-map_metadata",
                                    "-1",
                                    "-fflags",
                                    "+bitexact",
                                    "-flags:a",
                                    "+bitexact",
                                    "-f",
                                    "wav",
                                    output.toString())
                            .redirectOutput(ProcessBuilder.Redirect.DISCARD)
                            .redirectError(ProcessBuilder.Redirect.DISCARD)
                            .start();
        } catch (IOException e) {
            throw new ApiProblem(
                    503,
                    "RENDITION_DECODER_UNAVAILABLE",
                    "Install ffmpeg to import a recording.",
                    "RETRY_UPLOAD");
        }
        try {
            if (!process.waitFor(60, TimeUnit.SECONDS) || process.exitValue() != 0)
                throw new ApiProblem(
                        422,
                        "RENDITION_DECODE_FAILED",
                        "The recording could not be decoded.",
                        "CHOOSE_FILE");
        } finally {
            stop(process);
        }
    }

    Wave verify(Path path) throws IOException {
        if (!Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS)
                || Files.size(path) > 64L * 1024 * 1024)
            throw new IOException("The rendition file is missing or too large.");
        try (var input = AudioSystem.getAudioInputStream(path.toFile())) {
            var fmt = input.getFormat();
            long frames = input.getFrameLength();
            if (!AudioFormat.Encoding.PCM_SIGNED.equals(fmt.getEncoding())
                    || fmt.isBigEndian()
                    || fmt.getChannels() != 2
                    || fmt.getSampleSizeInBits() != 16
                    || fmt.getSampleRate() != RATE
                    || frames <= 0
                    || frames > RATE * 300
                    || Files.size(path) < frames * 4 + 44)
                throw new IOException("Expected stereo PCM16 at 44.1 kHz, up to five minutes.");
            return new Wave(Math.toIntExact(frames), hash(path));
        } catch (UnsupportedAudioFileException e) {
            throw new IOException("The WAV format is unreadable.", e);
        }
    }

    /** Compare decoded sample bytes, so container metadata alone cannot create a new take. */
    void checkChangedAudio(Path input, Path output, int frames, Double start, Double end)
            throws IOException {
        int first = start == null ? 0 : (int) Math.round(start * RATE);
        int last = end == null ? frames : (int) Math.round(end * RATE);
        try (var before = AudioSystem.getAudioInputStream(input.toFile());
                var after = AudioSystem.getAudioInputStream(output.toFile())) {
            byte[] a = new byte[32768], b = new byte[a.length];
            boolean changed = false;
            for (int offset = 0; offset < frames * 4; offset += a.length) {
                int size = Math.min(a.length, frames * 4 - offset);
                if (before.readNBytes(a, 0, size) != size || after.readNBytes(b, 0, size) != size)
                    throw new IOException("The generated audio contains a truncated sample block.");
                int within = Math.clamp(first * 4 - offset, 0, size);
                int until = Math.clamp(last * 4 - offset, 0, size);
                if (!Arrays.equals(a, 0, within, b, 0, within)
                        || !Arrays.equals(a, until, size, b, until, size))
                    throw new IOException("Generation changed audio outside the selected range.");
                if (!Arrays.equals(a, within, until, b, within, until)) changed = true;
            }
            if (!changed)
                throw new IOException(
                        "The engine returned the same audio as the source. Try a different"
                                + " direction.");
        } catch (UnsupportedAudioFileException e) {
            throw new IOException("The generated WAV format is unreadable.", e);
        }
    }

    static String hash(Path path) throws IOException {
        try {
            MessageDigest sha = MessageDigest.getInstance("SHA-256");
            try (var in = new DigestInputStream(Files.newInputStream(path), sha)) {
                in.transferTo(OutputStream.nullOutputStream());
            }
            return HexFormat.of().formatHex(sha.digest());
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }

    JsonNode generate(Map<String, Object> request, String id)
            throws IOException, InterruptedException {
        if (!Files.isExecutable(python) || !Files.isRegularFile(script))
            throw new IOException(
                    "The local rendition worker is unavailable. Install the recording rendition"
                            + " engine and try again.");
        Path result = directory.resolve(id + ".json"), logFile = directory.resolve(id + ".log");
        ProcessBuilder builder = new ProcessBuilder(python.toString(), script.toString());
        builder.redirectOutput(result.toFile()).redirectError(logFile.toFile());
        builder.environment().remove(PROGRESS_ENV);
        try {
            builder.environment().put(PROGRESS_ENV, Files.createFile(progressFile(id)).toString());
        } catch (IOException e) {
            log.debug("Could not attach rendition progress for {}", id, e);
        }
        Process process = null;
        try {
            process = builder.start();
            try (var out = process.getOutputStream()) {
                out.write(json.writeValueAsBytes(request));
            }
            if (!process.waitFor(timeout, TimeUnit.SECONDS))
                throw new IOException(
                        "The local rendition exceeded its time limit. Try a shorter selection.");
            if (!Files.isRegularFile(result) || Files.size(result) > 65536)
                throw new IOException("The local rendition worker returned no usable result.");
            JsonNode reply;
            try {
                reply = json.readTree(Files.readAllBytes(result));
            } catch (RuntimeException e) {
                throw new IOException(
                        "The local rendition worker returned unreadable output. Check its"
                                + " installation.",
                        e);
            }
            if (reply == null
                    || !reply.isObject()
                    || (process.exitValue() != 0
                            && !"failed".equals(reply.path("state").asString())))
                throw new IOException(
                        "The local rendition worker stopped before finishing. Check its"
                                + " installation.");
            return reply;
        } finally {
            if (process != null) stop(process);
            clearProgress(id);
        }
    }

    private static void stop(Process process) {
        if (process.isAlive()) {
            var children = process.descendants().toList();
            process.destroyForcibly();
            children.reversed().forEach(ProcessHandle::destroyForcibly);
        }
    }
}
