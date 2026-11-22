import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, symlinkSync, existsSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { refreshGuardCopies } from "../guard-refresh.ts";

test("a plugin start refreshes installed guard copies and installs none", () => {
  const dir = mkdtempSync(join(tmpdir(), "guard-"));
  const guard = join(dir, "new.py"); writeFileSync(guard, "new");
  const installed = join(dir, "a"), fresh = join(dir, "b"), linked = join(dir, "c");
  for (const root of [installed, linked]) mkdirSync(join(root, ".bb", "hooks"), { recursive: true });
  mkdirSync(fresh);
  writeFileSync(join(installed, ".bb/hooks/guest-thread-guard.py"), "old");
  writeFileSync(join(dir, "elsewhere.py"), "keep");
  symlinkSync(join(dir, "elsewhere.py"), join(linked, ".bb/hooks/guest-thread-guard.py"));
  assert.deepEqual(refreshGuardCopies(guard, [installed, fresh, linked, installed]), [join(installed, ".bb/hooks/guest-thread-guard.py")]);
  assert.equal(readFileSync(join(installed, ".bb/hooks/guest-thread-guard.py"), "utf8"), "new");
  assert.equal(existsSync(join(fresh, ".bb")), false);
  assert.equal(readFileSync(join(dir, "elsewhere.py"), "utf8"), "keep");
  assert.deepEqual(refreshGuardCopies(guard, [installed]), []);
});
