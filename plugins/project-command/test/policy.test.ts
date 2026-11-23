import { test } from "node:test";
import assert from "node:assert/strict";
import { decide, type Lookup } from "../policy";

const projects: Record<string, string> = {thr_me: "proj_a", thr_kid: "proj_a", thr_other: "proj_x", thr_b: "proj_b"};
const l: Lookup = {
  agent: {user: "ag1", home: "/home/ag1", projects: ["proj_a", "proj_b"], telegram: {owner: 100, chats: [-200]}},
  caller: {threadId: "thr_me", projectId: "proj_a"},
  threadProject: async id => projects[id] ?? null,
  realpath: async p => p.replace("/home/ag1/link-out", "/etc"),
};
const ok = async (argv: string[], input?: string) => {
  const d = await decide(argv, l, input);
  assert.equal(d.ok, true, JSON.stringify(d));
  return d as Extract<typeof d, {ok: true}>;
};
const refused = async (argv: string[], re: RegExp, input?: string) => {
  const d = await decide(argv, l, input);
  assert.equal(d.ok, false, JSON.stringify(d));
  assert.match((d as {reason: string}).reason, re);
};

test("подтред: проект и родитель подставляются, чужое — отказ", async () => {
  const d = await ok(["thread", "spawn", "--prompt", "сделай"]);
  assert.deepEqual(d.argv.slice(-4), ["--project", "proj_a", "--parent-thread", "thr_me"]);
  await ok(["thread", "spawn", "--project", "proj_b", "--parent-thread", "thr_kid", "--prompt", "x"]);
  await refused(["thread", "spawn", "--project", "proj_x", "--prompt", "x"], /Проект proj_x не ваш/);
  await refused(["thread", "spawn", "--parent-thread", "thr_other", "--prompt", "x"], /Тред thr_other не из ваших/);
  await refused(["thread", "spawn", "--machine", "host_1", "--prompt", "x"], /машину/);
  await refused(["thread", "spawn", "--environment", "env_123", "--prompt", "x"], /машину или окружение/);
  await refused(["thread", "spawn", "--environment", "/srv/other", "--prompt", "x"], /вне вашей домашней папки/);
  await ok(["thread", "spawn", "--environment", "/home/ag1/ws", "--prompt", "x"]);
});

test("свои треды — да, чужие — нет; список — только свой проект", async () => {
  await ok(["thread", "tell", "thr_kid", "--message-file", "-"], "текст");
  await ok(["thread", "output", "thr_kid"]);
  await refused(["thread", "log", "thr_other"], /не из ваших/);
  await refused(["thread", "tell", "thr_kid", "--message-file", "/home/ag1/link-out/passwd"], /вне вашей/);
  const list = await ok(["thread", "list", "--json"]);
  assert.deepEqual(list.argv.slice(-2), ["--project", "proj_a"]);
  await refused(["thread", "list", "--project", "proj_x"], /не ваш/);
  await refused(["thread", "delete", "thr_kid"], /не входит в список/);
});

test("расписания: только агентские и в своём проекте; скрипт — отказ с заменой", async () => {
  const d = await ok(["automation", "create", "--name", "утро", "--cron", "0 9 * * *", "--timezone", "Europe/Moscow", "--prompt", "сводка"]);
  assert.deepEqual(d.argv.slice(-2), ["--project", "proj_a"]);
  await refused(["automation", "create", "--name", "s", "--in", "5m", "--script", "rm -rf /"], /расписание-агент/);
  await refused(["automation", "list", "--project", "proj_x"], /не ваш/);
  await refused(["automation", "create", "--name", "t", "--in", "5m", "--prompt", "x", "--target-thread", "thr_other"], /не из ваших/);
});

test("Telegram: send и архив — через мост; файлы только свои; вызовы моста — только из списка и про своё", async () => {
  await ok(["telegram-projects", "send", "--text", "привет"]);
  await ok(["telegram-projects", "search-history", "отчёт"]);
  await refused(["telegram-projects", "send", "--text", "x", "--file", "/var/lib/bb/secret"], /вне вашей/);
  const rpc = await ok(["plugin", "rpc", "call", "telegram-projects", "sendToTelegram"], JSON.stringify({threadId: "thr_me", topicId: 0, text: "x"}));
  assert.deepEqual(rpc.argv.slice(-3), ["--input-file", "-", "--json"]);
  await refused(["plugin", "rpc", "call", "telegram-projects", "sendToTelegram"], /не из ваших/, JSON.stringify({threadId: "thr_other"}));
  await refused(["plugin", "rpc", "call", "telegram-projects", "configureChatPair"], /не разрешён/, "{}");
  await refused(["plugin", "rpc", "call", "automations", "automations_list"], /не входит в список/, "{}");
  await refused(["plugin", "install", "evil"], /не входит в список/);
});

test("проекты: создать только в своей папке; голос — только свой файл; прочее — отказ", async () => {
  const d = await ok(["project", "create", "--name", "Новый", "--root", "/home/ag1/new"]);
  assert.equal(d.record?.createdProjectRoot, "/home/ag1/new");
  await refused(["project", "create", "--name", "x", "--root", "/srv/projects/owner"], /вне вашей/);
  await refused(["project", "create", "--name", "x", "--root", "/home/ag1/link-out/x"], /вне вашей/);
  assert.equal((await ok(["project", "list"])).filterProjects, true);
  await ok(["voice", "transcribe", "/home/ag1/in/voice.ogg"]);
  await refused(["voice", "transcribe", "/root/voice.ogg"], /вне вашей/);
  for (const argv of [["file", "read", "/etc/passwd"], ["terminal", "run", "--", "id"], ["machine", "list"], ["settings", "show"], ["environment", "list"]])
    await refused(argv, /не входит в список/);
});


test("Telegram: в личку — только своему владельцу, в чаты — только свои; список адресатов — из своих привязок", async () => {
  await ok(["telegram-projects", "send", "--person", "100", "--text", "владельцу"]);
  await refused(["telegram-projects", "send", "--person", "200000002", "--text", "x"], /не среди ваших адресатов.*не отправлено/);
  await ok(["telegram-projects", "send", "--chat", "-200", "--text", "в свою группу"]);
  await refused(["telegram-projects", "send", "--chat", "-999", "--text", "x"], /не привязан к вашим проектам/);
  await refused(["telegram-projects", "search-history", "--chat", "-999"], /не привязан/);
  const t = await ok(["telegram-projects", "send", "--targets"]);
  assert.deepEqual(t.targets, {people: [100], chats: [-200]});
});
