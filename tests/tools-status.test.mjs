import test from "node:test";
import assert from "node:assert/strict";
import { render, clean, toolsReport } from "../tools-status.ts";
import { mkdtempSync, mkdirSync, writeFileSync, utimesSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const report = {
  tools: [
    { id: "telegram-projects", state: "installed", reason: "needs a login: токен бота" },
    { id: "browser", state: "needs_setup", reason: "no browser on /var/lib/x/runtime/chrome", action: "run setup.py at https://example.com" },
    { id: "speech", state: "installed" },
  ],
  accesses: [{ name: "TEST_TOKEN", what: "ключ трекера", level: "organization", state: "present" }],
};

test("owner view: every tool in words, no paths or addresses", () => {
  const text = render(report, false);
  assert.match(text, /browser: нужна настройка/);
  assert.doesNotMatch(text, /\/var\/lib|https?:/);
  assert.match(text, /TEST_TOKEN — ключ трекера \(organization\)/);
});

test("guest view: only what a guest can apply, accesses by name and purpose", () => {
  const text = render(report, true);
  assert.match(text, /telegram-projects: работает/);
  assert.doesNotMatch(text, /browser/);
  assert.doesNotMatch(text, /токен бота/);
  assert.match(text, /TEST_TOKEN — ключ трекера$/m);
});

test("long tokens and paths are cut", () => {
  assert.equal(clean("key abcdefghijklmnopqrstuvwxyz0123456789ABCD in /home/a/b"), "key … in …");
});

test("a report made before this plugin version was loaded is checked again; a fresh one is read as is", async () => {
  const folder = mkdtempSync(join(tmpdir(), "tools-"));
  mkdirSync(join(folder, "local"));
  const file = join(folder, "local", "tools-report.json");
  writeFileSync(file, JSON.stringify({ tools: [{ id: "speech", state: "installed" }] }));
  utimesSync(file, new Date(0), new Date(0));                   // made long before the update
  let refreshed = 0;
  const refresh = async () => { refreshed++; writeFileSync(file, JSON.stringify({ tools: [{ id: "pdf", state: "installed" }] })); };
  const first = await toolsReport(folder, false, refresh);
  assert.equal(refreshed, 1);
  assert.match(first.stdout, /pdf: работает/);
  await toolsReport(folder, false, refresh);                     // now fresh: no second check
  assert.equal(refreshed, 1);
});

test("С110: accounts — connected, waiting for the owner with the step, errors; no values or paths", async () => {
  const { renderAccounts } = await import("../tools-status.ts");
  const out = renderAccounts({ tools: [
    { id: "google-workspace", state: "installed" },
    { id: "github", state: "needs_login", action: "вход gh по коду устройства: владелец жмёт Authorize" },
    { id: "browser", state: "excluded" },
    { id: "speech", state: "error", reason: "служба не отвечает /var/run/x.sock" },
  ], accesses: [{ name: "AGENTMAIL_API_KEY", what: "почта агента", state: "present" }, { name: "OLD", state: "missing" }] }, "2026-11-25 12:00 UTC");
  assert.match(out, /Подключено:\n- google-workspace\n- AGENTMAIL_API_KEY — почта агента/);
  assert.match(out, /Ждёт владельца:\n- github: нужен вход — вход gh по коду устройства/);
  assert.match(out, /Ошибки:\n- speech: служба не отвечает …/);
  assert.doesNotMatch(out, /browser|OLD|\/var\/run/);
  assert.match(renderAccounts({}), /Подключено:\n- нет\nЖдёт владельца:\n- ничего/);
});
