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

async function load(binding, telegram = bridge()) {
  let command, instructions, changed, contributed, rpcHandlers;
  const queries = [], schedules = [], aiServices = [], services = [], stops = [];
  await plugin({
    settings: { define: () => ({ get: async () => binding, onChange: fn => { changed = fn; } }) },
    agents: { configure: callback => { instructions = callback; }, registerTool: () => {},
              contributeInstructions: provider => { contributed = provider; } },
    storage: { database: () => ({ exec() {}, prepare: () => ({ all: () => [{ name: "main", file: "/data/plugins/personal-assistant/data.db" }], get() {}, run() {} }) }) },
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
  assert.deepEqual(config(owner.projectId, owner.threadId).skills, ["dispatch", "integrations", "task-tracking", "agent-mail", "voice-breakdown"]);
  assert.deepEqual(config(owner.projectId, "group"), {skills: ["task-tracking", "agent-mail", "voice-breakdown"], tools: []});
  assert.deepEqual(config("other", owner.threadId), {skills: ["task-tracking", "agent-mail", "voice-breakdown"], tools: []});
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
    assert.deepEqual(config("other", "group").skills, ["task-tracking", "agent-mail", "voice-breakdown", "secrets-store"]);
    assert.match(contact("any-thread") ?? "", /vallis-secret list/);
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
  assert.equal(await rpc().turnRules({ projectId: "unknown" }), null);
});
