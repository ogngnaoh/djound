import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(new URL("../web/package.json", import.meta.url));
const { chromium, expect } = require("@playwright/test");
const baseURL = process.env.DJOUND_PREVIEW_URL || "http://127.0.0.1:18774";
const testFile = fileURLToPath(
  new URL("../fixtures/audio/sample-warm.wav", import.meta.url),
);
const chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const browser = await chromium.launch(
  existsSync(chrome) ? { executablePath: chrome } : {},
);

async function checkLayout(page) {
  await page.evaluate(() => document.fonts.ready);
  assert.ok(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    "horizontal overflow",
  );
}

async function checkSession(viewport) {
  const context = await browser.newContext({
    baseURL,
    viewport,
    acceptDownloads: true,
  });
  try {
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("response", (response) => {
      if (response.url().startsWith(baseURL) && response.status() >= 400)
        errors.push(response.url());
    });
    await page.goto("/");
    await checkLayout(page);
    await page.getByRole("button", { name: "Jazz trio", exact: true }).click();
    await page.getByRole("button", { name: "Play To Zanarkand" }).click();
    await expect(page.locator("#elapsed")).not.toHaveText("0:00");
    await page.getByRole("link", { name: "Try an edit" }).click();

    const audio = page.locator("#audio");
    const parent = page.getByRole("combobox", { name: "Editing from" });
    const heard = page.locator("#heard-name");
    await expect(heard).toHaveText("Jazz trio");
    await expect(parent).toHaveValue("jazz");
    await expect(page.locator("#elapsed")).not.toHaveText("0:00");
    await checkLayout(page);
    await page.locator("#play").click();
    await expect(audio).toHaveJSProperty("paused", false);
    await page
      .getByRole("textbox", { name: "Describe your edit" })
      .fill("Give it a warmer tone");
    await page.getByRole("button", { name: "Create edit" }).click();
    const ready = page.getByRole("button", { name: "Play new edit" });
    await expect(ready).toBeVisible({ timeout: 20000 });
    await expect(heard).toHaveText("Jazz trio");
    await expect(audio).toHaveJSProperty("paused", false);
    const at = await audio.evaluate((element) => element.currentTime);
    await ready.click();
    await expect(heard).toHaveText("Warm mix");
    await expect(audio).toHaveJSProperty("paused", false);
    assert.ok(
      (await audio.evaluate((element) => element.currentTime)) >= at - 0.5,
      "revision restarted audio",
    );
    const target = await parent.inputValue();
    await page
      .getByRole("button", { name: "Compare Jazz trio", exact: true })
      .click();
    await expect(heard).toHaveText("Jazz trio");
    await expect(parent).toHaveValue(target);
    await expect(
      page.getByRole("button", { name: "Compare Jazz trio", exact: true }),
    ).toBeFocused();
    await page
      .getByRole("button", { name: "Compare Warm mix", exact: true })
      .click();
    await expect(heard).toHaveText("Warm mix");

    const exporting = page.waitForEvent("download");
    await page.getByRole("button", { name: "Export audio" }).click();
    const download = await exporting;
    assert.match(download.suggestedFilename(), /Warm mix\.wav$/);
    const exported = await readFile(await download.path());
    assert.equal(exported.toString("ascii", 0, 4), "RIFF");
    assert.equal(exported.toString("ascii", 8, 12), "WAVE");
    const response = await context.request.get("/media/edit.wav");
    assert.ok(response.ok(), "prepared Jazz trio missing");
    const parentWav = await response.body();
    assert.equal(
      exported.length,
      parentWav.length,
      "exported revision changed the clip duration",
    );
    assert.ok(
      !exported.equals(parentWav),
      "exported the parent instead of its revision",
    );

    // Leave while auditioning the parent with a pending request for the child.
    await page
      .getByRole("button", { name: "Compare Jazz trio", exact: true })
      .click();
    const nextRequest = page.getByRole("textbox", {
      name: "Describe your edit",
    });
    await nextRequest.fill("Keep this groove for the next change");
    await page.getByRole("link", { name: "Library", exact: true }).click();
    await page.getByRole("link", { name: "Open Studio" }).click();
    await expect(heard).toHaveText("Jazz trio");
    await expect(parent).toHaveValue(target);
    await expect(nextRequest).toHaveValue(
      "Keep this groove for the next change",
    );
    await page.reload();
    await expect(nextRequest).toHaveValue(
      "Keep this groove for the next change",
    );

    await page.goto("/");
    await page.getByRole("link", { name: "Bring a recording" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await page.locator("input[type=file]").setInputFiles(testFile);
    await page.getByRole("button", { name: "Open in Studio" }).click();
    await expect(page.locator("#project-title")).toHaveText("sample-warm");
    await expect(heard).toHaveText("Original");
    await page.getByRole("link", { name: "Library", exact: true }).click();
    await expect(page.locator("#record-list li")).toHaveCount(2);
    await page.reload();
    await expect(page.locator("#record-list li")).toHaveCount(2);
    await checkLayout(page);
    assert.deepEqual(errors, []);
    console.log(
      `Passed ${viewport.width}×${viewport.height}: demo handoff, explicit playback, parent comparison, WAV export and persistent import`,
    );
  } finally {
    await context.close();
  }
}

try {
  await checkSession({ width: 1440, height: 900 });
  await checkSession({ width: 390, height: 844 });
} finally {
  await browser.close();
}
