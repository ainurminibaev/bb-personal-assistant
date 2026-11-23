import type { BbPluginApi } from "@get-bb/plugin-sdk";
import policy from "./.bb/AGENTS.md" with { type: "text" };
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { refreshGuardCopies } from "./guard-refresh";
import { deliverScheduleAlerts } from "./schedule-alerts";
import { ownerContact } from "./owner-contact";

export default async function personalAssistant(bb: BbPluginApi) {
  const settings = bb.settings.define({
    projectId: { type: "string", label: "Personal assistant project ID", default: "" },
    threadId: { type: "string", label: "Personal coordinator thread ID", default: "" },
    ownerContact: { type: "boolean", label: "Tell every thread of this BB how to reach the owner through the assistant (assistant installation only)", default: true },
  });
  let binding = await settings.get();
  settings.onChange(next => { binding = next; });
  const matches = (project?: string, thread?: string) =>
    Boolean(binding.projectId && binding.threadId &&
      binding.projectId === project && binding.threadId === thread);

  // An assistant installation tells every thread how to reach the owner; an employee and the agents' BB do not.
  const contact = ownerContact(bb, () => binding.ownerContact !== false);
  bb.agents.contributeInstructions(({threadId}) => contact.instructions(threadId));

  // The credentials store (С37): its skill only where the installer put vallis-secret on the PATH of this BB.
  // Checked at every start of a thread: the installer may put vallis-secret after the plugin loaded.
  const storeSkills = () => (process.env.PATH ?? "").split(":").some(dir => dir && existsSync(join(dir, "vallis-secret")))
    ? ["secrets-store"] : [];
  bb.agents.configure(({project, thread}) =>
    matches(project.id, thread.id)
      ? { instructions: policy, skills: ["dispatch", "integrations", "task-tracking", "agent-mail", ...storeSkills()], tools: [] }
      // Every conversation keeps the tasks given in it and can get the agent's own mail: groups and people's chats too.
      : { skills: ["task-tracking", "agent-mail", ...storeSkills()], tools: [] }
  );

  // The built plugin runs from dist/; the guard ships next to it in the package root.
  const here = dirname(fileURLToPath(import.meta.url));
  const guard = [join(here, "hooks", "guest-thread-guard.py"), join(here, "..", "hooks", "guest-thread-guard.py")]
    .find(path => existsSync(path)) ?? join(here, "hooks", "guest-thread-guard.py");
  void Promise.resolve().then(() => bb.sdk.projects.list({})).then(projects => {
    const roots = projects.flatMap(p => (p as { sources?: { path?: string }[] }).sources ?? []).flatMap(s => s.path ? [s.path] : []);
    refreshGuardCopies(guard, roots);
  }).catch(() => { /* an unreadable workspace keeps its copy; setup.py refreshes it too */ });
  // Schedules in guest conversations are not capped; a burst past the guard's thresholds reaches the owner once.
  bb.background.schedule("guest-schedule-alerts", "* * * * *", async () => {
    const projects = await bb.sdk.projects.list({});
    const roots = projects.flatMap(p => (p as { sources?: { path?: string }[] }).sources ?? []).flatMap(s => s.path ? [s.path] : []);
    await deliverScheduleAlerts(bb, roots);
  });
  bb.cli.register({
    name: "personal-assistant",
    summary: "Check the coordinator and its project tasks without starting a model.",
    commands: [
      { name: "doctor", usage: "bb personal-assistant doctor", summary: "Check the configured coordinator thread and project." },
      { name: "work", usage: "bb personal-assistant work", summary: "List open child tasks from the personal coordinator." },
      { name: "owner-thread", usage: "bb personal-assistant owner-thread", summary: "The assistant conversation that reaches the owner in Telegram now." },
      { name: "alerts", usage: "bb personal-assistant alerts", summary: "Warnings about many schedules in guest conversations sent to the owner." },
    ],
    async run(argv, ctx) {
      if (argv.length !== 1 || !["doctor", "work", "owner-thread"].includes(argv[0]))
        return { exitCode: 2, stderr: "Usage: bb personal-assistant doctor | work | owner-thread" };
      if (argv[0] === "alerts") {
        const sent = (await bb.storage.kv.get<{ at: number; conversation: string; via: string; text: string }[]>("schedule-alerts:sent")) ?? [];
        return { exitCode: 0, stdout: sent.map(e => `${new Date(e.at).toISOString()} ${e.via}: ${e.text}`).join("\n") || "no warnings sent" };
      }
      if (argv[0] === "owner-thread") {
        const id = await contact.refresh();
        return id ? { exitCode: 0, stdout: id }
          : { exitCode: 1, stderr: "No assistant conversation reaches the owner here: not an assistant installation, "
              + "the bridge is off, the private chat is not paired, or ownerContact is off." };
      }
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
