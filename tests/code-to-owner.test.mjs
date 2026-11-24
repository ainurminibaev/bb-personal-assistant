import test from "node:test";
import assert from "node:assert/strict";
import { extractCode, codeToOwner } from "../code-to-owner.ts";

test("С85: one code next to a code word; several different — none", () => {
  assert.equal(extractCode("Используйте этот код, чтобы подтвердить адрес. 482913 Если адрес указали не вы…"), "482913");
  assert.equal(extractCode("Your verification code is G-551203"), "G-551203");
  assert.equal(extractCode("код 111111, а потом другой код 222222"), undefined);
  assert.equal(extractCode("Заказ 123456 оформлен"), undefined);
});

test("С85: the code goes only to the owner's private chat; the agent sees no code", async () => {
  const now = Date.parse("2026-11-24T13:20:00Z");
  process.env.AGENTMAIL_API_KEY = "test-key";
  const sent = [];
  globalThis.fetch = async (url) => {
    const u = String(url);
    if (u.endsWith("/inboxes")) return { ok: true, json: async () => ({ inboxes: [{ inbox_id: "agent@agentmail.to" }] }) };
    if (u.includes("/messages?")) return { ok: true, json: async () => ({ messages: [
      { message_id: "<m1@x>", timestamp: "2026-11-24T13:18:53Z", from: "Google <no-reply@accounts.google.com>", subject: "Подтвердите адрес" }] }) };
    return { ok: true, json: async () => ({ subject: "Подтвердите адрес", text: "Используйте этот код, чтобы подтвердить, что адрес принадлежит Вам. 731904 Если…" }) };
  };
  const rpc = async (method, input) => {
    if (method === "status") return { chatBindings: [{ topicId: 0, threadId: "thr_owner" }, { topicId: 5, threadId: "thr_topic" }] };
    sent.push([method, input]); return { queued: true };
  };
  const out = await codeToOwner(["--from", "google"], rpc, now);
  assert.equal(out.exitCode, 0);
  assert.doesNotMatch(out.stdout, /731904/);                       // the agent never sees the code
  assert.equal(sent.length, 1);
  assert.equal(sent[0][0], "sendToTelegram");
  assert.equal(sent[0][1].threadId, "thr_owner");                  // the owner's private chat only
  assert.equal(sent[0][1].topicId, 0);
  assert.match(sent[0][1].text, /731904/);
  // An old letter (outside the window) sends nothing.
  sent.length = 0;
  const late = await codeToOwner(["--minutes", "1"], rpc, now + 10 * 60_000);
  assert.equal(late.exitCode, 1);
  assert.equal(sent.length, 0);
});
