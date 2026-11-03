package com.djound;

import org.springframework.core.io.FileSystemResource;
import org.springframework.http.*;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.nio.file.Files;
import java.util.*;

@RestController
@RequestMapping("/api")
public class ApiController {
    private final Explorations explorations;
    private final MediaStore media;
    private final com.djound.generation.GenerationService generations;

    public ApiController(
            Explorations explorations,
            MediaStore media,
            com.djound.generation.GenerationService generations) {
        this.generations = generations;
        this.explorations = explorations;
        this.media = media;
    }

    @GetMapping("/workspace")
    public Map<String, Object> workspace() {
        Map<String, Object> result = new LinkedHashMap<>();
        String active = generations.activeAttemptId();
        boolean fixture = generations.configuration().fixture();
        result.put("mode", generations.configuration().mode());
        result.put("configuration", fixture ? "CONFIGURED" : "UNCONFIGURED");
        result.put("eligibility", fixture ? "ELIGIBLE" : "UNKNOWN");
        result.put("canSubmit", fixture && active == null);
        result.put("canCreateExploration", active == null);
        result.put("activeAttemptId", active);
        result.put(
                "inputPolicy",
                Map.of(
                        "formats",
                        List.of("WAV_PCM16", "MP3"),
                        "maxBytes",
                        MediaStore.MAX_BYTES,
                        "minDurationMs",
                        1000,
                        "maxDurationMs",
                        300000,
                        "sampleRates",
                        List.of(44100, 48000),
                        "channels",
                        List.of(1, 2),
                        "maxTextCodePoints",
                        4000,
                        "providerCompatibilityKnown",
                        false));
        return result;
    }

    @GetMapping("/explorations")
    public Map<String, Object> list() {
        return Map.of("items", explorations.list());
    }

    @GetMapping("/explorations/{id}")
    public Models.Exploration get(@PathVariable String id) {
        return explorations.get(id);
    }

    @PostMapping(value = "/explorations", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    public ResponseEntity<Models.Receipt> create(
            @RequestPart MultipartFile file,
            @RequestParam String initialDraft,
            @RequestHeader("Idempotency-Key") String key)
            throws IOException {
        var receipt = explorations.create(file, initialDraft, key);
        return ResponseEntity.status(201).header("Location", receipt.location()).body(receipt);
    }

    @PutMapping(value = "/explorations/{id}/draft", consumes = MediaType.APPLICATION_JSON_VALUE)
    public Models.Draft saveDraft(
            @PathVariable String id, @RequestBody Models.DraftUpdate command) {
        return explorations.saveDraft(id, command);
    }

    @PutMapping(value = "/explorations/{id}/source", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    public ResponseEntity<Models.Receipt> replaceSource(
            @PathVariable String id,
            @RequestPart MultipartFile file,
            @RequestParam long expectedSourceRevision,
            @RequestHeader("Idempotency-Key") String key)
            throws IOException {
        var receipt = explorations.replaceSource(id, file, expectedSourceRevision, key);
        return ResponseEntity.ok().header("Location", receipt.location()).body(receipt);
    }

    @PostMapping(value = "/explorations/{id}/requests", consumes = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<Models.CreationReceipt> createRequest(
            @PathVariable String id,
            @RequestBody Models.CreateRequestCommand command,
            @RequestHeader("Idempotency-Key") String key) {
        var receipt = generations.create(id, command, key);
        return ResponseEntity.accepted().header("Location", receipt.statusUrl()).body(receipt);
    }

    @GetMapping("/attempts/{id}")
    public Models.Attempt attempt(@PathVariable String id) {
        return generations.get(id);
    }

    @RequestMapping(
            value = "/media/{id}",
            method = {RequestMethod.GET, RequestMethod.HEAD})
    public ResponseEntity<FileSystemResource> media(@PathVariable String id) {
        var recording = explorations.recording(id);
        var path = media.path(explorations.storageKey(id));
        if (!Files.isRegularFile(path))
            throw new ApiProblem(
                    404,
                    "MEDIA_UNAVAILABLE",
                    "This audio file is unavailable. Your exploration is still saved.",
                    "RETRY_PLAYBACK",
                    "RETURN_TO_LIBRARY");
        return ResponseEntity.ok()
                .contentType(MediaType.parseMediaType(recording.contentType()))
                .contentLength(recording.byteSize())
                .header("Accept-Ranges", "bytes")
                .header("Cache-Control", "private, max-age=31536000, immutable")
                .eTag("\"" + recording.sha256() + "\"")
                .body(new FileSystemResource(path));
    }
}
