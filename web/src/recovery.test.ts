import "fake-indexeddb/auto";
import { test } from "node:test";
import assert from "node:assert/strict";
import { pendingCommand, PendingCommandConflict } from "./recovery.ts";
test("simultaneous tabs cannot overwrite the first pending command", async () => {
  const slot = "concurrent";
  const first = {
    key: "first-key",
    text: "Original command",
    expectedSourceRevision: 1,
  };
  const second = {
    key: "second-key",
    text: "Competing command",
    expectedSourceRevision: 1,
  };
  const results = await Promise.allSettled([
    pendingCommand(slot, first),
    pendingCommand(slot, second),
  ]);
  assert.equal(results.filter((r) => r.status === "fulfilled").length, 1);
  const loser = results.find(
    (r) => r.status === "rejected",
  ) as PromiseRejectedResult;
  assert.ok(loser.reason instanceof PendingCommandConflict);
  const stored = await pendingCommand<typeof first>(slot);
  assert.ok(stored);
  assert.deepEqual(loser.reason.command, stored);
  const otherKey = stored.key === first.key ? second.key : first.key;
  await pendingCommand(slot, null, otherKey);
  assert.deepEqual(
    await pendingCommand(slot),
    stored,
    "a competing tab must not clear the owner command",
  );
  await pendingCommand(slot, null, stored.key);
  assert.equal(await pendingCommand(slot), undefined);
});
test("same-key retry keeps the exact originally stored payload", async () => {
  const slot = "same-key";
  const original = {
    key: "retry-key",
    text: "Frozen direction",
    expectedSourceRevision: 4,
  };
  await pendingCommand(slot, original);
  await pendingCommand(slot, {
    ...original,
    text: "Later typing",
    expectedSourceRevision: 5,
  });
  assert.deepEqual(await pendingCommand(slot), original);
  await assert.rejects(pendingCommand(slot, null), /owner key/);
  assert.deepEqual(await pendingCommand(slot), original);
});
test("an old response cannot delete a newer pending command", async () => {
  const slot = "late-clear";
  await pendingCommand(slot, { key: "old-key" });
  await pendingCommand(slot, null, "old-key");
  await pendingCommand(slot, { key: "new-key" });
  await pendingCommand(slot, null, "old-key");
  assert.deepEqual(await pendingCommand(slot), { key: "new-key" });
});

test("uncertain retry owns the same admission slot as creation and preserves its exact request and predecessor", async () => {
  const slot = "creation-retry-admission";
  const retry = {
    kind: "RETRY",
    version: 1,
    key: "retry-ack-lost",
    explorationId: "exploration-a",
    requestId: "immutable-request",
    submittedText: "Original direction",
    body: { expectedPreviousAttemptId: "failed-attempt" },
  };
  await pendingCommand(slot, retry);
  await assert.rejects(
    pendingCommand(slot, {
      kind: "CREATE",
      key: "new-request",
      body: { text: "Later notes" },
    }),
    PendingCommandConflict,
  );
  const recovered = await pendingCommand<typeof retry>(slot);
  assert.deepEqual(recovered, retry);
  await pendingCommand(slot, {
    ...retry,
    body: { expectedPreviousAttemptId: "new-active-attempt" },
  });
  assert.deepEqual(await pendingCommand(slot), retry);
  await pendingCommand(slot, null, "new-request");
  assert.deepEqual(await pendingCommand(slot), retry);
  await pendingCommand(slot, null, retry.key);
  assert.equal(await pendingCommand(slot), undefined);
});

import { captureAcknowledgement } from "./remix/commandPolicy.ts";
test("a captured acknowledgement survives delayed draft save and exact uncertain replay", async () => {
  let currentMarker = "unknown-at-click";
  const body = {
    text: "Frozen words",
    expectedDraftRevision: 3,
    ...captureAcknowledgement(currentMarker),
  };
  currentMarker = "different-unknown-after-click";
  const command = {
    kind: "CREATE",
    key: "ack-captured",
    body: { ...body, expectedDraftRevision: 4 },
  };
  await pendingCommand("ack-capture", command);
  const recovered = await pendingCommand<typeof command>("ack-capture");
  assert.equal(
    recovered?.body.acknowledgedUnknownAttemptId,
    "unknown-at-click",
  );
  assert.notEqual(recovered?.body.acknowledgedUnknownAttemptId, currentMarker);
  assert.equal(recovered?.body.expectedDraftRevision, 4);
  assert.deepEqual(recovered, command);
});
test("legacy pending create and retry bodies replay without adding a live acknowledgement", async () => {
  for (const command of [
    {
      key: "legacy-create",
      body: { text: "Old frozen words", expectedDraftRevision: 1 },
    },
    {
      key: "legacy-retry",
      kind: "RETRY",
      body: { expectedPreviousAttemptId: "old-failed-attempt" },
    },
  ]) {
    const slot = command.key;
    await pendingCommand(slot, command);
    const recovered = await pendingCommand<typeof command>(slot);
    assert.deepEqual(recovered, command);
    assert.equal(
      Object.hasOwn(recovered!.body, "acknowledgedUnknownAttemptId"),
      false,
    );
  }
  assert.deepEqual(captureAcknowledgement(), {});
});
