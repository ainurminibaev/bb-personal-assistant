import type { BbPluginApi } from "@get-bb/plugin-sdk";
import { spawn } from "node:child_process";
import { realpath as fsRealpath } from "node:fs/promises";
import { z } from "zod";
import { defineRpcContract } from "@get-bb/plugin-sdk";
import { decide, type Agent } from "./policy";

/**
 * Агенты под своими учётками Linux на общем сервере.
 * 1) Инструмент project_command: команды в пределах своих проектов (правила — policy.ts).
 * 2) Ход агента запускается под его учёткой: для тредов его проектов подставляется обёртка запуска,
 *    которую кладёт установщик (/usr/local/lib/bb-agents/<учётка>/{claude,codex,bin}).
 */
export default function plugin(bb: BbPluginApi) {
  const settings = bb.settings.define({
    agents: { type: "string", label: "Агенты: JSON [{user, home, projects, telegram: {owner, chats}}]", default: "[]" },
    bbCommand: { type: "string", label: "Команда bb на сервере", default: "bb" },
    serverUrl: { type: "string", label: "Адрес сервера для команды bb", default: process.env.BB_SERVER_URL ?? "" },
    runnerRoot: { type: "string", label: "Папка обёрток запуска", default: "/usr/local/lib/bb-agents" },
  });

  const parseAgents = (raw: string): Agent[] => {
    try {
      const v = JSON.parse(raw);
      return Array.isArray(v) ? v.filter(a => a && typeof a.user === "string" && typeof a.home === "string")
        .map(a => ({user: a.user, home: a.home, projects: Array.isArray(a.projects) ? a.projects.map(String) : [],
          telegram: a.telegram && typeof a.telegram === "object" ? {owner: Number(a.telegram.owner) || undefined,
            chats: Array.isArray(a.telegram.chats) ? a.telegram.chats.map(Number) : []} : undefined})) : [];
    } catch { return []; }
  };
  /** Агент проекта: из настройки или из проектов, созданных им самим. */
  async function agentOfProject(projectId: string): Promise<Agent | undefined> {
    const agents = parseAgents((await settings.get()).agents);
    const owner = await bb.storage.kv.get<string>("owned:" + projectId);
    const found = agents.find(a => a.projects.includes(projectId) || a.user === owner);
    if (!found) return undefined;
    const owned: string[] = [];
    for (const key of await bb.storage.kv.list("owned:"))
      if (await bb.storage.kv.get<string>(key) === found.user) owned.push(key.slice("owned:".length));
    return {...found, projects: [...new Set([...found.projects, ...owned])]};
  }
  async function threadRow(threadId: string) {
    const r = await bb.sdk.threads.get({threadId}).catch(() => null);
    return r ? ("thread" in r ? (r as {thread: {projectId: string}}).thread : r as unknown as {projectId: string}) : null;
  }

  function run(cmd: string, argv: string[], env: Record<string, string>, input: string | undefined, signal?: AbortSignal) {
    return new Promise<{code: number; out: string; err: string}>(resolve => {
      const child = spawn(cmd, argv, {env: {...process.env, ...env}, signal, stdio: ["pipe", "pipe", "pipe"]});
      let out = "", err = "";
      child.stdout.on("data", d => { if (out.length < 200_000) out += d; });
      child.stderr.on("data", d => { if (err.length < 50_000) err += d; });
      child.on("error", e => resolve({code: 127, out, err: String(e)}));
      child.on("close", code => resolve({code: code ?? 1, out, err}));
      child.stdin.end(input ?? "");
    });
  }

  bb.agents.registerTool({
    name: "project_command",
    description:
      "Run a workspace command within your own projects: start and message your sub-threads, read their results, " +
      "manage your schedules, send to your own Telegram chats and groups and read their archive, create projects in your " +
      "home folder, transcribe your own voice file. Pass the command as an argument list, e.g. " +
      "[\"thread\",\"spawn\",\"--prompt\",\"…\"], [\"automation\",\"create\",\"--name\",\"…\",\"--cron\",\"0 9 * * *\",\"--timezone\",\"Europe/Moscow\",\"--prompt\",\"…\"], " +
      "[\"telegram-projects\",\"send\",\"--text\",\"…\"]. Multi-line text goes in `input` with --message-file - or --prompt-file -. " +
      "The shell command is not available to you; use this tool instead. Anything outside your projects is refused with the reason.",
    parameters: z.object({
      args: z.array(z.string().max(20_000)).min(1).max(60),
      input: z.string().max(200_000).optional(),
    }),
    async execute({args, input}, {threadId, signal}) {
      const caller = await threadRow(threadId);
      if (!caller) return JSON.stringify({ok: false, reason: "Тред вызова не найден."});
      const agent = await agentOfProject(caller.projectId);
      if (!agent) return JSON.stringify({ok: false, reason: "Этот разговор не принадлежит ни одному агенту — инструмент недоступен."});
      const d = await decide(args, {
        agent, caller: {threadId, projectId: caller.projectId},
        threadProject: async id => (await threadRow(id))?.projectId ?? null,
        realpath: async p => fsRealpath(p).catch(async () => {
          // Новый путь (ещё не создан): проверяем ближайшую существующую папку.
          const parent = p.replace(/\/[^/]*\/?$/, "") || "/";
          const real = await fsRealpath(parent).catch(() => null);
          return real ? real + p.slice(parent.length) : null;
        }),
      }, input);
      if (!d.ok) return JSON.stringify({ok: false, reason: d.reason});
      if (d.targets) return JSON.stringify({ok: true, targets: d.targets});
      const cfg = await settings.get();
      let argv = d.argv;
      if (d.record) {
        const machines = await run(cfg.bbCommand, ["machine", "list", "--json"], {BB_SERVER_URL: cfg.serverUrl}, undefined, signal);
        const host = (JSON.parse(machines.out || "[]") as {id: string; role?: string}[]).find(m => m.role === "server")
          ?? (JSON.parse(machines.out || "[]") as {id: string}[])[0];
        if (host) argv = [...argv, "--machine", host.id];
      }
      const res = await run(cfg.bbCommand, argv, {BB_SERVER_URL: cfg.serverUrl, BB_THREAD_ID: threadId,
        BB_PROJECT_ID: caller.projectId}, d.input, signal);
      if (d.record && res.code === 0) {
        const created = JSON.parse(res.out) as {id?: string; project?: {id: string}};
        const id = created.project?.id ?? created.id;
        if (id) await bb.storage.kv.set("owned:" + id, agent.user);
      }
      let out = res.out;
      if (d.filterProjects && res.code === 0) {
        const mineIds = new Set((await agentOfProject(caller.projectId))?.projects ?? []);
        out = JSON.stringify((JSON.parse(res.out) as {id: string}[]).filter(p => mineIds.has(p.id)), null, 2);
      }
      return JSON.stringify({ok: res.code === 0, exitCode: res.code, stdout: out, stderr: res.err.slice(0, 4000)});
    },
  });

  // Проверка решения без модели: администратор BB смотрит, что инструмент сделал бы для данного треда.
  // Агентам недоступно — до API BB у них нет сети.
  const admin = defineRpcContract({
    checkCommand: {
      input: z.object({threadId: z.string().min(1), args: z.array(z.string()).min(1), input: z.string().optional()}).strict(),
      output: z.object({ok: z.boolean(), reason: z.string().optional(), argv: z.array(z.string()).optional()}),
    },
  });
  bb.rpc.register(admin, {
    checkCommand: async ({threadId, args, input}) => {
      const caller = await threadRow(threadId);
      if (!caller) return {ok: false, reason: "Тред вызова не найден."};
      const agent = await agentOfProject(caller.projectId);
      if (!agent) return {ok: false, reason: "Этот разговор не принадлежит ни одному агенту — инструмент недоступен."};
      const d = await decide(args, {agent, caller: {threadId, projectId: caller.projectId},
        threadProject: async id => (await threadRow(id))?.projectId ?? null,
        realpath: async p => fsRealpath(p).catch(() => null)}, input);
      return d.ok ? {ok: true, argv: d.argv} : {ok: false, reason: d.reason};
    },
  }, {experimental_discoverable: true});

  // Ход агента — под его учёткой: обёртки запуска кладёт установщик.
  async function runnerFor(threadId: string | null | undefined) {
    if (!threadId) return undefined;
    const row = await threadRow(threadId);
    const agent = row ? await agentOfProject(row.projectId) : undefined;
    return agent ? `${(await settings.get()).runnerRoot}/${agent.user}` : undefined;
  }
  bb.providers.experimental_contributeEnv("claude-code", async context => {
    const dir = await runnerFor(context.threadId);
    return dir ? [{name: "BB_CLAUDE_CODE_EXECUTABLE", value: dir + "/claude", reason: "Ход агента под его учётной записью"}] : [];
  });
  bb.providers.experimental_contributeEnv("codex", async context => {
    const dir = await runnerFor(context.threadId);
    return dir ? [
      {name: "BB_CODEX_BRIDGE_APP_SERVER_COMMAND", value: dir + "/codex", reason: "Ход агента под его учётной записью"},
      {name: "BB_CODEX_BRIDGE_APP_SERVER_ARGS", value: JSON.stringify(["app-server"]), reason: "Ход агента под его учётной записью"},
      {name: "PATH", value: `${dir}/bin:${process.env.PATH ?? "/usr/bin:/bin"}`, reason: "Ход агента под его учётной записью"},
    ] : [];
  });
}
