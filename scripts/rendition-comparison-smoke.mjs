// Run against a disposable LOCAL project containing two ready parent/child takes.
// The session keeps a favorite and submits a passage edit to the real backend.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { correspondingPosition } from "../web/src/renditions/songTime.ts";

const require = createRequire(new URL("../web/package.json", import.meta.url));
const { chromium, expect } = require("@playwright/test");
const base = process.env.DJOUND_TEST_BASE_URL || "";
const projectId = process.env.DJOUND_TEST_PROJECT_ID || "";
if (
  !/^http:\/\/(localhost|127\.0\.0\.1):\d+$/.test(base) ||
  !/^[a-f0-9-]{36}$/i.test(projectId) ||
  process.env.DJOUND_TEST_ALLOW_WRITE !== "1"
)
  throw new Error(
    "Supply an isolated DJOUND_TEST_BASE_URL, DJOUND_TEST_PROJECT_ID and DJOUND_TEST_ALLOW_WRITE=1.",
  );

const url = base + "/api/renditions/" + projectId;
async function project() {
  const response = await fetch(url);
  assert.equal(response.status, 200);
  return response.json();
}
const initial = await project();
const working = initial.takes.find(
  (t) => t.state === "ready" && t.parentId !== "original",
);
assert.ok(working, "Seed a playable child of another take.");
const parent = initial.takes.find(
  (t) => t.id === working.parentId && t.state === "ready",
);
assert.ok(parent);
const name = (take) =>
  "Take " + (initial.takes.indexOf(take) + 1) + " · " + take.label;
const chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const browser = await chromium.launch(
  existsSync(chrome) ? { executablePath: chrome } : {},
);
try {
  const page = await browser.newPage({ acceptDownloads: true });
  const audio = page.locator("audio[data-take-id]");
  const button = (label) =>
    page.getByRole("button", { name: label, exact: true });
  const draft = page.getByRole("textbox", {
    name: "Describe a version or change",
  });
  const transport = (id, paused) =>
    expect
      .poll(() =>
        audio.evaluate((a) => ({
          id: a.dataset.takeId,
          ready: a.readyState >= 2 && !a.seeking,
          paused: a.paused,
        })),
      )
      .toEqual({ id, ready: true, paused });
  await page.goto(base + "/renditions/" + projectId + "?take=" + working.id);
  await transport(working.id, true);
  // Put the native player inside the performance and prepare a scoped edit.
  const at = working.durationSeconds / 2;
  await audio.evaluate((a, t) => {
    a.currentTime = t;
  }, at);
  await transport(working.id, true);
  const prompt = "Give this passage a softer sound.";
  await draft.fill(prompt);
  await button("Choose a passage").click();
  const start = page.getByLabel("Passage start in seconds");
  const end = page.getByLabel("Passage end in seconds");
  await start.fill("1");
  await end.fill("3");
  await button("Compare original").click();
  await transport("original", true);
  const expected = correspondingPosition(at, working.songTimeMap, undefined);
  assert.ok(
    Math.abs((await audio.evaluate((a) => a.currentTime)) - expected) < 0.12,
  );
  assert.equal(new URL(page.url()).searchParams.get("take"), working.id);
  await expect(draft).toHaveValue(prompt);
  await expect(start).toHaveValue("1");
  await expect(end).toHaveValue("3");
  const download = page.getByRole("link", { name: /download/i });
  await expect(download).toHaveAttribute("href", working.exportUrl);
  const downloaded = page.waitForEvent("download");
  await download.click();
  const file = await downloaded;
  assert.equal(
    createHash("sha256")
      .update(await readFile(await file.path()))
      .digest("hex"),
    working.sha256,
  );
  if (initial.favoriteId !== working.id) {
    await page.getByRole("button", { name: /^Keep .*favorite$/i }).click();
    await expect
      .poll(async () => (await project()).favoriteId)
      .toBe(working.id);
  }
  // A/B while playing must carry both the clock and transport back to the same take.
  await audio.evaluate((a) => a.play());
  await button("Return to " + name(working)).click();
  await transport(working.id, false);
  assert.ok(Math.abs((await audio.evaluate((a) => a.currentTime)) - at) < 1.2);
  await button("Compare " + name(parent)).click();
  await transport(parent.id, false);
  assert.equal(new URL(page.url()).searchParams.get("take"), working.id);
  await audio.evaluate((a) => a.pause());

  const response = page.waitForResponse(
    (r) => r.url() === url + "/takes" && r.request().method() === "POST",
  );
  await button("Make a take").click();
  const accepted = await response;
  assert.equal(accepted.status(), 202);
  const command = accepted.request().postDataJSON();
  assert.deepEqual(
    [
      command.parentId,
      command.startSeconds,
      command.endSeconds,
      command.prompt,
    ],
    [working.id, 1, 3, prompt],
  );
  // Listening to a reference is temporary. Choosing it from history is explicit.
  await button("Compare original").click();
  await transport("original", true);
  await button("Play original").click();
  await expect
    .poll(() => new URL(page.url()).searchParams.get("take"))
    .toBe("original");
  await transport("original", false);
  await expect(
    page.getByRole("heading", { name: "Make a version of Original" }),
  ).toBeVisible();
  await expect(start).toHaveCount(0);

  // A question always resumes on its recorded parent even if another take is sounding.
  const question = initial.takes.find(
    (t) => t.state === "clarify" && t.parentId === working.id,
  );
  if (question) {
    const entry = page
      .getByRole("complementary", { name: "Take history" })
      .getByRole("article")
      .filter({ hasText: question.request })
      .first();
    await entry.getByRole("button", { name: "Revise request" }).click();
    await expect
      .poll(() => new URL(page.url()).searchParams.get("take"))
      .toBe(working.id);
    await expect(draft).toHaveValue(question.request);
    await button("Compare original").click();
    await expect(draft).toHaveValue(question.request);
    await expect(
      page.getByRole("heading", { name: "Make a version of " + name(working) }),
    ).toBeVisible();
    await button("Play " + name(parent)).click();
    await expect(draft).toHaveValue("");
    await expect(
      page.getByRole("heading", { name: "Make a version of " + name(parent) }),
    ).toBeVisible();
  }

  const result = (await accepted.json()).takes.find(
    (t) => t.requestId === command.requestId,
  );
  assert.ok(result);
  await expect
    .poll(
      async () => (await project()).takes.find((t) => t.id === result.id).state,
      { timeout: 600000, intervals: [2000] },
    )
    .toBe("ready");
  await page.getByRole("button", { name: /^Play new take/ }).click();
  await transport(result.id, false);
  await expect
    .poll(() => new URL(page.url()).searchParams.get("take"))
    .toBe(result.id);
  assert.equal((await project()).favoriteId, working.id);
  console.log(
    "Passed paused/playing A/B, mapped seeking, scoped request, favorite, exact WAV export, explicit history and ready-result selection.",
  );
} finally {
  await browser.close();
}
