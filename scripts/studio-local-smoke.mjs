// Real LOCAL inference through the redesigned Studio, in an isolated installation.
import { createRequire } from "node:module";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { createHash } from "node:crypto";

const require = createRequire(new URL("../web/package.json", import.meta.url));
const { chromium, expect } = require("@playwright/test");
const base = process.env.DJOUND_TEST_BASE_URL;
const guidedWalkthrough = process.env.DJOUND_CHECK_GUIDED === "1";
const audioOnly = process.env.DJOUND_CHECK_AUDIO_ONLY === "1";
const jazzWalkthrough = process.env.DJOUND_CHECK_JAZZ === "1";
if (audioOnly && !guidedWalkthrough && !jazzWalkthrough)
  throw new Error("Use DJOUND_CHECK_GUIDED=1 for the audio-only walkthrough.");
if (
  !/^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(base || "") ||
  process.env.DJOUND_TEST_ALLOW_WRITE !== "1"
)
  throw new Error(
    "Use an isolated loopback LOCAL installation with DJOUND_TEST_ALLOW_WRITE=1.",
  );
const root = fileURLToPath(new URL("../", import.meta.url));
const evidence =
  process.env.DJOUND_SCREENSHOT_DIR || `/tmp/djound-studio-local-${Date.now()}`;
await mkdir(evidence, { recursive: true });
const chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const browser = await chromium.launch({
  headless: true,
  ...(existsSync(chrome) ? { executablePath: chrome } : {}),
});
const context = await browser.newContext({
  viewport: { width: 1440, height: 960 },
  ...(process.env.DJOUND_RECORD_VIDEO === "1"
    ? {
        recordVideo: {
          dir: path.join(evidence, "video"),
          size: { width: 1440, height: 960 },
        },
      }
    : {}),
});
const page = await context.newPage();
page.setDefaultTimeout(15000);
const errors = [],
  external = [],
  submissions = [],
  plannerRequests = [],
  auditions = [],
  timings = [];
let id,
  lostReceipt,
  expectedPlannerFailure = false,
  expectedLostResponse = false;
page.on("pageerror", (error) => errors.push(error.message));
page.on("console", (message) => {
  if (message.type() !== "error") return;
  const url = message.location().url;
  if (
    expectedPlannerFailure &&
    url.includes(`/api/explorations/${id}/local/plans`) &&
    message.text().includes("503")
  )
    return;
  if (
    expectedLostResponse &&
    url.includes(`/api/explorations/${id}/requests`) &&
    message.text().includes("ERR_FAILED")
  )
    return;
  errors.push(message.text());
});
page.on("request", (request) => {
  if (/^https?:/.test(request.url()) && new URL(request.url()).origin !== base)
    external.push(request.url());
  if (request.method() === "POST" && /\/requests$/.test(request.url()))
    submissions.push({
      key: request.headers()["idempotency-key"],
      body: request.postDataJSON(),
    });
  if (request.method() === "POST" && /\/local\/plans$/.test(request.url()))
    plannerRequests.push(request.postDataJSON());
});
const button = (name) => page.getByRole("button", { name, exact: true });
const direction = () =>
  page.getByRole("textbox", { name: "Describe a change", exact: true });
const row = (title) =>
  page.locator(".version-row").filter({ has: button(`Inspect ${title}`) });
const shot = (name) =>
  page.screenshot({
    path: path.join(evidence, `${name}.png`),
    fullPage: false,
    animations: "disabled",
  });
const sha256 = (bytes) => createHash("sha256").update(bytes).digest("hex");
async function get(route) {
  const response = await context.request.get(base + route);
  expect(response.status(), await response.text()).toBe(200);
  return response.json();
}
const local = () => get(`/api/explorations/${id}/local`);
const exploration = () => get(`/api/explorations/${id}`);
async function openGuidedEdits() {
  const panel = page.locator("details.guided-edits");
  await expect(panel).toBeVisible();
  if ((await panel.getAttribute("open")) === null)
    await panel.locator("summary").click();
  return panel;
}
async function settled(label, started) {
  await expect
    .poll(async () => (await local()).activeJob, { timeout: 180000 })
    .toBeNull();
  const state = await local();
  expect(state.latestJob?.state, JSON.stringify(state)).toBe("SUCCEEDED");
  timings.push({ operation: label, milliseconds: Date.now() - started });
  return state;
}
async function useVersion(recording) {
  await row(recording.title)
    .getByRole("button", { name: "Use this version", exact: true })
    .click();
  await expect
    .poll(async () => (await exploration()).draft.basisRecordingId)
    .toBe(recording.id);
  await expect(
    page.getByRole("heading", {
      name: `Refining ${recording.title}`,
      exact: true,
    }),
  ).toBeVisible();
  await expect(direction()).toBeVisible();
}
async function interpret(text) {
  if (text !== undefined) await direction().fill(text);
  const before = (await local()).latestJob?.id;
  const started = Date.now();
  await button("Review changes").click();
  await expect(
    page.getByRole("heading", { name: "Proposed changes", exact: true }),
  ).toBeVisible({ timeout: 90000 });
  const state = await settled("plan", started);
  expect(state.latestJob.id).not.toBe(before);
  expect(state.plan.canGenerate, JSON.stringify(state.plan)).toBe(true);
  await expect(button("Create version")).toBeEnabled();
  return state.plan;
}
async function auditionTone(basis, tone) {
  const previousUrl = await page.locator("audio").evaluate(a => a.currentSrc);
  const before = Date.now();
  const reply = page.waitForResponse((r) =>
    r.request().method() === "POST" && r.url().endsWith(`/api/explorations/${id}/local/tone-auditions`));
  await button("Preview tone").click();
  const response = await reply;
  expect(response.status()).toBe(200);
  expect(response.headers()["content-type"]).toContain("audio/wav");
  expect(response.headers()["cache-control"]).toContain("no-store");
  expect(response.request().postDataJSON()).toMatchObject({basisRecordingId: basis.id, stem: "lead", tone});
  await expect.poll(() => page.locator("audio").evaluate((a, previous) =>
    !a.paused && a.currentSrc !== previous && a.currentSrc.startsWith("blob:") && a.currentTime > .05,
    previousUrl)).toBe(true);
  const clickToPlayingMs = Date.now() - before;
  // Read the exact Blob that the single transport is playing. Preview responses
  // use no-store and need not retain a separate network-debugger body.
  const played = await page.locator("audio").evaluate(async a => {
    const bytes = await (await fetch(a.currentSrc)).arrayBuffer();
    const hash = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
    return {size: bytes.byteLength, sha256: Array.from(hash, n => n.toString(16).padStart(2, "0")).join("")};
  });
  expect(played.size).toBeGreaterThan(44);
  expect(played.size).toBe(Number(response.headers()["content-length"]));
  auditions.push({basisTitle: basis.title, stem: "lead", tone, wavSha256: played.sha256, clickToPlayingMs});
  return played.sha256;
}
async function create(count, loseResponse = false) {
  const previousAttempt = (await exploration()).latestAttempt?.id;
  const routePattern = `**/api/explorations/${id}/requests`;
  if (loseResponse) {
    expectedLostResponse = true;
    await page.route(
      routePattern,
      async (route) => {
        // The server accepts one real render. Only delivery of its receipt is lost.
        const response = await route.fetch();
        expect(response.status()).toBe(202);
        lostReceipt = await response.json();
        await route.abort("failed");
      },
      { times: 1 },
    );
  }
  const started = Date.now();
  await button("Create version").click();
  if (loseResponse) {
    await expect.poll(() => !!lostReceipt).toBe(true);
    await expect(
      page
        .getByRole("alert")
        .filter({ hasText: /fetch|interrupted|response/i }),
    ).toBeVisible();
    await page.unroute(routePattern);
    await page.goto(`${base}/remix/${id}?mode=backend`);
    const replayResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().endsWith(`/api/explorations/${id}/requests`),
    );
    await button("Recover previous creation").click();
    const replay = await (await replayResponse).json();
    expect(replay.replayed).toBe(true);
    expect(replay.attemptId).toBe(lostReceipt.attemptId);
    await expect(button("Recover previous creation")).toHaveCount(0, {
      timeout: 60000,
    });
  } else {
    await expect(
      page.getByRole("heading", {
        name: `Version ${count} is ready.`,
        exact: true,
      }),
    ).toBeVisible({ timeout: 60000 });
  }
  await expect
    .poll(
      async () => {
        const attempt = (await exploration()).latestAttempt;
        return (
          attempt?.id !== previousAttempt &&
          ["SUCCEEDED", "FAILED"].includes(attempt?.state)
        );
      },
      { timeout: 60000 },
    )
    .toBe(true);
  const saved = await exploration();
  expect(saved.latestAttempt.state, JSON.stringify(saved.latestAttempt)).toBe(
    "SUCCEEDED",
  );
  if (loseResponse) expect(saved.latestAttempt.id).toBe(lostReceipt.attemptId);
  expect(saved.renditions).toHaveLength(count);
  const recording = saved.renditions.at(-1);
  expect(recording.provenance).toBe("LOCAL");
  await expect(button(`Inspect ${recording.title}`)).toBeVisible();
  const response = await context.request.get(base + recording.mediaUrl);
  expect(response.status()).toBe(200);
  const bytes = await response.body();
  expect(sha256(bytes)).toBe(recording.sha256);
  await writeFile(path.join(evidence, `version-${count}.wav`), bytes);
  const range = await context.request.get(base + recording.mediaUrl, {
    headers: { Range: "bytes=0-127" },
  });
  expect(range.status()).toBe(206);
  expect((await range.body()).length).toBe(128);
  timings.push({
    operation: loseResponse ? "render-with-receipt-recovery" : "render",
    milliseconds: Date.now() - started,
  });
  return recording;
}
async function pause() {
  await button("Pause playback").click();
  expect(await page.locator("audio").evaluate((a) => a.paused)).toBe(true);
}
async function playRow(recording) {
  await row(recording.title)
    .getByRole("button", { name: `Play ${recording.title}`, exact: true })
    .click();
  await expect
    .poll(() =>
      page
        .locator("audio")
        .evaluate((a) => (a.currentSrc ? new URL(a.currentSrc).pathname : "")),
    )
    .toBe(recording.mediaUrl);
  await expect
    .poll(() => page.locator("audio").evaluate((a) => a.currentTime))
    .toBeGreaterThan(0.1);
}
async function walkthrough() {
  expect((await get("/api/workspace")).mode).toBe("LOCAL");
  await page.goto(`${base}/remix/new?mode=backend`);
  await expect(page).toHaveTitle(/Djound/);
  await expect(
    page.getByRole("heading", { name: "Start with a melody.", exact: true }),
  ).toBeVisible();
  await expect(page.locator("vite-error-overlay")).toHaveCount(0);
  const [chooser] = await Promise.all([
    page.waitForEvent("filechooser"),
    button("Choose audio file").and(page.locator("button")).click(),
  ]);
  const sourcePath = (jazzWalkthrough && process.env.DJOUND_JAZZ_SOURCE) ||
    path.join(root, "fixtures/audio/local-demo-source.wav");
  await chooser.setFiles(sourcePath);
  await button("Continue to melody review").click();
  await expect(page).toHaveURL(/\/remix\/exp_[^/]+\/melody/);
  id = new URL(page.url()).pathname.split("/")[2];
  const source = (await exploration()).original;
  expect(source.sha256).toBe(sha256(await readFile(sourcePath)));
  await expect(button("Detect melody")).toBeEnabled();
  const started = Date.now();
  await button("Detect melody").click();
  await expect(
    page.getByRole("link", { name: "Edit notes", exact: true }),
  ).toBeVisible({ timeout: 180000 });
  const state = await settled("transcribe", started);
  if (audioOnly) {
    expect(state.available).toBe(true);
    expect(state.freeformAvailable).toBe(false);
    expect(state.freeformUnavailableReason).toBeTruthy();
  }
  expect(state.notes.notes.length).toBeGreaterThan(jazzWalkthrough ? 0 : 10);
  await expect(
    page.getByLabel("Selected note pitch", { exact: true }),
  ).toHaveCount(0);
  const choices = page.getByRole("group", {
    name: "Choose a recording to play",
  });
  await choices
    .getByRole("button", { name: "Detected melody", exact: true })
    .click();
  await expect
    .poll(() => page.locator("audio").evaluate((a) => a.currentTime))
    .toBeGreaterThan(0.1);
  await choices.getByRole("button", { name: "Original", exact: true }).click();
  await expect
    .poll(() =>
      page
        .locator("audio")
        .evaluate((a) => (a.currentSrc ? new URL(a.currentSrc).pathname : "")),
    )
    .toBe(source.mediaUrl);
  await expect(page.locator("audio")).toHaveCount(1);
  await pause();
  await shot("01-melody-review");
  await button("Use this melody").click();
  await expect(page).toHaveURL(new RegExp(`/remix/${id}$`));
  await expect(
    page.getByRole("heading", { name: "Refining Original", exact: true }),
  ).toBeVisible();
  const confirmed = (await local()).notes;
  expect(confirmed.status).toBe("CONFIRMED");
  expect(confirmed.notes).toEqual(state.notes.notes);

  if (jazzWalkthrough) {
    const previousWording = "Make this a jazz orchestra with a saxophone solo";
    await direction().fill(previousWording);
    await button("Review changes").click();
    await expect(page.getByRole("alert")).toBeVisible({timeout: 30000});
    expect((await exploration()).renditions).toHaveLength(0);
    await button("Try swing combo").click();
    await expect(direction()).toHaveValue("arrange this melody as a swing combo");
    const preset = await interpret();
    await button("Edit request").click();
    await button("Return to previous draft").click();
    await expect(direction()).toHaveValue(previousWording);
    const jazz = await interpret("make this soundtrack jazz like");
    expect(jazz.recipe).toEqual(preset.recipe);
    expect(jazz.basisRecordingId).toBe(source.id);
    expect(jazz.recipe).toMatchObject({
      instrument: "recorded", bass: true, drums: true,
      groove: { pattern: "swing_ride" }, accompanimentStyle: "swing_combo",
    });
    expect(jazz.recipe.harmony.chords.some(Boolean)).toBe(true);
    await expect(page.getByLabel("Chart accompaniment")).toHaveValue("swing_combo");
    await shot("02-jazz-review");
    const first = await create(1);
    await playRow(first);
    await pause();
    await useVersion(first);
    const quieter = await interpret("quieter drums");
    const priorMix = jazz.recipe.mix ?? {};
    const strip = priorMix.drums ?? {gainDb: 0, pan: 0, muted: false};
    expect(quieter.recipe).toEqual({...jazz.recipe, mix: {...priorMix,
      drums: {...strip, gainDb: strip.gainDb - 3}}});
    expect(quieter.basisRecordingId).toBe(first.id);
    const second = await create(2);
    await playRow(second);
    await pause();
    await shot("03-refined-combo");
    expect((await local()).notes).toEqual(confirmed);
    expect(errors).toEqual([]);
    expect(external).toEqual([]);
    expect(submissions).toHaveLength(2);
    const original = await context.request.get(base + source.mediaUrl);
    expect(original.ok()).toBe(true);
    await writeFile(path.join(evidence, "original.wav"), await original.body());
    console.log(JSON.stringify({result: "PASS", explorationId: id, evidence, timings,
      plans: [jazz, quieter], snapshot: await exploration(), localSnapshot: await local(),
      acceptedKey: submissions[1].key, acceptedCommand: submissions[1].body,
      attemptId: second.attemptId}, null, 2));
    return;
  }

  const firstPlan = await interpret(
    guidedWalkthrough ? "edit: use electric piano; add bass; add drums" : "Use electric piano, add bass and a steady drum beat.",
  );
  if (guidedWalkthrough) expect(firstPlan.model.origin).toBe("GUIDED_COMMANDS");
  expect(firstPlan.recipe).toMatchObject({
    instrument: "electric_piano",
    bass: true,
    drums: true,
    density: "steady",
  });
  await shot("02-reviewed-text");
  const first = await create(1);
  expect(first.localRecipe).toEqual(firstPlan.recipe);
  expect(first.localNoteRevision).toBe(firstPlan.noteRevision);
  await shot("03-first-result");
  await useVersion(first);
  const secondPlan = await interpret(
    guidedWalkthrough ? "edit: remove drums; make the backing soft" : "Remove drums and make the backing quieter.",
  );
  if (guidedWalkthrough) expect(secondPlan.model.origin).toBe("GUIDED_COMMANDS");
  expect(secondPlan.recipe).toMatchObject({
    instrument: "electric_piano",
    bass: true,
    drums: false,
    backingVolume: "soft",
  });
  expect(secondPlan.basisRecordingId).toBe(first.id);
  const second = await create(2);
  expect(second.localRecipe).toEqual(secondPlan.recipe);
  expect(second.sha256).not.toBe(first.sha256);
  expect((await local()).notes).toEqual(confirmed);
  expect(
    (await exploration()).renditions.find((r) => r.id === first.id),
  ).toEqual(first);
  await shot("04-refinement-result");

  await useVersion(second);
  const recoveryDraft = audioOnly
    ? "Give the rhythm the hush of a room after closing time."
    : "Use guitar for this version.";
  await direction().fill(recoveryDraft);
  if (audioOnly) {
    await expect(button("Review changes")).toBeEnabled();
    await expect(button("Adjust arrangement")).toBeEnabled();
    await button("Review changes").click();
    await expect(page.getByRole("alert")).toContainText(state.freeformUnavailableReason, {timeout: 20000});
    expect((await local()).latestJob).toMatchObject({state: "FAILED", outcomeCode: "LOCAL_PLANNER_UNAVAILABLE"});
    expect((await exploration()).renditions).toHaveLength(2);
    await expect(button("Create version")).toHaveCount(0);
    await page.reload();
    await expect(direction()).toHaveValue(recoveryDraft);
    await expect(button("Review changes")).toBeEnabled();
    const panel = await openGuidedEdits();
    await button("Compose guided edit").click();
    await page.getByLabel("Add a guided edit").selectOption("use guitar");
    await expect(panel.getByText(recoveryDraft, {exact:true})).toBeVisible();
    await expect(button("Review changes")).toBeEnabled();
  }
  const recoveryText = await direction().inputValue();
  const preparationJob = (await local()).latestJob.id;
  const plannerPattern = `**/api/explorations/${id}/local/plans`;
  expectedPlannerFailure = true;
  await page.route(
    plannerPattern,
    (route) =>
      route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          code: "LOCAL_ENGINE_UNAVAILABLE",
          detail: "Planner unavailable for this recovery check.",
        }),
      }),
    { times: 1 },
  );
  await button("Review changes").click();
  await expect(page.getByRole("alert")).toContainText(
    "Planner unavailable for this recovery check.",
  );
  await expect(direction()).toHaveValue(recoveryText);
  await page.unroute(plannerPattern);
  await shot("05-planner-unavailable");
  await button("Adjust arrangement").click();
  await expect(page.getByLabel("Lead", { exact: true })).toHaveValue(
    "electric_piano",
  );
  await expect(page.getByLabel("Bass", { exact: true })).toHaveValue("on");
  await expect(page.getByLabel("Drums", { exact: true })).toHaveValue("off");
  await expect(page.getByLabel("Backing volume", { exact: true })).toHaveValue(
    "soft",
  );
  await page.getByLabel("Lead", { exact: true }).selectOption("guitar");
  const initialPlanResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().endsWith(`/api/explorations/${id}/local/manual-plans`),
  );
  await button("Review arrangement").click();
  const correctionProposal = await (await initialPlanResponse).json();
  await expect(
    page.getByRole("heading", { name: "Proposed changes", exact: true }),
  ).toBeVisible();
  const correctedPlan = (await local()).plan;
  expect(correctionProposal.recipe).toEqual(secondPlan.recipe);
  expect(correctedPlan.recipe).toEqual({
    ...secondPlan.recipe,
    instrument: "guitar",
  });
  expect(correctedPlan.model.origin).toBe("USER_DEFINED");
  expect(correctedPlan.model.correction.kind).toBe("USER_EDIT");
  expect(correctedPlan.model.correction.initialRecipe).toEqual(
    secondPlan.recipe,
  );
  expect(correctedPlan.model.correction.modelSuggestedRecipe).toBeUndefined();
  expect((await local()).latestJob.id).toBe(preparationJob);
  await shot("06-direct-controls-review");
  const third = await create(3, true);
  expect(third.localRecipe).toEqual(correctedPlan.recipe);
  expect(third.direction).toContain("Guitar");
  expect((await local()).notes).toEqual(confirmed);
  expect((await exploration()).original).toEqual(source);
  expect(plannerRequests).toHaveLength(audioOnly ? 4 : 3);
  expect(submissions).toHaveLength(4); // Third receipt replay does not create a fourth version.
  expect(new Set(submissions.map((s) => s.key)).size).toBe(3);
  expect(submissions[3]).toEqual(submissions[2]);

  await playRow(second);
  await pause();
  await page.locator("audio").evaluate((a) => {
    a.currentTime = 5;
  });
  await button("Play playback").click();
  await expect
    .poll(() => page.locator("audio").evaluate((a) => a.currentTime))
    .toBeGreaterThan(5);
  await pause();
  await playRow({ ...source, title: "Original" });
  await pause();
  expect(
    await page.locator("audio").evaluate((a) => a.currentTime),
  ).toBeLessThan(1);
  await playRow(second);
  await pause();
  expect(
    await page.locator("audio").evaluate((a) => a.currentTime),
  ).toBeLessThan(1);
  await page.locator("audio").evaluate((a) => {
    a.currentTime = 5;
  });
  await button("Restart recording").click();
  expect(await page.locator("audio").evaluate((a) => a.currentTime)).toBe(0);
  expect(await page.locator("audio").evaluate((a) => a.paused)).toBe(true);
  await button("Inspect Version 3").click();
  await expect(page.locator(".transport-identity")).toContainText("Version 2");
  await row("Version 2")
    .getByRole("button", { name: "Keep Version 2", exact: true })
    .click();
  await expect
    .poll(
      async () =>
        (await exploration()).renditions.find((r) => r.id === second.id).kept,
    )
    .toBe(true);
  await shot("07-studio-final");

  // Review a direct proposal, then edit its words: old approval must no longer be creatable.
  await button("Adjust arrangement").click();
  await button("Review arrangement").click();
  await expect(button("Create version")).toBeEnabled();
  const stalePlan = (await local()).plan;
  const beforeChange = await exploration();
  const changedResponse = await context.request.put(
    `${base}/api/explorations/${id}/draft`,
    {
      data: {
        text: "Changed elsewhere; this old plan must not create audio.",
        basisRecordingId: stalePlan.basisRecordingId,
        expectedDraftRevision: beforeChange.draft.revision,
      },
    },
  );
  expect(changedResponse.status()).toBe(200);
  const changed = await exploration();
  await expect(button("Create version")).toHaveCount(0, { timeout: 10000 });
  await expect(page.getByRole("alert")).toContainText(/changed|review/i);
  const stale = await context.request.post(
    `${base}/api/explorations/${id}/requests`,
    {
      headers: { "Idempotency-Key": crypto.randomUUID() },
      data: {
        text: changed.draft.text,
        basisRecordingId: changed.draft.basisRecordingId,
        expectedSourceRevision: changed.sourceRevision,
        expectedDraftRevision: changed.draft.revision,
        localPlanId: stalePlan.id,
      },
    },
  );
  expect(stale.status()).toBe(409);
  expect((await stale.json()).code).toBe("PLAN_CHANGED");
  expect((await exploration()).renditions).toHaveLength(3);
  // Leave the isolated review installation clean, with no fabricated unsent request.
  const cleared = await context.request.put(
    `${base}/api/explorations/${id}/draft`,
    {
      data: {
        text: "",
        basisRecordingId: second.id,
        expectedDraftRevision: changed.draft.revision,
      },
    },
  );
  expect(cleared.status()).toBe(200);
  await page.evaluate(
    (projectId) => localStorage.removeItem(`djound.studio.draft.${projectId}`),
    id,
  );
  await page.goto(`${base}/remix/${id}?mode=backend&recording=${second.id}`);
  await expect(button("Inspect Version 2")).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  // Readiness changes must refresh without a reload or another inference attempt.
  await direction().fill(audioOnly ? "edit: use piano" : "Use piano.");
  await expect(button("Review changes")).toBeEnabled();
  const localPattern = `**/api/explorations/${id}/local`;
  await page.route(localPattern, async (route) => {
    const response = await route.fetch();
    const state = await response.json();
    await route.fulfill({
      response,
      json: {
        ...state,
        available: false,
        unavailableReason:
          "Engine temporarily unavailable for readiness check.",
      },
    });
  });
  await expect(button("Review changes")).toBeDisabled({ timeout: 10000 });
  await expect(button("Adjust arrangement")).toBeDisabled();
  await expect(
    page.getByText("Engine temporarily unavailable for readiness check.", {
      exact: false,
    }),
  ).toBeVisible();
  await page.unroute(localPattern);
  await expect(button("Review changes")).toBeEnabled({ timeout: 10000 });
  await expect(button("Adjust arrangement")).toBeEnabled();
  await direction().fill("");
  await page.evaluate(
    (projectId) => localStorage.removeItem(`djound.studio.draft.${projectId}`),
    id,
  );
  await page.setViewportSize({ width: 390, height: 844 });
  await shot("08-mobile-studio");
  await button("Describe a change").click();
  await expect(direction()).toBeVisible();
  await expect(button("Adjust arrangement")).toBeEnabled();
  await shot("09-mobile-controls");
  await page.keyboard.press("Escape");
  for (const width of [320, 390, 1440]) {
    await page.setViewportSize({ width, height: 960 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  }
  await page.goto(`${base}/?mode=backend`);
  await expect(
    page.getByRole("heading", { name: "Library", exact: true }),
  ).toBeVisible();
  const kept = page
    .locator(".kept-row")
    .filter({ hasText: `${changed.title} · Version 2` });
  await expect(kept).toBeVisible();
  await kept.getByRole("button", { name: "Play", exact: true }).click();
  await expect
    .poll(() =>
      page
        .locator("audio")
        .evaluate((a) => (a.currentSrc ? new URL(a.currentSrc).pathname : "")),
    )
    .toBe(second.mediaUrl);
  await pause();
  await shot("10-library");
  await kept.getByRole("link", { name: "Open project", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`recording=${second.id}`));
  await expect(button("Inspect Version 2")).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await page.reload();
  await expect(button("Inspect Version 2")).toBeVisible();
  expect(await page.locator("audio").evaluate((a) => a.paused)).toBe(true);
  const extraPlans = [];
  if (process.env.DJOUND_VERIFY_MIX !== "0") {
    await useVersion(first);
    const wording = "Let the drums sit this one out and move the bass a bit left.";
    await direction().fill(wording);
    const guided = await openGuidedEdits();
    const reviewCount = plannerRequests.length;
    const renderCount = submissions.length;
    await button("Compose guided edit").click();
    await expect(guided.getByText(wording, {exact: true})).toBeVisible();
    await expect(button("Review changes")).toBeDisabled();
    const picker = page.getByLabel("Add a guided edit");
    await picker.selectOption("lower bass by 3 dB");
    await page.reload();
    await openGuidedEdits();
    await expect(guided.getByText(wording, {exact: true})).toBeVisible();
    await expect(direction()).toHaveValue("edit: lower bass by 3 dB");
    await button("Restore original wording").click();
    await expect(direction()).toHaveValue(wording);
    await button("Resume guided draft").click();
    await expect(direction()).toHaveValue("edit: lower bass by 3 dB");
    expect(plannerRequests.length).toBe(reviewCount);
    expect(submissions.length).toBe(renderCount);
    await picker.selectOption("pan bass 50% left");
    await picker.selectOption("mute drums");
    await expect(direction()).toHaveValue("edit: lower bass by 3 dB; pan bass 50% left; mute drums");
    const mixPlan = await interpret((await direction().inputValue()).replace("3 dB","6 dB"));
    expect(mixPlan.model).toMatchObject({origin: "GUIDED_COMMANDS", inferenceAttempts: 0});
    expect(mixPlan.recipe.mix).toEqual({
      bass: {gainDb:-6, pan:-0.5, muted:false},
      drums: {gainDb:0, pan:0, muted:true},
    });
    for (const change of ["Bass gain","Bass pan","Drums playback"]) {
      await expect(page.locator(".change-row").filter({hasText:change})).toBeVisible();
    }
    await shot("11-guided-mix-review");
    const mixed = await create(4);
    expect(mixed.sha256).not.toBe(first.sha256);
    await useVersion(mixed);
    await direction().fill("edit: raise bass by 3 dB; reverse the melody");
    await button("Review changes").click();
    await expect(page.getByRole("alert")).toContainText(/command|guided|edit:/i, {timeout:20000});
    await expect(button("Create version")).toHaveCount(0);
    expect((await exploration()).renditions).toHaveLength(4);

    const unmutePlan = await interpret("edit: unmute drums");
    await button("Adjust arrangement").click();
    await page.getByText("Track mix", {exact:true}).click();
    await page.getByLabel("Mix track").selectOption("bass");
    await expect(page.getByLabel("Track gain")).toHaveValue("-6");
    await expect(page.getByLabel("Track pan")).toHaveValue("-50");
    await shot("12-bass-mix-controls");
    await button("Reset bass mix").click();
    await page.getByLabel("Mix track").selectOption("drums");
    await expect(page.getByLabel("Mute drums")).not.toBeChecked();
    await button("Review arrangement").click();
    await expect(button("Create version")).toBeVisible();
    const restoredPlan = (await local()).plan;
    expect(restoredPlan.recipe).toEqual(firstPlan.recipe);
    expect(restoredPlan.model.origin).toBe("GUIDED_COMMANDS");
    expect(restoredPlan.model.correction.originalPlanId).toBe(unmutePlan.id);
    expect(restoredPlan.model.correction.commandRecipe).toEqual(unmutePlan.recipe);
    const restored = await create(5);
    expect(restored.sha256).toBe(first.sha256);
    extraPlans.push(mixPlan, restoredPlan);

    // A newer confirmed melody can coexist with the selected version's older notes.
    const reconfirmed = await context.request.put(
      `${base}/api/explorations/${id}/local/notes`,
      { data: { expectedSourceRevision: confirmed.sourceRevision,
        expectedNoteRevision: confirmed.revision, notes: confirmed.notes, confirm: true } },
    );
    expect(reconfirmed.status()).toBe(200);
    const newMelody = await reconfirmed.json();
    expect(newMelody.revision).toBeGreaterThan(first.localNoteRevision);
    await page.reload();
    await useVersion(first);
    await interpret("edit: pan lead 0.001% left");
    await expect(page.locator(".change-row").filter({hasText:"Lead pan"})).toContainText("0.001% left");
    await expect(page.getByText(`This uses your current melody (revision ${newMelody.revision}).`, {exact:false})).toContainText(`Version 1 used revision ${first.localNoteRevision}`);
    expect((await exploration()).renditions).toHaveLength(5);

    // Render the uploaded performance with the existing accompaniment, then isolate it again.
    await button("Edit request").click();
    const performancePlan = await interpret("edit: use the uploaded recording");
    await expect(page.getByText("The lead plays your uploaded audio.", {exact:false})).toBeVisible();
    const performance = await create(6);
    expect(performance.localRecipe).toEqual({...first.localRecipe, instrument: "recorded"});
    expect(performance.sha256).not.toBe(first.sha256);
    await useVersion(performance);
    const soloPlan = await interpret("edit: remove bass; remove drums");
    const solo = await create(7);
    expect(solo.localRecipe).toMatchObject({instrument: "recorded", bass: false, drums: false});
    await playRow(solo);
    await pause();
    await useVersion(solo);
    await direction().fill("Put a swing ride behind my recorded take.");
    const priorPlannerCalls = plannerRequests.length;
    await button("Adjust arrangement").click();
    await page.getByLabel("Drum pattern", {exact: true}).selectOption("swing_ride");
    const tempoInput = page.getByLabel("Backing tempo", {exact: true});
    const firstBeatInput = page.getByLabel("First beat (ms)", {exact: true});
    await button("Tap tempo").click();
    await expect(tempoInput).toHaveValue("100");
    await expect(page.getByText("Tap two more beats.", {exact:true})).toBeVisible();
    // Fixed event timestamps exercise the browser control without tying its
    // BPM expectation to Playwright, machine load or the backend poll timer.
    await button("Tap tempo").evaluate((element) => {
      const first = performance.now() + 3000;
      for (const stamp of [first, first + 500, first + 1000]) {
        const tap = new MouseEvent("click", {bubbles:true});
        Object.defineProperty(tap, "timeStamp", {value:stamp});
        element.dispatchEvent(tap);
      }
    });
    await expect(tempoInput).toHaveValue("120");
    await button("Play playback").click();
    await expect.poll(() => page.locator("audio").evaluate(a => !a.paused && a.currentTime > .1)).toBe(true);
    const beforeMark = await page.locator("audio").evaluate(a => a.currentTime);
    await button("Use playhead").click();
    expect(Number(await firstBeatInput.inputValue())).toBeGreaterThanOrEqual(Math.floor(beforeMark * 1000));
    await pause();
    // Pause and seek the loaded element precisely before capturing again.
    await page.locator("audio").evaluate(a => { a.currentTime = .7102; });
    await expect.poll(() => page.locator("audio").evaluate(a => !a.seeking && Math.round(a.currentTime * 1000))).toBe(710);
    await button("Use playhead").click();
    await expect(firstBeatInput).toHaveValue("710");
    await button("Review arrangement").click();
    await expect(button("Create version")).toBeEnabled();
    const swingPlan = (await local()).plan;
    const swingRecipe = {
      ...solo.localRecipe, drums: true, bpm: 120,
      groove: {pattern: "swing_ride", downbeatMs: 710},
    };
    expect(swingPlan.recipe).toEqual(swingRecipe);
    expect(swingPlan.basisRecordingId).toBe(solo.id);
    expect(plannerRequests.length).toBe(priorPlannerCalls);
    const swing = await create(8);
    expect(swing.localRecipe).toEqual(swingRecipe);
    expect(swing.sha256).not.toBe(solo.sha256);
    await useVersion(swing);
    const straightPlan = await interpret("edit: use four on the floor at 120 bpm from 710 ms");
    expect(straightPlan.model).toMatchObject({origin: "GUIDED_COMMANDS", inferenceAttempts: 0});
    expect(straightPlan.basisRecordingId).toBe(swing.id);
    expect(straightPlan.recipe).toEqual({
      ...swingRecipe, groove: {pattern: "four_on_the_floor", downbeatMs: 710},
    });
    const straight = await create(9);
    expect(straight.localRecipe).toEqual(straightPlan.recipe);
    expect(straight.sha256).not.toBe(swing.sha256);
    await playRow(straight);
    await pause();
    await useVersion(straight);
    const returnPlan = await interpret("edit: use swing ride at 120 bpm from 710 ms");
    expect(returnPlan.model).toMatchObject({origin: "GUIDED_COMMANDS", inferenceAttempts: 0});
    expect(returnPlan.basisRecordingId).toBe(straight.id);
    expect(returnPlan.recipe).toEqual(swingRecipe);
    const returned = await create(10);
    expect(returned.localRecipe).toEqual(swingRecipe);
    expect(returned.sha256).toBe(swing.sha256);
    await useVersion(returned);
    const brightPlan = await interpret("make the lead brighter");
    const toneOrigin = {origin: "GUIDED_COMMANDS", syntax: "tone-phrases-v1", inferenceAttempts: 0};
    expect(brightPlan.model).toMatchObject(toneOrigin);
    expect(brightPlan.basisRecordingId).toBe(returned.id);
    const brightRecipe = {...swingRecipe, mix: {lead: {gainDb: 0, pan: 0, muted: false, tone: "bright"}}};
    expect(brightPlan.recipe).toEqual(brightRecipe);
    const bright = await create(11);
    expect(bright.localRecipe).toEqual(brightRecipe);
    expect(bright.sha256).not.toBe(returned.sha256);

    await useVersion(bright);
    await direction().fill("Choose a warm tone on the lead.");
    const beforeToneControl = plannerRequests.length;
    await button("Adjust arrangement").click();
    await page.getByText("Track mix", {exact: true}).click();
    await page.getByLabel("Mix track", {exact: true}).selectOption("lead");
    const toneControl = page.getByLabel("Track tone", {exact: true});
    await expect(toneControl).toHaveValue("bright");
    await toneControl.selectOption("warm");
    const beforeAuditions = await exploration();
    const beforeAuditionSubmissions = submissions.length;
    const warmAuditionSha = await auditionTone(bright, "warm");
    await shot("12-tone-audition");
    await page.locator("audio").evaluate(a => { a.currentTime = 3; });
    await expect.poll(() => page.locator("audio").evaluate(a => a.currentTime)).toBeGreaterThanOrEqual(3);
    await toneControl.selectOption("bright");
    expect(await auditionTone(bright, "bright")).toBe(bright.sha256);
    await expect(page.locator("audio")).toHaveCount(1);
    expect(await page.locator("audio").evaluate(a => a.currentTime)).toBeGreaterThanOrEqual(2.9);
    expect((await exploration()).renditions).toEqual(beforeAuditions.renditions);
    expect((await exploration()).latestAttempt).toEqual(beforeAuditions.latestAttempt);
    expect(submissions).toHaveLength(beforeAuditionSubmissions);
    await toneControl.selectOption("warm");
    await button("Review arrangement").click();
    await expect(button("Create version")).toBeEnabled();
    const warmPlan = (await local()).plan;
    const warmRecipe = {...swingRecipe, mix: {lead: {...brightRecipe.mix.lead, tone: "warm"}}};
    expect(warmPlan.recipe).toEqual(warmRecipe);
    expect(warmPlan.basisRecordingId).toBe(bright.id);
    expect(plannerRequests.length).toBe(beforeToneControl);
    const warm = await create(12);
    expect(warm.sha256).toBe(warmAuditionSha);
    expect(warm.localRecipe).toEqual(warmRecipe);
    expect(warm.sha256).not.toBe(bright.sha256);
    expect(warm.sha256).not.toBe(returned.sha256);

    await useVersion(warm);
    const neutralPlan = await interpret("reset the lead tone");
    expect(neutralPlan.model).toMatchObject(toneOrigin);
    expect(neutralPlan.basisRecordingId).toBe(warm.id);
    expect(neutralPlan.recipe).toEqual(swingRecipe);
    const neutral = await create(13);
    expect(neutral.localRecipe).toEqual(swingRecipe);
    expect(neutral.sha256).toBe(returned.sha256);
    await useVersion(neutral);
    await button("Suggest chords").click();
    await expect(direction()).toHaveValue("edit: suggest chords");
    const chartPlan = await interpret(await direction().inputValue());
    expect(chartPlan.basisRecordingId).toBe(neutral.id);
    expect(chartPlan.noteRevision).toBe((await local()).notes.revision);
    const chart = chartPlan.recipe.harmony;
    expect(chart).toMatchObject({bpm: 120, downbeatMs: 710});
    expect(chart.chords).toHaveLength(6);
    expect(chartPlan.recipe).toEqual({...swingRecipe, harmony: chart});
    const suggestions = chartPlan.model.chordSuggestions;
    expect(suggestions).toHaveLength(chart.chords.length);
    for (const [i, bar] of suggestions.entries()) {
      expect(bar.chord).toBe(chart.chords[i]);
      expect(bar.start).toBeCloseTo(.710 + 2 * i, 6);
      expect(bar.end).toBeCloseTo(Math.min(12, .710 + 2 * (i + 1)), 6);
      expect(typeof bar.ambiguous).toBe("boolean");
      expect(Array.isArray(bar.alternatives)).toBe(true);
      expect(bar.alternatives).not.toContain(bar.chord);
      expect(bar.origin).toBe(bar.chord === null ? "empty" : "suggested");
    }
    expect(suggestions.some(bar => bar.alternatives.length > 0)).toBe(true);
    await expect(page.locator("[data-chord-alternatives]").first()).toBeVisible();
    const harmonized = await create(14);
    expect(harmonized.localRecipe).toEqual(chartPlan.recipe);
    expect(harmonized.sha256).not.toBe(neutral.sha256);
    await playRow(harmonized);
    await pause();

    await useVersion(harmonized);
    const originalChord = chart.chords[1];
    const changedChord = originalChord === "Dm" ? "C" : "Dm";
    await direction().fill(`Use ${changedChord} in bar two and keep the other chords.`);
    const beforeBarControl = plannerRequests.length;
    await button("Adjust arrangement").click();
    const chordPicker = page.getByLabel("Chord for bar 2", {exact: true});
    await expect(chordPicker).toHaveValue(originalChord ?? "");
    await chordPicker.selectOption(changedChord);
    await button("Review arrangement").click();
    await expect(button("Create version")).toBeEnabled();
    const barPlan = (await local()).plan;
    const changedLabels = chart.chords.map((label, i) => i === 1 ? changedChord : label);
    expect(barPlan.recipe).toEqual({...swingRecipe, harmony: {...chart, chords: changedLabels}});
    expect(barPlan.basisRecordingId).toBe(harmonized.id);
    expect(plannerRequests.length).toBe(beforeBarControl);
    const correctedChart = await create(15);
    expect(correctedChart.localRecipe).toEqual(barPlan.recipe);
    expect(correctedChart.sha256).not.toBe(harmonized.sha256);
    await useVersion(correctedChart);
    const undoBarPlan = await interpret(`edit: set bar 2 to ${originalChord ?? "rest"}`);
    expect(undoBarPlan.basisRecordingId).toBe(correctedChart.id);
    expect(undoBarPlan.recipe).toEqual(chartPlan.recipe);
    const undoBar = await create(16);
    expect(undoBar.localRecipe).toEqual(chartPlan.recipe);
    expect(undoBar.sha256).toBe(harmonized.sha256);
    await useVersion(undoBar);
    const removeChartPlan = await interpret("edit: remove chords");
    expect(removeChartPlan.basisRecordingId).toBe(undoBar.id);
    expect(removeChartPlan.recipe).toEqual(neutral.localRecipe);
    const removedChart = await create(17);
    expect(removedChart.localRecipe).toEqual(neutral.localRecipe);
    expect(removedChart.sha256).toBe(neutral.sha256);
    expect((await exploration()).original.sha256).toBe(source.sha256);

    await useVersion(removedChart);
    const compound = await interpret("Switch to guitar and lower the drums by 6 dB.");
    expect(compound.model).toMatchObject({origin: "GUIDED_COMMANDS", inferenceAttempts: 0});
    expect(compound.basisRecordingId).toBe(removedChart.id);
    expect(compound.recipe).toEqual({...removedChart.localRecipe, instrument: "guitar",
      mix: {drums: {gainDb: -6, pan: 0, muted: false}}});
    const mixedGuitar = await create(18);
    expect(mixedGuitar.localRecipe).toEqual(compound.recipe);
    await playRow(mixedGuitar);
    await pause();
    extraPlans.push(performancePlan, soloPlan, swingPlan, straightPlan, returnPlan, brightPlan, warmPlan,
      neutralPlan, chartPlan, barPlan, undoBarPlan, removeChartPlan, compound);
  }
  expect(errors).toEqual([]);
  expect(external).toEqual([]);
  const snapshot = await exploration();
  const video = page.video();
  console.log(
    JSON.stringify(
      {
        result: "PASS",
        explorationId: id,
        evidence,
        timings,
        auditions,
        plans: [firstPlan, secondPlan, correctionProposal, correctedPlan, ...extraPlans],
        snapshot,
        localSnapshot: await local(),
        acceptedKey: submissions[2].key,
        acceptedCommand: submissions[2].body,
        attemptId: third.attemptId,
        ...(video
          ? {
              video: await video.path(),
              videoNote:
                "Actual browser walkthrough; Playwright video has no system audio.",
            }
          : {}),
        checks: [
          ...(audioOnly ? ["unsupported wording reaches the server with weights absent; no rendition is created and the original words survive reload for guided or direct editing"] : []),
          "real upload, actual transcription and listening review without opening optional editor",
          "confirmed notes unchanged across successive real local renders",
          guidedWalkthrough
            ? "two guided text plans with authoritative per-version recipes"
            : "two freeform text plans with authoritative per-version recipes",
          "simulated planner HTTP failure preserves text and direct controls inherit chosen version",
          "manual guitar render without another model preparation job",
          "accepted manual render survives lost receipt; explicit recovery replays exact command once",
          "same-track resume, different-track restart, paused Restart and inspected/playback independence",
          "Keep, Library playback, deep link and no autoplay on reload",
          "external draft revision invalidates visible approval; backend rejects stale plan",
          "engine readiness refresh disables and restores controls without reload or inference",
          "exact audio hashes, range media, original preservation",
          ...(extraPlans.length ? [
            "guided drafting preserves the original words and requires explicit review",
            "compound track mix, visible strip changes, and atomic rejection of an unsupported clause",
            "manual mix restoration is byte-identical; a newer melody revision is disclosed before creation",
            "recorded lead with direct swing drums, guided straight drums, and exact restoration on a 120 BPM pickup grid",
            "natural bright and neutral tone phrases and direct warm adjustment preserve the grid; resetting restores the served WAV exactly",
            "two on-demand full-mix auditions use one aligned player and create no version; the chosen preview matches the saved recording",
            "actual confirmed-note chord suggestion, direct one-bar correction, exact restoration and complete chart removal",
            "one plain text request switches the lead to guitar and lowers only the drums by 6 dB",
          ] : []),
          "desktop and 390/320 px layouts; mobile refinement controls",
          "no unexpected browser errors or external browser requests",
        ],
      },
      null,
      2,
    ),
  );
}
try {
  await walkthrough();
} catch (error) {
  console.error(error.stack || error);
  console.error(
    await page
      .locator("body")
      .innerText()
      .catch(() => ""),
  );
  await shot("failure").catch(() => {});
  throw error;
} finally {
  await context.close();
  await browser.close();
}
