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
    private final com.djound.local.LocalMusic local;
    private final MediaStore media;
    private final com.djound.generation.GenerationService generations;
    private final com.djound.generation.FixtureReviewControls controls;
    private final com.djound.availability.AvailabilityService availability;

    public ApiController(
            Explorations explorations,
            MediaStore media,
            com.djound.generation.GenerationService generations,
            com.djound.generation.FixtureReviewControls controls,
            com.djound.availability.AvailabilityService availability,
            com.djound.local.LocalMusic local) {
        this.local = local;
        this.availability = availability;
        this.controls = controls;
        this.generations = generations;
        this.explorations = explorations;
        this.media = media;
    }

    @GetMapping("/workspace")
    public Map<String, Object> workspace() {
        Map<String, Object> result = new LinkedHashMap<>();
        var activeAttempt = generations.active();
        String active = activeAttempt == null ? null : activeAttempt.id();
        boolean configured = generations.configuration().configured();
        boolean live = generations.configuration().live();
        boolean localMode = generations.configuration().local();
        boolean preparation = local.busy();
        boolean allowed = generations.allowanceAvailable();
        result.put("submissionAllowance", generations.submissionAllowance());
        result.put("mode", generations.configuration().mode());
        result.put("configuration", configured ? "CONFIGURED" : "UNCONFIGURED");
        var capacity = availability.view();
        result.put("eligibility", capacity.eligibility());
        result.put("reasonCode", capacity.reasonCode());
        result.put("observedAt", capacity.observedAt());
        result.put("providerResetAt", capacity.providerResetAt());
        result.put("nextProbeAt", capacity.nextProbeAt());
        result.put("supportsStatusCheck", capacity.supportsStatusCheck());
        result.put("canRecheck", capacity.canRecheck());
        var unknown = generations.unknownOutcome();
        boolean requiresAcknowledgement = generations.requiresAcknowledgement(unknown);
        result.put("unknownOutcome", unknown);
        result.put(
                "canSubmit",
                configured
                        && !preparation
                        && allowed
                        && active == null
                        && !requiresAcknowledgement
                        && capacity.allowsSubmission());
        result.put(
                "canSubmitWithAcknowledgement",
                configured
                        && !preparation
                        && allowed
                        && active == null
                        && requiresAcknowledgement
                        && capacity.allowsSubmission());
        result.put("canCreateExploration", active == null && !preparation);
        result.put("activeAttemptId", active);
        result.put(
                "activeExplorationId",
                activeAttempt == null ? null : activeAttempt.explorationId());
        result.put("fixtureControlsEnabled", controls.enabled());
        Map<String, Object> inputPolicy =
                new LinkedHashMap<>(
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
                                localMode ? 1000 : 4000,
                                "providerCompatibilityKnown",
                                live || localMode));
        inputPolicy.put("maxAssembledCodePoints", live ? 500 : 16000);
        String example =
                new com.djound.generation.PromptAssembler().assemble("", "x").instructions();
        inputPolicy.put(
                "instructionOverheadCodePoints", example.codePointCount(0, example.length()) - 1);
        inputPolicy.put(
                "providerMaxDurationMs",
                localMode ? Integer.valueOf(12000) : live ? Integer.valueOf(30000) : null);
        result.put("inputPolicy", inputPolicy);
        return result;
    }

    @GetMapping("/explorations/{id}/local")
    public com.djound.local.LocalModels.State localState(@PathVariable String id) {
        return local.state(id);
    }

    @PostMapping("/explorations/{id}/local/transcriptions")
    @ResponseStatus(HttpStatus.ACCEPTED)
    public com.djound.local.LocalModels.Job transcribe(
            @PathVariable String id,
            @RequestBody com.djound.local.LocalModels.SourceCommand command) {
        return local.transcribe(id, command);
    }

    @PutMapping("/explorations/{id}/local/notes")
    public com.djound.local.LocalModels.Notes saveLocalNotes(
            @PathVariable String id,
            @RequestBody com.djound.local.LocalModels.NotesCommand command) {
        return local.saveNotes(id, command);
    }

    @PostMapping("/explorations/{id}/local/previews")
    @ResponseStatus(HttpStatus.ACCEPTED)
    public com.djound.local.LocalModels.Job previewNotes(
            @PathVariable String id,
            @RequestBody com.djound.local.LocalModels.RevisionCommand command) {
        return local.preview(id, command);
    }

    @PostMapping(
            value = "/explorations/{id}/local/tone-auditions",
            consumes = MediaType.APPLICATION_JSON_VALUE,
            produces = "audio/wav")
    public ResponseEntity<byte[]> auditionLocalTone(
            @PathVariable String id,
            @RequestBody com.djound.local.LocalModels.ToneAuditionCommand command) {
        byte[] audio = local.auditionTone(id, command);
        return ResponseEntity.ok()
                .contentType(MediaType.parseMediaType("audio/wav"))
                .cacheControl(CacheControl.noStore())
                .contentLength(audio.length)
                .body(audio);
    }

    @PostMapping("/explorations/{id}/local/plans")
    @ResponseStatus(HttpStatus.ACCEPTED)
    public com.djound.local.LocalModels.Job planLocal(
            @PathVariable String id,
            @RequestBody com.djound.local.LocalModels.PlanCommand command) {
        return local.propose(id, command);
    }

    @PostMapping("/explorations/{id}/local/manual-plans")
    @ResponseStatus(HttpStatus.CREATED)
    public com.djound.local.LocalModels.Plan manualLocalPlan(
            @PathVariable String id,
            @RequestBody com.djound.local.LocalModels.PlanCommand command) {
        return local.manual(id, command);
    }

    @PostMapping("/local/plans/{id}/corrections")
    @ResponseStatus(HttpStatus.CREATED)
    public com.djound.local.LocalModels.Plan correctLocalPlan(
            @PathVariable String id,
            @RequestBody com.djound.local.LocalModels.CorrectionCommand command) {
        return local.correct(id, command);
    }

    @GetMapping("/local/jobs/{id}")
    public com.djound.local.LocalModels.JobResult localJobResult(@PathVariable String id) {
        return local.result(id);
    }

    @PostMapping("/local/jobs/{id}/cancel")
    public com.djound.local.LocalModels.Job cancelPreparation(@PathVariable String id) {
        return local.cancel(id);
    }

    @PostMapping("/attempts/{id}/cancel")
    public Models.Attempt cancelLocalAttempt(@PathVariable String id) {
        return generations.cancelLocal(id);
    }

    @GetMapping("/local/jobs/{id}/audio")
    public ResponseEntity<FileSystemResource> localAudio(@PathVariable String id) throws Exception {
        var path = local.previewAudio(id);
        return ResponseEntity.ok()
                .contentType(MediaType.parseMediaType("audio/wav"))
                .contentLength(Files.size(path))
                .header("Accept-Ranges", "bytes")
                .header("Cache-Control", "private, max-age=31536000, immutable")
                .body(new FileSystemResource(path));
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

    @PatchMapping(value = "/recordings/{id}", consumes = MediaType.APPLICATION_JSON_VALUE)
    public Models.Recording updateRecording(
            @PathVariable String id, @RequestBody Models.RecordingUpdate command) {
        return explorations.setKept(id, command);
    }

    @PostMapping(value = "/requests/{id}/retries", consumes = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<Models.RetryReceipt> retry(
            @PathVariable String id,
            @RequestBody Models.RetryCommand command,
            @RequestHeader("Idempotency-Key") String key) {
        var receipt = generations.retry(id, command, key);
        return ResponseEntity.accepted().header("Location", receipt.statusUrl()).body(receipt);
    }

    @GetMapping("/fixture/controls")
    public Models.FixtureControls fixtureControls() {
        return controls.get();
    }

    @PutMapping(
            value = "/fixture/controls/next-attempt",
            consumes = MediaType.APPLICATION_JSON_VALUE)
    public Models.FixtureControls fixtureOutcome(
            @RequestBody Models.FixtureOutcomeControl command) {
        return controls.setOutcome(command);
    }

    @PutMapping(value = "/fixture/controls/playback", consumes = MediaType.APPLICATION_JSON_VALUE)
    public Models.FixtureControls fixturePlayback(
            @RequestBody Models.FixturePlaybackControl command) {
        return controls.setPlayback(command);
    }

    @PostMapping("/workspace/availability/recheck")
    public Map<String, Object> recheckAvailability() {
        availability.recheck();
        return workspace();
    }

    @PutMapping(
            value = "/fixture/controls/availability",
            consumes = MediaType.APPLICATION_JSON_VALUE)
    public Map<String, Object> fixtureAvailability(
            @RequestBody Models.FixtureAvailabilityControl command) {
        availability.setScenario(command == null ? null : command.scenario());
        return workspace();
    }

    @RequestMapping(
            value = "/media/{id}",
            method = {RequestMethod.GET, RequestMethod.HEAD})
    public ResponseEntity<FileSystemResource> media(
            @PathVariable String id, jakarta.servlet.http.HttpServletResponse response) {
        var recording = explorations.recording(id);
        boolean reviewMedia = controls.enabled() && recording.provenance().equals("FIXTURE");
        if (reviewMedia) response.setHeader("Cache-Control", "no-store");
        controls.requirePlayable(id);
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
                .header(
                        "Cache-Control",
                        reviewMedia ? "no-store" : "private, max-age=31536000, immutable")
                .eTag("\"" + recording.sha256() + "\"")
                .body(new FileSystemResource(path));
    }
}
