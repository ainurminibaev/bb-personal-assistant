import { execFile } from "node:child_process";
import type { BbPluginApi } from "@bb/plugin-sdk";
import { z } from "zod";
import {
  checkCases, closeCase, findCases, formatDeliveryTarget, listCases, normalizeDeliveryTarget,
  openCase, readCard, recordDelivery, recordDeliveryFailure, setCase,
  type DeliveryTarget, type IndexRow,
} from "./cases";

/**
 * `bb personal-assistant case …` — the CLI over local/cases/.
 * Every call knows which conversation it came from (the thread's Telegram chat via the bridge, or the conversation's
 * root thread); a chat sees and changes only its own cases. Only the installation's coordinator thread may use `--all`.
 */

type Thread = { id: string; title?: string; parentThreadId?: string | null; sourceThreadId?: string | null };
const bridgeStatus = z.object({
  chatBindings: z.array(z.object({ topicId: z.number().nullish(), threadId: z.string().nullish() }).passthrough()).default([]),
}).passthrough();
const TELEGRAM_TITLE = /( — группа «|^группа «|^личка «)/;

const conversation = z.object({
  kind: z.enum(["owner", "group", "person"]).optional(),
  chatId: z.union([z.number(), z.string()]),
  topicId: z.number().nullish(),
}).passthrough().nullable();

export interface ChatContext {
  key: string;
  defaultDeliver: DeliveryTarget | null;
}

export async function chatContextOf(sdk: BbPluginApi["sdk"], threadId: string): Promise<ChatContext> {
  // The bridge knows the Telegram chat and topic of a thread (or of its conversation, for a subthread): numbers do not
  // change when a topic or a group is renamed, and two people with one name are two chats.
  const conv = await sdk.plugins.callRpc({ pluginId: "telegram-projects", method: "conversationOf", input: { threadId }, outputSchema: conversation })
    .catch(() => undefined);
  if (conv) {
    const key = `tg:${conv.chatId}:${conv.topicId ?? 0}`;
    let defaultDeliver: DeliveryTarget | null = null;
    if (conv.kind === "owner") defaultDeliver = { person: "owner" };
    else if (conv.kind === "person") defaultDeliver = { person: String(conv.chatId) };
    else if (conv.kind === "group" || Number(conv.chatId) < 0) {
      defaultDeliver = { chat: String(conv.chatId), ...(conv.topicId ? { topic: String(conv.topicId) } : {}) };
    } else if (conv.chatId !== undefined) {
      defaultDeliver = { person: String(conv.chatId) };
    }
    return { key, defaultDeliver };
  }
  // An older bridge without conversationOf: the binding of the owner's chat, then the conversation's title.
  const get = async (id: string) => (await sdk.threads.get({ threadId: id })) as unknown as Thread;
  let t = await get(threadId);
  for (let i = 0; i < 12 && t.parentThreadId; i++) t = await get(t.parentThreadId);
  const status = await sdk.plugins.callRpc({ pluginId: "telegram-projects", method: "status", input: null, outputSchema: bridgeStatus })
    .catch(() => null);
  if (status?.chatBindings.some(b => b.threadId === t.id && (b.topicId ?? 0) === 0)) {
    return { key: "tg:owner", defaultDeliver: { person: "owner" } };
  }
  if (t.title && TELEGRAM_TITLE.test(t.title)) return { key: `tg:${t.title.trim()}`, defaultDeliver: null };
  for (let i = 0; i < 12 && t.sourceThreadId; i++) t = await get(t.sourceThreadId).catch(() => ({ id: t.sourceThreadId! }));
  return { key: `thread:${t.id}`, defaultDeliver: null };
}

export async function chatOf(sdk: BbPluginApi["sdk"], threadId: string): Promise<string> {
  return (await chatContextOf(sdk, threadId)).key;
}

const automationsList = z.array(z.object({ id: z.string(), enabled: z.boolean() }).passthrough());

/** Enabled schedules of the project, or of every project of this BB when there is none (a terminal). */
async function liveSchedules(sdk: BbPluginApi["sdk"], projectId?: string): Promise<Set<string>> {
  const ids = projectId ? [projectId] : ((await sdk.projects.list({})) as { id: string }[]).map(p => p.id);
  const out = new Set<string>();
  for (const id of ids) {
    const list = await sdk.plugins.callRpc({ pluginId: "automations", method: "automations_list", input: { projectId: id }, outputSchema: automationsList });
    for (const a of list) if (a.enabled) out.add(a.id);
  }
  return out;
}

/** Environment for calling `bb telegram-projects send`: strictly the calling thread/project, never escalated. */
export function deliverEnv(base: NodeJS.ProcessEnv, ctx: { threadId?: string; projectId?: string }): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = { ...base };
  delete env.BB_THREAD_ID;
  delete env.BB_PROJECT_ID;
  if (ctx.threadId) env.BB_THREAD_ID = ctx.threadId;
  if (ctx.projectId) env.BB_PROJECT_ID = ctx.projectId;
  if (!env.BB_SERVER_URL && env.BB_SERVER_PORT) env.BB_SERVER_URL = `http://127.0.0.1:${env.BB_SERVER_PORT}`;
  return env;
}

export type BridgeSender = (
  args: string[],
  ctx: { threadId?: string; projectId?: string },
) => Promise<{ exitCode: number; stdout: string; stderr: string }>;

function defaultBridgeSender(args: string[], ctx: { threadId?: string; projectId?: string }) {
  return new Promise<{ exitCode: number; stdout: string; stderr: string }>(resolve => {
    const child = execFile("bb", args, { timeout: 60_000, maxBuffer: 4 * 1024 * 1024, env: deliverEnv(process.env, ctx) },
      (error, stdout, stderr) => {
        const code = error ? (typeof (error as { code?: unknown }).code === "number" ? (error as unknown as { code: number }).code : 1) : 0;
        resolve({ exitCode: code, stdout: String(stdout ?? ""), stderr: String(stderr ?? "") || (error && !code ? error.message : "") });
      });
    child.stdin?.end();
  });
}

/** Flags: `--name value`, repeated ones collected; positionals separately. */
export function flags(argv: string[]): { pos: string[]; one: (k: string) => string | undefined; many: (k: string) => string[]; has: (k: string) => boolean } {
  const pos: string[] = [], m = new Map<string, string[]>(), bare = new Set<string>();
  const BARE = new Set(["--here", "--all", "--closed", "--no-waiting", "--clear-deliver", "--force"]);
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith("--") && BARE.has(a)) bare.add(a);
    else if (a.startsWith("--")) { m.set(a, [...(m.get(a) ?? []), argv[i + 1] ?? ""]); i++; }
    else pos.push(a);
  }
  return { pos, one: k => m.get(k)?.at(-1), many: k => m.get(k) ?? [], has: k => bare.has(k) };
}

const row = (r: IndexRow) => `${r.id} | ${r.state} | ${r.title} | ${r.next} | ${r.place} | ${r.by} | ${r.date}`;

export const CASE_USAGE = "bb personal-assistant case open --title … [--by …] [--place …] [--message …] [--thread …] [--next …] [--promise …] [--schedule auto_…] [--space …] [--deliver-chat …] [--deliver-topic …] [--deliver-person …] [--deliver-reply-to …]\n"
  + "  | set <id> [--state open|waiting|decision|done_unverified|undelivered] [--next …] [--thread …] [--waiting-who …] [--waiting-what …] [--waiting-back <ISO>] [--no-waiting] [--promise …] [--schedule auto_…] [--unschedule auto_…] [--space …] [--person …] [--deliver-chat …] [--deliver-topic …] [--deliver-person …] [--deliver-reply-to …] [--clear-deliver] [--note …]\n"
  + "  | deliver <id> (--text … | --text-file …) [--file …] [--reply-to …] [--force]\n"
  + "  | close <id> [--result …] | show <id> | list [--here|--all] [--closed] | find <words> [--all] | check [--here|--all]";

function explicitDeliveryFromFlags(f: ReturnType<typeof flags>): DeliveryTarget | undefined {
  const chat = f.one("--deliver-chat");
  const topic = f.one("--deliver-topic");
  const person = f.one("--deliver-person");
  const replyTo = f.one("--deliver-reply-to");
  if (chat === undefined && topic === undefined && person === undefined && replyTo === undefined) return undefined;
  return {
    ...(chat !== undefined ? { chat } : {}),
    ...(topic !== undefined ? { topic } : {}),
    ...(person !== undefined ? { person } : {}),
    ...(replyTo !== undefined ? { replyTo } : {}),
  };
}

export async function runCase(
  bb: BbPluginApi,
  argv: string[],
  ctx: { threadId?: string; projectId?: string },
  root: string | undefined,
  coordinator: (threadId: string) => boolean,
  sendBridge: BridgeSender = defaultBridgeSender,
): Promise<{ exitCode: number; stdout?: string; stderr?: string }> {
  const [sub, ...rest] = argv;
  if (!root) return { exitCode: 1, stderr: ctx.threadId
    ? "This thread has no installation folder (project source)."
    : "From a terminal: set projectId (the coordinator project) in the personal-assistant plugin settings — the cases live in its folder." };
  const f = flags(rest);
  // A terminal of the server (no thread) is a person with access to the account: it sees every chat's cases, like
  // the coordinator; a case is opened only from its conversation.
  const terminal = !ctx.threadId;
  if (terminal && sub === "open") return { exitCode: 1, stderr: "A case is opened from its conversation: the chat it belongs to is that conversation's." };
  const chatCtx: ChatContext = terminal ? { key: "", defaultDeliver: null } : await chatContextOf(bb.sdk, ctx.threadId!);
  const chat = chatCtx.key;
  const all = terminal || f.has("--all");
  if (!terminal && all && !coordinator(ctx.threadId!)) return { exitCode: 1, stderr: "--all is for the coordinator thread of this installation; here you see the cases of this chat." };
  const scope = all ? null : chat;
  const mine = (id: string) => {
    const c = readCard(root, id);
    const sameSubthread = Boolean(ctx.threadId && c.thread && c.thread === ctx.threadId);
    if (!all && c.chat !== chat && !sameSubthread && !coordinator(ctx.threadId ?? "")) {
      throw new Error(`the case ${id} belongs to another chat`);
    }
    return c;
  };
  try {
    switch (sub) {
      case "open": {
        const explicit = explicitDeliveryFromFlags(f);
        const msgRaw = f.one("--message")?.trim();
        const inferredReply = msgRaw && /^\d+$/.test(msgRaw) && Number(msgRaw) > 0 ? msgRaw : undefined;
        let deliver: DeliveryTarget | null = null;
        if (explicit && (explicit.person || explicit.chat || explicit.topic)) {
          deliver = normalizeDeliveryTarget({ ...explicit, replyTo: explicit.replyTo ?? inferredReply });
        } else if (chatCtx.defaultDeliver) {
          deliver = normalizeDeliveryTarget({ ...chatCtx.defaultDeliver, replyTo: explicit?.replyTo ?? inferredReply });
        } else if (explicit) {
          deliver = normalizeDeliveryTarget(explicit);
        }
        const c = openCase(root, { title: f.one("--title") ?? "", chat, place: f.one("--place"), by: f.one("--by"), message: f.one("--message"),
          thread: f.one("--thread"), next: f.one("--next"), promise: f.one("--promise"), schedule: f.one("--schedule"), space: f.one("--space"),
          deliver });
        const dest = c.deliver ? ` Адрес ответа: ${formatDeliveryTarget(c.deliver)}.` : "";
        return { exitCode: 0, stdout: `${c.id}\nОткрыто дело «${c.title}».${dest} Расписание шага — с именем «${c.id}: <шаг>», затем case set ${c.id} --schedule <auto_…>.` };
      }
      case "set": {
        const id = f.pos[0]; mine(id);
        const c = setCase(root, id, { state: f.one("--state"), next: f.one("--next"), thread: f.one("--thread"),
          waitingWho: f.one("--waiting-who"), waitingWhat: f.one("--waiting-what"), waitingBack: f.one("--waiting-back"),
          noWaiting: f.has("--no-waiting"), promise: f.one("--promise"), scheduleAdd: f.many("--schedule"),
          scheduleRemove: f.many("--unschedule"), space: f.one("--space"), person: f.one("--person"),
          deliver: explicitDeliveryFromFlags(f), clearDeliver: f.has("--clear-deliver"), note: f.one("--note") });
        return { exitCode: 0, stdout: `${c.id}: ${c.state}${c.next ? `; дальше: ${c.next}` : ""}` };
      }
      case "deliver": {
        const id = f.pos[0];
        if (!id) return { exitCode: 2, stderr: `Usage: ${CASE_USAGE}` };
        const c = mine(id);
        const text = f.one("--text");
        const textFile = f.one("--text-file");
        const files = f.many("--file").filter(Boolean);
        if (textFile === "-") return { exitCode: 2, stderr: "--text-file cannot be stdin (-); pass a file path or --text" };
        if (!text?.trim() && !textFile?.trim()) {
          return { exitCode: 2, stderr: "deliver requires --text <TEXT> or --text-file <PATH>" };
        }
        const d = c.deliver;
        if (!d || (!d.person && !d.chat && !d.topic)) {
          return { exitCode: 1, stderr: `no_delivery_target: у дела ${c.id} не задан адрес ответа. Задай его через case set ${c.id} --deliver-chat … [--deliver-topic …] или --deliver-person …` };
        }
        if (c.delivered && !f.has("--force")) {
          const msg = typeof c.delivered.messageId === "number" ? `, msg ${c.delivered.messageId}` : "";
          return { exitCode: 1, stderr: `already_delivered: итог по делу ${c.id} уже доставлен (${c.delivered.at}${msg}). Не повторяй отправку без нужды; если нужно отправить ещё раз — добавь --force.` };
        }
        const sendArgs = ["telegram-projects", "send"];
        if (d.person) {
          sendArgs.push("--person", d.person);
        } else {
          if (d.chat) sendArgs.push("--chat", d.chat);
          if (d.topic) sendArgs.push("--topic", d.topic);
        }
        const replyTo = (f.one("--reply-to") ?? d.replyTo)?.trim();
        if (replyTo && /^\d+$/.test(replyTo) && Number(replyTo) > 0) sendArgs.push("--reply-to", replyTo);
        if (textFile?.trim()) sendArgs.push("--text-file", textFile);
        else if (text !== undefined) sendArgs.push("--text", text);
        for (const fp of files) sendArgs.push("--file", fp);

        const res = await sendBridge(sendArgs, ctx);
        if (res.exitCode !== 0) {
          const reason = (res.stderr || res.stdout || "bridge_send_failed").trim();
          recordDeliveryFailure(root, c.id, reason);
          return { exitCode: res.exitCode || 1, stderr: reason };
        }
        let parsed: { sent?: boolean; messageId?: number; note?: string } | null = null;
        try { parsed = JSON.parse(res.stdout.trim()); } catch { parsed = null; }
        if (!parsed || parsed.sent !== true) {
          const reason = (res.stdout || res.stderr || "unexpected_bridge_response").trim();
          recordDeliveryFailure(root, c.id, reason);
          return { exitCode: 1, stderr: reason };
        }
        const targetText = formatDeliveryTarget(d);
        recordDelivery(root, c.id, {
          at: new Date().toISOString(),
          ...(typeof parsed.messageId === "number" ? { messageId: parsed.messageId } : {}),
          target: targetText,
          ...(ctx.threadId ? { byThread: ctx.threadId } : {}),
        });
        const msg = typeof parsed.messageId === "number" ? `, msg ${parsed.messageId}` : "";
        const note = parsed.note ? ` ${parsed.note}` : "";
        return { exitCode: 0, stdout: `Доставлено по делу ${c.id} (${targetText}${msg}).${note}` };
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
