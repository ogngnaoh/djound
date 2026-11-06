// Measure elapsed time from version creation to audible playback in Chrome.
const { chromium, expect } = require("../../web/node_modules/@playwright/test");
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const base = process.env.DJOUND_TEST_BASE_URL;
const out = process.env.DJOUND_SCREENSHOT_DIR;
const cycles = 10;
if (
  !/^http:\/\/127\.0\.0\.1:\d+$/.test(base) ||
  !out ||
  process.env.DJOUND_TEST_ALLOW_WRITE !== "1"
)
  throw Error(
    "Use disposable playback benchmark installation and output directory",
  );
const digest = (bytes) =>
  crypto.createHash("sha256").update(bytes).digest("hex");
(async () => {
  const browser = await chromium.launch({ channel: "chrome" });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 960 },
  });
  const page = await context.newPage();
  page.setDefaultTimeout(25000);
  const button = (name) => page.getByRole("button", { name, exact: true });
  const pageErrors = [];
  page.on("pageerror", (e) => pageErrors.push(e.message));
  const getJson = async (route) => {
    const r = await context.request.get(base + route);
    expect(r.ok()).toBeTruthy();
    return r.json();
  };
  async function checkEarlyBasisSelection(versionNumber) {
    // Delay only terminal status delivery. The real render and the normal
    // workspace refresh still publish the row before that response arrives.
    const statusRoute = "**/api/attempts/*";
    let release;
    const held = new Promise((resolve) => {
      release = resolve;
    });
    await page.route(statusRoute, async (route) => {
      const response = await route.fetch();
      if ((await response.json()).state === "SUCCEEDED") await held;
      await route.fulfill({ response });
    });
    const title = "Version " + versionNumber;
    try {
      await button("Create version").click();
      const row = page
        .locator(".version-row")
        .filter({ has: button("Inspect " + title) });
      await row
        .getByRole("button", { name: "Use this version", exact: true })
        .click();
      await expect(
        row.getByRole("button", { name: "In use", exact: true }),
      ).toBeVisible();
    } finally {
      release();
    }
    await expect(
      page.getByRole("heading", { name: "Refining " + title, exact: true }),
    ).toBeVisible();
    await expect(button("Adjust arrangement")).toBeVisible();
    await page.unroute(statusRoute);
    console.log(
      "PASS: selected basis is editable after the creation receipt arrives",
    );
  }
  const records = [];
  let requestPaths = null;
  page.on("request", (r) => {
    if (
      requestPaths &&
      r.method() === "GET" &&
      r.url().startsWith(base + "/api/")
    ) {
      const u = new URL(r.url());
      requestPaths.push(u.pathname);
    }
  });
  try {
    fs.mkdirSync(out, { recursive: true });
    if (fs.existsSync(path.join(out, "observations.jsonl")))
      throw Error("Select an empty benchmark output directory.");
    expect((await getJson("/api/workspace")).mode).toBe("LOCAL");
    for (const seconds of [12, 4, 8]) {
      await page.goto(base + "/remix/new?mode=backend");
      await expect(page).toHaveTitle(/Djound/);
      await expect(page.locator("vite-error-overlay")).toHaveCount(0);
      const chooser = page.waitForEvent("filechooser");
      await button("Choose audio file").and(page.locator("button")).click();
      await (
        await chooser
      ).setFiles(path.join(out, "source-" + seconds + ".wav"));
      await button("Continue to melody review").click();
      await expect(page).toHaveURL(/\/remix\/exp_[^/]+\/melody/);
      const id = new URL(page.url()).pathname.split("/")[2];
      await button("Detect melody").click();
      await expect(page.getByRole("link", { name: "Edit notes" })).toBeVisible({
        timeout: 180000,
      });
      await button("Use this melody").click();
      await expect(button("Adjust arrangement")).toBeVisible();
      const total = cycles + (records.length === 0 ? 1 : 0);
      const extraRegressionTrial = seconds === 8 ? 1 : 0;
      for (
        let version = 1;
        version <= total + extraRegressionTrial;
        version++
      ) {
        if (version > 1) {
          await page
            .locator(".version-row")
            .filter({ has: button("Inspect Version " + (version - 1)) })
            .getByRole("button", { name: "Use this version", exact: true })
            .click();
        }
        await button("Adjust arrangement").click();
        await page
          .getByLabel("Lead", { exact: true })
          .selectOption("electric_piano");
        await page
          .getByLabel("Drums", { exact: true })
          .selectOption(version % 2 ? "on" : "off");
        await page.getByLabel("Bass", { exact: true }).selectOption("on");
        await button("Review arrangement").click();
        await expect(button("Create version")).toBeEnabled();
        if (version > total) {
          await checkEarlyBasisSelection(version);
          continue;
        }
        const title = "Version " + version;
        await page.evaluate((title) => {
          // Start on the actual click event, not on the automation call.
          const timing = (window.bench = {});
          const now = () => performance.timeOrigin + performance.now();
          document.addEventListener(
            "click",
            function start(e) {
              if (
                e.target.closest("button")?.textContent.trim() !==
                "Create version"
              )
                return;
              document.removeEventListener("click", start, true);
              timing.start = now();
              const target = "Play " + title;
              // Use the real row button as soon as it is visible, excluding tester hesitation.
              const observer = new MutationObserver(() => {
                const playButton = document.querySelector(
                  'button[aria-label="' + target + '"]',
                );
                if (
                  !playButton ||
                  playButton.disabled ||
                  !playButton.getClientRects().length
                )
                  return;
                observer.disconnect();
                timing.ready = now();
                playButton.click();
              });
              observer.observe(document.body, {
                subtree: true,
                attributes: true,
                childList: true,
              });
              document.addEventListener(
                "playing",
                function playing(e) {
                  if (!(e.target instanceof HTMLAudioElement)) return;
                  timing.playing = now();
                  timing.media = new URL(e.target.currentSrc).pathname;
                  document.removeEventListener("playing", playing, true);
                },
                true,
              );
            },
            true,
          );
        }, title);
        requestPaths = [];
        await button("Create version").click();
        await page.waitForFunction(
          () =>
            window.bench?.playing &&
            document.querySelector("audio").currentTime > 0.05,
          null,
          { timeout: 60000 },
        );
        const timing = await page.evaluate(() => window.bench);
        const observedPaths = requestPaths;
        requestPaths = null;
        const state = await getJson("/api/explorations/" + id);
        const recording = state.renditions.at(-1);
        expect(state.latestAttempt.state).toBe("SUCCEEDED");
        expect(recording.title).toBe(title);
        expect(timing.media).toBe(recording.mediaUrl);
        const audio = await context.request.get(base + recording.mediaUrl);
        expect(digest(await audio.body())).toBe(recording.sha256);
        const entry = {
          seconds,
          version,
          first: !records.length,
          id,
          recording: recording.id,
          sha256: recording.sha256,
          clickToPlayingMs: Math.round(timing.playing - timing.start),
          clickToButtonMs: Math.round(timing.ready - timing.start),
          buttonToPlayingMs: Math.round(timing.playing - timing.ready),
          // Both clocks are on this host. updatedAt is the database terminal timestamp.
          terminalToButtonMs: Math.round(
            timing.ready - Date.parse(state.latestAttempt.updatedAt),
          ),
          acceptedToTerminalMs:
            Date.parse(state.latestAttempt.updatedAt) -
            Date.parse(state.latestAttempt.createdAt),
          getCount: observedPaths.length,
          statusGets: observedPaths.filter((x) => x.includes("/attempts/"))
            .length,
        };
        records.push(entry);
        fs.appendFileSync(
          path.join(out, "observations.jsonl"),
          JSON.stringify(entry) + "\n",
        );
        console.log(JSON.stringify(entry));
        await button("Pause playback").click();
      }
    }
    console.log(
      "Saved " + records.length + " verified playback observations to " + out,
    );
    if (pageErrors.length) throw Error(pageErrors.join("\n"));
    await page.screenshot({ path: path.join(out, "studio.png") });
  } catch (e) {
    console.error(
      "URL",
      page.url(),
      (await page.locator("body").innerText()).slice(-2400),
    );
    throw e;
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
