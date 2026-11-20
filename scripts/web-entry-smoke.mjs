// Verify a packaged installation or local Vite entry without changing recordings.
import { createRequire } from "node:module";
import { existsSync } from "node:fs";
import assert from "node:assert/strict";

const require = createRequire(new URL("../web/package.json", import.meta.url));
const { chromium } = require("@playwright/test");
const base = process.env.DJOUND_TEST_BASE_URL ?? "http://127.0.0.1:5173";
assert.match(base, /^http:\/\/(127\.0\.0\.1|localhost):\d+$/);
const chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const browser = await chromium.launch(existsSync(chrome) ? { executablePath: chrome } : {});
try {
  const page = await browser.newPage();
  const errors = [];
  const apiCalls = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.startsWith("/api/")) apiCalls.push(request.url());
  });
  for (const route of ["/", "/renditions", "/tools/library", "/?mode=prototype"]) {
    const response = await page.goto(base + route);
    assert.equal(response.status(), 200, route);
    assert.equal(await page.locator("body").innerText(), "", route);
    assert.equal(await page.locator("main, nav, button, audio, img, canvas").count(), 0, route);
  }
  assert.deepEqual(errors, []);
  assert.deepEqual(apiCalls, []);
  console.log("Empty web entry passed on home and prior studio routes.");
} finally {
  await browser.close();
}
