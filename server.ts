import type { BbPluginApi } from "@get-bb/plugin-sdk";
import { z } from "zod";
import policy from "./.bb/AGENTS.md" with { type: "text" };
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { basename, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { COMMITMENTS_USAGE, commitmentsScript, runCommitments } from "./commitments-command";
import { refreshGuardCopies } from "./guard-refresh";
import { deliver, deliverScheduleAlerts } from "./schedule-alerts";
import { ownerContact } from "./owner-contact";
import { toolsReport } from "./tools-status";
import { codeToOwner } from "./code-to-owner";
import { CASE_USAGE, runCase } from "./case-command";
import { openCasesHint } from "./cases";
import { pauseControl } from "./pause";
import { autoCases } from "./auto-case";
import { formatRegistry, organizationOf, registry } from "./team";
import { formatUsage, usage } from "./usage";
import { organizationRoot, turnRules } from "./turn-rules";
import { registerServerSpeech } from "./server-speech";

/** For the bridge: the rules in force for a conversation's project and their fingerprint (0.4.4). */
// The contract object as defineRpcContract would return it (an identity helper): the plugin has no runtime SDK import.
export const rpcContract = ({
  turnRules: {
    input: z.object({ projectId: z.string().min(1) }).strict(),
    output: z.object({ version: z.string(), text: z.string() }).nullable(),
  },
  // С94: is this installation paused by its owner — the bridge shows no «thinking» on a message that waits.
  pauseStatus: {
    input: z.object({}).strict(),
    output: z.object({ paused: z.boolean(), since: z.number().nullable() }),
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
  // Google Drive, Docs and Sheets (С51): its skill where the installer prepared the line's login folder.
  const googleSkills = () => existsSync(join(homedir(), ".secrets", "google-workspace")) ? ["google-workspace"] : [];
  // Commitments between people and the daily digest (С91, С92): only the owner's conversation reads the groups.
  // A group's or a person's conversation gets only «что я обещал?» (my-commitments): its own chat, the person's own promises.
  const commitmentSkills = (owner = true) => (process.env.PATH ?? "").split(":").some(dir => dir && existsSync(join(dir, "vallis-commitments")))
    ? [owner ? "commitments" : "my-commitments"] : [];
  // The credentials store (С37, С43): an agent answers about its own accesses from the store's list, never from memory.
  const accessLine = () => storeSkills().length
    ? "О своих доступах (учётки, входы, ключи) отвечай по списку хранилища: `vallis-secret list` — названия и назначение, без значений."
    : null;
  // Rules for every conversation of both kinds (С43, С44, С46, С49, С56, С61, С64, С69, С77, С85, С86, С101, С103, С104, С106): short, the details live in the skills.
  const common = [
    "О своих возможностях, подключениях и памяти отвечай по действующим настройкам, а не по старой переписке: `bb personal-assistant tools` (в гостевом разговоре — `--guest`) и справка места; утвердительно и с действием для человека (С43).",
    "Поручение владельца выполняешь там, где оно дано, и не просишь повторить его в другом месте. Ответ, отчёт и напоминание — туда, где дана просьба, если явно не сказано иное; сам место не меняешь (С44). Просьба в группе «напиши/отпиши Олегу …» — пишешь Олегу в этой же группе с отметкой, даже если личка с ним открыта; в личку — только если так сказано («напиши ему в личку»). Просьба в личке владельца «напиши Олегу» — это личка Олега; закрыта — сразу скажи и предложи группу. Место названо («в чате поддержки») — туда. Не владелец в своей личке просит передать что-то третьему — только в общий чат, где есть оба, с подписью «по просьбе …»; нельзя — честно откажи и предложи написать в общем чате; владельца об этом не спрашивай (только если человек сам просит о доступе).",
    "На месте отвечаешь, если верно всё сразу: ответ — одно сообщение без файла; хватает разговора и одного-двух обращений к инструментам; до пары минут; это не продолжение дела, у которого уже есть подтред. Иначе — в подтред: нужно читать код, документы, длинную историю или несколько источников; будет файл или несколько шагов с проверкой; дольше пары минут или с ожиданием людей; в чате идут другие разговоры; продолжение дела с подтредом — в тот же подтред. Решаешь сам, не спрашиваешь «уводить ли»; человеку — одна живая строка, что взял и когда вернёшься; итог подтреда, запущенного по просьбе человека, — в тот же разговор одним сообщением. По ходу дела пишешь только туда, где дана просьба; владельцу в личку из чужого разговора — только итог или вопрос, который нужен именно от него, не промежуточное (С49, С69).",
    "Голосовое длиннее порога (мост пишет длительность: «Голосовое (4 мин 54 с): …»; порог — в `$(vallis-pdf --settings)`, по умолчанию 3 минуты), в том числе пересланное, разбираешь навыком voice-breakdown: схема картинкой и PDF с подробностями, потом действуешь по сути. Короче — только если об этом просят (С56).",
    "Владелец просит код подтверждения из твоей почты — `bb personal-assistant code-to-owner [--from <отправитель>]`: код уйдёт только ему в личку, сам ты его не видишь и никуда не пишешь; в группы и другим людям коды не отправляются (С85).",
    "Вопрос о правилах, решениях и документах своей организации (как у нас принято, кто за что отвечает, что решили, где лежит документ) — если тебе выдан поиск знаний организации, сначала ищи там (catalog_search «знания» → его query) и в ответе называй источник: документ, задачу или сообщение со ссылкой. Не нашёл — так и скажи, потом ищи другими способами (С86).",
    "Перед работой на другой машине и в начале расписания сначала посмотри состояние машин (machine_status или `bb tools machines`). Не на связи, команды не выполняет или экран спит — так и скажи и предложи, что сделать (разбудить, разблокировать, перенести); не обещай работу на этой машине (С104).",
    "Не хватает права или настройки (бот не может создать тему, нет доступа в репозиторий, трекер или сервис) — сам идёшь к владельцу с готовой просьбой, не через других и не перекладывая работу на него: одной строкой — какое право, где и зачем, и точный путь, куда нажать («Чтобы я сам создавал темы, включите мне в этой группе „Управление темами“: группа → Администраторы → я»), с кнопками (`bb telegram-projects send --person owner --ask`). После ответа сам проверь, что право появилось, и продолжай дело; не появилось — скажи, что именно ещё не так (С77).",
    "Сообщение читается за пять секунд: сверху то, что нужно от человека — вопрос или решение, выделено; ниже две-три строки сути; подробности — по просьбе или файлом. Живым языком, не отчётом и не сплошным абзацем. Задача, документ, страница названы по смыслу, и название — ссылка; голых адресов отдельной строкой нет, номер задачи — только внутри ссылки (С64).",
    "Ход дольше пары секунд начинаешь первой строкой ответа по сути просьбы, человеческими словами: что сделаешь и когда вернёшься («уточню у Валлиса, можно ли выпустить раньше, и вернусь»); не пересказываешь свои действия и инструменты — эта строка видна человеку, пока ты работаешь (С46).",
    "Одно сообщение — сам ответ: готов в этом ходе или будет через несколько минут — человек получает одно сообщение с ним, без «передал исполнителю, он ответит»; строку о передаче пишешь, только когда ответ придёт позже, и называешь срок. Говоришь от одного лица: отдельного «исполнителя» в чате нет, работу подтреда или другого разговора подаёшь как свою; передал её разговору этого же чата, и он ответит в ближайшие минуты, — сам молчишь. Отправил ответ в этот чат командой (`bb telegram-projects send`) — итог хода ровно NO_REPLY, иначе мост пошлёт второе сообщение; отвечаешь итогом хода — командой его не дублируешь (С101).",
    "Нужно дополнить своё только что отправленное сообщение (пришло дополнение к поручению, вспомнил деталь) — на него ещё не ответили: правишь его; ответили: одна короткая строка только с новым, без повтора сказанного (С101).",
    "Поправил своё сообщение, которое люди уже могли прочитать, — сообщаешь об этом ответом на исправленное и одной-двумя строками, что изменилось: сама правка в чате почти незаметна. Опечатка сразу после отправки — без второго сообщения (С106).",
    "Пишешь в чат, группу или личку первым за сегодня (и личку владельца тоже) — сам или от имени владельца — начинаешь с приветствия по времени суток в часовом поясе организации (доброе утро / добрый день / добрый вечер / доброй ночи) и в стиле переписки этого чата, не шаблоном; сегодня там уже писал — не повторяешь. Писал ли сегодня и который там час, не угадываешь: это говорит канал (справка хода, ответ команды отправки в другой чат, проверка перед отправкой от имени владельца). Ответ на обращение к тебе в идущем разговоре приветствия не требует (С103).",
    "Расписание ставишь только под названную надобность и в том же сообщении говоришь, что поставил; повторяющееся — одно с повтором и сроком окончания; название человеческими словами; поручение закрыто — его расписания снимаешь (С61).",
    "Поручение, которое ждёт (человека, файла, решения), имеет срок («итог завтра в 10:00») или передано другому, — сразу дело: `bb personal-assistant case open --title … --by … --place … --next … [--promise …]`, подтред по навыку ведения поручений, расписание шага с именем «<id дела>: <шаг>» и `case set <id> --schedule <номер>`. Срок, названный участником с правом поручать, ставишь без отдельного разрешения владельца (С8, С65).",
    "Кто в организации за что отвечает, кто ставит задачи, кто куратор и владелец — `bb personal-assistant team` (в гостевом разговоре — `--guest`); по нему решаешь, кого звать (С94).",
    "Владелец в своей личке говорит «останови», «поставь на паузу» — `bb personal-assistant pause`; «включи», «продолжай» — `bb personal-assistant resume`. Другим людям это недоступно; данные и ждущие сообщения сохраняются (С94).",
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
  // Case cards live in the installation's folder (the coordinator project of the plugin settings), not in the folder of
  // the thread's project: a group moved into a folder of its own (С76) keeps its cases; the command gives a chat only
  // its own cases, so the group never reads the files itself.
  // Without a coordinator project in the settings (a stand, an employee installed by setup.py) the cases live in this
  // plugin's own data folder (<BB data>/plugins/personal-assistant/, next to its data.db): it exists on every
  // installation from the first load, survives updates and goes into the backup of BB's data.
  const pluginFolder = (() => {
    try {
      const rows = bb.storage.database().prepare("pragma database_list").all() as { name: string; file: string }[];
      const main = rows.find(r => r.name === "main")?.file;
      return main ? dirname(main) : undefined;
    } catch { return undefined; }
  })();
  // The installation's own folder inside the organization's copy (employees/<name>): the coordinator project of the
  // settings when it is there, else the one project of this BB that lives in an organization's copy.
  const installationFolder = () => {
    const own = binding.projectId ? folders.get(binding.projectId) : undefined;
    if (own && organizationRoot(own)) return own;
    const inside = [...new Set(folders.values())].filter(f => organizationRoot(f) && basename(dirname(f)) === "employees");
    return inside.length === 1 ? inside[0] : undefined;
  };
  const casesRoot = (projectId?: string) =>
    (binding.projectId && folders.get(binding.projectId)) || pluginFolder || (projectId ? folders.get(projectId) : undefined);
  // С94: the owner pauses this employee; while paused every turn but the owner's private chat waits in BB's queue.
  const pause = pauseControl(bb);
  bb.experimental_hooks?.on("message.dispatch", ctx => pause.decide(ctx));
  // A new session gets the organization's rules as they are now (a running session keeps what it started with:
  // the bridge brings later edits on every Telegram turn).
  bb.agents.contributeInstructions(({threadId, projectId}) =>
    [contact.instructions(threadId), accessLine(), common, turnRules(folders.get(projectId), "")?.text ?? null,
      openCasesHint(casesRoot(projectId))]
      .filter(Boolean).join("\n\n") || null);
  bb.rpc.register(rpcContract, {
    turnRules: async ({ projectId }) => {
      if (!folders.has(projectId)) await refreshFolders().catch(() => {});
      return turnRules(folders.get(projectId), [accessLine(), common].filter(Boolean).join("\n"), installationFolder());
    },
    pauseStatus: async () => {
      await pause.ready;
      return { paused: pause.isPaused(), since: pause.isPaused() ? pause.since() : null };
    },
  });

  // The credentials store (С37): its skill only where the installer put vallis-secret on the PATH of this BB.
  bb.agents.configure(({project, thread}) =>
    matches(project.id, thread.id)
      ? { instructions: policy, skills: ["dispatch", "integrations", "task-tracking", "tell-person", "agent-mail", "voice-breakdown", ...storeSkills(), ...googleSkills(), ...commitmentSkills()], tools: [] }
      // Every conversation keeps the tasks given in it, passes words to people (С90) and can get the agent's own mail: groups and people's chats too.
      : { skills: ["task-tracking", "tell-person", "agent-mail", "voice-breakdown", ...storeSkills(), ...googleSkills(), ...commitmentSkills(false)], tools: [] }
  );

  // The built plugin runs from dist/; the guard ships next to it in the package root.
  const here = dirname(fileURLToPath(import.meta.url));
  const guard = [join(here, "hooks", "guest-thread-guard.py"), join(here, "..", "hooks", "guest-thread-guard.py")]
    .find(path => existsSync(path)) ?? join(here, "hooks", "guest-thread-guard.py");
  void Promise.resolve().then(() => bb.sdk.projects.list({})).then(async projects => {
    const roots = projects.flatMap(p => (p as { sources?: { path?: string }[] }).sources ?? []).flatMap(s => s.path ? [s.path] : []);
    // A conversation may run in an environment folder of its own (a checkout or worktree next to the source): its copy
    // of the guard is refreshed too, or an old copy keeps refusing what the new release allows (stand, 25.11).
    for (const p of projects as { id: string }[]) {
      const envs = await Promise.resolve(bb.sdk.environments?.list?.({ projectId: p.id, limit: 100 })).catch(() => []) ?? [];
      for (const e of envs as { path?: string | null }[]) if (e.path) roots.push(e.path);
    }
    refreshGuardCopies(guard, roots);
  }).catch(() => { /* an unreadable workspace keeps its copy; setup.py refreshes it too */ });
  // Schedules in guest conversations are not capped; a burst past the guard's thresholds reaches the owner once.
  bb.background.schedule("guest-schedule-alerts", "* * * * *", async () => {
    // С94: after «включи» — one line to the owner about schedule runs skipped because they waited past their period.
    await pause.report(text => deliver(bb, text)).catch(() => null);
    // С65: a schedule made from a Telegram chat becomes a case by itself, without the agent's will.
    await autoCases(bb, casesRoot()).catch(() => []);
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
      { name: "code-to-owner", usage: "bb personal-assistant code-to-owner [--from TEXT] [--minutes N]", summary: "A confirmation code from your own mailbox to the owner only, in his private chat (С85); you never see the code." },
      { name: "team", usage: "bb personal-assistant team [--guest]", summary: "Who does what in the organization: position, duties, who gives tasks, curator, owner, state (from the passports)." },
      { name: "usage", usage: "bb personal-assistant usage [--today] [--json]", summary: "What this employee spent today and in 7 days: turns, tokens, the share of the model subscription window." },
      { name: "pause", usage: "bb personal-assistant pause [--reason TEXT] | resume | pause-status", summary: "The owner stops this employee and starts it again; data and waiting messages are kept." },
      { name: "case", usage: CASE_USAGE, summary: "Case cards of tracked tasks on disk: open, set, close, show, list, find, check (this chat's cases)." },
      { name: "commitments", usage: COMMITMENTS_USAGE, summary: "Commitments between people and the daily digests: the work of vallis-commitments on the server's side, in the installation's folder." },
      { name: "tools", usage: "bb personal-assistant tools [--guest]", summary: "What this agent can use: tools and accesses from the installer's last check, in words, without values." },
    ],
    async run(argv, ctx) {
      if (argv[0] === "commitments") {
        // One list per installation (С91, С92): the coordinator project's folder, else this plugin's own data folder.
        if (binding.projectId && !folders.has(binding.projectId)) await refreshFolders().catch(() => {});
        return runCommitments(commitmentsScript(dirname(fileURLToPath(import.meta.url))), casesRoot(), argv.slice(1), ctx);
      }
      if (argv[0] === "usage" && argv.slice(1).every(a => a === "--json" || a === "--today")) {
        const day = await usage(bb, 1);
        const week = argv.includes("--today") ? null : await usage(bb, 7);
        return { exitCode: 0, stdout: argv.includes("--json") ? JSON.stringify({ day, week }) : formatUsage(day, week) };
      }
      if (argv[0] === "pause" || argv[0] === "resume" || argv[0] === "pause-status") {
        const i = argv.indexOf("--reason");
        return pause.command(argv[0] === "pause-status" ? "status" : argv[0], ctx.threadId, i > 0 ? argv[i + 1] : undefined);
      }
      if (argv[0] === "team" && argv.slice(1).every(a => a === "--guest")) {
        if (ctx.projectId && !folders.has(ctx.projectId)) await refreshFolders().catch(() => {});
        const org = organizationOf(ctx.projectId ? folders.get(ctx.projectId) : undefined);
        if (!org) return { exitCode: 1, stderr: "This installation does not work from an organization copy: no list of employees here." };
        const rows = registry(org.clone).map(p => p.id === org.me && pause.isPaused() ? { ...p, state: "paused" } : p);
        return { exitCode: 0, stdout: formatRegistry(rows, argv.includes("--guest")) };
      }
      if (argv[0] === "case") {
        if (ctx.projectId && !folders.has(ctx.projectId)) await refreshFolders().catch(() => {});
        if (binding.projectId && !folders.has(binding.projectId)) await refreshFolders().catch(() => {});
        const project = ctx.projectId ?? binding.projectId;
        return runCase(bb, argv.slice(1), { ...ctx, projectId: project }, casesRoot(project),
          id => Boolean(binding.threadId) && id === binding.threadId);
      }
      if (argv[0] === "code-to-owner") {
        // С85: never in a guest conversation — the guest guard does not let it through; here only the owner's bridge.
        return codeToOwner(argv.slice(1), (method, input) =>
          bb.sdk.plugins.callRpc({ pluginId: "telegram-projects", method, input, outputSchema: z.any() } as never));
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
