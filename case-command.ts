import type { BbPluginApi } from "@get-bb/plugin-sdk";
import { z } from "zod";
import { checkCases, closeCase, findCases, listCases, openCase, readCard, setCase, type IndexRow } from "./cases";

/**
 * `bb personal-assistant case …` — the only writer of case cards. A conversation sees and changes the cases of its own
 * chat; the coordinator thread of the installation may see all (`--all`).
 *
 * The chat of a thread: walk up to the conversation thread (the top of its subthreads), then
 *  - the owner's private chat with the bot — the bridge's binding of topic 0 (it moves to the new thread on a restart
 *    or on a switch of the assistant pair, so the cases follow);
 *  - a Telegram group, topic or a person's chat — the conversation's title, which the bridge keeps the same for one
 *    place («<тема> — группа «…»», «личка «…»»);
 *  - a plain BB conversation — the thread itself, following BB's handoff (`sourceThreadId`) back to the first one.
 */
type Thread = { id: string; title?: string | null; parentThreadId?: string | null; sourceThreadId?: string | null; projectId?: string };

const bridgeStatus = z.object({ chatBindings: z.array(z.object({ topicId: z.number().optional(), threadId: z.string().nullish() }).passthrough()) }).passthrough();
const TELEGRAM_TITLE = /( — группа «|^группа «|^личка «)/;

const conversation = z.object({ chatId: z.union([z.number(), z.string()]), topicId: z.number().nullish() }).passthrough().nullable();

export async function chatOf(sdk: BbPluginApi["sdk"], threadId: string): Promise<string> {
  // The bridge knows the Telegram chat and topic of a thread (or of its conversation, for a subthread): numbers do not
  // change when a topic or a group is renamed, and two people with one name are two chats.
  const conv = await sdk.plugins.callRpc({ pluginId: "telegram-projects", method: "conversationOf", input: { threadId }, outputSchema: conversation })
    .catch(() => undefined);
  if (conv) return `tg:${conv.chatId}:${conv.topicId ?? 0}`;
  // An older bridge without conversationOf: the binding of the owner's chat, then the conversation's title.
  const get = async (id: string) => (await sdk.threads.get({ threadId: id })) as unknown as Thread;
  let t = await get(threadId);
  for (let i = 0; i < 12 && t.parentThreadId; i++) t = await get(t.parentThreadId);
  const status = await sdk.plugins.callRpc({ pluginId: "telegram-projects", method: "status", input: null, outputSchema: bridgeStatus })
    .catch(() => null);
  if (status?.chatBindings.some(b => b.threadId === t.id && (b.topicId ?? 0) === 0)) return "tg:owner";
  if (t.title && TELEGRAM_TITLE.test(t.title)) return `tg:${t.title.trim()}`;
  for (let i = 0; i < 12 && t.sourceThreadId; i++) t = await get(t.sourceThreadId).catch(() => ({ id: t.sourceThreadId! }));
  return `thread:${t.id}`;
}

const automationsList = z.array(z.object({ id: z.string(), enabled: z.boolean() }).passthrough());

async function liveSchedules(sdk: BbPluginApi["sdk"], projectId: string): Promise<Set<string>> {
  const list = await sdk.plugins.callRpc({ pluginId: "automations", method: "automations_list", input: { projectId }, outputSchema: automationsList });
  return new Set(list.filter(a => a.enabled).map(a => a.id));
}

/** Flags: `--name value`, repeated ones collected; positionals separately. */
export function flags(argv: string[]): { pos: string[]; one: (k: string) => string | undefined; many: (k: string) => string[]; has: (k: string) => boolean } {
  const pos: string[] = [], m = new Map<string, string[]>(), bare = new Set<string>();
  const BARE = new Set(["--here", "--all", "--closed", "--no-waiting"]);
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith("--") && BARE.has(a)) bare.add(a);
    else if (a.startsWith("--")) { m.set(a, [...(m.get(a) ?? []), argv[i + 1] ?? ""]); i++; }
    else pos.push(a);
  }
  return { pos, one: k => m.get(k)?.at(-1), many: k => m.get(k) ?? [], has: k => bare.has(k) };
}

const row = (r: IndexRow) => `${r.id} | ${r.state} | ${r.title} | ${r.next} | ${r.place} | ${r.by} | ${r.date}`;

export const CASE_USAGE = "bb personal-assistant case open --title … [--by …] [--place …] [--message …] [--thread …] [--next …] [--promise …] [--schedule auto_…] [--space …]\n"
  + "  | set <id> [--state open|waiting|decision|done_unverified|undelivered] [--next …] [--thread …] [--waiting-who …] [--waiting-what …] [--waiting-back <ISO>] [--no-waiting] [--promise …] [--schedule auto_…] [--unschedule auto_…] [--space …] [--person …] [--note …]\n"
  + "  | close <id> [--result …] | show <id> | list [--here|--all] [--closed] | find <words> [--all] | check [--here|--all]";

export async function runCase(bb: BbPluginApi, argv: string[], ctx: { threadId?: string; projectId?: string },
  root: string | undefined, coordinator: (threadId: string) => boolean): Promise<{ exitCode: number; stdout?: string; stderr?: string }> {
  const [sub, ...rest] = argv;
  if (!root) return { exitCode: 1, stderr: "This thread has no installation folder (project source)." };
  if (!ctx.threadId || !ctx.projectId) return { exitCode: 1, stderr: "Run it from a thread of the installation." };
  const f = flags(rest);
  const chat = await chatOf(bb.sdk, ctx.threadId);
  const all = f.has("--all");
  if (all && !coordinator(ctx.threadId)) return { exitCode: 1, stderr: "--all is for the coordinator thread of this installation; here you see the cases of this chat." };
  const scope = all ? null : chat;
  const mine = (id: string) => {
    const c = readCard(root, id);
    if (!all && c.chat !== chat && !coordinator(ctx.threadId!)) throw new Error(`the case ${id} belongs to another chat`);
    return c;
  };
  try {
    switch (sub) {
      case "open": {
        const c = openCase(root, { title: f.one("--title") ?? "", chat, place: f.one("--place"), by: f.one("--by"), message: f.one("--message"),
          thread: f.one("--thread"), next: f.one("--next"), promise: f.one("--promise"), schedule: f.one("--schedule"), space: f.one("--space") });
        return { exitCode: 0, stdout: `${c.id}\nОткрыто дело «${c.title}». Расписание шага — с именем «${c.id}: <шаг>», затем case set ${c.id} --schedule <auto_…>.` };
      }
      case "set": {
        const id = f.pos[0]; mine(id);
        const c = setCase(root, id, { state: f.one("--state"), next: f.one("--next"), thread: f.one("--thread"),
          waitingWho: f.one("--waiting-who"), waitingWhat: f.one("--waiting-what"), waitingBack: f.one("--waiting-back"),
          noWaiting: f.has("--no-waiting"), promise: f.one("--promise"), scheduleAdd: f.many("--schedule"),
          scheduleRemove: f.many("--unschedule"), space: f.one("--space"), person: f.one("--person"), note: f.one("--note") });
        return { exitCode: 0, stdout: `${c.id}: ${c.state}${c.next ? `; дальше: ${c.next}` : ""}` };
      }
      case "close": {
        const id = f.pos[0]; mine(id);
        const { card, schedules } = closeCase(root, id, f.one("--result") ?? "");
        const lines = [`${card.id}: закрыто.`];
        if (schedules.length) lines.push(`Сними расписания дела: ${schedules.map(s => `bb automation delete ${s}`).join("; ")}`);
        lines.push(`Найди и сними забытые: bb automation list --project ${ctx.projectId} — имена, начинающиеся с «${card.id}:».`);
        if (card.thread) lines.push(`Отметка уборки: bb thread-cleanup task ${card.thread} closed`);
        return { exitCode: 0, stdout: lines.join("\n") };
      }
      case "show": {
        const c = mine(f.pos[0]);
        return { exitCode: 0, stdout: JSON.stringify(c, null, 1) };
      }
      case "list": {
        const rows = listCases(root, scope, f.has("--closed"));
        return { exitCode: 0, stdout: rows.map(row).join("\n") || "Открытых дел нет." };
      }
      case "find": {
        const rows = findCases(root, scope, f.pos.join(" "));
        return { exitCode: 0, stdout: rows.map(row).join("\n") || "Ничего не найдено." };
      }
      case "check": {
        const problems = checkCases(root, scope, await liveSchedules(bb.sdk, ctx.projectId));
        return { exitCode: 0, stdout: problems.join("\n") || "У каждого открытого дела есть следующий шаг." };
      }
      default:
        return { exitCode: 2, stderr: `Usage: ${CASE_USAGE}` };
    }
  } catch (e) {
    return { exitCode: 1, stderr: e instanceof Error ? e.message : String(e) };
  }
}
