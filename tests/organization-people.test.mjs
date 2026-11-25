import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { organizationPeople, parsePeople } from "../team.ts";

const hash = s => createHash("sha256").update(s).digest("hex");

function org(people) {
  const root = mkdtempSync(join(tmpdir(), "orgp-"));
  writeFileSync(join(root, "organization.yml"), `name: Пример\nemployees:\n  - mgr-agent\n  - dev-agent\n${people}\nspaces: []\n`);
  for (const [id, pass] of [["mgr-agent", "owner: Анна Владелец\ncurator: Анна Владелец\n"],
                            ["dev-agent", "owner: Анна Владелец\ncurator: Борис Куратор   # технические решения\n"]]) {
    mkdirSync(join(root, "employees", id), { recursive: true });
    writeFileSync(join(root, "employees", id, "passport.yml"), pass);
  }
  return root;
}

const PEOPLE = `people:                 # люди организации
  Анна Владелец:
    telegram: 1000001     # владелец
  Борис Куратор:
    telegram: 1000002
  Вера Менеджер:
    telegram: 1000003
  Без ID:
    note: нет телеграма
  Гость Бывший:
    telegram: 1000004
    approved: false`;

test("С111: people of organization.yml — nested names, Telegram IDs, approved:false", () => {
  const p = parsePeople(PEOPLE + "\nother: 1\n");
  assert.deepEqual(p.map(x => [x.name, x.telegram, x.approved]),
    [["Анна Владелец", 1000001, true], ["Борис Куратор", 1000002, true], ["Вера Менеджер", 1000003, true],
     ["Без ID", undefined, true], ["Гость Бывший", 1000004, false]]);
});

test("С111: organizationPeople — roles from passports, the same list for every employee, version follows the list", () => {
  const root = org(PEOPLE);
  const dev = organizationPeople(join(root, "employees", "dev-agent"), hash);
  const mgr = organizationPeople(join(root, "employees", "mgr-agent"), hash);
  assert.equal(dev.organization, "Пример");
  assert.equal(dev.me, "dev-agent");
  assert.equal(mgr.me, "mgr-agent");
  assert.deepEqual(dev.people, mgr.people);
  assert.equal(dev.version, mgr.version);
  assert.match(dev.version, /^op-[0-9a-f]{8}$/);
  assert.deepEqual(dev.people, [
    { id: 1000001, name: "Анна Владелец", roles: ["owner", "curator", "approved"], of: ["mgr-agent"] },
    { id: 1000002, name: "Борис Куратор", roles: ["curator", "approved"], of: ["dev-agent"] },
    { id: 1000003, name: "Вера Менеджер", roles: ["approved"] },
    { id: 1000004, name: "Гость Бывший", roles: [] },
  ]);
  const other = organizationPeople(join(org(PEOPLE.replace("Вера Менеджер", "Вера Новая")), "employees", "dev-agent"), hash);
  assert.notEqual(other.version, dev.version);
});

test("С111: without an organization copy — null (the bridge revokes nothing)", () => {
  assert.equal(organizationPeople(join(tmpdir(), "nowhere", "proj"), hash), null);
  assert.equal(organizationPeople(undefined, hash), null);
});
