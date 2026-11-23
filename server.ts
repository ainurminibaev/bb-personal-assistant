import type { BbPluginApi } from "@get-bb/plugin-sdk";
import policy from "./.bb/AGENTS.md" with { type: "text" };
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { refreshGuardCopies } from "./guard-refresh";

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
      ? { instructions: policy, skills: ["dispatch", "integrations", "task-tracking", "agent-mail"], tools: [] }
      // Every conversation keeps the tasks given in it and can get the agent's own mail: groups and people's chats too.
      : { skills: ["task-tracking", "agent-mail"], tools: [] }
  );

  // The built plugin runs from dist/; the guard ships next to it in the package root.
  const here = dirname(fileURLToPath(import.meta.url));
  const guard = [join(here, "hooks", "guest-thread-guard.py"), join(here, "..", "hooks", "guest-thread-guard.py")]
    .find(path => existsSync(path)) ?? join(here, "hooks", "guest-thread-guard.py");
  void Promise.resolve().then(() => bb.sdk.projects.list({})).then(projects => {
    const roots = projects.flatMap(p => (p as { sources?: { path?: string }[] }).sources ?? []).flatMap(s => s.path ? [s.path] : []);
    refreshGuardCopies(guard, roots);
  }).catch(() => { /* an unreadable workspace keeps its copy; setup.py refreshes it too */ });
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
