// Real LOCAL inference through the redesigned Studio, in an isolated installation.
// Browser plugin unavailable: use installed Chrome through Playwright.
import { createRequire } from "node:module";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { createHash } from "node:crypto";

const require = createRequire(new URL("../web/package.json", import.meta.url));
const { chromium, expect } = require("@playwright/test");
const base = process.env.DJOUND_TEST_BASE_URL;
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
  await direction().fill(text);
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
try {
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
  const sourcePath = path.join(root, "fixtures/audio/local-demo-source.wav");
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
  expect(state.notes.notes.length).toBeGreaterThan(10);
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

  const firstPlan = await interpret(
    "Use electric piano, add bass and a steady drum beat.",
  );
  expect(firstPlan.recipe).toMatchObject({
    instrument: "electric_piano",
    bass: true,
    drums: true,
    density: "steady",
  });
  await shot("02-reviewed-text");
  const first = await create(1);
  expect(first.localRecipe).toEqual(firstPlan.recipe);
  await shot("03-first-result");
  await useVersion(first);
  const secondPlan = await interpret(
    "Remove drums and make the backing quieter.",
  );
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
  await direction().fill("Use guitar for this version.");
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
  await expect(direction()).toHaveValue("Use guitar for this version.");
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
  expect(plannerRequests).toHaveLength(3); // Two actual plans and one simulated unavailable response.
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
  await direction().fill("Use piano.");
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
        plans: [firstPlan, secondPlan, correctionProposal, correctedPlan],
        snapshot,
        localSnapshot: await local(),
        acceptedKey: submissions[2].key,
        acceptedCommand: submissions[2].body,
        attemptId: snapshot.latestAttempt.id,
        ...(video
          ? {
              video: await video.path(),
              videoNote:
                "Actual browser walkthrough; Playwright video has no system audio.",
            }
          : {}),
        checks: [
          "real upload, actual transcription and listening review without opening optional editor",
          "confirmed notes unchanged across successive real local renders",
          "two actual text LLM plans with authoritative per-version recipes",
          "simulated planner HTTP failure preserves text and direct controls inherit chosen version",
          "manual guitar render without another model preparation job",
          "accepted manual render survives lost receipt; explicit recovery replays exact command once",
          "same-track resume, different-track restart, paused Restart and inspected/playback independence",
          "Keep, Library playback, deep link and no autoplay on reload",
          "external draft revision invalidates visible approval; backend rejects stale plan",
          "engine readiness refresh disables and restores controls without reload or inference",
          "exact audio hashes, range media, original preservation",
          "desktop and 390/320 px layouts; mobile refinement controls",
          "no unexpected browser errors or external browser requests",
        ],
      },
      null,
      2,
    ),
  );
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
