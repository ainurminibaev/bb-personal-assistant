import assert from "node:assert/strict";
import { test } from "node:test";
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

// Every skill the plugin hands to a thread must ship in the release's .bb/skills: BB refuses the whole configuration
// («agent configure failed: unknown skill id …») and the thread gets no skills at all (0.4.5 voice-breakdown, 0.4.7
// my-commitments on the stand). With every optional condition on, so each conditional skill is listed too.
const root = join(dirname(fileURLToPath(import.meta.url)), "..");

test("every skill handed to a conversation exists in the release with its own name", async () => {
  const bin = mkdtempSync(join(tmpdir(), "bin-")), home = mkdtempSync(join(tmpdir(), "home-"));
  for (const tool of ["vallis-secret", "vallis-commitments"]) { writeFileSync(join(bin, tool), "#!/bin/sh\n"); chmodSync(join(bin, tool), 0o755); }
  mkdirSync(join(home, ".secrets", "google-workspace"), { recursive: true });
  process.env.PATH = `${bin}:${process.env.PATH}`;
  process.env.HOME = home;
  const { default: plugin } = await import("../dist/server.js?skills");
  let configure;
  await plugin({
    settings: { define: () => ({ get: async () => ({ projectId: "personal", threadId: "owner" }), onChange() {} }) },
    agents: { configure: c => { configure = c; }, registerTool() {}, contributeInstructions() {} },
    storage: { database: () => ({ exec() {}, prepare: () => ({ all: () => [], get() {}, run() {} }) }), kv: { get: async () => undefined, set: async () => {}, delete: async () => {} } },
    cli: { register() {} }, rpc: { register() {} }, background: { schedule() {}, service() {} },
    experimental_hooks: { on() {} }, experimental_aiServices: { register: () => ({ dispose() {} }) },
    sdk: { projects: { list: async () => [] }, threads: { list: async () => [] },
      plugins: { getSettings: async () => ({ values: {} }), callRpc: async () => { throw Error("plugin_not_found"); } } },
  });
  const handed = new Set([
    ...configure({ project: { id: "personal" }, thread: { id: "owner" } }).skills,
    ...configure({ project: { id: "group-project" }, thread: { id: "group" } }).skills,
  ]);
  assert.ok(handed.has("secrets-store") && handed.has("google-workspace") && handed.has("commitments") && handed.has("my-commitments"),
    [...handed].join(", "));
  for (const id of handed) {
    const file = join(root, ".bb", "skills", id, "SKILL.md");
    assert.ok(existsSync(file), `skill ${id} is handed out but .bb/skills/${id}/SKILL.md is not in the release`);
    assert.match(readFileSync(file, "utf8"), new RegExp(`^---[\\s\\S]*?\\nname: ${id}\\n`), `skill ${id}: its SKILL.md is named otherwise`);
  }
});
