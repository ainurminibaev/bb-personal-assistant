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

test("С85: the newest letter wins whatever order the mail service lists them in", async () => {
  const now = Date.parse("2026-11-24T13:20:00Z");
  process.env.AGENTMAIL_API_KEY = "test-key";
  const asked = [];
  globalThis.fetch = async (url) => {
    const u = String(url);
    if (u.endsWith("/inboxes")) return { ok: true, json: async () => ({ inboxes: [{ inbox_id: "agent@agentmail.to" }] }) };
    if (u.includes("/messages?")) return { ok: true, json: async () => ({ messages: [
      { message_id: "old", timestamp: "2026-11-24T13:05:00Z", subject: "Код" },
      { message_id: "new", timestamp: "2026-11-24T13:19:00Z", subject: "Код" }] }) };
    asked.push(decodeURIComponent(u.split("/messages/")[1]));
    return { ok: true, json: async () => ({ subject: "Код", text: asked.at(-1) === "new" ? "Ваш код 222222" : "Ваш код 111111" }) };
  };
  const sent = [];
  const rpc = async (method, input) => method === "status" ? { chatBindings: [{ topicId: 0, threadId: "thr_owner" }] } : (sent.push(input), {});
  assert.equal((await codeToOwner([], rpc, now)).exitCode, 0);
  assert.deepEqual(asked, ["new"]);
  assert.match(sent[0].text, /222222/);
});

// The Google letter of 25.11 (digits made up): the code stands alone on its line; «код» comes again after it and the
// footer has a year and a street number — that made the old reading ambiguous and the owner got nothing.
const GOOGLE = `Подтверждение адреса электронной почты

agent@agentmail.to


Этот адрес электронной почты недавно был указан для подтверждения Вашего адреса.
Используйте этот код, чтобы подтвердить, что адрес принадлежит Вам.

604217

Если адрес указали не вы, возможно, кто-то ввел его по ошибке. В таком случае никаких действий от вас не требуется. Никому не сообщайте этот код.

Команда Google Аккаунтов


Это сообщение о важных изменениях в вашем аккаунте и сервисах Google.

© 2026 Google Inc.,1600 Amphitheatre Parkway, Mountain View, CA 94043, USA`;

test("С85: Google's letter — the code alone on its line; the footer's year and address are not codes", () => {
  assert.equal(extractCode(`Подтвердите адрес электронной почты\n${GOOGLE}`), "604217");
  assert.equal(extractCode(GOOGLE.replace(/\n/g, " ")), "604217");        // flattened HTML: the code-word reading
  assert.equal(extractCode("Ваш код:\n111111\n\nили\n222222"), undefined); // two lone codes — none
  assert.equal(extractCode("© 2026 Google\n\n2026\n"), undefined);          // a year is never a code
});

test("С85: no unambiguous code — nothing sent, the agent is told to offer the whole letter; --whole sends it", async () => {
  const now = Date.parse("2026-11-25T10:00:00Z");
  process.env.AGENTMAIL_API_KEY = "test-key";
  globalThis.fetch = async (url) => {
    const u = String(url);
    if (u.endsWith("/inboxes")) return { ok: true, json: async () => ({ inboxes: [{ inbox_id: "agent@agentmail.to" }] }) };
    if (u.includes("/messages?")) return { ok: true, json: async () => ({ messages: [
      { message_id: "g1", timestamp: "2026-11-25T09:57:00Z", from: "Google <no-reply@accounts.google.com>", subject: "Код" }] }) };
    return { ok: true, json: async () => ({ subject: "Код", text: "Ваш код:\n111111\n\nили\n222222" }) };
  };
  const sent = [];
  const rpc = async (method, input) => method === "status" ? { chatBindings: [{ topicId: 0, threadId: "thr_owner" }] } : (sent.push(input), {});
  const unclear = await codeToOwner(["--from", "google"], rpc, now);
  assert.equal(unclear.exitCode, 1);
  assert.equal(sent.length, 0);
  assert.match(unclear.stderr, /письмо целиком/);
  assert.match(unclear.stderr, /--whole/);
  assert.doesNotMatch(unclear.stderr, /запросите новый/i);
  const whole = await codeToOwner(["--from", "google", "--whole"], rpc, now);
  assert.equal(whole.exitCode, 0);
  assert.equal(sent[0].threadId, "thr_owner");
  assert.match(sent[0].text, /целиком/);
  assert.match(sent[0].text, /111111/);
  assert.doesNotMatch(whole.stdout, /111111/);                              // the agent never sees the letter
});
