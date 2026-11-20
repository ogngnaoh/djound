// Run against a disposable local studio. Uploads two uniquely named copies of a
// bundled fixture and moves only those recordings to Trash when the run finishes.
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";

const require = createRequire(new URL("../web/package.json", import.meta.url));
const { chromium, expect } = require("@playwright/test");
const base = process.env.DJOUND_TEST_BASE_URL || "";
assert.match(base, /^http:\/\/(?:localhost|127\.0\.0\.1):\d+$/);
const api = base + "/api/renditions";
const listing = async () => {
  const response = await fetch(api);
  assert.equal(response.status, 200);
  return response.json();
};
const run = randomUUID();
const recovered = "Recovered " + run + ".wav";
const dismissed = "Dismissed " + run + ".wav";
const names = new Set([recovered, dismissed]);
const contents = await readFile(
  new URL("../fixtures/audio/sample-pulse.wav", import.meta.url),
);
const chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const browser = await chromium.launch(
  existsSync(chrome) ? { executablePath: chrome } : {},
);
const page = await browser.newPage();
page.setDefaultTimeout(10000);
const button = (name) => page.getByRole("button", { name, exact: true });
const dialog = page.getByRole("dialog", { name: "Import recording" });
const file = dialog.locator('input[type="file"]');
const choose = (name, buffer = contents) =>
  file.setInputFiles({ name, mimeType: "audio/wav", buffer });
const uploaded = [];
let releaseResponse;

try {
  await page.goto(base + "/?mode=backend");
  await expect(page).toHaveTitle(/Djound/);
  const opener = button("Import recording");
  await opener.focus();
  await page.keyboard.press("Enter");
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText(/MP3, WAV, or M4A/);
  await page.keyboard.press("Escape");
  await expect(opener).toBeFocused();
  await opener.click();
  await expect(button("Choose recording")).toBeEnabled();
  await choose("empty.wav", Buffer.alloc(0));
  await expect(dialog.getByRole("alert")).toContainText("empty");
  await choose("invalid.wav", Buffer.from("This is not an audio file."));
  await expect(dialog.getByRole("alert")).toContainText(
    /Choose an M4A voice memo, WAV, or MP3 recording/,
  );
  await expect(button("Choose recording")).toBeEnabled();

  await page.route(api, async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    const response = await route.fetch();
    assert.equal(response.status(), 201);
    const project = await response.json();
    assert.ok(names.has(project.title));
    const key = await route.request().headerValue("Idempotency-Key");
    assert.ok(key);
    uploaded.push({ key, id: project.id, title: project.title });
    // The first request reached the real server; its success response never
    // reaches the browser. Reload needs to recover the actual stored file and key.
    if (uploaded.length === 1) return route.abort("connectionreset");
    if (project.title === dismissed) {
      await new Promise((resolve) => {
        releaseResponse = resolve;
      });
    }
    await route.fulfill({ response });
  });
  await choose(recovered);
  await expect(
    dialog.getByRole("button", { name: "Resume import" }),
  ).toBeEnabled();
  await page.reload();
  await button("Resume import").click();
  await expect(dialog).toContainText(recovered);
  await dialog.getByRole("button", { name: "Resume import" }).click();
  await expect(page.locator("[data-working-take-id]")).toHaveAttribute(
    "data-working-take-id",
    "original",
  );
  assert.equal(new URL(page.url()).pathname, "/renditions/" + uploaded[0].id);
  assert.equal(new URL(page.url()).searchParams.get("take"), "original");
  assert.deepEqual(uploaded[0], uploaded[1]);
  assert.equal(
    (await listing()).filter((p) => p.title === recovered).length,
    1,
  );

  await page.goBack();
  await opener.click();
  await expect(button("Choose recording")).toBeEnabled();
  await choose(dismissed);
  await expect.poll(() => typeof releaseResponse).toBe("function");
  await expect(dialog.getByRole("status")).toContainText(dismissed);
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  releaseResponse();
  await expect(opener).toBeEnabled();
  const row = page.locator("#recording-" + uploaded[2].id);
  await expect(row).toBeVisible();
  assert.equal(new URL(page.url()).pathname, "/");
  assert.equal(uploaded.length, 3);
  await row.click();
  await expect(page.locator("[data-working-take-id]")).toHaveAttribute(
    "data-working-take-id",
    "original",
  );
  await expect(page.locator("audio[data-take-id]")).toHaveAttribute(
    "data-take-id",
    "original",
  );
  await expect(page.locator("vite-error-overlay")).toHaveCount(0);
  console.log(
    "Passed file validation, keyboard dismissal, one import after lost response and reload, Original playback selection, and staying in the library after dismissing an unfinished import.",
  );
} finally {
  releaseResponse?.();
  try {
    for (const project of await listing()) {
      if (!names.has(project.title)) continue;
      const response = await fetch(api + "/" + project.id + "/trash", {
        method: "PUT",
      });
      assert.equal(response.status, 204);
    }
  } finally {
    await browser.close();
  }
}
