import assert from "node:assert/strict";
import { test } from "node:test";
import plugin from "../dist/server.js";

async function load(binding) {
  let command, instructions, changed;
  const queries = [];
  await plugin({
    settings: { define: () => ({ get: async () => binding, onChange: fn => { changed = fn; } }) },
    agents: { configure: callback => { instructions = callback; }, registerTool: () => {} },
    storage: { database: () => ({ exec() {}, prepare: () => ({ all: () => [{ name: "main", file: "/data/plugins/personal-assistant/data.db" }], get() {}, run() {} }) }) },
    cli: { register: callback => { command = callback; } },
    sdk: { threads: { list: async query => {
      queries.push(query);
      return query.threadIds ? [{id: "owner", projectId: "personal"}] :
        [{id: "child", title: "PDF", status: "active"}];
    } } },
  });
  return { command, instructions, queries, changeBinding: next => changed(next) };
}

test("coordinator rules apply only to the configured thread in its own project", async () => {
  const owner = {projectId: "personal", threadId: "owner"};
  const { instructions } = await load(owner);
  const config = (projectId, threadId) => instructions({project: {id: projectId}, thread: {id: threadId}});
  assert.match(config(owner.projectId, owner.threadId).instructions, /projects\.md/);
  assert.deepEqual(config(owner.projectId, owner.threadId).skills, ["dispatch", "integrations", "task-tracking", "agent-mail"]);
  assert.deepEqual(config(owner.projectId, "group"), {skills: ["task-tracking", "agent-mail"], tools: []});
  assert.deepEqual(config("other", owner.threadId), {skills: ["task-tracking", "agent-mail"], tools: []});
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
