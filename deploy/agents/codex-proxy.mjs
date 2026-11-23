#!/usr/bin/env node
// Запуск codex app-server агента из обёртки runner (под учёткой BB).
//   codex-proxy.mjs <учётка агента> [аргументы codex]
// Протокол между BB и codex — строки JSON. Всё проходит без изменений, кроме одного сообщения BB:
// «skills/extraRoots/set» со списком папок навыков BB. Эти папки лежат во временных каталогах учётки BB
// и закрыты от агента, поэтому они копируются в ~/.bb-runtime/skills агента и пути подменяются.
// Ход идёт под учёткой агента через sudo (agent-exec); сигналы и код выхода передаются как есть.
import { spawn, execFileSync } from "node:child_process";
import { cpSync, mkdirSync, rmSync, chmodSync, readdirSync, statSync } from "node:fs";
import { createHash } from "node:crypto";
import { basename, join } from "node:path";
import { createInterface } from "node:readline";

const agent = process.argv[2];
const args = process.argv.slice(3);
const home = execFileSync("getent", ["passwd", agent]).toString().split(":")[5];
const runtime = join(home, ".bb-runtime", "skills");

function groupWritable(path) {
  try {
    const st = statSync(path);
    chmodSync(path, (st.mode & 0o7777) | (st.isDirectory() ? 0o2070 : 0o060));
    if (st.isDirectory()) for (const e of readdirSync(path)) groupWritable(join(path, e));
  } catch { /* недоступное пропускаем: агент просто не увидит этот файл */ }
}
/** Папка навыков BB → копия у агента (по хешу пути, повторная копия перезаписывает). */
function share(root) {
  const dst = join(runtime, createHash("sha256").update(root).digest("hex").slice(0, 12) + "-" + basename(root));
  rmSync(dst, {recursive: true, force: true});
  mkdirSync(dst, {recursive: true});
  cpSync(root, dst, {recursive: true, dereference: true});
  groupWritable(dst);
  return dst;
}

const child = spawn("sudo", ["-n", "-u", agent,
  "--preserve-env=BB_THREAD_ID,BB_PROJECT_ID,BB_ENVIRONMENT_ID,BB_THREAD_STORAGE,BB_APP_SURFACE",
  "/usr/local/lib/bb-agents/agent-exec", "codex", ...args], {stdio: ["pipe", "inherit", "inherit"]});
for (const sig of ["SIGTERM", "SIGINT", "SIGHUP"]) process.on(sig, () => child.kill(sig));
child.on("exit", (code, signal) => { if (signal) process.kill(process.pid, signal); else process.exit(code ?? 1); });
child.stdin.on("error", () => {});

const lines = createInterface({input: process.stdin, crlfDelay: Infinity});
lines.on("line", line => {
  let out = line;
  if (line.includes("skills/extraRoots/set")) {
    try {
      const msg = JSON.parse(line);
      if (msg.method === "skills/extraRoots/set" && Array.isArray(msg.params?.extraRoots)) {
        msg.params.extraRoots = msg.params.extraRoots.map(r => {
          if (typeof r === "string") return share(r);
          if (r && typeof r.path === "string") return {...r, path: share(r.path)};
          return r;
        });
        out = JSON.stringify(msg);
      }
    } catch { /* не JSON — передаём как есть */ }
  }
  child.stdin.write(out + "\n");
});
lines.on("close", () => child.stdin.end());
