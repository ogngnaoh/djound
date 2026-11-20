package com.djound.rendition;

import com.djound.ApiProblem;

import org.springframework.core.io.FileSystemResource;
import org.springframework.http.*;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.nio.file.Files;
import java.util.List;

@RestController
@RequestMapping("/api/renditions")
final class RenditionController {
    private final RenditionProjects projects;

    RenditionController(RenditionProjects projects) {
        this.projects = projects;
    }

    @GetMapping
    List<RenditionProjects.Index> list(@RequestParam(defaultValue = "active") String view) {
        return switch (view) {
            case "active" -> projects.list();
            case "trash" -> projects.list(true);
            default ->
                    throw new ApiProblem(
                            400, "RENDITION_VIEW_INVALID", "Choose the active library or Trash.");
        };
    }

    @PostMapping(consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    @ResponseStatus(HttpStatus.CREATED)
    RenditionProjects.State create(
            @RequestPart("file") MultipartFile file,
            @RequestHeader(value = "Idempotency-Key", required = false) String key)
            throws IOException, InterruptedException {
        return projects.create(file, key);
    }

    @GetMapping("/{id}")
    RenditionProjects.State state(@PathVariable String id) {
        return projects.state(id);
    }

    @PutMapping("/{id}/trash")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    void trash(@PathVariable String id) {
        projects.setTrashed(id, true);
    }

    @DeleteMapping("/{id}/trash")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    void restore(@PathVariable String id) {
        projects.setTrashed(id, false);
    }

    @PostMapping("/{id}/takes")
    @ResponseStatus(HttpStatus.ACCEPTED)
    RenditionProjects.State submit(
            @PathVariable String id, @RequestBody RenditionProjects.Command body) {
        return projects.submit(id, body);
    }

    @PostMapping("/{id}/takes/{takeId}/answer")
    @ResponseStatus(HttpStatus.ACCEPTED)
    RenditionProjects.State answer(
            @PathVariable String id,
            @PathVariable String takeId,
            @RequestBody RenditionProjects.Answer body) {
        return projects.answer(id, takeId, body);
    }

    record Favorite(String takeId) {}

    @PutMapping("/{id}/favorite")
    RenditionProjects.State favorite(@PathVariable String id, @RequestBody Favorite body) {
        if (body.takeId() == null || body.takeId().isBlank())
            throw new ApiProblem(422, "RENDITION_TAKE_REQUIRED", "Choose the version to keep.");
        return projects.favorite(id, body.takeId());
    }

    @GetMapping("/{id}/takes/{takeId}/waveform")
    ResponseEntity<int[]> waveform(@PathVariable String id, @PathVariable String takeId)
            throws IOException {
        var peaks = RenditionWaveform.peaks(projects.path(id, takeId));
        return ResponseEntity.ok().header("Cache-Control", "private, no-cache").body(peaks);
    }

    @GetMapping("/{id}/takes/{takeId}/{operation:audio|export}")
    ResponseEntity<FileSystemResource> audio(
            @PathVariable String id, @PathVariable String takeId, @PathVariable String operation)
            throws IOException {
        var file = projects.path(id, takeId);
        var reply =
                ResponseEntity.ok()
                        .contentType(MediaType.parseMediaType("audio/wav"))
                        .contentLength(Files.size(file))
                        .header("Cache-Control", "private, no-cache")
                        .header("Accept-Ranges", "bytes");
        if (operation.equals("export"))
            reply.header(
                    "Content-Disposition",
                    ContentDisposition.attachment()
                            .filename("djound-" + takeId + ".wav")
                            .build()
                            .toString());
        return reply.body(new FileSystemResource(file));
    }
}
