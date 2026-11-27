import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtempSync, mkdirSync, readFileSync, existsSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import plugin from "../dist/server.js";

// An installation folder and a BB with: the owner's private chat (bound to topic 0), a group topic conversation, and
// subthreads of both. Schedules: the case's own one is on until removed.
async function setup({ conversations = null, binding = { projectId: "proj", threadId: "owner-chat" }, pluginDir = null } = {}) {
  const root = mkdtempSync(join(tmpdir(), "cases-"));
  const threads = {
    "owner-chat": { id: "owner-chat", title: "🟢 Действующий · Личный помощник", parentThreadId: null },
    "owner-chat-2": { id: "owner-chat-2", title: "⏸ Резерв · Личный помощник", parentThreadId: null },
    "task-1": { id: "task-1", title: "⏳ разбор оплат", parentThreadId: "owner-chat" },
    "group": { id: "group", title: "Финансы — группа «Команда»", parentThreadId: null },
    "group-new": { id: "group-new", title: "Финансы — группа «Команда»", parentThreadId: null },
    "group-task": { id: "group-task", title: "⏳ отчёт", parentThreadId: "group" },
    "plain": { id: "plain", title: "Обычный тред", parentThreadId: null },
    "plain-next": { id: "plain-next", title: "Обычный тред", parentThreadId: null, sourceThreadId: "plain" },
  };
  const state = { owner: "owner-chat", live: new Set(["auto_a"]), made: [] };
  const sent = [], minute = [];
  let command, contributed;
  await plugin({
    settings: { define: () => ({ get: async () => binding, onChange() {} }) },
    agents: { configure() {}, registerTool() {}, contributeInstructions: p => { contributed = p; } },
    storage: { database: () => ({ exec() {}, prepare: () => ({ all: () => pluginDir ? [{ name: "main", file: join(pluginDir, "data.db") }] : [], get() {}, run() {} }) }), kv: { get: async () => undefined, set: async () => {} } },
    cli: { register: c => { command = c; } },
    rpc: { register() {} },
    background: { schedule: (name, cron, fn) => { if (name === "guest-schedule-alerts") minute.push(fn); }, service() {} },
    experimental_aiServices: { register: () => ({ dispose() {} }) },
    sdk: {
      projects: { list: async () => [{ id: "proj", sources: [{ path: root, isDefault: true }] },
                                     { id: "group-proj", sources: [{ path: mkdtempSync(join(tmpdir(), "group-")), isDefault: true }] }],
                  get: async () => ({ sources: [{ path: root }] }) },
      threads: { get: async ({ threadId }) => threads[threadId], list: async () => [], send: async a => { sent.push(a); return {}; } },
      plugins: {
        getSettings: async () => ({ values: { installKind: "assistant" } }),
        callRpc: async ({ pluginId, method, input, outputSchema }) => {
          if (pluginId === "telegram-projects" && method === "status")
            return outputSchema.parse({ chatBindings: [{ topicId: 0, threadId: state.owner }] });
          if (pluginId === "automations") return outputSchema.parse(state.made.length ? state.made : [...state.live].map(id => ({ id, enabled: true })));
          if (pluginId === "telegram-projects" && method === "conversationOf" && conversations)
            return outputSchema.parse(conversations[input.threadId] ?? null);
          throw Error("plugin_not_found");
        },
      },
    },
  });
  await new Promise(r => setTimeout(r, 20));
  const run = async (threadId, ...argv) => command.run(["case", ...argv], { threadId, projectId: threadId.startsWith("moved") ? "group-proj" : "proj" });
  const terminal = async (...argv) => command.run(["case", ...argv], {});
  const tick = async () => { for (const fn of minute) await fn(); };
  return { root, run, terminal, state, sent, tick, hint: threadId => contributed({ threadId, projectId: "proj" }) };
}

test("a case opened in a task subthread lives on disk: card and index line, written together", async () => {
  const { root, run } = await setup();
  const opened = await run("task-1", "open", "--title", "Разбор оплат за ноябрь", "--by", "Анна", "--place", "личка владельца",
    "--thread", "task-1", "--next", "сверить выгрузку", "--promise", "Анне — итог до 25.11 18:00");
  assert.equal(opened.exitCode, 0, opened.stderr);
  const id = opened.stdout.split("\n")[0];
  assert.match(id, /^c-\d{8}-razbor-oplat-za-noyabr$/);
  const card = readFileSync(join(root, "local", "cases", `${id}.md`), "utf8");
  assert.match(card, /title: "Разбор оплат за ноябрь"/);
  assert.match(card, /promises: \["Анне — итог до 25.11 18:00"\]/);
  assert.match(readFileSync(join(root, "local", "cases", "index.md"), "utf8"), new RegExp(`^${id} \\| open \\| личка владельца \\| Анна`));
});

test("reminders belong to the case: check finds a case without a next step, close hands its schedules to remove", async () => {
  const { run, state } = await setup();
  const id = (await run("task-1", "open", "--title", "Отчёт", "--thread", "task-1")).stdout.split("\n")[0];
  assert.match((await run("task-1", "check", "--here")).stdout, /нет ни расписания следующего шага, ни ожидания с датой/);
  assert.equal((await run("task-1", "set", id, "--schedule", "auto_a", "--next", "проверить в 16:00")).exitCode, 0);
  assert.equal((await run("task-1", "check")).stdout, "У каждого открытого дела есть следующий шаг.");
  state.live.delete("auto_a");   // the schedule was removed or switched off behind the case's back
  assert.match((await run("task-1", "check")).stdout, /расписания нет или выключено — auto_a/);
  state.live.add("auto_a");
  const closed = await run("task-1", "close", id, "--result", "итог отправлен Анне");
  assert.match(closed.stdout, /bb automation delete auto_a/);
  assert.match(closed.stdout, /bb thread-cleanup task task-1 closed/);
  assert.equal((await run("task-1", "list")).stdout, "Открытых дел нет.");
  assert.match((await run("task-1", "list", "--closed")).stdout, /closed \| Отчёт/);
  assert.match((await run("task-1", "set", id, "--state", "closed")).stderr, /case close/);
});

test("after a restart the new conversation of the same chat gets its open cases, and only them", async () => {
  const { run, state, hint } = await setup();
  await run("task-1", "open", "--title", "Дело лички");
  await run("group-task", "open", "--title", "Дело группы");
  await run("plain", "open", "--title", "Дело обычного треда");
  // The owner's private chat moved to the other thread of the pair; a group topic got a new conversation; a plain
  // thread was handed off.
  state.owner = "owner-chat-2";
  assert.match((await run("owner-chat-2", "list", "--here")).stdout, /Дело лички/);
  assert.doesNotMatch((await run("owner-chat-2", "list", "--here")).stdout, /Дело группы/);
  const group = (await run("group-new", "list", "--here")).stdout;
  assert.match(group, /Дело группы/);
  assert.doesNotMatch(group, /Дело лички/);
  assert.match((await run("plain-next", "list")).stdout, /Дело обычного треда/);
  // The new conversation is told to pick them up, without a model turn to find out.
  assert.match(hint("group-new"), /case list --here/);
});

test("a chat cannot read or change another chat's cases; only the coordinator sees all", async () => {
  const { run } = await setup();
  const id = (await run("task-1", "open", "--title", "Личное дело владельца")).stdout.split("\n")[0];
  assert.match((await run("group", "show", id)).stderr, /another chat/);
  assert.match((await run("group", "close", id)).stderr, /another chat/);
  assert.match((await run("group", "list", "--all")).stderr, /coordinator/);
  assert.match((await run("owner-chat", "list", "--all")).stdout, /Личное дело владельца/);
  assert.match((await run("group", "find", "Личное")).stdout, /Ничего не найдено/);
});

test("closed lines older than 90 days move to the archive list; no open case — no hint", async () => {
  const { root, run, hint } = await setup();
  assert.doesNotMatch(String(hint("task-1")), /case list --here/);
  const dir = join(root, "local", "cases");
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "index.md"), "c-20250101-staroe | closed | личка | Анна | Старое | — | 2025-01-01 | tg:owner\n");
  await run("task-1", "open", "--title", "Новое");
  assert.doesNotMatch(readFileSync(join(dir, "index.md"), "utf8"), /staroe/);
  assert.match(readFileSync(join(dir, "index-archive.md"), "utf8"), /staroe/);
  assert.ok(existsSync(join(dir, "index.md")));
});

test("with the bridge's conversationOf a chat is its numbers: a renamed topic keeps its cases, two people with one name do not share", async () => {
  const conversations = {
    "group": { chatId: -1001, topicId: 7 }, "group-renamed": { chatId: -1001, topicId: 7 },
    "alex-1": { chatId: 501, topicId: 0 }, "alex-2": { chatId: 502, topicId: 0 },
    "moved-group": { chatId: -1001, topicId: 7 },
  };
  const { run } = await setup({ conversations });
  await run("group", "open", "--title", "Отчёт группы");
  await run("alex-1", "open", "--title", "Дело первого Александра");
  assert.match((await run("group-renamed", "list")).stdout, /Отчёт группы/);
  assert.doesNotMatch((await run("alex-2", "list")).stdout, /первого Александра/);
  // The group moved into a project and folder of its own (С76): cases stay in the installation's folder.
  assert.match((await run("moved-group", "list")).stdout, /Отчёт группы/);
});

test("a terminal of the server sees every chat's cases from the installation's folder; a case is opened only from its chat", async () => {
  const { run, terminal } = await setup();
  await run("task-1", "open", "--title", "Дело лички");
  await run("group-task", "open", "--title", "Дело группы");
  const list = await terminal("list");
  assert.equal(list.exitCode, 0, list.stderr);
  assert.match(list.stdout, /Дело лички/);
  assert.match(list.stdout, /Дело группы/);
  assert.match((await terminal("open", "--title", "x")).stderr, /from its conversation/);
  assert.equal((await terminal("check")).exitCode, 0);
});

test("without any setting the cases live in the plugin's own data folder: a stand and a fresh employee need nothing", async () => {
  const pluginDir = mkdtempSync(join(tmpdir(), "plugin-"));
  const { run, terminal } = await setup({ binding: {}, pluginDir });
  const id = (await run("group-task", "open", "--title", "Итог к 10:00")).stdout.split("\n")[0];
  assert.ok(existsSync(join(pluginDir, "local", "cases", `${id}.md`)));
  const list = await terminal("list");
  assert.equal(list.exitCode, 0, list.stderr);
  assert.match(list.stdout, /Итог к 10:00/);
  assert.equal((await terminal("check")).exitCode, 0);
});

test("a schedule made from a Telegram chat becomes a case by itself; the agent is told its id", async () => {
  const { run, terminal, state, sent, tick } = await setup();
  const now = Date.now();
  state.made.push(
    { id: "auto_q", name: "Итог по вопросам поставщику", enabled: true, createdByThreadId: "group-task", createdAt: now,
      nextRunAt: now + 86400000, trigger: { triggerType: "once" } },
    { id: "auto_svc", name: "Уборка", enabled: true, createdByThreadId: "plain", createdAt: now, trigger: { triggerType: "schedule" } });
  await tick();
  const list = await terminal("list");
  assert.match(list.stdout, /Итог по вопросам поставщику/);          // the agent only created a schedule
  assert.doesNotMatch(list.stdout, /Уборка/);                         // not from a Telegram chat: stays a schedule
  const id = list.stdout.split(" | ")[0];
  assert.match((await run("group-task", "show", id)).stdout, /"auto_q"/);
  assert.match(sent.at(-1).input[0].text, new RegExp(`привязано к делу ${id}.*людям о ней не пиши`));
  assert.equal(sent.at(-1).mode, "steer");                            // never a turn of its own → never a message to people
  assert.doesNotMatch(sent.at(-1).input[0].text, /NO_REPLY/);         // it joins a running answer: must not silence it
  assert.equal(sent.at(-1).threadId, "group-task");
  const before = sent.length;
  await tick();                                                        // once only
  assert.equal(sent.length, before);
  // A schedule named «<case id>: <step>» joins that case instead of opening another.
  state.made.push({ id: "auto_step", name: `${id}: напомнить поставщику`, enabled: true, createdByThreadId: "group-task", createdAt: now });
  await tick();
  assert.equal((await terminal("list")).stdout.split("\n").filter(l => l.includes("поставщику")).length, 1);
  assert.match((await run("group-task", "show", id)).stdout, /"auto_step"/);
});

test("backward compatibility: legacy case card without deliver/delivered fields loads and updates cleanly", async () => {
  const { root, run } = await setup({
    conversations: { "group": { kind: "group", chatId: -10077, topicId: 12 } },
  });
  const dir = join(root, "local", "cases");
  mkdirSync(dir, { recursive: true });
  const legacyCard = [
    "---",
    'id: "c-20261120-legacy"',
    'title: "Старое дело"',
    'state: "open"',
    'opened: "2026-11-20T10:00:00.000Z"',
    'since: "2026-11-20T10:00:00.000Z"',
    'chat: "tg:-10077:12"',
    'place: "группа «Тест»"',
    'by: "Анна"',
    'message: ""',
    'thread: "group"',
    'next: "проверить"',
    "waiting: null",
    "schedules: []",
    "promises: []",
    "places: []",
    'people: ["Анна"]',
    'result: ""',
    "---",
    "2026-11-20 10:00 принято",
    "",
  ].join("\n");
  writeFileSync(join(dir, "c-20261120-legacy.md"), legacyCard);
  writeFileSync(join(dir, "index.md"), "c-20261120-legacy | open | группа «Тест» | Анна | Старое дело | проверить | 2026-11-20 | tg:-10077:12\n");

  const shown = await run("group", "show", "c-20261120-legacy");
  assert.equal(shown.exitCode, 0, shown.stderr);
  const card = JSON.parse(shown.stdout);
  assert.equal(card.deliver, null);
  assert.equal(card.delivered, null);

  const noTarget = await run("group", "deliver", "c-20261120-legacy", "--text", "Ответ");
  assert.equal(noTarget.exitCode, 1);
  assert.match(noTarget.stderr, /no_delivery_target/);

  const upd = await run("group", "set", "c-20261120-legacy", "--deliver-chat", "-10077", "--deliver-topic", "12");
  assert.equal(upd.exitCode, 0, upd.stderr);
  const card2 = JSON.parse((await run("group", "show", "c-20261120-legacy")).stdout);
  assert.deepEqual(card2.deliver, { chat: "-10077", topic: "12" });
});

test("step 1 mycelium: case open saves delivery address, case deliver sends via bridge with caller thread, blocks duplicate, records refusal", async () => {
  const { chmodSync } = await import("node:fs");
  const { root, run } = await setup({
    conversations: {
      "group": { kind: "group", chatId: -100555, topicId: 7 },
      "group-task": { kind: "group", chatId: -100555, topicId: 7 },
      "owner-chat": { kind: "owner", chatId: 42, topicId: 0 },
    },
  });

  const opened = await run("group", "open", "--title", "Проверка доставки", "--by", "Иван", "--place", "группа «Тест», тема 7", "--message", "321", "--thread", "group-task");
  assert.equal(opened.exitCode, 0, opened.stderr);
  const id = opened.stdout.split("\n")[0].trim();
  const card = JSON.parse((await run("group", "show", id)).stdout);
  assert.deepEqual(card.deliver, { chat: "-100555", topic: "7", replyTo: "321" });
  assert.equal(card.delivered, null);

  const dmOpened = await run("group", "open", "--title", "Ответ в личку", "--deliver-person", "777", "--deliver-reply-to", "55");
  assert.equal(dmOpened.exitCode, 0, dmOpened.stderr);
  const dmId = dmOpened.stdout.split("\n")[0].trim();
  const dmCard = JSON.parse((await run("group", "show", dmId)).stdout);
  assert.deepEqual(dmCard.deliver, { person: "777", replyTo: "55" });

  const binDir = mkdtempSync(join(tmpdir(), "fake-bb-"));
  const logFile = join(binDir, "calls.jsonl");
  const modeFile = join(binDir, "mode.txt");
  writeFileSync(modeFile, "ok");
  const fakeBb = join(binDir, "bb");
  writeFileSync(fakeBb, `#!/usr/bin/env node
const fs = require("node:fs");
const mode = fs.readFileSync(${JSON.stringify(modeFile)}, "utf8").trim();
fs.appendFileSync(${JSON.stringify(logFile)}, JSON.stringify({ argv: process.argv.slice(2), thread: process.env.BB_THREAD_ID || null, project: process.env.BB_PROJECT_ID || null }) + "\\n");
if (mode === "ok") {
  process.stdout.write(JSON.stringify({ sent: true, messageId: 901 }));
  process.exit(0);
} else {
  process.stderr.write("send_refused: нет прав писать в этот чат");
  process.exit(1);
}
`);
  chmodSync(fakeBb, 0o755);

  const prevPath = process.env.PATH;
  process.env.PATH = `${binDir}:${prevPath}`;
  try {
    const res = await run("group-task", "deliver", id, "--text", "Итог готов");
    assert.equal(res.exitCode, 0, res.stderr);
    assert.match(res.stdout, /msg 901/);

    const calls1 = readFileSync(logFile, "utf8").trim().split("\n").map(l => JSON.parse(l));
    assert.equal(calls1.length, 1);
    assert.deepEqual(calls1[0].argv, ["telegram-projects", "send", "--chat", "-100555", "--topic", "7", "--reply-to", "321", "--text", "Итог готов"]);
    assert.equal(calls1[0].thread, "group-task");

    const after = JSON.parse((await run("group", "show", id)).stdout);
    assert.equal(after.delivered.messageId, 901);
    assert.equal(after.delivered.byThread, "group-task");
    assert.ok(after.journal.some(j => j.includes("доставлено") && j.includes("msg 901")));

    const dup = await run("group-task", "deliver", id, "--text", "Повтор");
    assert.equal(dup.exitCode, 1);
    assert.match(dup.stderr, /already_delivered/);
    assert.equal(readFileSync(logFile, "utf8").trim().split("\n").length, 1);

    writeFileSync(modeFile, "refuse");
    const refused = await run("group", "deliver", dmId, "--text", "Попытка");
    assert.equal(refused.exitCode, 1);
    assert.match(refused.stderr, /send_refused: нет прав писать в этот чат/);
    const dmAfter = JSON.parse((await run("group", "show", dmId)).stdout);
    assert.equal(dmAfter.delivered, null);
    assert.equal(dmAfter.state, "undelivered");
    assert.ok(dmAfter.journal.some(j => j.includes("доставка не прошла") && j.includes("send_refused")));
  } finally {
    process.env.PATH = prevPath;
  }
});
