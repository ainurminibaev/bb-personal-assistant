import assert from "node:assert/strict";
import { test } from "node:test";
import plugin from "../dist/server.js";

async function load(binding) {
  let command, instructions;
  const queries = [];
  await plugin({
    settings: { define: () => ({ get: async () => binding }) },
    agents: { configure: callback => { instructions = callback; } },
    cli: { register: callback => { command = callback; } },
    sdk: { threads: { list: async query => {
      queries.push(query);
      return query.threadIds ? [{id: "owner", projectId: "personal"}] :
        [{id: "child", title: "PDF", status: "active"}];
    } } },
  });
  return { command, instructions, queries };
}

test("coordinator rules apply only to the configured thread in its own project", async () => {
  const owner = {projectId: "personal", threadId: "owner"};
  const { instructions } = await load(owner);
  const config = (projectId, threadId) => instructions({project: {id: projectId}, thread: {id: threadId}});
  assert.match(config(owner.projectId, owner.threadId).instructions, /projects\.md/);
  assert.deepEqual(config(owner.projectId, owner.threadId).skills, ["dispatch"]);
  assert.deepEqual(config(owner.projectId, "group"), {skills: [], tools: []});
  assert.deepEqual(config("other", owner.threadId), {skills: [], tools: []});
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
