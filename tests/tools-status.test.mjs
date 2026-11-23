import test from "node:test";
import assert from "node:assert/strict";
import { render, clean } from "../tools-status.ts";

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
