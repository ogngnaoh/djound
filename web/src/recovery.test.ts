import "fake-indexeddb/auto";
import { test } from "node:test";
import assert from "node:assert/strict";
import { pendingCommand, PendingCommandConflict } from "./recovery.ts";
test("simultaneous tabs cannot overwrite the first pending command", async () => {
  const slot = "concurrent";
  const first = {
    key: "first-key",
    path: "/takes",
    body: { parentId: "original", prompt: "Play it as a jazz trio" },
  };
  const second = {
    key: "second-key",
    path: "/takes",
    body: { parentId: "original", prompt: "Make a study soundtrack" },
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
    path: "/takes",
    body: {
      parentId: "piano-duet",
      prompt: "Keep the ending",
      startSeconds: 10,
      endSeconds: 20,
    },
  };
  await pendingCommand(slot, original);
  await pendingCommand(slot, {
    ...original,
    body: { ...original.body, parentId: "original", prompt: "Later typing" },
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

test("a clarification answer and a new edit cannot claim the same recording while a request is unconfirmed", async () => {
  const slot = "studio-edit.recording";
  const answer = {
    key: "answer-key",
    path: "/takes/parent-take/answer",
    body: { requestId: "answer-key", choice: "keep" },
  };
  await pendingCommand(slot, answer);
  await assert.rejects(
    pendingCommand(slot, {
      key: "new-key",
      path: "/takes",
      body: {
        requestId: "new-key",
        prompt: "Play it on piano",
        parentId: "original",
      },
    }),
    PendingCommandConflict,
  );
  assert.deepEqual(await pendingCommand(slot), answer);
  await pendingCommand(slot, null, answer.key);
  assert.equal(await pendingCommand(slot), undefined);
});
