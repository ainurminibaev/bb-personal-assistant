import { existsSync, lstatSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

/**
 * setup.py copies the guest thread guard into each installed workspace (.bb/hooks). A plugin update
 * does not rerun setup, so every start refreshes copies that are already there. It never installs a
 * new one: that stays an explicit setup step, with the owner's approval of the hook in Codex.
 */
export function refreshGuardCopies(guard: string, roots: readonly string[]) {
  const source = readFileSync(guard);
  const refreshed: string[] = [];
  for (const root of new Set(roots)) {
    const target = join(root, ".bb", "hooks", "guest-thread-guard.py");
    if (!existsSync(target) || lstatSync(target).isSymbolicLink() || readFileSync(target).equals(source)) continue;
    writeFileSync(target, source, { mode: 0o755 });
    refreshed.push(target);
  }
  return refreshed;
}
