package com.djound.mix;

import static com.djound.mix.MixModels.*;

import org.springframework.core.io.FileSystemResource;
import org.springframework.http.*;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.nio.file.*;
import java.util.*;

@RestController
@RequestMapping("/api/mixes")
public class MixController {
    private final MixProjects projects;

    public MixController(MixProjects projects) {
        this.projects = projects;
    }

    @GetMapping
    public Map<String, List<State>> list() {
        return Map.of("items", projects.list());
    }

    @PostMapping(consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    @ResponseStatus(HttpStatus.CREATED)
    public State create(
            @RequestPart MultipartFile file,
            @RequestHeader(value = "Idempotency-Key", required = false) String key)
            throws IOException {
        return projects.create(file, key);
    }

    @GetMapping("/{id}")
    public State state(@PathVariable String id) {
        return projects.state(id);
    }

    @PostMapping("/{id}/retry")
    public State retry(@PathVariable String id) {
        return projects.retry(id);
    }

    @PutMapping("/{id}/selection")
    public State select(@PathVariable String id, @RequestBody Select command) {
        return projects.select(id, command);
    }

    @PostMapping("/{id}/edits")
    public Reply edit(
            @PathVariable String id,
            @RequestBody Edit command,
            @RequestHeader(value = "Idempotency-Key", required = false) String key)
            throws Exception {
        return projects.edit(id, command, key);
    }

    @PutMapping("/{id}/heard")
    public State heard(@PathVariable String id, @RequestBody Target target) {
        return projects.heard(id, target.takeId());
    }

    @PostMapping("/{id}/keep")
    public State keep(
            @PathVariable String id,
            @RequestBody Keep command,
            @RequestHeader("Idempotency-Key") String key) {
        return projects.keep(id, command, key);
    }

    @PostMapping("/{id}/undo")
    public State undo(@PathVariable String id, @RequestBody Target target) {
        return projects.undo(id, target.takeId());
    }

    @GetMapping("/{id}/import")
    public ResponseEntity<FileSystemResource> imported(@PathVariable String id) throws IOException {
        return resource(projects.uploadPath(id), "audio/mpeg", null);
    }

    @GetMapping("/{id}/takes/{takeId}/audio")
    public ResponseEntity<FileSystemResource> audio(
            @PathVariable String id, @PathVariable String takeId) throws IOException {
        return resource(projects.takePath(id, takeId, false), "audio/wav", null);
    }

    @GetMapping("/{id}/takes/{takeId}/export")
    public ResponseEntity<FileSystemResource> export(
            @PathVariable String id, @PathVariable String takeId) throws IOException {
        return resource(
                projects.takePath(id, takeId, true), "audio/wav", "djound-" + takeId + ".wav");
    }

    private static ResponseEntity<FileSystemResource> resource(Path path, String type, String name)
            throws IOException {
        var response =
                ResponseEntity.ok()
                        .contentType(MediaType.parseMediaType(type))
                        .contentLength(Files.size(path))
                        .header("Accept-Ranges", "bytes")
                        .header("Cache-Control", "private, max-age=31536000, immutable");
        if (name != null)
            response.header(
                    "Content-Disposition",
                    ContentDisposition.attachment().filename(name).build().toString());
        return response.body(new FileSystemResource(path));
    }
}
