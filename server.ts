import type { BbPluginApi } from "@get-bb/plugin-sdk";
import policy from "./.bb/AGENTS.md" with { type: "text" };
import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { pickOwnerReply, relayText } from "./owner-relay";

export default async function personalAssistant(bb: BbPluginApi) {
  const settings = bb.settings.define({
    projectId: { type: "string", label: "Personal assistant project ID", default: "" },
    threadId: { type: "string", label: "Personal coordinator thread ID", default: "" },
  });
  let binding = await settings.get();
  settings.onChange(next => { binding = next; });
  const matches = (project?: string, thread?: string) =>
    Boolean(binding.projectId && binding.threadId &&
      binding.projectId === project && binding.threadId === thread);

  bb.agents.configure(({project, thread}) =>
    matches(project.id, thread.id)
      ? { instructions: policy, skills: ["dispatch", "integrations"], tools: ["relay_owner_message"] }
      // The relay tool checks the owner's conversation itself, by the guest thread guard's rule.
      : { skills: [], tools: ["relay_owner_message"] }
  );

  // P20: pass the owner's own reply from this thread's BB journal to a worker thread.
  const db = bb.storage.database();
  const relays = () => { db.exec("CREATE TABLE IF NOT EXISTS relays (key TEXT PRIMARY KEY, at INTEGER NOT NULL)"); return db; };
  const bbDataDir = () => {
    const file = (db.prepare("PRAGMA database_list").all() as { name: string; file: string }[]).find(r => r.name === "main")?.file ?? "";
    return dirname(dirname(dirname(file))); // <dataDir>/plugins/<id>/data.db
  };
  // The built plugin runs from dist/; the guard ships next to it in the package root.
  const here = dirname(fileURLToPath(import.meta.url));
  const guard = [join(here, "hooks", "guest-thread-guard.py"), join(here, "..", "hooks", "guest-thread-guard.py")]
    .find(path => existsSync(path)) ?? join(here, "hooks", "guest-thread-guard.py");
  const isOwnerThread = (threadId: string) => new Promise<boolean>(resolve =>
    execFile("python3", ["-I", guard, "--thread", threadId, bbDataDir()], { timeout: 20_000 },
      (error, stdout) => resolve(!error && stdout.trim() === "owner")));
  bb.agents.registerTool({
    name: "relay_owner_message",
    description: "Pass the owner's own reply from this conversation, word for word from BB's journal, to a worker thread that needs " +
      "the owner's permission (publish, release, delete, send on the owner's behalf). Add, in your words, the one action and its object. " +
      "By default the owner's latest reply is passed; a short answer goes with your message it answered. You cannot write or edit the owner's words. " +
      "If the action or object does not follow unambiguously from the owner's words, ask the owner instead.",
    parameters: {
      type: "object", additionalProperties: false, required: ["threadId", "action", "object"],
      properties: {
        threadId: { type: "string", description: "The worker thread that should receive the owner's words." },
        action: { type: "string", description: "One action the owner asked for, in your words." },
        object: { type: "string", description: "What the action applies to." },
        sequence: { type: "integer", description: "A specific earlier owner reply of this thread; default: the latest." },
      },
    },
    async execute(raw, { threadId, signal }) {
      const args = raw as { threadId?: unknown; action?: unknown; object?: unknown; sequence?: unknown };
      if (typeof args.threadId !== "string" || !/^thr_[a-z0-9]{6,40}$/.test(args.threadId) ||
          typeof args.action !== "string" || args.action.length < 3 || args.action.length > 200 ||
          typeof args.object !== "string" || args.object.length < 2 || args.object.length > 200 ||
          (args.sequence !== undefined && !Number.isSafeInteger(args.sequence)))
        throw Error("invalid_arguments");
      if (args.threadId === threadId) throw Error("relay_to_another_thread");
      if (!await isOwnerThread(threadId)) throw Error("relay_only_from_owner_conversation");
      const events = await bb.sdk.threads.events.list({ threadId, order: "desc", limit: "300", signal,
        types: ["client/turn/requested", "item/completed"] }) as any[];
      const reply = pickOwnerReply(events, Date.now(), args.sequence as number | undefined);
      if ("error" in reply) throw Error(reply.error);
      const key = `${threadId}:${reply.sequence}:${args.threadId}`;
      if (relays().prepare("SELECT 1 FROM relays WHERE key=?").get(key)) throw Error("already_relayed_to_this_thread");
      const target = await bb.sdk.threads.get({ threadId: args.threadId, signal });
      if (!target?.id || target.archivedAt || target.deletedAt) throw Error("target_thread_unavailable");
      await bb.sdk.threads.send({ threadId: args.threadId, mode: "queue-if-active",
        input: [{ type: "text", text: relayText(reply, threadId, args.action, args.object), mentions: [] }] });
      relays().prepare("INSERT INTO relays(key,at) VALUES(?,?)").run(key, Date.now());
      return JSON.stringify({ relayed: true, threadId: args.threadId, ownerReply: reply.sequence });
    },
  });

  bb.cli.register({
    name: "personal-assistant",
    summary: "Check the coordinator and its project tasks without starting a model.",
    commands: [
      { name: "doctor", usage: "bb personal-assistant doctor", summary: "Check the configured coordinator thread and project." },
      { name: "work", usage: "bb personal-assistant work", summary: "List open child tasks from the personal coordinator." },
    ],
    async run(argv, ctx) {
      if (argv.length !== 1 || !["doctor", "work"].includes(argv[0]))
        return { exitCode: 2, stderr: "Usage: bb personal-assistant doctor | work" };
      if (!binding.projectId || !binding.threadId)
        return { exitCode: 1, stderr: "Set projectId and threadId in the plugin settings." };
      if (argv[0] === "doctor") {
        const rows = await bb.sdk.threads.list({
          threadIds: [binding.threadId], archived: false, includeHidden: true, limit: 1, signal: ctx.signal,
        });
        if (!rows.some(row => matches(row.projectId, row.id)))
          return { exitCode: 1, stderr: "The configured project has no active coordinator with this thread ID." };
        return { exitCode: 0, stdout: "Coordinator and project verified." };
      }
      if (!matches(ctx.projectId, ctx.threadId))
        return { exitCode: 1, stderr: "Open this command in the registered coordinator thread." };
      const tasks = await bb.sdk.threads.list({
        parentThreadId: binding.threadId, archived: false, includeHidden: false, limit: 100, signal: ctx.signal,
      });
      return { exitCode: 0, stdout: tasks.map(t => `${t.status}\t${t.title || t.id}`).join("\n") ||
        "No open child threads." };
    },
  });
}
