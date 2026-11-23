#!/usr/bin/env node
// Попросить штатный Codex обновить вход: app-server, запрос account/read с refreshToken=true.
// Обновление выполняет сам Codex (его код, его блокировка в CODEX_HOME); здесь только вызов.
//   codex-refresh.mjs <путь к codex>
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";

const child = spawn(process.argv[2] || "codex", ["app-server"], {stdio: ["pipe", "pipe", "ignore"]});
const send = msg => child.stdin.write(JSON.stringify(msg) + "\n");
const timer = setTimeout(() => { console.error("Codex не ответил за 60 с"); child.kill(); process.exit(75); }, 60_000);
createInterface({input: child.stdout}).on("line", line => {
  let msg; try { msg = JSON.parse(line); } catch { return; }
  if (msg.id === 1) {
    send({jsonrpc: "2.0", method: "initialized"});
    send({jsonrpc: "2.0", id: 2, method: "account/read", params: {refreshToken: true}});
  } else if (msg.id === 2) {
    clearTimeout(timer); child.stdin.end(); child.kill();
    if (msg.error) { console.error("Codex: " + (msg.error.message ?? "ошибка обновления входа")); process.exit(1); }
    process.exit(0);
  }
});
send({jsonrpc: "2.0", id: 1, method: "initialize", params: {clientInfo: {name: "bb-agents-login", title: null, version: "1"}}});
