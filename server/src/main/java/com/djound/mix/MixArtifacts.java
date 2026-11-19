package com.djound.mix;

import com.djound.ApiProblem;
import com.djound.Installation;
import com.djound.local.LocalWorker;

import org.springframework.stereotype.Component;

import tools.jackson.databind.JsonNode;

import java.io.*;
import java.nio.file.*;
import java.security.*;
import java.util.HexFormat;

import javax.sound.sampled.*;

@Component
class MixArtifacts {
    static final int RATE = 44100;
    final Path root;
    final LocalWorker worker;

    MixArtifacts(Installation installation, LocalWorker worker) throws IOException {
        this.root = installation.rootPath();
        this.worker = worker;
        Files.createDirectories(root.resolve("mix-imports"));
    }

    record FileRef(Path path, String sha) {}

    record Wave(FileRef file, int frames) {}

    Path uploaded(String id) {
        if (!id.matches("[0-9a-f-]{36}")) throw new IllegalArgumentException("Invalid project ID");
        return root.resolve("mix-imports").resolve(id + ".mp3");
    }

    String storePath(Path path) {
        return root.relativize(path).toString();
    }

    Path existing(String stored) {
        if (stored == null) throw invalid("The audio has not finished preparing.");
        Path path = root.resolve(stored).normalize();
        if (!path.startsWith(root) || !Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS))
            throw invalid("The saved audio is unavailable on disk.");
        return path;
    }

    static String sha256(Path path) throws IOException {
        MessageDigest digest;
        try {
            digest = MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
        try (var input = Files.newInputStream(path);
                var checked = new DigestInputStream(input, digest)) {
            checked.transferTo(OutputStream.nullOutputStream());
        }
        return HexFormat.of().formatHex(digest.digest());
    }

    boolean matches(String stored, String expectedHash) {
        if (stored == null || expectedHash == null) return false;
        try {
            return sha256(existing(stored)).equals(expectedHash);
        } catch (IOException | ApiProblem e) {
            return false;
        }
    }

    void discardDuplicate(Path path) {
        try {
            Files.deleteIfExists(path);
        } catch (IOException e) {
            org.slf4j.LoggerFactory.getLogger(MixArtifacts.class)
                    .warn("Could not remove duplicate preview {}", path, e);
        }
    }

    FileRef verified(String job, JsonNode result, String name) throws IOException {
        String hash = result.path(name + "Sha256").asText("");
        Path path = worker.audio(job, result.path(name + "Path").asText(""));
        if (!hash.matches("[0-9a-f]{64}") || !sha256(path).equals(hash))
            throw invalid("The prepared file did not match the worker checksum. Retry this step.");
        return new FileRef(path, hash);
    }

    Wave wave(String job, JsonNode result, Integer expectedFrames) throws Exception {
        FileRef file = verified(job, result, "audio");
        int frames = result.path("frames").asInt(-1);
        if (frames < 1 || frames > 300 * RATE || expectedFrames != null && expectedFrames != frames)
            throw invalid("The local render changed the length of the song.");
        try (var input = AudioSystem.getAudioInputStream(file.path().toFile())) {
            AudioFormat format = input.getFormat();
            if (!AudioFormat.Encoding.PCM_SIGNED.equals(format.getEncoding())
                    || format.getSampleRate() != RATE
                    || format.getSampleSizeInBits() != 16
                    || format.getChannels() != 2
                    || format.isBigEndian()
                    || input.getFrameLength() != frames
                    || Files.size(file.path()) < 4L * frames)
                throw invalid(
                        "The render must contain stereo 44.1 kHz PCM16 with the original frame"
                                + " count.");
        }
        return new Wave(file, frames);
    }

    static ApiProblem invalid(String message) {
        return new ApiProblem(422, "MIX_AUDIO_INVALID", message, "RETRY");
    }
}
