import type { BbPluginApi } from "@get-bb/plugin-sdk";
import { z } from "zod";
import policy from "./.bb/AGENTS.md" with { type: "text" };
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { COMMITMENTS_USAGE, commitmentsScript, runCommitments } from "./commitments-command";
import { refreshGuardCopies } from "./guard-refresh";
import { deliverScheduleAlerts } from "./schedule-alerts";
import { ownerContact } from "./owner-contact";
import { toolsReport } from "./tools-status";
import { turnRules } from "./turn-rules";
import { registerServerSpeech } from "./server-speech";

/** For the bridge: the rules in force for a conversation's project and their fingerprint (0.4.4). */
// The contract object as defineRpcContract would return it (an identity helper): the plugin has no runtime SDK import.
export const rpcContract = ({
  turnRules: {
    input: z.object({ projectId: z.string().min(1) }).strict(),
    output: z.object({ version: z.string(), text: z.string() }).nullable(),
  },
});


export default async function personalAssistant(bb: BbPluginApi) {
  // Voice through the server's speech service (Codex first, local models as the fallback).
  registerServerSpeech(bb);
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
  // Checked at every start of a thread: the installer may put vallis-secret after the plugin loaded.
  const storeSkills = () => (process.env.PATH ?? "").split(":").some(dir => dir && existsSync(join(dir, "vallis-secret")))
    ? ["secrets-store"] : [];
  // Commitments between people and the daily digest (С91, С92): only the owner's conversation reads the groups.
  // A group's or a person's conversation gets only «что я обещал?» (my-commitments): its own chat, the person's own promises.
  const commitmentSkills = (owner = true) => (process.env.PATH ?? "").split(":").some(dir => dir && existsSync(join(dir, "vallis-commitments")))
    ? [owner ? "commitments" : "my-commitments"] : [];
  // The credentials store (С37, С43): an agent answers about its own accesses from the store's list, never from memory.
  const accessLine = () => storeSkills().length
    ? "О своих доступах (учётки, входы, ключи) отвечай по списку хранилища: `vallis-secret list` — названия и назначение, без значений."
    : null;
  // Rules for every conversation of both kinds (С43, С44, С46, С49, С61, С64, С69, С77): short, the details live in the skills.
  const common = [
    "О своих возможностях, подключениях и памяти отвечай по действующим настройкам, а не по старой переписке: `bb personal-assistant tools` (в гостевом разговоре — `--guest`) и справка места; утвердительно и с действием для человека (С43).",
    "Поручение владельца выполняешь там, где оно дано, и не просишь повторить его в другом месте. Ответ, отчёт и напоминание — туда, где дана просьба, если явно не сказано иное; сам место не меняешь (С44). Просьба в группе «напиши/отпиши Олегу …» — пишешь Олегу в этой же группе с отметкой, даже если личка с ним открыта; в личку — только если так сказано («напиши ему в личку»). Просьба в личке владельца «напиши Олегу» — это личка Олега; закрыта — сразу скажи и предложи группу. Место названо («в чате поддержки») — туда. Не владелец в своей личке просит передать что-то третьему — только в общий чат, где есть оба, с подписью «по просьбе …»; нельзя — честно откажи и предложи написать в общем чате; владельца об этом не спрашивай (только если человек сам просит о доступе).",
    "На месте отвечаешь, если верно всё сразу: ответ — одно сообщение без файла; хватает разговора и одного-двух обращений к инструментам; до пары минут; это не продолжение дела, у которого уже есть подтред. Иначе — в подтред: нужно читать код, документы, длинную историю или несколько источников; будет файл или несколько шагов с проверкой; дольше пары минут или с ожиданием людей; в чате идут другие разговоры; продолжение дела с подтредом — в тот же подтред. Решаешь сам, не спрашиваешь «уводить ли»; человеку — одна живая строка, что взял и когда вернёшься; итог подтреда, запущенного по просьбе человека, — в тот же разговор одним сообщением. По ходу дела пишешь только туда, где дана просьба; владельцу в личку из чужого разговора — только итог или вопрос, который нужен именно от него, не промежуточное (С49, С69).",
    "Не хватает права или настройки (бот не может создать тему, нет доступа в репозиторий, трекер или сервис) — сам идёшь к владельцу с готовой просьбой, не через других и не перекладывая работу на него: одной строкой — какое право, где и зачем, и точный путь, куда нажать («Чтобы я сам создавал темы, включите мне в этой группе „Управление темами“: группа → Администраторы → я»), с кнопками (`bb telegram-projects send --person owner --ask`). После ответа сам проверь, что право появилось, и продолжай дело; не появилось — скажи, что именно ещё не так (С77).",
    "Сообщение читается за пять секунд: сверху то, что нужно от человека — вопрос или решение, выделено; ниже две-три строки сути; подробности — по просьбе или файлом. Живым языком, не отчётом и не сплошным абзацем. Задача, документ, страница названы по смыслу, и название — ссылка; голых адресов отдельной строкой нет, номер задачи — только внутри ссылки (С64).",
    "Ход дольше пары секунд начинаешь первой строкой ответа по сути просьбы, человеческими словами: что сделаешь и когда вернёшься («уточню у Валлиса, можно ли выпустить раньше, и вернусь»); не пересказываешь свои действия и инструменты — эта строка видна человеку, пока ты работаешь (С46).",
    "Расписание ставишь только под названную надобность и в том же сообщении говоришь, что поставил; повторяющееся — одно с повтором и сроком окончания; название человеческими словами; поручение закрыто — его расписания снимаешь (С61).",
  ].join("\n");
  // Project folders, for the rules read from files (refreshed every minute; contributeInstructions must stay synchronous).
  const folders = new Map<string, string>();
  const refreshFolders = async () => {
    const projects = await bb.sdk.projects.list({});
    for (const p of projects as { id: string; sources?: { path?: string; isDefault?: boolean }[] }[]) {
      const src = p.sources ?? [];
      const path = (src.find(s => s.isDefault) ?? src[0])?.path;
      if (path) folders.set(p.id, path);
    }
  };
  void refreshFolders().catch(() => {});
  // A new session gets the organization's rules as they are now (a running session keeps what it started with:
  // the bridge brings later edits on every Telegram turn).
  bb.agents.contributeInstructions(({threadId, projectId}) =>
    [contact.instructions(threadId), accessLine(), common, turnRules(folders.get(projectId), "")?.text ?? null]
      .filter(Boolean).join("\n\n") || null);
  bb.rpc.register(rpcContract, {
    turnRules: async ({ projectId }) => {
      if (!folders.has(projectId)) await refreshFolders().catch(() => {});
      return turnRules(folders.get(projectId), [accessLine(), common].filter(Boolean).join("\n"));
    },
  });

  // The credentials store (С37): its skill only where the installer put vallis-secret on the PATH of this BB.
  bb.agents.configure(({project, thread}) =>
    matches(project.id, thread.id)
      ? { instructions: policy, skills: ["dispatch", "integrations", "task-tracking", "tell-person", "agent-mail", "voice-breakdown", ...storeSkills(), ...commitmentSkills()], tools: [] }
      // Every conversation keeps the tasks given in it, passes words to people (С90) and can get the agent's own mail: groups and people's chats too.
      : { skills: ["task-tracking", "tell-person", "agent-mail", "voice-breakdown", ...storeSkills(), ...commitmentSkills(false)], tools: [] }
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
    await refreshFolders().catch(() => {});
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
      { name: "commitments", usage: COMMITMENTS_USAGE, summary: "Commitments between people and the daily digests: the work of vallis-commitments on the server's side, in the installation's folder." },
      { name: "tools", usage: "bb personal-assistant tools [--guest]", summary: "What this agent can use: tools and accesses from the installer's last check, in words, without values." },
    ],
    async run(argv, ctx) {
      if (argv[0] === "commitments") {
        // One list per installation: in the folder of the coordinator project, else in this plugin's own data folder.
        if (binding.projectId && !folders.has(binding.projectId)) await refreshFolders().catch(() => {});
        const own = (() => {
          try {
            const main = (bb.storage.database().prepare("pragma database_list").all() as { name: string; file: string }[])
              .find(r => r.name === "main")?.file;
            return main ? dirname(main) : undefined;
          } catch { return undefined; }
        })();
        const root = (binding.projectId && folders.get(binding.projectId)) || own;
        return runCommitments(commitmentsScript(dirname(fileURLToPath(import.meta.url))), root, argv.slice(1), ctx);
      }
      if (argv[0] === "tools" && argv.slice(1).every(a => a === "--guest")) {
        const project = ctx.projectId ? await bb.sdk.projects.get({ projectId: ctx.projectId }).catch(() => undefined) : undefined;
        const sources = (project as { sources?: { path?: string; isDefault?: boolean }[] } | undefined)?.sources ?? [];
        const folder = (sources.find(s => s.isDefault) ?? sources[0])?.path;
        return toolsReport(folder, argv.includes("--guest"));
      }
      if (argv.length !== 1 || !["doctor", "work", "owner-thread", "alerts"].includes(argv[0]))
        return { exitCode: 2, stderr: "Usage: bb personal-assistant doctor | work | owner-thread | alerts | tools [--guest]" };
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
