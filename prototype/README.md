# Djound listening room prototype

Serve this directory from localhost, then open the homepage. The Library and Studio use the browser’s IndexedDB on that origin. They are independent of the production interface and local inference service.

    python3 -m http.server 18774 --bind 127.0.0.1 --directory prototype

The homepage and seeded Studio session use two matching, locally supplied WAV excerpts at `prototype/media/original.wav` and `prototype/media/edit.wav`. This workspace uses the approved 20.5-second To Zanarkand piano and jazz trio comparison. Recordings are excluded from Git. To reproduce this listening session in another checkout, copy both excerpts from your local media backup. Personal imports and their edits are stored in the browser.

The demo plays a prepared arrangement. Warmth and focus revisions render audio from the selected performance in the browser. The progress sequence illustrates the planned engine interaction. Production inference and job reporting are separate from this design preview.

With the preview server running, run `node scripts/prototype-smoke.mjs` from the repository root. It uses Playwright from the existing web dependencies and temporary browser profiles for a desktop and phone walkthrough.
