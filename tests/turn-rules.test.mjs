import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, utimesSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { organizationRoot, roleEssence, turnRules } from "../turn-rules.ts";

function org() {
  const root = mkdtempSync(join(tmpdir(), "org-"));
  writeFileSync(join(root, "organization.yml"), "name: test\n");
  mkdirSync(join(root, "shared"));
  writeFileSync(join(root, "shared", "AGENTS.md"), "# Правила\n1. Делай там, где просят.\n");
  const folder = join(root, "employees", "bot");
  mkdirSync(join(folder, "local"), { recursive: true });
  writeFileSync(join(folder, "AGENTS.md"), "# Роль\n## Карта проекта\nдлинная карта\n## Как работаешь\n- коротко\n## Мои учётки\nсекретно не тут\n## Не делаешь\n- лишнего\n");
  writeFileSync(join(folder, "local", "agents-custom.md"), "Заметка: сроки — у исполнителей.\n");
  return { root, folder };
}

test("the rules in force: platform, organization, role essence and the employee's notes, with a fingerprint", () => {
  const { root, folder } = org();
  assert.equal(organizationRoot(folder), root);
  const r = turnRules(folder, "Поручение владельца выполняешь там, где оно дано (С44).");
  assert.ok(r);
  assert.match(r.text, /Общие правила платформы[\s\S]*С44/);
  assert.match(r.text, /Делай там, где просят/);
  assert.match(r.text, /## Как работаешь[\s\S]*## Не делаешь/);
  assert.doesNotMatch(r.text, /длинная карта|секретно не тут/, "maps and accounts stay in the file");
  assert.match(r.text, /сроки — у исполнителей/);
  assert.match(r.version, /^[a-f0-9]{12}$/);
});

test("an edit of any part changes the fingerprint; no edit keeps it", () => {
  const { folder } = org();
  const a = turnRules(folder, "x");
  assert.equal(turnRules(folder, "x").version, a.version);
  const notes = join(folder, "local", "agents-custom.md");
  writeFileSync(notes, "Заметка: новая строка.\n");
  utimesSync(notes, new Date(), new Date(Date.now() + 5000)); // a later mtime, as a real edit
  const b = turnRules(folder, "x");
  assert.notEqual(b.version, a.version);
  assert.match(b.text, /новая строка/);
});

test("outside an organization (the assistant, a conversation in a folder of its own) the platform's rules still go", () => {
  const plain = mkdtempSync(join(tmpdir(), "plain-"));
  const r = turnRules(plain, "Поручение со сроком — сразу дело.");
  assert.match(r.text, /^### Общие правила платформы\nПоручение со сроком — сразу дело\.$/);
  assert.equal(turnRules(undefined, "Поручение со сроком — сразу дело.").version, r.version);
  assert.notEqual(turnRules(plain, "другое правило").version, r.version);   // an edit of the platform's rules reaches it
  assert.equal(turnRules(plain, ""), null);
  assert.equal(roleEssence("# Роль\n## Как пишешь в Telegram\nпросто\n## Инструменты\nсписок"), "## Как пишешь в Telegram\nпросто");
});
