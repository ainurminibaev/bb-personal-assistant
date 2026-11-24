import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import plugin from "../dist/server.js";

// An employee's BB: its own project inside the organization's copy and an older project of a group outside it (the
// conversation of the leaders' group lived there). No coordinator project in the settings, as after setup.py.
test("the bridge asks the rules for a group's project outside the copy: it gets the organization's rules and the role", async () => {
  const root = mkdtempSync(join(tmpdir(), "org-"));
  writeFileSync(join(root, "organization.yml"), "name: test\nemployees: [bot]\n");
  mkdirSync(join(root, "shared"));
  writeFileSync(join(root, "shared", "AGENTS.md"), "# Правила организации\nОтвечай там, где спросили.\n");
  const own = join(root, "employees", "bot");
  mkdirSync(own, { recursive: true });
  writeFileSync(join(own, "AGENTS.md"), "# Роль\n## Как работаешь\n- веду встречи\n");
  const old = mkdtempSync(join(tmpdir(), "botsman-team-"));
  let rpc;
  await plugin({
    settings: { define: () => ({ get: async () => ({}), onChange() {} }) },
    agents: { configure() {}, registerTool() {}, contributeInstructions() {} },
    storage: { database: () => ({ exec() {}, prepare: () => ({ all: () => [], get() {}, run() {} }) }), kv: { get: async () => undefined, set: async () => {}, delete: async () => {} } },
    cli: { register() {} }, rpc: { register: (_c, h) => { rpc = h; } }, background: { schedule() {}, service() {} },
    experimental_hooks: { on() {} }, experimental_aiServices: { register: () => ({ dispose() {} }) },
    sdk: { projects: { list: async () => [{ id: "own", sources: [{ path: own, isDefault: true }] }, { id: "group-old", sources: [{ path: old, isDefault: true }] }] },
      threads: { list: async () => [] },
      plugins: { getSettings: async () => ({ values: {} }), callRpc: async () => { throw Error("plugin_not_found"); } } },
  });
  await new Promise(r => setTimeout(r, 20));
  const rules = await rpc.turnRules({ projectId: "group-old" });
  assert.match(rules.text, /### Общие правила платформы/);
  assert.match(rules.text, /### Общие правила организации[\s\S]*Отвечай там, где спросили/);
  assert.match(rules.text, /### Роль — суть[\s\S]*веду встречи/);
  assert.equal(rules.version, (await rpc.turnRules({ projectId: "own" })).version);
});
