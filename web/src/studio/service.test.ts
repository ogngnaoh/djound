import test from "node:test";
import assert from "node:assert/strict";
import { createStudioService } from "./service.ts";
import { auditionStart, auditionUnavailable } from "./auditionEligibility.ts";
import {
  changeBar,
  chartBassNotice,
  chordSuggestions,
  removeChart,
} from "./harmony.ts";
import {
  activeStems,
  allTracksMuted,
  backingTiming,
  hasActiveBacking,
} from "./mixState.ts";
import { DEFAULT_RECIPE, type Recipe } from "./types.ts";

function storage() {
  const values = new Map<string, string>();
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
      removeItem: (key: string) => values.delete(key),
      get length() {
        return values.size;
      },
      key: (index: number) => [...values.keys()][index] ?? null,
    },
  });
}

test("prototype review preserves basis and creates one immutable version per proposal", async () => {
  storage();
  const service = createStudioService("prototype", "normal");
  const project = await service.get("evening");
  const before = structuredClone(project.tracks);
  const proposal = await service.propose(
    project,
    "Make the backing sparse.",
    project.basisId,
  );
  const created = await service.create(project, proposal);
  assert.equal(created.tracks.length, 5);
  assert.equal(created.basisId, project.basisId);
  assert.deepEqual(created.tracks.slice(0, 4), before);
  assert.equal(created.tracks.at(-1)?.recipe.density, "sparse");
  assert.equal((await service.create(project, proposal)).tracks.length, 5);
  const reloaded = createStudioService("prototype", "normal");
  assert.equal((await reloaded.get("evening")).tracks.length, 5);
});

test("changing a reviewed request invalidates creation", async () => {
  storage();
  const service = createStudioService("prototype", "normal");
  const project = await service.get("evening");
  const proposal = await service.propose(
    project,
    "Make backing sparse.",
    project.basisId,
  );
  await service.saveDraft(project, "Use piano instead.", project.basisId);
  await assert.rejects(service.create(project, proposal), /out of date/);
  assert.equal((await service.get("evening")).tracks.length, 4);
});

test("editing a returned proposal cannot alter its frozen snapshot", async () => {
  storage();
  const service = createStudioService("prototype", "normal");
  const project = await service.get("evening");
  const proposal = await service.propose(
    project,
    "Make backing sparse.",
    project.basisId,
  );
  proposal.recipe.instrument = "piano";
  await assert.rejects(service.create(project, proposal), /out of date/);
});

test("cancelled creation never publishes an audio version", async () => {
  storage();
  const service = createStudioService("prototype", "normal");
  const project = await service.get("evening");
  const proposal = await service.propose(
    project,
    "Make backing sparse.",
    project.basisId,
  );
  const controller = new AbortController();
  const pending = service.create(project, proposal, controller.signal);
  controller.abort();
  await assert.rejects(pending, { name: "AbortError" });
  assert.equal((await service.get("evening")).tracks.length, 4);
});

test("direct controls remain available after an unclear language request", async () => {
  storage();
  const service = createStudioService("prototype", "unclear");
  const project = await service.get("evening");
  await assert.rejects(
    service.propose(project, "Make it brighter", project.basisId),
    /couldn't interpret/,
  );
  const proposal = await service.propose(
    project,
    "Make it brighter",
    project.basisId,
    { ...project.tracks.at(-1)!.recipe, instrument: "piano" },
  );
  assert.equal(proposal.recipe.instrument, "piano");
  assert.equal(proposal.canGenerate, true);
});

test("engine unavailable leaves saved audio and Keep accessible", async () => {
  storage();
  const service = createStudioService("prototype", "unavailable");
  const project = await service.get("evening");
  assert.equal(project.available, false);
  assert.equal(project.tracks[0].url, "/audio/original.wav");
  await service.keep(project.id, project.tracks[1].id, true);
  assert.equal((await service.get(project.id)).tracks[1].kept, true);
  await assert.rejects(service.detect(project), /engine is unavailable/);
});

test("prototype melody preview renders edited notes and survives a service reload", async () => {
  const { indexedDB } = await import("fake-indexeddb");
  Object.defineProperty(globalThis, "indexedDB", {
    configurable: true,
    value: indexedDB,
  });
  storage();
  const service = createStudioService("prototype", "normal");
  const project = await service.get("evening");
  const first = await service.preview(project);
  assert.ok(first.previewUrl?.startsWith("blob:"));
  const firstAudio = new Uint8Array(
    await (await fetch(first.previewUrl!)).arrayBuffer(),
  );
  assert.equal(new TextDecoder().decode(firstAudio.slice(0, 4)), "RIFF");
  const notes = first.notes.map((note, index) =>
    index ? note : { ...note, pitch: note.pitch + 5 },
  );
  const edited = await service.saveNotes(first, notes, true);
  assert.equal(edited.previewUrl, undefined);
  const second = await service.preview(edited);
  const secondAudio = new Uint8Array(
    await (await fetch(second.previewUrl!)).arrayBuffer(),
  );
  assert.notDeepEqual(firstAudio, secondAudio);
  const reloaded = await createStudioService("prototype", "normal").get(
    project.id,
  );
  assert.ok(reloaded.previewUrl?.startsWith("blob:"));
  assert.deepEqual(
    new Uint8Array(await (await fetch(reloaded.previewUrl!)).arrayBuffer()),
    secondAudio,
  );
});

test("backend recovery replays the exact durable command and clears a terminal receipt", async () => {
  const { indexedDB } = await import("fake-indexeddb");
  Object.defineProperty(globalThis, "indexedDB", {
    configurable: true,
    value: indexedDB,
  });
  const { pendingCommand } = await import("../recovery.ts");
  const { api } = await import("../api.ts");
  const { pendingBackendCreation, recoverBackendCreation } =
    await import("./service.ts");
  const command = {
    key: "test-recovery-key",
    explorationId: "server-project",
    body: {
      localPlanId: "reviewed-plan",
      text: "Remove drums",
      basisRecordingId: "version-one",
      expectedSourceRevision: 2,
      expectedDraftRevision: 4,
    },
  };
  await pendingCommand("studio-creation", command);
  assert.deepEqual(await pendingBackendCreation(), {
    projectId: command.explorationId,
    text: command.body.text,
    basisId: command.body.basisRecordingId,
  });
  await assert.rejects(
    recoverBackendCreation("different-project"),
    /No pending creation/,
  );
  const originalPost = api.POST,
    originalGet = api.GET;
  const calls: unknown[] = [];
  try {
    api.POST = (async (path: string, options: unknown) => {
      calls.push({ path, options });
      return {
        data: { attemptId: "existing-attempt" },
        response: new Response(null, { status: 202 }),
      };
    }) as unknown as typeof api.POST;
    api.GET = (async () => ({
      data: {
        state: "FAILED",
        outcome: { detail: "Recorded backend fixture failure" },
      },
    })) as unknown as typeof api.GET;
    await assert.rejects(
      recoverBackendCreation(command.explorationId),
      /Recorded backend fixture failure/,
    );
    assert.deepEqual(calls, [
      {
        path: "/api/explorations/{id}/requests",
        options: {
          params: {
            path: { id: command.explorationId },
            header: { "Idempotency-Key": command.key },
          },
          body: command.body,
        },
      },
    ]);
    assert.equal(await pendingBackendCreation(), null);
  } finally {
    api.POST = originalPost;
    api.GET = originalGet;
    await pendingCommand("studio-creation", null, command.key);
  }
});

test("manual backend review calls manual-plans then corrections without the language planner", async () => {
  storage();
  const { api } = await import("../api.ts");
  const prototype = createStudioService("prototype", "normal");
  const project = await prototype.get("evening");
  const recipe = {
    ...project.tracks.at(-1)!.recipe,
    density: "sparse" as const,
  };
  const plan = {
    id: "manual-parent",
    sourceRevision: project.sourceRevision,
    noteRevision: project.noteRevision,
    draftRevision: project.draftRevision,
    text: project.draft,
    basisRecordingId: project.basisId,
    recipe: project.tracks.at(-1)!.recipe,
    basisRecipe: project.tracks.at(-1)!.recipe,
    canGenerate: true,
    warnings: [],
    summary: "Direct controls",
    model: { origin: "USER_DEFINED" },
  };
  const calls: { path: string; options: any }[] = [];
  const originalPost = api.POST;
  try {
    api.POST = (async (path: string, options: unknown) => {
      calls.push({ path, options });
      if (path === "/api/explorations/{id}/local/manual-plans")
        return { data: plan, response: new Response(null, { status: 201 }) };
      if (path === "/api/local/plans/{id}/corrections")
        return {
          data: { ...plan, id: "corrected-manual", recipe },
          response: new Response(null, { status: 201 }),
        };
      throw new Error(`Unexpected endpoint ${path}`);
    }) as unknown as typeof api.POST;
    const reviewed = await createStudioService("backend", "normal").propose(
      project,
      project.draft,
      project.basisId,
      recipe,
    );
    assert.deepEqual(
      calls.map((c) => c.path),
      [
        "/api/explorations/{id}/local/manual-plans",
        "/api/local/plans/{id}/corrections",
      ],
    );
    assert.equal(calls[1].options.params.path.id, "manual-parent");
    assert.deepEqual(calls[1].options.body.recipe, recipe);
    assert.equal(
      calls[0].options.body.expectedDraftRevision,
      project.draftRevision,
    );
    assert.equal(reviewed.id, "corrected-manual");
    assert.deepEqual(reviewed.recipe, recipe);
  } finally {
    api.POST = originalPost;
  }
});

test("reset clears only prototype request and note edits, restoring the canonical request", async () => {
  storage();
  const service = createStudioService("prototype", "normal");
  const extra = await service.sample();
  localStorage.setItem("djound.studio.draft.evening", "An edited request");
  localStorage.setItem(
    `djound.studio.draft.${extra.id}`,
    "Unsaved upload direction",
  );
  localStorage.setItem(
    "djound.melody-edits.v1.prototype.evening.1",
    "prototype edits",
  );
  localStorage.setItem(
    "djound.melody-edits.v1.backend.evening.1",
    "backend edits",
  );
  localStorage.setItem(
    "djound.studio.draft.backend-project",
    "backend request",
  );
  localStorage.setItem("unrelated-app", "leave alone");
  localStorage.setItem("djound.last.prototype", extra.id);
  localStorage.setItem("djound.last.backend", "backend-project");
  // Reset must also find previously saved IDs when the empty scenario hides them.
  createStudioService("prototype", "empty").reset!();
  const reset = createStudioService("prototype", "normal");
  assert.equal((await reset.get("evening")).draft, "Make the backing sparse.");
  assert.equal(localStorage.getItem("djound.studio.draft.evening"), null);
  assert.equal(localStorage.getItem(`djound.studio.draft.${extra.id}`), null);
  assert.equal(
    localStorage.getItem("djound.melody-edits.v1.prototype.evening.1"),
    null,
  );
  assert.equal(
    localStorage.getItem("djound.melody-edits.v1.backend.evening.1"),
    "backend edits",
  );
  assert.equal(
    localStorage.getItem("djound.studio.draft.backend-project"),
    "backend request",
  );
  assert.equal(localStorage.getItem("unrelated-app"), "leave alone");
  assert.equal(localStorage.getItem("djound.last.prototype"), "evening");
  assert.equal(localStorage.getItem("djound.last.backend"), "backend-project");
  assert.equal((await reset.list()).length, 3);
});

test("backend history uses immutable recipes, never request text or the latest proposal", async () => {
  const { api } = await import("../api.ts");
  const originalGet = api.GET;
  const authoritative = {
    instrument: "electric_piano",
    bass: true,
    drums: false,
    density: "sparse",
    backingVolume: "soft",
    bpm: 100,
  };
  const recording = {
    explorationId: "project",
    title: "Version 1",
    id: "version-one",
    kind: "RENDITION",
    provenance: "LOCAL",
    durationMs: 12000,
    mediaUrl: "/api/media/version-one",
    kept: true,
    direction: "Use guitar, add drums and make everything louder.",
    localRecipe: authoritative,
    localNoteRevision: 7,
  };
  const exploration = {
    id: "project",
    title: "Recorded melody",
    updatedAt: "2026-11-05T12:00:00Z",
    sourceRevision: 1,
    draft: {
      revision: 3,
      text: "next request",
      basisRecordingId: "version-one",
    },
    original: {
      ...recording,
      id: "original",
      kind: "ORIGINAL",
      localRecipe: null,
      localNoteRevision: null,
      fileName: "melody.wav",
    },
    renditions: [
      recording,
      { ...recording, id: "legacy", provenance: "LIVE", localRecipe: null },
    ],
  };
  let preparationActive = false;
  let freeformAvailable: boolean | undefined;
  let attemptState: string | null = null;
  try {
    api.GET = (async (path: string) => ({
      data: path.endsWith("/local")
        ? {
            available: true,
            freeformAvailable,
            freeformUnavailableReason:
              freeformAvailable === false
                ? "Install the optional model."
                : null,
            activeJob: preparationActive
              ? { id: "active-preparation", state: "RUNNING" }
              : null,
            notes: { revision: 1, status: "CONFIRMED", notes: [] },
            plan: {
              basisRecordingId: "version-one",
              basisRecipe: {
                ...authoritative,
                instrument: "guitar",
                drums: true,
              },
            },
          }
        : {
            ...exploration,
            latestAttempt: attemptState ? { state: attemptState } : null,
          },
    })) as unknown as typeof api.GET;
    const project = await createStudioService("backend", "normal").get(
      "project",
    );
    assert.deepEqual(project.tracks[1].recipe, authoritative);
    assert.equal(project.tracks[1].recipeKnown, true);
    assert.equal(project.tracks[1].noteRevision, 7);
    assert.equal(project.tracks[0].noteRevision, undefined);
    assert.equal(project.tracks[2].recipeKnown, false);
    assert.deepEqual(project.tracks[0].recipe, {
      instrument: "piano",
      bass: false,
      drums: false,
      density: "steady",
      backingVolume: "normal",
      bpm: 100,
    });
    assert.equal(project.tracks[0].recipeKnown, true);
    assert.equal(project.tracks[0].title, "Original");
    assert.equal(project.processing, false);
    assert.equal(
      project.freeformAvailable,
      true,
      "An older server qualified core and planner together",
    );
    const service = createStudioService("backend", "normal");
    freeformAvailable = false;
    const coreOnly = await service.get("project");
    assert.equal(coreOnly.available, true);
    assert.equal(coreOnly.freeformAvailable, false);
    assert.equal(
      coreOnly.freeformUnavailableReason,
      "Install the optional model.",
    );
    preparationActive = true;
    assert.equal((await service.get("project")).processing, true);
    preparationActive = false;
    for (const state of [
      "ACCEPTED",
      "SUBMITTING",
      "RUNNING",
      "SAVING",
      "RECONCILING",
      "SUCCEEDED",
      "FAILED",
    ]) {
      attemptState = state;
      const current = await service.get("project");
      assert.equal(
        current.processing,
        !["SUCCEEDED", "FAILED"].includes(state),
      );
      assert.equal(
        current.available,
        true,
        "Processing must not change engine readiness",
      );
    }
  } finally {
    api.GET = originalGet;
  }
});

test("a completed language review uses its own plan when another tab has created a newer plan", async () => {
  storage();
  const { api } = await import("../api.ts");
  const project = await createStudioService("prototype", "normal").get(
    "evening",
  );
  const basis = project.tracks.at(-1)!;
  const plan = {
    id: "submitted-plan",
    text: project.draft,
    basisRecordingId: basis.id,
    recipe: basis.recipe,
    basisRecipe: basis.recipe,
    noteRevision: project.noteRevision,
    canGenerate: true,
    warnings: [],
  };
  const job = { id: "submitted-job", state: "SUCCEEDED" };
  const newerPlan = {
    ...plan,
    id: "other-plan",
    text: "another tab's manual review",
  };
  const post = api.POST;
  const get = api.GET;
  try {
    api.POST = (async () => ({
      data: job,
      response: new Response(null, { status: 202 }),
    })) as unknown as typeof api.POST;
    api.GET = (async (
      path: string,
      options: { params: { path: { id: string } } },
    ) => {
      if (path === "/api/explorations/{id}/local")
        return { data: { latestJob: job, activeJob: null, plan: newerPlan } };
      assert.equal(path, "/api/local/jobs/{id}");
      assert.equal(options.params.path.id, job.id);
      return { data: { job, plan } };
    }) as unknown as typeof api.GET;
    const proposal = await createStudioService("backend", "normal").propose(
      project,
      project.draft,
      basis.id,
    );
    assert.equal(proposal.id, plan.id);
    assert.equal(proposal.text, plan.text);
  } finally {
    api.POST = post;
    api.GET = get;
  }
});

test("a reviewed chart edit changes one bar; resting all bars drops only the silent tracks", () => {
  const parent: Recipe = {
    ...DEFAULT_RECIPE,
    bass: true,
    drums: true,
    groove: { pattern: "swing_ride", downbeatMs: 100 },
    bpm: 120,
    harmony: { bpm: 80, downbeatMs: 200, chords: ["C", null, "Ebm"] },
    mix: {
      lead: { gainDb: 0, pan: 0, muted: true },
      drums: { gainDb: 0, pan: 0.4, muted: true },
      chords: { gainDb: -6, pan: -0.5, muted: false, tone: "bright" },
      bass: { gainDb: 2, pan: 0.2, muted: false, tone: "warm" },
    },
  };
  const snapshot = structuredClone(parent);
  const minor = changeBar(parent, 2, "Dm");
  assert.deepEqual(parent, snapshot);
  assert.deepEqual(minor.harmony, {
    ...snapshot.harmony,
    chords: ["C", null, "Dm"],
  });
  assert.deepEqual(minor.mix, parent.mix);
  assert.equal(allTracksMuted(minor), false);
  const rests = changeBar(changeBar(minor, 0, null), 2, null);
  assert.deepEqual(activeStems(rests), ["lead", "drums"]);
  assert.deepEqual(rests.mix, {
    lead: parent.mix?.lead,
    drums: parent.mix?.drums,
  });
  assert.equal(allTracksMuted(rests), true);
  assert.deepEqual(rests.groove, parent.groove);
  assert.deepEqual(parent, snapshot);
  const resumed = changeBar(rests, 1, "F#m");
  assert.deepEqual(activeStems(resumed), ["lead", "bass", "drums", "chords"]);
  assert.equal(resumed.mix?.bass, undefined);
  assert.equal(resumed.mix?.chords, undefined);
  assert.equal(allTracksMuted(resumed), false);
  const removed = removeChart(minor);
  assert.deepEqual(activeStems(removed), ["lead", "bass", "drums"]);
  assert.equal(removed.harmony, undefined);
  assert.equal(removed.mix?.chords, undefined);
  assert.deepEqual(removed.mix?.bass, parent.mix?.bass);
  assert.deepEqual(parent, snapshot);
});

test("backing notices and version tempos follow the parts scheduled to play", () => {
  const solo: Recipe = { ...DEFAULT_RECIPE, bass: false, drums: false };
  const chartOnly: Recipe = {
    ...solo,
    harmony: { bpm: 73, downbeatMs: 200, chords: ["Dm", null] },
  };
  assert.equal(hasActiveBacking(solo), false);
  assert.equal(backingTiming(solo), "No backing parts are active.");
  assert.equal(hasActiveBacking(chartOnly), true);
  assert.equal(backingTiming(chartOnly), "Chords follow the 73 BPM chart.");
  const band: Recipe = {
    ...chartOnly,
    bass: true,
    drums: true,
    bpm: 112,
    groove: { pattern: "swing_ride", downbeatMs: 0 },
  };
  assert.equal(
    backingTiming(band),
    "Chords and bass follow the 73 BPM chart. Drums play at 112 BPM.",
  );
  const resting = changeBar(band, 0, null);
  assert.equal(hasActiveBacking(resting), true);
  assert.equal(
    backingTiming(resting),
    "Chords and bass rest in every bar. Drums play at 112 BPM.",
  );
  const restingWithoutDrums = { ...resting, drums: false };
  assert.equal(hasActiveBacking(restingWithoutDrums), false);
  assert.equal(
    backingTiming(restingWithoutDrums),
    "Chords and bass rest in every bar.",
  );
  assert.equal(
    backingTiming(removeChart(restingWithoutDrums)),
    "Bass plays at 112 BPM.",
  );
});

test("suggestion evidence must match the grid and cannot change the reviewed chart", () => {
  const chart: NonNullable<Recipe["harmony"]> = {
    bpm: 120,
    downbeatMs: 100,
    chords: ["D", null],
  };
  const raw = [
    {
      start: 0.1,
      end: 2.1,
      chord: "Am",
      ambiguous: true,
      alternatives: ["C", "Em"],
    },
    { start: 2.1, end: 3, chord: null, ambiguous: false, alternatives: [] },
  ];
  assert.deepEqual(chordSuggestions(chart, raw), [
    { chord: "Am", ambiguous: true, alternatives: ["C", "Em"] },
    { chord: null, ambiguous: false, alternatives: [] },
  ]);
  assert.deepEqual(chart.chords, ["D", null]);
  assert.equal(chordSuggestions(chart, undefined), undefined);
  assert.equal(chordSuggestions(chart, [raw[0]]), undefined);
  assert.equal(chordSuggestions({ ...chart, downbeatMs: 0 }, raw), undefined);
  assert.equal(
    chordSuggestions(chart, [raw[0], { ...raw[1], alternatives: ["H"] }]),
    undefined,
  );
});

test("chart review explains enabled bass resting and resuming without a switch change", () => {
  const chart: Recipe = {
    ...DEFAULT_RECIPE,
    bass: false,
    harmony: { bpm: 73, downbeatMs: 200, chords: [null, null] },
  };
  const enabled = { ...chart, bass: true };
  assert.equal(
    chartBassNotice(enabled, chart),
    "Bass stays silent until a bar has a chord.",
  );
  assert.equal(
    chartBassNotice(removeChart(enabled), enabled),
    "Bass resumes the melody pulse.",
  );
  assert.equal(chartBassNotice(removeChart(chart), chart), undefined);
  assert.equal(
    chartBassNotice(changeBar(enabled, 0, "Dm"), enabled),
    undefined,
  );
});

test("tone audition binds the saved basis and revisions without changing the draft or creating a version", async () => {
  storage();
  const project = await createStudioService("prototype", "normal").get(
    "evening",
  );
  const basis = {
    ...project.tracks[1],
    recipeKnown: true,
    noteRevision: project.noteRevision,
  };
  const current = {
    ...project,
    basisId: basis.id,
    tracks: [project.tracks[0], basis],
    draft: "my original words",
  };
  const snapshot = structuredClone(current);
  const { api } = await import("../api.ts");
  const { POST } = api;
  const signal = new AbortController().signal;
  const wav = new Blob(["saved mix"], { type: "audio/wav" });
  const service = createStudioService("backend", "normal");
  try {
    api.POST = (async (path: string, options: any) => {
      assert.equal(path, "/api/explorations/{id}/local/tone-auditions");
      assert.deepEqual(options.params, { path: { id: project.id } });
      assert.deepEqual(options.body, {
        basisRecordingId: basis.id,
        expectedNoteRevision: current.noteRevision,
        expectedSourceRevision: current.sourceRevision,
        expectedDraftRevision: current.draftRevision,
        stem: "lead",
        tone: "neutral",
      });
      assert.equal(options.signal, signal);
      assert.equal(options.parseAs, "blob");
      assert.equal(options.cache, "no-store");
      return { data: wav };
    }) as unknown as typeof api.POST;
    assert.equal(
      await service.auditionTone!(current, basis.id, "lead", "neutral", signal),
      wav,
    );
    api.POST = (async () => ({
      error: { detail: "Dry stems unavailable." },
    })) as unknown as typeof api.POST;
    await assert.rejects(
      service.auditionTone!(current, basis.id, "lead", "warm", signal),
      /Dry stems unavailable/,
    );
    assert.deepEqual(current, snapshot);
  } finally {
    api.POST = POST;
  }
});

test("auditions compare only one audible stem in the current unchanged arrangement", async () => {
  storage();
  const project = await createStudioService("prototype", "normal").get(
    "evening",
  );
  const bright = { gainDb: 0, pan: 0, muted: false, tone: "bright" } as const;
  const basis = {
    ...project.tracks[1],
    recipeKnown: true,
    noteRevision: project.noteRevision,
    recipe: { ...DEFAULT_RECIPE, mix: { lead: bright } },
  };
  const current = { ...project, basisId: basis.id };
  const neutral: Recipe = { ...DEFAULT_RECIPE };
  assert.equal(auditionUnavailable(current, basis, neutral, "lead"), undefined);
  assert.match(
    auditionUnavailable(current, basis, neutral, "bass")!,
    /other changes/,
  );
  assert.match(
    auditionUnavailable(current, basis, { ...neutral, drums: false }, "lead")!,
    /other changes/,
  );
  assert.match(
    auditionUnavailable(
      current,
      basis,
      { ...neutral, mix: { lead: { ...bright, gainDb: -1 } } },
      "lead",
    )!,
    /other changes/,
  );
  assert.match(
    auditionUnavailable(current, basis, neutral, "chords")!,
    /track that plays/,
  );
  const muted = {
    ...basis,
    recipe: { ...basis.recipe, mix: { lead: { ...bright, muted: true } } },
  };
  assert.match(
    auditionUnavailable(current, muted, muted.recipe, "lead")!,
    /track that plays/,
  );
  assert.match(
    auditionUnavailable(
      { ...current, noteRevision: 99 },
      basis,
      neutral,
      "lead",
    )!,
    /current melody/,
  );
  assert.match(
    auditionUnavailable(
      { ...current, basisId: "other" },
      basis,
      neutral,
      "lead",
    )!,
    /basis to save/,
  );
  const preview = {
    ...basis,
    kind: "preview" as const,
    id: "preview",
    auditionBasisId: basis.id,
  };
  assert.equal(auditionStart(basis, preview, 4, false), 4);
  assert.equal(auditionStart(preview, preview, 5, false), 5);
  assert.equal(auditionStart(preview, basis, 5, false), 0);
  assert.equal(auditionStart(preview, preview, 12, true), 0);
  assert.equal(auditionStart(current.tracks[0], preview, 5, false), 0);
});
