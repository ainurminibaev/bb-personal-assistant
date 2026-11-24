import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";

/**
 * `bb personal-assistant commitments …` — the list of commitments and the digests (С91, С92), run on the server's side.
 *
 * The agent's turn may be sandboxed (Codex writes only inside its own folders), and every conversation of the
 * installation must see one list. So the agent's `vallis-commitments` only passes its arguments here, and the work is
 * done by the same program started by the plugin:
 *  - the folder is the installation's own, chosen here and nowhere else: <installation>/local/commitments;
 *  - the calling conversation is the thread BB names for this call, not what the agent says: the bridge then decides
 *    what that conversation may read and where it may send.
 */
export const COMMITMENTS_USAGE = "bb personal-assistant commitments <collect|add|close|update|reopen|list|render|post|mine|chats|recipients|set|guide> …";

export function commitmentsScript(here: string): string | undefined {
  return [join(here, "deploy", "vallis-commitments"), join(here, "..", "deploy", "vallis-commitments")].find(existsSync);
}

/** The environment of the started program: the installation's folder and the calling thread, nothing from the agent. */
export function commitmentsEnv(base: NodeJS.ProcessEnv, root: string, ctx: { threadId?: string; projectId?: string }) {
  const env: NodeJS.ProcessEnv = { ...base, PYTHONIOENCODING: "utf-8", VALLIS_COMMITMENTS_HOME: join(root, "local", "commitments") };
  // A call from a terminal has no thread: the bridge then refuses to read the archive, as it should.
  delete env.BB_THREAD_ID; delete env.BB_PROJECT_ID;
  if (ctx.threadId) env.BB_THREAD_ID = ctx.threadId;
  if (ctx.projectId) env.BB_PROJECT_ID = ctx.projectId;
  if (!env.BB_SERVER_URL && env.BB_SERVER_PORT) env.BB_SERVER_URL = `http://127.0.0.1:${env.BB_SERVER_PORT}`;
  return env;
}

export function runCommitments(script: string | undefined, root: string | undefined, argv: string[],
  ctx: { threadId?: string; projectId?: string }, base: NodeJS.ProcessEnv = process.env):
  Promise<{ exitCode: number; stdout?: string; stderr?: string }> {
  if (!script) return Promise.resolve({ exitCode: 1, stderr: "vallis-commitments is not in this plugin's package." });
  if (!root) return Promise.resolve({ exitCode: 1, stderr: "This installation has no folder for the list of commitments yet: tell the owner, do not keep the list anywhere else." });
  return new Promise(resolve => {
    const child = execFile("python3", [script, ...argv],
      { timeout: 600_000, maxBuffer: 16 * 1024 * 1024, env: commitmentsEnv(base, root, ctx) },
      (error, stdout, stderr) => {
        const code = error ? (typeof (error as { code?: unknown }).code === "number" ? (error as unknown as { code: number }).code : 1) : 0;
        resolve({ exitCode: code, stdout: String(stdout), stderr: String(stderr) || (error && !code ? error.message : "") });
      });
    child.stdin?.end();
  });
}
