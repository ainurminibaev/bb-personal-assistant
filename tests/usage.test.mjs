import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import plugin from "../dist/server.js";

// The employee's account: Codex logs of today with two model calls and the account's window; one Claude Code session.
function home() {
  const h = mkdtempSync(join(tmpdir(), "emp-"));
  const now = new Date();
  const dir = join(h, ".codex", "sessions", String(now.getUTCFullYear()), String(now.getUTCMonth() + 1).padStart(2, "0"), String(now.getUTCDate()).padStart(2, "0"));
  mkdirSync(dir, { recursive: true });
  const call = (total, cached, used) => JSON.stringify({ timestamp: new Date().toISOString(), type: "event_msg", payload: { type: "token_count",
    info: { last_token_usage: { total_tokens: total, cached_input_tokens: cached } },
    rate_limits: { primary: { used_percent: used, window_minutes: 10080, resets_at: Math.floor(Date.now() / 1000) + 86400 }, secondary: null } } });
  const old = JSON.stringify({ timestamp: "2020-01-01T00:00:00Z", type: "event_msg", payload: { type: "token_count", info: { last_token_usage: { total_tokens: 999999 } } } });
  writeFileSync(join(dir, "rollout-a.jsonl"), [old, call(120000, 100000, 14), "torn{", call(30000, 20000, 15)].join("\n") + "\n");
  mkdirSync(join(h, ".claude", "projects", "w"), { recursive: true });
  writeFileSync(join(h, ".claude", "projects", "w", "s.jsonl"), JSON.stringify({ timestamp: new Date().toISOString(), type: "assistant",
    message: { usage: { input_tokens: 1000, output_tokens: 500, cache_read_input_tokens: 8500, cache_creation_input_tokens: 0 } } }) + "\n");
  return h;
}

test("an employee's spend: turns from its BB, tokens and the model window from its own agents' logs", async () => {
  process.env.HOME = home();
  let command;
  const now = Date.now();
  await plugin({
    settings: { define: () => ({ get: async () => ({}), onChange() {} }) },
    agents: { configure() {}, registerTool() {}, contributeInstructions() {} },
    storage: { database: () => ({ exec() {}, prepare: () => ({ all: () => [], get() {}, run() {} }) }), kv: { get: async () => undefined, set: async () => {} } },
    cli: { register: c => { command = c; } }, rpc: { register() {} },
    background: { schedule() {}, service() {} }, experimental_hooks: { on() {} },
    experimental_aiServices: { register: () => ({ dispose() {} }) },
    sdk: {
      projects: { list: async () => [] },
      threads: {
        list: async () => [{ id: "a", updatedAt: now }, { id: "b", updatedAt: now - 30 * 86400000 }],
        events: { list: async ({ threadId }) => threadId === "a"
          ? [{ createdAt: now - 3600000 }, { createdAt: now - 3 * 86400000 }, { createdAt: now - 9 * 86400000 }] : [{ createdAt: now }] },
      },
      plugins: { getSettings: async () => ({ values: {} }), callRpc: async () => { throw Error("plugin_not_found"); } },
    },
  });
  const out = await command.run(["usage"], { threadId: "x", projectId: "p" });
  assert.equal(out.exitCode, 0, out.stderr);
  assert.match(out.stdout, /За сутки: 1 ходов, 160 тыс\. токенов \(Codex 150 тыс\., Claude 10 тыс\.\)/);
  assert.match(out.stdout, /За неделю: 2 ходов/);           // a thread quiet for a month is not read
  assert.match(out.stdout, /неделя — 15 %/);                 // the latest mark of the account's window
  const json = JSON.parse((await command.run(["usage", "--json"], { threadId: "x", projectId: "p" })).stdout);
  assert.equal(json.day.tokens.codex, 150000);
  assert.equal(json.day.tokens.cached, 128500);
});
