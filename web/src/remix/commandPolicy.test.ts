import { test } from "node:test";
import assert from "node:assert/strict";
import { isDefiniteRejection } from "./commandPolicy.ts";

test("known pre-admission errors release the command but unknown failures remain uncertain", () => {
  assert.equal(isDefiniteRejection(503, "PROVIDER_NOT_CONFIGURED"), true);
  assert.equal(isDefiniteRejection(503, "CAPACITY_UNAVAILABLE"), true);
  assert.equal(isDefiniteRejection(503, "LIVE_ALLOWANCE_UNPROVISIONED"), true);
  assert.equal(isDefiniteRejection(503, "LIVE_ALLOWANCE_EXHAUSTED"), true);
  assert.equal(isDefiniteRejection(503, "SOURCE_UNAVAILABLE"), true);
  assert.equal(isDefiniteRejection(503, "MEDIA_STORAGE_FAILED"), false);
  assert.equal(isDefiniteRejection(503, "UPSTREAM_TIMEOUT"), false);
  assert.equal(isDefiniteRejection(500, "INTERNAL_ERROR"), false);
  assert.equal(isDefiniteRejection(409, "DRAFT_CONFLICT"), true);
});
