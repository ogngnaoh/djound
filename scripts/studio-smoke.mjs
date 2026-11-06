// Browser plugin unavailable: use Playwright against an isolated browser profile.
// This suite stays in Prototype mode and never invokes an AI model or writes backend data.
import { createRequire } from "node:module";
import { existsSync } from "node:fs";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
const require = createRequire(new URL("../web/package.json", import.meta.url));
const { chromium, expect } = require("@playwright/test");
const base = process.env.DJOUND_TEST_BASE_URL || "http://127.0.0.1:5173";
if (!/^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(base))
  throw new Error("Use a loopback preview URL.");
const evidence =
  process.env.DJOUND_SCREENSHOT_DIR || `/tmp/djound-studio-${Date.now()}`;
await mkdir(evidence, { recursive: true });
const chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const browser = await chromium.launch({
  headless: true,
  ...(existsSync(chrome) ? { executablePath: chrome } : {}),
});
const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
page.setDefaultTimeout(12000);
const errors = [],
  backendRequests = [];
// Fail closed if mode persistence regresses: this suite must never submit real work.
await page.route("**/api/**", (route) => {
  backendRequests.push(route.request().url());
  return route.abort("blockedbyclient");
});
page.on("pageerror", (e) => errors.push(e.message));
page.on("console", (m) => {
  if (m.type() === "error") errors.push(m.text());
});
const shot = async (name) =>
  page.screenshot({
    path: path.join(evidence, `${name}.png`),
    fullPage: false,
    animations: "disabled",
  });
async function scenario(value) {
  await page.getByRole("button", { name: /Prototype workspace/ }).click();
  await page.getByLabel("Review scenario").selectOption(value);
  await expect(
    page.getByRole("heading", {
      name: "Evening melody",
      exact: true,
      level: 1,
    }),
  ).toBeVisible();
}
try {
  await page.goto(`${base}/?mode=prototype`);
  await expect(page).toHaveTitle(/Djound/);
  await expect(
    page.getByRole("heading", { name: "Library", exact: true }),
  ).toBeVisible();
  await expect(page.locator("vite-error-overlay")).toHaveCount(0);
  await shot("library");
  await page.getByLabel("Find a project").fill("absent project");
  await expect(page.getByText("No projects found")).toBeVisible();
  await page.getByRole("button", { name: "Clear search" }).click();
  await page
    .getByRole("navigation")
    .getByRole("link", { name: "Project Studio" })
    .click();
  await expect(
    page.getByRole("heading", {
      name: "Evening melody",
      exact: true,
      level: 1,
    }),
  ).toBeVisible();
  await expect(page.locator("audio")).toHaveCount(1);
  await shot("studio");
  await page
    .getByRole("button", { name: "Play Version 1", exact: true })
    .click();
  await expect
    .poll(() => page.locator("audio").evaluate((a) => a.currentTime))
    .toBeGreaterThan(0.2);
  await page
    .getByRole("button", { name: "Inspect Version 2", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Refining Version 3", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".transport-identity")).toContainText("Version 1");
  await page
    .getByRole("button", { name: "Pause playback", exact: true })
    .click();
  const paused = await page.locator("audio").evaluate((a) => a.currentTime);
  await page
    .getByRole("button", { name: "Play playback", exact: true })
    .click();
  await expect
    .poll(() => page.locator("audio").evaluate((a) => a.currentTime))
    .toBeGreaterThan(paused);
  await page
    .getByRole("button", { name: "Restart recording", exact: true })
    .click();
  expect(
    await page.locator("audio").evaluate((a) => a.currentTime),
  ).toBeLessThan(0.3);
  await page
    .getByRole("button", { name: "Pause playback", exact: true })
    .click();
  await page
    .getByLabel("Describe a change", { exact: true })
    .fill("Use piano and make the backing sparse.");
  await page
    .getByRole("button", { name: "Review changes", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Proposed changes" }),
  ).toBeVisible();
  await shot("review");
  const proposedDrums = page.getByLabel("Proposed drums", { exact: true });
  if (!(await proposedDrums.isVisible())) {
    await page.getByText("Unchanged arrangement settings", { exact: true }).click();
  }
  await proposedDrums.selectOption("on");
  await expect(
    page.getByRole("button", { name: "Create version", exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "Review arrangement", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Proposed changes" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Create version", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Cancel creation", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText("cancelled");
  await expect(
    page.getByRole("button", { name: "Inspect Version 4", exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "Create version", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Version 4 is ready." }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Refining Version 3", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".transport-identity")).toContainText("Version 1");
  await page.getByRole("button", { name: "Keep", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Kept", exact: true }),
  ).toBeVisible();
  await shot("result");
  await page
    .getByRole("navigation")
    .getByRole("link", { name: "Library", exact: true })
    .click();
  await expect(
    page.getByText("Evening melody · Version 4", { exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("button", { name: /Prototype workspace/ }),
  ).toBeVisible();
  await expect(
    page.getByText("Evening melody · Version 4", { exact: true }),
  ).toBeVisible();
  expect(await page.locator("audio").evaluate((a) => a.paused)).toBe(true);
  await page
    .getByRole("navigation")
    .getByRole("link", { name: "New project" })
    .click();
  await page.getByRole("button", { name: /Try a sample melody/ }).click();
  await page
    .getByRole("button", { name: "Detect melody", exact: true })
    .click();
  await expect(
    page.getByRole("link", { name: "Edit notes", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByLabel("Selected note pitch", { exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByText("Optional — only if something sounds wrong."),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Use this melody", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Refining Original", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Project actions", exact: true })
    .click();
  await page
    .getByRole("link", { name: "Review melody notes", exact: true })
    .click();
  await page.getByRole("link", { name: "Edit notes", exact: true }).click();
  const pitch = page.getByLabel("Selected note pitch", { exact: true });
  const oldPitch = await pitch.inputValue();
  await pitch.selectOption(String(Number(oldPitch) + 1));
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await expect(pitch).toHaveValue(oldPitch);
  await page.getByRole("button", { name: "Redo", exact: true }).click();
  await expect(pitch).toHaveValue(String(Number(oldPitch) + 1));
  await shot("note-editor");
  await page.getByRole("button", { name: "Done editing", exact: true }).click();
  await page
    .getByRole("button", { name: "Preview melody", exact: true })
    .click();
  await expect
    .poll(() => page.locator("audio").evaluate((a) => a.currentTime))
    .toBeGreaterThan(0.1);
  expect(await page.locator("audio").getAttribute("src")).toMatch(/^blob:/);
  await page
    .getByRole("button", { name: "Use this melody", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Refining Original", exact: true }),
  ).toBeVisible();
  await scenario("unclear");
  await page
    .getByLabel("Describe a change", { exact: true })
    .fill("Make it cosmic");
  await page
    .getByRole("button", { name: "Review changes", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText("couldn't interpret");
  await page
    .getByRole("button", { name: "Adjust arrangement", exact: true })
    .click();
  await page.getByLabel("Lead", { exact: true }).selectOption("piano");
  await page
    .getByRole("button", { name: "Review arrangement", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Proposed changes" }),
  ).toBeVisible();
  await scenario("creation-failure");
  await page
    .getByLabel("Describe a change", { exact: true })
    .fill("Use guitar and add drums");
  await page
    .getByRole("button", { name: "Review changes", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Create version", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText(/failed/i);
  await scenario("stale");
  await page
    .getByLabel("Describe a change", { exact: true })
    .fill("Use guitar and add drums");
  await page
    .getByRole("button", { name: "Review changes", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Create version", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText(/out of date|review/i);
  await scenario("unavailable");
  await expect(
    page.getByRole("button", { name: "Review changes", exact: true }),
  ).toBeDisabled();
  await page
    .getByRole("button", { name: "Play Original", exact: true })
    .click();
  await expect
    .poll(() => page.locator("audio").evaluate((a) => a.currentTime))
    .toBeGreaterThan(0.1);
  await scenario("normal");
  await page.setViewportSize({ width: 390, height: 844 });
  await page
    .getByRole("button", { name: "Describe a change", exact: true })
    .click();
  await expect(
    page.getByLabel("Describe a change", { exact: true }),
  ).toBeFocused();
  await page
    .getByLabel("Describe a change", { exact: true })
    .fill("Keep these words");
  await page.getByLabel("Describe a change", { exact: true }).press("Space");
  expect(await page.locator("audio").evaluate((a) => a.paused)).toBe(true);
  await shot("mobile-refinement");
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("button", { name: "Describe a change", exact: true }),
  ).toBeFocused();
  await page.getByRole("button", { name: "View all", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Unkeep Version 2", exact: true }),
  ).toBeVisible();
  await page.locator('summary[aria-label="Volume controls"]').click();
  const volume = page.getByRole("slider", { name: "Volume", exact: true });
  await volume.focus();
  await volume.press("Home");
  expect(await page.locator("audio").evaluate((a) => a.volume)).toBe(0);
  await page.keyboard.press("Escape");
  for (const width of [320, 390, 768, 1024, 1280, 1440]) {
    await page.setViewportSize({ width, height: 960 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await shot("mobile-studio");
  expect(errors).toEqual([]);
  expect(backendRequests).toEqual([]);
  console.log(
    JSON.stringify(
      {
        result: "PASS",
        flows: [
          "Library/search",
          "inspect/play/basis independence",
          "pause/resume/restart",
          "review/cancel/create/Keep/reload",
          "explicit prototype choice survives navigation and reload; no backend requests",
          "sample/melody/edit/undo/redo/preview/confirm",
          "unclear/manual fallback",
          "creation failure",
          "stale proposal",
          "unavailable playback",
          "mobile sheet/focus/volume/history",
          "six responsive widths",
        ],
        evidence,
      },
      null,
      2,
    ),
  );
} catch (error) {
  await shot("failure");
  console.error(await page.locator("body").innerText());
  throw error;
} finally {
  await browser.close();
}
