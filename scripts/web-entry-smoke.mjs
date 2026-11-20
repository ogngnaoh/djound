// Set DJOUND_TEST_RENDER=1 on an isolated local installation for the full rehearsal.
import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";

const require = createRequire(new URL("../web/package.json", import.meta.url));
const { chromium, expect } = require("@playwright/test");
const base = process.env.DJOUND_TEST_BASE_URL ?? "http://127.0.0.1:5173";
assert.match(base, /^http:\/\/(127\.0\.0\.1|localhost):\d+$/);
const chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const render = process.env.DJOUND_TEST_RENDER === "1";
const browser = await chromium.launch(
  existsSync(chrome) ? { executablePath: chrome } : {},
);
const errors = [];

async function openPage() {
  const context = await browser.newContext({
    baseURL: base,
    acceptDownloads: true,
  });
  const page = await context.newPage();
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("response", (response) => {
    if (response.url().startsWith(base + "/") && response.status() >= 400)
      errors.push(response.status() + " " + response.url());
  });
  return page;
}

async function audioBytes(request, url) {
  const response = await request.get(url);
  assert.equal(response.status(), 200, url);
  const bytes = await response.body();
  assert.equal(bytes.toString("ascii", 0, 4), "RIFF");
  assert.equal(bytes.toString("ascii", 8, 12), "WAVE");
  return bytes;
}

function pcm(bytes) {
  for (let start = 12; start + 8 <= bytes.length; ) {
    const length = bytes.readUInt32LE(start + 4);
    if (bytes.toString("ascii", start, start + 4) === "data") {
      assert.ok(length > 0 && start + 8 + length <= bytes.length);
      return bytes.subarray(start + 8, start + 8 + length);
    }
    start += 8 + length + (length % 2);
  }
  assert.fail("WAV file has no audio samples");
}

async function checkEntry(page) {
  const writes = [];
  const track = (request) => {
    if (request.url().startsWith(base + "/api/") && request.method() !== "GET")
      writes.push(request.url());
  };
  page.on("request", track);
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Evening Study" }),
  ).toBeVisible();
  const seek = page.getByRole("slider");
  await expect(seek).toBeEnabled();
  await page.getByRole("button", { name: "Play Evening Study" }).click();
  await expect(page.locator("#elapsed")).not.toHaveText("0:00");
  await page.getByRole("button", { name: "Jazz trio", exact: true }).click();
  await expect(page.locator("#player")).toHaveAttribute("data-source", "edit");
  await expect
    .poll(async () => Number(await seek.inputValue()))
    .toBeGreaterThan(6);
  await page.getByRole("button", { name: "Pause playback" }).click();
  // Evening Study's trio enters after a two-bar introduction.
  const editTime = Number(await seek.inputValue());
  assert.ok(editTime > 6, "The trio restarted or skipped its song-time map");
  const handoff = page.getByRole("link", { name: "Try an edit" });
  const demo = new URL(await handoff.getAttribute("href"), base);
  assert.equal(demo.searchParams.get("demo"), "edit");
  assert.ok(Math.abs(Number(demo.searchParams.get("at")) - editTime) < 0.05);
  await page.getByRole("link", { name: "Bring a recording" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("button", { name: "Close import" }).click();
  await expect(
    page.getByRole("heading", { name: "Your library" }),
  ).toBeVisible();
  await expect(
    page
      .locator("#feature:visible")
      .or(
        page.getByText("Bring your first recording to open it in the Studio."),
      ),
  ).toBeVisible();
  page.off("request", track);
  assert.deepEqual(writes, [], "Previewing the example created server data");
  return demo;
}

async function checkExample(page, demo) {
  // Following the exact href also exercises opening the link in a new tab.
  await page.goto(demo.href);
  await expect(page.locator("#project-title")).toHaveText("Evening Study");
  const selected = new URL(page.url()).searchParams.get("edit");
  const audio = page.locator("#audio");
  await expect(page.locator("#parent")).toHaveValue(selected);
  await expect(audio).toHaveJSProperty("paused", true);
  const target = Number(demo.searchParams.get("at"));
  await expect
    .poll(() => audio.evaluate((el) => el.currentTime))
    .toBeCloseTo(target, 1);
  const src = await audio.evaluate((el) => el.currentSrc);
  assert.ok(src.endsWith("/takes/" + selected + "/audio"));
  assert.ok(
    pcm(await audioBytes(page.request, src)).equals(
      pcm(await audioBytes(page.request, "/media/edit.wav")),
    ),
    "Studio opened a different performance",
  );
}

async function importRecording(page) {
  const name = "Rehearsal " + randomUUID().slice(0, 8) + ".wav";
  const file = await readFile(
    new URL("../fixtures/audio/local-demo-source.wav", import.meta.url),
  );
  await page.goto("/library.html#import");
  await page.locator("#file-input").setInputFiles({
    name,
    mimeType: "audio/wav",
    buffer: file,
  });
  // Drop the first response after the real server has accepted this upload.
  let previousKey, firstId;
  const endpoint = base + "/api/renditions";
  await page.route(endpoint, async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    const response = await route.fetch();
    assert.equal(response.status(), 201);
    firstId = (await response.json()).id;
    previousKey = route.request().headers()["idempotency-key"];
    await route.abort("failed");
  });
  const open = page.getByRole("button", { name: "Open in Studio" });
  await open.click();
  await expect(page.locator("#import-error")).not.toBeEmpty();
  await page.unroute(endpoint);
  const retried = page.waitForRequest(
    (r) => r.url() === endpoint && r.method() === "POST",
  );
  await open.click();
  assert.ok(previousKey);
  assert.equal((await retried).headers()["idempotency-key"], previousKey);
  await expect(page.locator("#project-title")).toHaveText(name);
  const projectId = new URL(page.url()).searchParams.get("project");
  assert.equal(projectId, firstId, "Upload retry created a duplicate project");
  await expect(page.locator("#heard-name")).toHaveText("Original");
  return { name, projectId };
}

async function makeEdit(page, projectId, from, prompt) {
  const before = await page.locator("#heard-name").textContent();
  await expect(page.locator("#parent")).toHaveValue(from);
  await page.getByRole("textbox", { name: "Describe your edit" }).fill(prompt);
  const submitted = page.waitForResponse(
    (r) =>
      r.url() === base + "/api/renditions/" + projectId + "/takes" &&
      r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Create edit" }).click();
  const response = await submitted;
  assert.equal(response.status(), 202);
  const { parentId, requestId } = response.request().postDataJSON();
  assert.equal(parentId, from);
  const terminal = page.locator(
    "#job:is([data-state=ready],[data-state=failed],[data-state=clarify],[data-state=rejected],[data-state=unconfirmed])",
  );
  await terminal.waitFor({ timeout: 10 * 60 * 1000 });
  assert.equal(
    await terminal.getAttribute("data-state"),
    "ready",
    await terminal.innerText(),
  );
  await expect(page.locator("#heard-name")).toHaveText(before);
  await page.getByRole("button", { name: "Play new edit" }).click();
  const result = await page.request.get("/api/renditions/" + projectId);
  assert.equal(result.status(), 200);
  const take = (await result.json()).takes.find(
    (t) => t.requestId === requestId,
  );
  assert.equal(take?.state, "ready");
  assert.equal(take.parentId, from);
  await expect(page.locator("#parent")).toHaveValue(take.id);
  await expect(page.locator("#heard-name")).toHaveText(take.label);
  await expect(page.locator("#audio")).toHaveJSProperty("paused", false);
  const bytes = await audioBytes(page.request, take.audioUrl);
  assert.equal(createHash("sha256").update(bytes).digest("hex"), take.sha256);
  return { take, bytes };
}

async function rehearse(page) {
  const { name, projectId } = await importRecording(page);
  const original = await audioBytes(
    page.request,
    "/api/renditions/" + projectId + "/takes/original/audio",
  );
  const first = await makeEdit(
    page,
    projectId,
    "original",
    "Play this as a jazz trio.",
  );
  assert.ok(
    !pcm(first.bytes).equals(pcm(original)),
    "The edit played the uploaded audio",
  );
  const revision = await makeEdit(
    page,
    projectId,
    first.take.id,
    "Keep the groove and give the melody to a saxophone.",
  );
  assert.ok(
    !pcm(revision.bytes).equals(pcm(first.bytes)),
    "The revision replayed its parent",
  );
  for (const { take } of [first, revision]) {
    await page.locator("#compare-" + take.id).click();
    await expect(page.locator("#heard-name")).toHaveText(take.label);
    await expect(page.locator("#audio")).toHaveJSProperty(
      "src",
      new URL(take.audioUrl, base).href,
    );
    await expect(page.locator("#parent")).toHaveValue(revision.take.id);
  }
  await page.getByRole("button", { name: "Favorite this performance" }).click();
  await expect(page.locator("#favorite")).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await page.getByRole("link", { name: "Library", exact: true }).click();
  await expect(page.locator("#feature-title")).toHaveText(name);
  await page.getByRole("link", { name: "Open Studio" }).click();
  await expect(page.locator("#heard-name")).toHaveText(revision.take.label);
  // A new browser has no cached draft, history, audio, or selected edit.
  const fresh = await openPage();
  await fresh.goto("/library.html");
  await fresh
    .getByRole("link")
    .filter({ has: fresh.getByText(name, { exact: true }) })
    .click();
  await expect(fresh.locator("#heard-name")).toHaveText(revision.take.label);
  await expect(fresh.locator("#favorite")).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await fresh.locator("#play").click();
  await expect(fresh.locator("#elapsed")).not.toHaveText("0:00");
  const download = fresh.waitForEvent("download");
  await fresh
    .getByRole("button", { name: "Export audio", exact: true })
    .click();
  const finished = await download;
  assert.match(finished.suggestedFilename(), / - Edit 2 - .+\.wav$/);
  assert.ok(!finished.suggestedFilename().includes(".wav - "));
  assert.match(finished.suggestedFilename(), /\.wav$/i);
  assert.ok(
    (await readFile(await finished.path())).equals(revision.bytes),
    "Export did not match the chosen edit's audio",
  );
  return {
    projectId,
    first: first.take.id,
    revision: revision.take.id,
    exported: finished.suggestedFilename(),
  };
}

try {
  const page = await openPage();
  const demo = await checkEntry(page);
  const result = { entry: "passed" };
  if (render) {
    await checkExample(page, demo);
    result.rehearsal = await rehearse(page);
  }
  assert.deepEqual(errors, []);
  console.log(JSON.stringify(result, null, 2));
} finally {
  await browser.close();
}
