// Uses an isolated studio with two playable takes. Only the test browser sees the
// simulated project states, failed audio response, and outgoing edit request.
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(new URL("../web/package.json", import.meta.url));
const { chromium, expect } = require("@playwright/test");
const base = process.env.DJOUND_TEST_BASE_URL || "";
const id = process.env.DJOUND_TEST_PROJECT_ID || "";
assert.match(base, /^http:\/\/(localhost|127\.0\.0\.1):\d+$/);
assert.match(id, /^[a-f0-9-]{36}$/i);
const path = "/renditions/" + id;
const api = base + "/api" + path;
const response = await fetch(api);
assert.equal(response.status, 200);
const project = await response.json();
const [favorite, linked] = project.takes.filter(
  (t) => t.state === "ready" && t.audioUrl,
);
assert.ok(linked, "Seed two playable takes.");
const name = (take) =>
  "Take " + (project.takes.indexOf(take) + 1) + " · " + take.label;
const chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const browser = await chromium.launch(
  existsSync(chrome) ? { executablePath: chrome } : {},
);
try {
  const page = await browser.newPage();
  const audio = page.locator("audio[data-take-id]");
  const draft = page.getByRole("textbox", {
    name: "Describe a version or change",
  });
  const button = (name) => page.getByRole("button", { name, exact: true });
  const message = page
    .getByRole("status")
    .filter({ hasText: /The linked take/ });
  const open = (takeId) =>
    page.goto(base + path + "?view=studio&take=" + takeId);
  const current = async (id) => {
    await expect(page.locator("[data-working-take-id]")).toHaveAttribute(
      "data-working-take-id",
      id,
    );
    await expect(audio).toHaveAttribute("data-take-id", id);
    await expect
      .poll(() => audio.evaluate((a) => a.readyState >= 2 && !a.error))
      .toBe(true);
  };
  const origin = {
    library: { pathname: "/", search: "?view=favorites&q=piano", index: 0 },
  };
  await page.addInitScript((state) => {
    history.replaceState({ ...history.state, usr: state }, "");
  }, origin);
  let snapshot = { ...project, favoriteId: favorite.id };
  await page.route(api, (route) => route.fulfill({ json: snapshot }));
  const sent = [];
  await page.route(api + "/takes", (route) => {
    sent.push(route.request().postDataJSON());
    return route.fulfill({ status: 202, json: snapshot });
  });

  await open("missing-take");
  await expect(message).toContainText("your favorite, " + name(favorite));
  await current(favorite.id);
  await expect(draft).toBeDisabled();
  await expect(button("Choose a passage")).toBeDisabled();
  await expect(page.getByRole("button", { name: /^Jazz Trio/ })).toBeDisabled();
  assert.equal(new URL(page.url()).searchParams.get("take"), "missing-take");
  await button("Use " + name(favorite)).click();
  await expect(message).toHaveCount(0);
  await expect(draft).toBeEnabled();
  assert.equal(new URL(page.url()).searchParams.get("take"), favorite.id);
  assert.equal(new URL(page.url()).searchParams.get("view"), "studio");
  assert.deepEqual(await page.evaluate(() => history.state.usr), origin);
  await expect(page.getByRole("link", { name: "Favorites" })).toHaveAttribute(
    "href",
    "/" + origin.library.search,
  );
  await draft.fill("Give the melody to a cello.");
  await button("Make a take").click();
  await expect
    .poll(() => sent.map((cmd) => cmd.parentId))
    .toEqual([favorite.id]);

  // An explicitly linked Original always opens Original, including with a favorite.
  await open("original");
  await current("original");
  await expect(message).toHaveCount(0);
  await expect(draft).toBeEnabled();
  // The favorite might itself be missing. History still offers every playable take.
  snapshot = { ...project, favoriteId: "missing-favorite" };
  await open("missing-take");
  await expect(message).toContainText("You can listen to Original");
  await current("original");
  await button("Play " + name(linked)).click();
  await current(linked.id);
  await expect(message).toHaveCount(0);
  await expect(draft).toBeEnabled();

  snapshot = {
    ...project,
    favoriteId: favorite.id,
    takes: project.takes.map((t) =>
      t.id === linked.id
        ? { ...t, state: "clarify", parentId: "original", audioUrl: null }
        : t,
    ),
  };
  await open(linked.id);
  await expect(message).toContainText("needs more direction");
  await page
    .locator(".mix-takes article")
    .filter({ has: page.getByText(name(linked), { exact: true }) })
    .getByRole("button", { name: "Revise request" })
    .click();
  await current("original");
  await expect(message).toHaveCount(0);
  await expect(draft).toHaveValue(linked.request);

  // Publishing the linked take must leave ongoing fallback playback undisturbed.
  snapshot = {
    ...project,
    favoriteId: favorite.id,
    takes: project.takes.map((t) =>
      t.id === linked.id ? { ...t, state: "running", audioUrl: null } : t,
    ),
  };
  await open(linked.id);
  await expect(message).toContainText("still being made");
  await current(favorite.id);
  await audio.evaluate((a) => a.play());
  snapshot = { ...project, favoriteId: favorite.id };
  await expect(message).toContainText("The linked take is ready", {
    timeout: 10000,
  });
  await current(favorite.id);
  assert.equal(await audio.evaluate((a) => a.paused), false);
  await expect(draft).toBeDisabled();
  await button("Open " + name(linked)).click();
  await current(linked.id);
  await expect(message).toHaveCount(0);
  await expect(draft).toBeEnabled();

  // A ready database row can point to media the browser can't open.
  const brokenUrl = new URL(linked.audioUrl, base).href;
  await page.route(brokenUrl, (route) => route.fulfill({ status: 404 }));
  await open(linked.id);
  await expect(message).toContainText("could not be played");
  await current(favorite.id);
  await expect(draft).toBeDisabled();
  await page.unroute(brokenUrl);
  await button("Retry " + name(linked)).click();
  await current(linked.id);
  await expect(message).toHaveCount(0);
  await expect(draft).toBeEnabled();
  assert.equal(sent.length, 1);
  console.log(
    "Passed missing, unfinished, unplayable and explicit Original take links, confirmed edit parent and retained library return.",
  );
} finally {
  await browser.close();
}
