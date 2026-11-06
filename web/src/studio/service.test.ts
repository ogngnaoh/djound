import test from "node:test";
import assert from "node:assert/strict";
import { createStudioService } from "./service.ts";

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
  let attemptState: string | null = null;
  try {
    api.GET = (async (path: string) => ({
      data: path.endsWith("/local")
        ? {
            available: true,
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
    const service = createStudioService("backend", "normal");
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
