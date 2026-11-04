package com.djound;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.web.multipart.MultipartFile;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.io.*;
import java.nio.file.*;
import java.security.*;
import java.util.HexFormat;
import java.util.UUID;
import java.util.concurrent.*;

@Component
public class MediaStore {
    public static final long MAX_BYTES = 64L * 1024 * 1024;
    private final Installation installation;
    private final String ffprobe;
    private final ObjectMapper json = new ObjectMapper();

    public MediaStore(Installation installation, @Value("${djound.ffprobe}") String ffprobe) {
        this.installation = installation;
        this.ffprobe = ffprobe;
    }

    public record Upload(
            Path path,
            String fileName,
            long bytes,
            String hash,
            int durationMs,
            int sampleRate,
            int channels,
            String codec,
            String contentType)
            implements AutoCloseable {
        public void close() throws IOException {
            Files.deleteIfExists(path);
        }
    }

    public Upload ingest(MultipartFile file) throws IOException {
        String fileName = safeName(file.getOriginalFilename());
        Path temp = Files.createTempFile(installation.root.resolve("temporary"), "upload-", ".tmp");
        try {
            MessageDigest digest = sha();
            long size = 0;
            try (InputStream in = file.getInputStream();
                    OutputStream out = Files.newOutputStream(temp)) {
                byte[] buffer = new byte[32768];
                int n;
                while ((n = in.read(buffer)) != -1) {
                    size += n;
                    if (size > MAX_BYTES)
                        throw new ApiProblem(
                                413,
                                "UPLOAD_TOO_LARGE",
                                "Choose an audio file no larger than 64 MiB.",
                                "CHOOSE_FILE");
                    digest.update(buffer, 0, n);
                    out.write(buffer, 0, n);
                }
            }
            return inspect(temp, fileName, size, HexFormat.of().formatHex(digest.digest()));
        } catch (Exception e) {
            Files.deleteIfExists(temp);
            throw e;
        }
    }

    public Upload validateFile(Path path, String fileName) throws IOException {
        MessageDigest digest = sha();
        long size = 0;
        try (var in = Files.newInputStream(path)) {
            byte[] buffer = new byte[32768];
            int n;
            while ((n = in.read(buffer)) != -1) {
                size += n;
                if (size > MAX_BYTES) throw invalid("The output exceeds the 64 MiB limit.");
                digest.update(buffer, 0, n);
            }
        }
        return inspect(path, safeName(fileName), size, HexFormat.of().formatHex(digest.digest()));
    }

    private Upload inspect(Path temp, String fileName, long size, String hash) throws IOException {
        if (size == 0) throw invalid("The selected file is empty.");
        byte[] header = new byte[12];
        try (InputStream in = Files.newInputStream(temp)) {
            in.readNBytes(header, 0, 12);
        }
        boolean wav =
                new String(header, 0, 4, java.nio.charset.StandardCharsets.US_ASCII).equals("RIFF")
                        && new String(header, 8, 4, java.nio.charset.StandardCharsets.US_ASCII)
                                .equals("WAVE");
        boolean mp3 =
                (header[0] == 'I' && header[1] == 'D' && header[2] == '3')
                        || ((header[0] & 255) == 255 && (header[1] & 224) == 224);
        if (!wav && !mp3)
            throw new ApiProblem(
                    415, "UNSUPPORTED_MEDIA", "Choose WAV PCM16 or MP3 audio.", "CHOOSE_FILE");
        if (wav) validateWaveStructure(temp, size);
        JsonNode data = probe(temp);
        JsonNode streams = data.path("streams");
        JsonNode audio = null;
        for (JsonNode stream : streams) {
            if (stream.path("codec_type").asString().equals("video")
                    && stream.path("disposition").path("attached_pic").asInt() == 1) continue;
            if (!stream.path("codec_type").asString().equals("audio") || audio != null)
                throw invalid(
                        "The file must contain exactly one audio stream and no playable"
                                + " video.");
            audio = stream;
        }
        if (audio == null) throw invalid("The file contains no audio stream.");
        String codec = audio.path("codec_name").asString();
        String format = data.path("format").path("format_name").asString();
        if (!(wav && format.equals("wav") && codec.equals("pcm_s16le"))
                && !(mp3 && format.equals("mp3") && codec.equals("mp3")))
            throw new ApiProblem(
                    415,
                    "UNSUPPORTED_CODEC",
                    "Choose WAV with 16-bit PCM or MP3 audio.",
                    "CHOOSE_FILE");
        int rate = audio.path("sample_rate").asInt();
        int channels = audio.path("channels").asInt();
        if ((rate != 44100 && rate != 48000) || (channels != 1 && channels != 2))
            throw invalid("Audio must be mono or stereo at 44.1 or 48 kHz.");
        double seconds = data.path("format").path("duration").asDouble();
        if (!Double.isFinite(seconds) || seconds < 1 || seconds > 300)
            throw invalid("Audio must be between 1 and 300 seconds long.");
        if (audio.path("nb_read_frames").asInt() <= 0)
            throw invalid("The recording contains no readable audio frames.");
        return new Upload(
                temp,
                fileName,
                size,
                hash,
                (int) Math.round(seconds * 1000),
                rate,
                channels,
                codec,
                wav ? "audio/wav" : "audio/mpeg");
    }

    public Path temporaryPath(String name) {
        if (!name.matches("result-[0-9a-f-]{36}\\.tmp"))
            throw new IllegalArgumentException("Invalid result temporary name");
        return installation.root.resolve("temporary").resolve(name);
    }

    public void link(Path temporary, String storageKey) throws IOException {
        Files.createLink(path(storageKey), temporary);
    }

    public void cleanupUnreferenced(
            java.util.Set<String> recordings, java.util.Set<String> temporaryNames)
            throws IOException {
        try (var paths = Files.list(installation.root.resolve("media"))) {
            for (var file : paths.toList())
                if (file.getFileName().toString().matches("[0-9a-f-]{36}\\.audio")
                        && Files.isRegularFile(file, LinkOption.NOFOLLOW_LINKS)
                        && !recordings.contains(file.getFileName().toString())) Files.delete(file);
        }
        try (var paths = Files.list(installation.root.resolve("temporary"))) {
            for (var file : paths.toList())
                if (file.getFileName().toString().matches("(upload-|result-).*\\.tmp")
                        && Files.isRegularFile(file, LinkOption.NOFOLLOW_LINKS)
                        && !temporaryNames.contains(file.getFileName().toString()))
                    Files.delete(file);
        }
    }

    private JsonNode probe(Path path) throws IOException {
        Process process;
        try {
            process =
                    new ProcessBuilder(
                                    ffprobe,
                                    "-v",
                                    "error",
                                    "-protocol_whitelist",
                                    "file",
                                    "-count_frames",
                                    "-show_entries",
                                    "stream=codec_type,codec_name,sample_rate,channels,nb_read_frames:stream_disposition=attached_pic:format=format_name,duration",
                                    "-of",
                                    "json",
                                    path.toString())
                            .redirectErrorStream(true)
                            .start();
        } catch (IOException e) {
            throw new ApiProblem(
                    503,
                    "MEDIA_PROBE_UNAVAILABLE",
                    "Audio validation is unavailable. Check the local ffprobe installation.",
                    "RETRY_UPLOAD");
        }
        try (var executor = Executors.newVirtualThreadPerTaskExecutor()) {
            Future<byte[]> output =
                    executor.submit(
                            () -> {
                                try (var in = process.getInputStream();
                                        var bytes = new ByteArrayOutputStream()) {
                                    byte[] b = new byte[8192];
                                    int n;
                                    while ((n = in.read(b)) != -1) {
                                        if (bytes.size() + n > 1024 * 1024) {
                                            terminate(process);
                                            throw new IOException("Probe output exceeded limit");
                                        }
                                        bytes.write(b, 0, n);
                                    }
                                    return bytes.toByteArray();
                                }
                            });
            try {
                if (!process.waitFor(10, TimeUnit.SECONDS)) {
                    terminate(process);
                    throw invalid("Audio validation took too long. Choose another file.");
                }
                byte[] body = output.get(2, TimeUnit.SECONDS);
                if (process.exitValue() != 0)
                    throw invalid("The file could not be read as supported audio.");
                try {
                    return json.readTree(body);
                } catch (Exception e) {
                    throw invalid("The file is corrupt or contains unreadable audio.");
                }
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw invalid("Audio validation was interrupted.");
            } catch (ExecutionException | TimeoutException e) {
                throw invalid("Audio validation could not finish.");
            } finally {
                terminate(process);
            }
        }
    }

    private static void terminate(Process process) {
        process.descendants().forEach(ProcessHandle::destroyForcibly);
        process.destroyForcibly();
    }

    private static void validateWaveStructure(Path file, long bytes) throws IOException {
        try (var channel = java.nio.channels.FileChannel.open(file, StandardOpenOption.READ)) {
            java.nio.ByteBuffer word =
                    java.nio.ByteBuffer.allocate(8).order(java.nio.ByteOrder.LITTLE_ENDIAN);
            channel.position(4);
            word.limit(4);
            while (word.hasRemaining())
                if (channel.read(word) < 0) throw invalid("The WAV header is truncated.");
            word.flip();
            long end = Integer.toUnsignedLong(word.getInt()) + 8;
            if (end > bytes || end < 44)
                throw invalid("The WAV file is truncated or has an invalid size.");
            long offset = 12;
            boolean found = false;
            while (offset + 8 <= end) {
                word.clear();
                channel.position(offset);
                while (word.hasRemaining())
                    if (channel.read(word) < 0) throw invalid("The WAV file is truncated.");
                word.flip();
                int tag = word.getInt();
                long length = Integer.toUnsignedLong(word.getInt());
                if (length > end - offset - 8)
                    throw invalid("The WAV file contains a truncated audio chunk.");
                if (tag == 0x61746164) {
                    found = true;
                    if (length == 0) throw invalid("The WAV file contains no audio.");
                }
                offset += 8 + length + (length & 1);
            }
            if (!found || offset != end) throw invalid("The WAV file has an invalid chunk layout.");
        }
    }

    public String publish(Upload upload) throws IOException {
        String key = UUID.randomUUID() + ".audio";
        // Atomic no-replace publication on the same local filesystem. createLink fails if the
        // target exists.
        Files.createLink(path(key), upload.path());
        Files.delete(upload.path());
        return key;
    }

    public Path path(String key) {
        if (!key.matches("[0-9a-f-]{36}\\.audio"))
            throw new IllegalStateException("Invalid stored media key");
        return installation.root.resolve("media").resolve(key);
    }

    static MessageDigest sha() {
        try {
            return MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }

    static String safeName(String name) {
        if (name == null) return "Recording";
        String cleaned = name.replace('\\', '/');
        cleaned =
                cleaned.substring(cleaned.lastIndexOf('/') + 1)
                        .replaceAll("[\\p{Cntrl}]", "")
                        .strip();
        return cleaned.isEmpty()
                ? "Recording"
                : cleaned.substring(0, Math.min(cleaned.length(), 255));
    }

    static ApiProblem invalid(String detail) {
        return new ApiProblem(422, "INVALID_AUDIO", detail, "CHOOSE_FILE");
    }
}
