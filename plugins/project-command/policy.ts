/**
 * Какие команды агент может выполнить инструментом project_command и в каких пределах.
 * Один файл: список разрешённого и слова отказа. Новая возможность — новая строка здесь.
 *
 * Агент — учётка Linux со своими проектами. Его пределы: треды, расписания и чаты своих
 * проектов, файлы в своей домашней папке. Всё, что за пределами, получает отказ с причиной
 * и, где есть, с подсказкой, как сделать то же в пределах.
 */

/** telegram: личка владельца агента и чаты (группы), привязанные к его проектам; больше ему никуда писать нельзя. */
export type Agent = { user: string; home: string; projects: string[]; telegram?: { owner?: number; chats?: number[] } };

export type Lookup = {
  /** Проект треда или null, если треда нет. */
  threadProject(threadId: string): Promise<string | null>;
  /** Тред, из которого вызван инструмент, и его проект. */
  caller: { threadId: string; projectId: string };
  agent: Agent;
  /** Реальный путь (с раскрытием ссылок) или null, если пути нет. */
  realpath(path: string): Promise<string | null>;
};

export type Decision =
  | { ok: true; argv: string[]; record?: { createdProjectRoot: string } ; filterProjects?: boolean; input?: string;
      /** Ответ без вызова: список адресатов строится из привязок агента, а не из всех целей моста. */
      targets?: { people: number[]; chats: number[] } }
  | { ok: false; reason: string };

const no = (reason: string): Decision => ({ ok: false, reason });

export const REFUSALS = {
  unknown: (cmd: string) =>
    `Команда «${cmd}» не входит в список разрешённых. Доступно: подтреды и сообщения своим тредам (thread …), ` +
    `свои расписания (automation …), отправка в свои чаты и чтение их архива (telegram-projects send / search-history), ` +
    `свои проекты (project create / list), расшифровка своего голосового файла (voice transcribe), справка (status, skill, guide).`,
  foreignThread: (id: string) => `Тред ${id} не из ваших проектов — с ним работать нельзя.`,
  foreignProject: (id: string) => `Проект ${id} не ваш — в нём ничего делать нельзя.`,
  machine: "Выбирать машину или окружение другого проекта нельзя: подтред запускается рядом с вами.",
  outsideHome: (p: string) => `Путь ${p} вне вашей домашней папки — используйте файлы и папки внутри неё.`,
  script:
    "Расписания-скрипты запрещены: они выполнялись бы не под вашей учётной записью. Замена: расписание-агент " +
    "(automation create --prompt «запусти мой скрипт ./job.sh в рабочей папке и пришли итог»), он запустит ваш скрипт у вас.",
  rpcMethod: (m: string) => `Вызов ${m} не разрешён. Для отправки в Telegram — telegram-projects send; для подтредов — thread spawn.`,
  inputRequired: "Для этого вызова передайте поле input — текст JSON с параметрами.",
  person: (p: string) => `Человек ${p} не среди ваших адресатов: писать в личку можно только своему владельцу. Сообщение не отправлено.`,
  chat: (c: string) => `Чат ${c} не привязан к вашим проектам. Сообщение не отправлено.`,
};

/** Флаги, которые запускают работу вне пределов агента. */
// --new-environment создаёт рабочую копию в папке данных BB — ход шёл бы не под учёткой агента.
const ELSEWHERE = ["--machine", "--host", "--new-machine", "--environment-provider", "--machine-inputs", "--new-environment", "--base-branch"];
const SCRIPT_FLAGS = ["--script", "--script-file", "--interpreter", "--working-directory", "--env-json", "--host"];
const THREAD_BY_ID = new Set(["tell", "message", "show", "get", "log", "messages", "output", "wait", "stop", "archive", "read"]);
const AUTOMATION = new Set(["create", "list", "show", "update", "pause", "resume", "run", "runs", "delete"]);
const RPC_METHODS = new Set(["sendToTelegram", "sendDelegated", "spawnDelegated", "returnGroupResult",
  "telegramDeliveryStatus", "setChildUrgency", "continueChildOnActive", "archiveAssistantThread"]);

function flag(argv: string[], name: string): string | undefined {
  const i = argv.findIndex(a => a === name || a.startsWith(name + "="));
  if (i < 0) return undefined;
  return argv[i].includes("=") ? argv[i].slice(name.length + 1) : argv[i + 1];
}
const has = (argv: string[], name: string) => argv.some(a => a === name || a.startsWith(name + "="));
/** Позиционные аргументы после подкоманды (значения флагов пропускаются). */
function positionals(argv: string[], from: number) {
  const out: string[] = [];
  for (let i = from; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith("--")) { if (!a.includes("=") && i + 1 < argv.length && !argv[i + 1].startsWith("--")) i++; continue; }
    out.push(a);
  }
  return out;
}

async function inHome(l: Lookup, path: string): Promise<boolean> {
  const real = await l.realpath(path);
  const home = (await l.realpath(l.agent.home)) ?? l.agent.home;
  return Boolean(real && (real === home || real.startsWith(home + "/")));
}
const mine = (l: Lookup, projectId: string | null | undefined) => Boolean(projectId && l.agent.projects.includes(projectId));
async function ownThread(l: Lookup, id: string) { return mine(l, await l.threadProject(id)); }

export async function decide(argv: string[], l: Lookup, input?: string): Promise<Decision> {
  if (!argv.length) return no(REFUSALS.unknown(""));
  const [cmd, sub] = argv;
  if (argv.some(a => ELSEWHERE.includes(a.split("=")[0]))) return no(REFUSALS.machine);

  if (cmd === "status" || cmd === "guide" || (cmd === "skill" && (sub === "list" || sub === "show")))
    return { ok: true, argv };

  if (cmd === "thread") {
    if (sub === "spawn" || sub === "create") {
      const project = flag(argv, "--project") ?? l.caller.projectId;
      if (!mine(l, project)) return no(REFUSALS.foreignProject(project));
      const parent = flag(argv, "--parent-thread");
      if (parent && !(await ownThread(l, parent))) return no(REFUSALS.foreignThread(parent));
      const owner = flag(argv, "--lifecycle-owner-thread");
      if (owner && !(await ownThread(l, owner))) return no(REFUSALS.foreignThread(owner));
      const env = flag(argv, "--environment");
      if (env && env.startsWith("/") && !(await inHome(l, env))) return no(REFUSALS.outsideHome(env));
      if (env && !env.startsWith("/")) return no(REFUSALS.machine);
      for (const f of ["--prompt-file"]) {
        const p = flag(argv, f);
        if (p && p !== "-" && !(await inHome(l, p))) return no(REFUSALS.outsideHome(p));
      }
      const out = [...argv];
      if (!has(out, "--project")) out.push("--project", project);
      if (!parent && !has(out, "--parent-self")) out.push("--parent-thread", l.caller.threadId);
      return { ok: true, argv: out, input };
    }
    if (sub === "list") {
      const project = flag(argv, "--project") ?? l.caller.projectId;
      if (!mine(l, project)) return no(REFUSALS.foreignProject(project));
      const out = has(argv, "--project") ? [...argv] : [...argv, "--project", project];
      return { ok: true, argv: out };
    }
    if (sub && THREAD_BY_ID.has(sub)) {
      const ids = positionals(argv, 2).filter(a => /^thr_/.test(a));
      const self = has(argv, "--self");
      if (!ids.length && !self) return no(`Укажите номер треда: thread ${sub} <id>.`);
      for (const id of ids) if (!(await ownThread(l, id))) return no(REFUSALS.foreignThread(id));
      const mf = flag(argv, "--message-file");
      if (mf && mf !== "-" && !(await inHome(l, mf))) return no(REFUSALS.outsideHome(mf));
      return { ok: true, argv, input };
    }
    return no(REFUSALS.unknown(`thread ${sub ?? ""}`.trim()));
  }

  if (cmd === "automation" && sub && AUTOMATION.has(sub)) {
    if (argv.some(a => SCRIPT_FLAGS.includes(a.split("=")[0]))) return no(REFUSALS.script);
    const project = flag(argv, "--project") ?? l.caller.projectId;
    if (!mine(l, project)) return no(REFUSALS.foreignProject(project));
    const target = flag(argv, "--target-thread");
    if (target && !(await ownThread(l, target))) return no(REFUSALS.foreignThread(target));
    const env = flag(argv, "--environment");
    if (env && !(env.startsWith("/") && (await inHome(l, env)))) return no(REFUSALS.machine);
    const out = has(argv, "--project") ? [...argv] : [...argv, "--project", project];
    return { ok: true, argv: out };
  }

  if (cmd === "telegram-projects" && (sub === "send" || sub === "search-history")) {
    const tg = l.agent.telegram ?? {};
    const person = flag(argv, "--person");
    if (sub === "send" && person !== undefined && Number(person) !== tg.owner) return no(REFUSALS.person(person));
    const chat = flag(argv, "--chat");
    if (chat !== undefined && !(tg.chats ?? []).includes(Number(chat))) return no(REFUSALS.chat(chat));
    if (sub === "send" && has(argv, "--targets"))
      return { ok: true, argv: [], targets: { people: tg.owner ? [tg.owner] : [], chats: tg.chats ?? [] } };
    for (const f of ["--file", "--text-file"]) {
      const p = flag(argv, f);
      if (p && sub === "send" && p !== "-" && !(await inHome(l, p))) return no(REFUSALS.outsideHome(p));
    }
    return { ok: true, argv, input };
  }

  if (cmd === "plugin" && sub === "rpc" && argv[2] === "call" && argv[3] === "telegram-projects") {
    const method = argv[4];
    if (!RPC_METHODS.has(method)) return no(REFUSALS.rpcMethod(method));
    if (!input) return no(REFUSALS.inputRequired);
    let body: Record<string, unknown>;
    try { body = JSON.parse(input); } catch { return no("Поле input — не JSON."); }
    for (const k of ["threadId", "parentThreadId"]) {
      const id = body[k];
      if (typeof id === "string" && !(await ownThread(l, id))) return no(REFUSALS.foreignThread(id));
    }
    if (typeof body.projectId === "string" && !mine(l, body.projectId)) return no(REFUSALS.foreignProject(body.projectId));
    if (Array.isArray(body.files)) for (const f of body.files)
      if (typeof f === "string" && !(await inHome(l, f))) return no(REFUSALS.outsideHome(f));
    return { ok: true, argv: ["plugin", "rpc", "call", "telegram-projects", method, "--input-file", "-", "--json"], input };
  }

  if (cmd === "project" && sub === "list") return { ok: true, argv: ["project", "list", "--json"], filterProjects: true };
  if (cmd === "project" && sub === "create") {
    const root = flag(argv, "--root");
    if (!root) return no("Укажите папку проекта: project create --name <имя> --root <папка в вашей домашней папке>.");
    if (!(await inHome(l, root))) return no(REFUSALS.outsideHome(root));
    return { ok: true, argv: [...argv.filter(a => a !== "--json"), "--json"], record: { createdProjectRoot: root } };
  }

  if (cmd === "voice" && sub === "transcribe") {
    const p = positionals(argv, 2)[0];
    if (!p || !(await inHome(l, p))) return no(REFUSALS.outsideHome(p ?? "(не указан)"));
    return { ok: true, argv };
  }

  return no(REFUSALS.unknown([cmd, sub].filter(Boolean).join(" ")));
}
