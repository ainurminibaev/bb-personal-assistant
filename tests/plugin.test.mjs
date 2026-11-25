import assert from "node:assert/strict";
import { test } from "node:test";
import plugin from "../dist/server.js";

// The bridge as other plugins see it: its settings and its status (the private chat is topic 0).
function bridge({kind = "assistant", owner = "assistant-main", off = false} = {}) {
  const state = {kind, owner, off, calls: 0};
  state.plugins = {
    getSettings: async ({pluginId}) => {
      if (state.off || pluginId !== "telegram-projects") throw Error("plugin_not_found");
      return {ok: true, values: {installKind: state.kind}};
    },
    callRpc: async ({pluginId, method, outputSchema}) => {
      state.calls++;
      if (state.off || pluginId !== "telegram-projects" || method !== "status") throw Error("plugin_not_found");
      return outputSchema.parse({chatBindings: [{topicId: 5, threadId: "group-topic"},
        ...(state.owner ? [{topicId: 0, threadId: state.owner, projectId: "personal"}] : [])]});
    },
  };
  return state;
}

const settle = () => new Promise(resolve => setTimeout(resolve, 10));

async function load(binding, telegram = bridge(), dataFile = "/data/plugins/personal-assistant/data.db") {
  let command, instructions, changed, contributed, rpcHandlers;
  const queries = [], schedules = [], aiServices = [], services = [], stops = [];
  await plugin({
    settings: { define: () => ({ get: async () => binding, onChange: fn => { changed = fn; } }) },
    agents: { configure: callback => { instructions = callback; }, registerTool: () => {},
              contributeInstructions: provider => { contributed = provider; } },
    storage: { database: () => ({ exec() {}, prepare: () => ({ all: () => [{ name: "main", file: dataFile }], get() {}, run() {} }) }) },
    cli: { register: callback => { command = callback; } },
    rpc: { register: (_contract, handlers) => { rpcHandlers = handlers; } },
    background: { schedule: (name, cron) => { schedules.push([name, cron]); },
                  service: (name, def) => { services.push(name); const ac = new AbortController(); stops.push(() => ac.abort());
                    void def.start(ac.signal); } },
    experimental_aiServices: { register: declaration => { aiServices.push(declaration); return { dispose() {} }; } },
    sdk: { plugins: telegram.plugins, threads: { list: async query => {
      queries.push(query);
      return query.threadIds ? [{id: "owner", projectId: "personal"}] :
        [{id: "child", title: "PDF", status: "active"}];
    } } },
  });
  await settle();
  for (const stop of stops) stop();   // the plugin is unloaded: its background services stop
  return { command, instructions, queries, schedules, aiServices, services, rpc: () => rpcHandlers, changeBinding: next => changed(next),
           contact: threadId => contributed({threadId, projectId: "any"}) };
}

test("owner contact refreshes in the plugin's background service, so a reload stops it", async () => {
  const p = await load({ ownerContact: true });
  assert.ok(p.services.includes("owner-contact"));
});

test("coordinator rules apply only to the configured thread in its own project", async () => {
  const owner = {projectId: "personal", threadId: "owner"};
  const { instructions } = await load(owner);
  const config = (projectId, threadId) => instructions({project: {id: projectId}, thread: {id: threadId}});
  assert.match(config(owner.projectId, owner.threadId).instructions, /projects\.md/);
  assert.deepEqual(config(owner.projectId, owner.threadId).skills, ["dispatch", "integrations", "task-tracking", "tell-person", "agent-mail", "voice-breakdown"]);
  assert.deepEqual(config(owner.projectId, "group"), {skills: ["task-tracking", "tell-person", "agent-mail", "voice-breakdown"], tools: []});
  assert.deepEqual(config("other", owner.threadId), {skills: ["task-tracking", "tell-person", "agent-mail", "voice-breakdown"], tools: []});
});

test("the credentials store skill comes only where vallis-secret is on the PATH", async () => {
  const { mkdtempSync, writeFileSync } = await import("node:fs");
  const { tmpdir } = await import("node:os");
  const dir = mkdtempSync(`${tmpdir()}/vallis-`);
  writeFileSync(`${dir}/vallis-secret`, "#!/bin/sh\n", {mode: 0o755});
  const path = process.env.PATH;
  process.env.PATH = `${dir}:${path}`;
  try {
    const owner = {projectId: "personal", threadId: "owner"};
    const { instructions, contact } = await load(owner);
    const config = (projectId, threadId) => instructions({project: {id: projectId}, thread: {id: threadId}});
    assert.ok(config(owner.projectId, owner.threadId).skills.includes("secrets-store"));
    assert.deepEqual(config("other", "group").skills, ["task-tracking", "tell-person", "agent-mail", "voice-breakdown", "secrets-store"]);
    assert.match(contact("any-thread") ?? "", /vallis-secret list/);
  } finally {
    process.env.PATH = path;
  }
});

test("the commitments skill comes only to the owner's conversation, where vallis-commitments is on the PATH", async () => {
  const { mkdtempSync, writeFileSync } = await import("node:fs");
  const { tmpdir } = await import("node:os");
  const dir = mkdtempSync(`${tmpdir()}/vallis-`);
  writeFileSync(`${dir}/vallis-commitments`, "#!/bin/sh\n", {mode: 0o755});
  const path = process.env.PATH;
  process.env.PATH = `${dir}:${path}`;
  try {
    const owner = {projectId: "personal", threadId: "owner"};
    const { instructions } = await load(owner);
    const config = (projectId, threadId) => instructions({project: {id: projectId}, thread: {id: threadId}});
    assert.ok(config(owner.projectId, owner.threadId).skills.includes("commitments"));
    assert.ok(!config(owner.projectId, owner.threadId).skills.includes("my-commitments"));
    for (const guest of [config(owner.projectId, "group"), config("other", "group")]) {
      assert.ok(!guest.skills.includes("commitments"));
      assert.ok(guest.skills.includes("my-commitments"));
    }
  } finally {
    process.env.PATH = path;
  }
});

test("work from a group cannot query personal task names through this command", async () => {
  const owner = {projectId: "personal", threadId: "owner"};
  const { command, queries } = await load(owner);
  assert.equal((await command.run(["work"], {...owner, threadId: "group"})).exitCode, 1);
  assert.equal(queries.length, 0);
  assert.match((await command.run(["work"], owner)).stdout, /active\s+PDF/);
  assert.equal(queries[0].parentThreadId, owner.threadId);
});

test("doctor rejects incomplete and mismatched bindings", async () => {
  for (const b of [{}, {projectId: "other", threadId: "owner"}]) {
    const { command } = await load(b);
    assert.equal((await command.run(["doctor"], {})).exitCode, 1);
  }
  const { command } = await load({projectId: "personal", threadId: "owner"});
  assert.equal((await command.run(["doctor"], {})).exitCode, 0);
});

test("a settings change transfers rules and task queries without a plugin reload", async () => {
  const previous = {projectId: "personal", threadId: "old"};
  const next = {...previous, threadId: "owner"};
  const {instructions, command, queries, changeBinding} = await load(previous);
  const rules = id => instructions({project:{id:"personal"},thread:{id}}).instructions;
  assert.ok(rules("old"));
  changeBinding(next);
  assert.ok(rules("owner"));
  assert.equal(rules("old"), undefined);
  assert.equal(rules("group"), undefined);
  assert.equal((await command.run(["work"], previous)).exitCode, 1);
  assert.equal((await command.run(["work"], next)).exitCode, 0);
  assert.equal(queries.at(-1).parentThreadId, "owner");
  assert.equal((await command.run(["doctor"], {})).exitCode, 0);
});

test("an assistant installation tells every other thread the active assistant conversation", async () => {
  const telegram = bridge();
  const { contact, command } = await load({projectId: "personal", threadId: "owner"}, telegram);
  const text = contact("new-thread-of-another-project");
  assert.match(text, /bb thread tell assistant-main/);
  assert.match(text, /Владельцу в Telegram от/);
  assert.doesNotMatch(contact("assistant-main"), /bb thread tell/); // the assistant itself needs no detour
  assert.match(contact("assistant-main"), /\(С49, С69\)/);              // the common rules reach it too
  assert.match(contact("assistant-main"), /первой строкой ответа по сути просьбы[^\n]*\(С46\)/); // the live first line
  assert.match(contact("assistant-main"), /На месте отвечаешь[^\n]*\(С49, С69\)/);          // on the spot / a subthread
  assert.match(contact("assistant-main"), /читается за пять секунд[^\n]*\(С64\)/);
  assert.match(contact("assistant-main"), /code-to-owner[^\n]*только ему в личку[^\n]*\(С85\)/);
  assert.match(contact("assistant-main"), /Голосовое длиннее порога[^\n]*в том числе пересланное[^\n]*voice-breakdown[^\n]*\(С56\)/); // every conversation, also employees
  assert.match(contact("assistant-main"), /Не хватает права или настройки[^\n]*сам идёшь к владельцу с готовой просьбой[^\n]*точный путь, куда нажать[^\n]*сам проверь, что право появилось[^\n]*\(С77\)/); // ask the owner for a right yourself
  assert.match(contact("assistant-main"), /правилах, решениях и документах своей организации[^\n]*сначала ищи там[^\n]*называй источник[^\n]*Не нашёл — так и скажи[^\n]*\(С86\)/); // every conversation
  assert.match(contact("assistant-main"), /Перед работой на другой машине и в начале расписания[^\n]*machine_status[^\n]*не обещай работу на этой машине \(С104\)/); // every conversation
  assert.match(contact("assistant-main"), /Одно сообщение — сам ответ[^\n]*без «передал исполнителю[^\n]*называешь срок[^\n]*от одного лица[^\n]*NO_REPLY[^\n]*\(С101\)/); // every conversation
  assert.match(contact("assistant-main"), /дополнить своё только что отправленное сообщение[^\n]*не правкой[^\n]*ответом на своё сообщение[^\n]*Дополню[^\n]*без повторной отметки человека \(С101\)/); // every conversation
  assert.match(contact("assistant-main"), /Поправил своё сообщение[^\n]*ответом на исправленное[^\n]*что изменилось[^\n]*Опечатка сразу после отправки[^\n]*\(С106\)/); // every conversation
  assert.match(contact("assistant-main"), /Пишешь в чат, группу или личку первым за сегодня[^\n]*по времени суток в часовом поясе организации[^\n]*в стиле переписки этого чата[^\n]*не повторяешь[^\n]*не угадываешь[^\n]*канал[^\n]*\(С103\)/); // every conversation
  assert.match(contact("assistant-main"), /владельцу в личку из чужого разговора — только итог или вопрос[^\n]*не промежуточное/); // no progress notes to the owner          // messages read in five seconds
  telegram.owner = "assistant-reserve";            // the pair switched to the reserve thread
  contact("x"); await settle();
  assert.match(contact("y"), /bb thread tell assistant-reserve/);
  assert.equal((await command.run(["owner-thread"], {})).stdout, "assistant-reserve");
});

test("no owner contact for an employee, without the bridge, or when switched off; the common rules stay", async () => {
  for (const telegram of [bridge({kind: "employee"}), bridge({off: true}), bridge({owner: null})]) {
    const { contact, command } = await load({}, telegram);
    assert.doesNotMatch(contact("t"), /Владельцу в Telegram от/);
    assert.match(contact("t"), /\(С44\)/);
    assert.equal((await command.run(["owner-thread"], {})).exitCode, 1);
  }
  const { contact } = await load({ownerContact: false});
  assert.doesNotMatch(contact("t"), /Владельцу в Telegram от/);
});

test("the assistant's role reads the same marker that other threads are told to use", async () => {
  const { instructions, contact } = await load({projectId: "personal", threadId: "owner"});
  const policy = instructions({project: {id: "personal"}, thread: {id: "owner"}}).instructions;
  const marker = "Владельцу в Telegram от";
  assert.match(contact("other-thread"), new RegExp(marker));
  assert.match(policy, new RegExp(marker));
  assert.match(policy, /agent_roster_list/);           // roles: only while the roles list is installed
  assert.match(policy, /task-tracking skill/);         // kept as an ordinary tracked task
});

test("guest schedule bursts are checked every minute, without a model turn", async () => {
  const { schedules } = await load({ projectId: "personal", threadId: "owner" });
  assert.deepEqual(schedules, [["guest-schedule-alerts", "* * * * *"]]);
});

test("voice through the server speech service: one transcription service, ready only when it is installed", async () => {
  const { aiServices } = await load({projectId: "personal", threadId: "owner"});
  assert.deepEqual(aiServices.map(s => s.id), ["server-speech"]);
  assert.equal(typeof aiServices[0].transcribe, "function");
  assert.equal(aiServices[0].complete, undefined);
  const status = await aiServices[0].status();
  assert.equal(typeof status.ready, "boolean");
});

test("the bridge asks for the rules in force by project; a project outside an organization has none", async () => {
  const { rpc } = await load({});
  assert.equal(typeof rpc().turnRules, "function");
  // A project outside an organization still gets the platform's common rules (they are for every conversation).
  assert.match((await rpc().turnRules({ projectId: "unknown" })).text, /^### Общие правила платформы/);
  assert.deepEqual(await rpc().pauseStatus({}), { paused: false, since: null });
});

test("commitments run on the server's side: the installation's folder and the calling thread, not the agent's", async () => {
  const { mkdtempSync, writeFileSync, readFileSync, existsSync } = await import("node:fs");
  const { tmpdir } = await import("node:os");
  const dir = mkdtempSync(`${tmpdir()}/vallis-plugin-`);
  // A stand-in for bb: writes down who called and answers as the bridge does for a chat this conversation reads.
  const fake = `${dir}/bb`;
  writeFileSync(fake, `#!/bin/sh\nprintf '%s %s\\n' "$BB_THREAD_ID" "$*" >> ${dir}/calls\necho '{"messages":[{"chat":-1009990001111,"authorId":9002}]}'\n`, {mode: 0o755});
  const saved = {...process.env};
  Object.assign(process.env, {BB_CLI: fake, BB_THREAD_ID: "agents-own-thread", VALLIS_COMMITMENTS_HOME: `${dir}/elsewhere`, HOME: `${dir}/home`});
  try {
    const owner = {projectId: "personal", threadId: "owner"};
    const { command } = await load(owner, bridge(), `${dir}/plugins/personal-assistant/data.db`);
    const store = `${dir}/plugins/personal-assistant/local/commitments`;
    assert.match((await command.run(["commitments", "guide"], owner)).stdout, /vallis-commitments collect/);
    assert.equal((await command.run(["commitments", "chats", "add", "-1009990001111", "--title", "Команда"], owner)).exitCode, 0);
    assert.ok(existsSync(`${store}/config.json`));                 // the one folder of the installation
    assert.ok(!existsSync(`${dir}/elsewhere`) && !existsSync(`${dir}/home`));
    const added = await command.run(["commitments", "add", "--chat", "-1009990001111", "--who", "Борис", "--who-id", "9002",
      "--what", "прислать смету", "--message", "102"], owner);
    assert.equal(added.exitCode, 0, added.stderr);
    // a group's conversation sees the same list, and the bridge is asked in the name of the thread BB gave
    const mine = await command.run(["commitments", "mine", "--chat", "-1009990001111"], {projectId: "team", threadId: "group-thread"});
    assert.match(mine.stdout, /1\. Борис: прислать смету/);
    assert.match(readFileSync(`${dir}/calls`, "utf8"), /^group-thread telegram-projects search-history --chat -1009990001111 --last 1$/m);
    assert.doesNotMatch(readFileSync(`${dir}/calls`, "utf8"), /agents-own-thread/);
    const refused = await command.run(["commitments", "close", "99", "--by", "x"], owner);
    assert.equal(refused.exitCode, 1);
    assert.match(refused.stderr, /no commitment 99/);
  } finally {
    for (const k of Object.keys(process.env)) if (!(k in saved)) delete process.env[k];
    Object.assign(process.env, saved);
  }
});
