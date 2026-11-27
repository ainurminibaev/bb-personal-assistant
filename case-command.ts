import { execFile } from "node:child_process";
import { realpathSync } from "node:fs";
import { isAbsolute, resolve, sep } from "node:path";
import type { BbPluginApi } from "@bb/plugin-sdk";
import { z } from "zod";
import {
  acquireDeliveryLease, checkCases, closeCase, findCases, formatDeliveryTarget, listCases,
  normalizeDeliveryTarget, openCase, readCard, recordDelivery, recordDeliveryFailure,
  releaseDeliveryLease, setCase,
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

export interface BridgeSendResult {
  exitCode: number;
  stdout: string;
  stderr: string;
  timedOut?: boolean;
}

export type BridgeSender = (
  args: string[],
  ctx: { threadId?: string; projectId?: string },
) => Promise<BridgeSendResult>;

function defaultBridgeSender(args: string[], ctx: { threadId?: string; projectId?: string }): Promise<BridgeSendResult> {
  const bin = process.env.BB_CLI || "bb";
  return new Promise(resolvePromise => {
    const child = execFile(bin, args, { timeout: 60_000, maxBuffer: 4 * 1024 * 1024, env: deliverEnv(process.env, ctx) },
      (error, stdout, stderr) => {
        const err = error as (Error & { code?: unknown; killed?: boolean; signal?: string }) | null;
        const timedOut = Boolean(err && (err.killed || err.signal));
        const code = err ? (typeof err.code === "number" ? err.code : 1) : 0;
        const errText = String(stderr ?? "").trim() || (err ? err.message : "");
        resolvePromise({ exitCode: code, stdout: String(stdout ?? ""), stderr: errText, timedOut });
      });
    child.stdin?.end();
  });
}

/** Check whether `targetPath` resolves strictly inside `baseDir` (following symlinks when present). */
function isInsideDir(baseDir: string, targetPath: string): boolean {
  try {
    const realBase = realpathSync(baseDir);
    const realTarget = realpathSync(targetPath);
    return realTarget === realBase || realTarget.startsWith(realBase + sep);
  } catch {
    const absBase = resolve(baseDir);
    const absTarget = resolve(targetPath);
    return absTarget === absBase || absTarget.startsWith(absBase + sep);
  }
}

/** Flags: `--name value` or `--name=value`, repeated ones collected; positionals separately. */
export function flags(argv: string[]): { pos: string[]; one: (k: string) => string | undefined; many: (k: string) => string[]; has: (k: string) => boolean } {
  const pos: string[] = [], m = new Map<string, string[]>(), bare = new Set<string>();
  const BARE = new Set(["--here", "--all", "--closed", "--no-waiting", "--clear-deliver", "--force"]);
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith("--") && BARE.has(a)) {
      bare.add(a);
    } else if (a.startsWith("--") && a.includes("=")) {
      const eq = a.indexOf("=");
      const k = a.slice(0, eq), v = a.slice(eq + 1);
      m.set(k, [...(m.get(k) ?? []), v]);
    } else if (a.startsWith("--")) {
      m.set(a, [...(m.get(a) ?? []), argv[i + 1] ?? ""]);
      i++;
    } else {
      pos.push(a);
    }
  }
  return { pos, one: k => m.get(k)?.at(-1), many: k => m.get(k) ?? [], has: k => bare.has(k) };
}

const row = (r: IndexRow) => `${r.id} | ${r.state} | ${r.title} | ${r.next} | ${r.place} | ${r.by} | ${r.date}`;

export const CASE_USAGE = "bb personal-assistant case open --title … [--by …] [--place …] [--message …] [--thread …] [--next …] [--promise …] [--schedule auto_…] [--space …] [--deliver-chat …] [--deliver-topic …] [--deliver-person …] [--deliver-reply-to …]\n"
  + "  | set <id> [--state open|waiting|decision|done_unverified|undelivered] [--next …] [--thread …] [--waiting-who …] [--waiting-what …] [--waiting-back <ISO>] [--no-waiting] [--promise …] [--schedule auto_…] [--unschedule auto_…] [--space …] [--person …] [--deliver-chat …] [--deliver-topic …] [--deliver-person …] [--deliver-reply-to …] [--clear-deliver] [--note …]\n"
  + "  | deliver <id> (--text … | --text-file …) [--file …] [--reply-to …] [--force]\n"
  + "  | close <id> [--result …] | show <id> | list [--here|--all] [--closed] | find <words> [--all] | check [--here|--all]";

const isPosInt = (v: string | undefined): boolean => Boolean(v && /^\d+$/.test(v.trim()) && Number(v.trim()) > 0);

function explicitDeliveryFromFlags(f: ReturnType<typeof flags>): { target?: DeliveryTarget; error?: string } {
  const chat = f.one("--deliver-chat");
  const topic = f.one("--deliver-topic");
  const person = f.one("--deliver-person");
  const replyTo = f.one("--deliver-reply-to");
  if (person !== undefined && (chat !== undefined || topic !== undefined)) {
    return { error: "cannot combine --deliver-person with --deliver-chat or --deliver-topic" };
  }
  if (replyTo !== undefined && replyTo.trim() !== "" && !isPosInt(replyTo)) {
    return { error: `--deliver-reply-to must be a positive integer message ID, got: ${replyTo}` };
  }
  if (chat === undefined && topic === undefined && person === undefined && replyTo === undefined) return {};
  return {
    target: {
      ...(chat !== undefined ? { chat } : {}),
      ...(topic !== undefined ? { topic } : {}),
      ...(person !== undefined ? { person } : {}),
      ...(replyTo !== undefined ? { replyTo } : {}),
    },
  };
}

async function callerProjectDir(sdk: BbPluginApi["sdk"], projectId?: string): Promise<string | undefined> {
  if (!projectId) return undefined;
  const proj = await sdk.projects.get({ projectId }).catch(() => undefined) as { sources?: { path?: string; isDefault?: boolean }[] } | undefined;
  const src = proj?.sources ?? [];
  return (src.find(s => s.isDefault) ?? src[0])?.path;
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
        const exp = explicitDeliveryFromFlags(f);
        if (exp.error) return { exitCode: 2, stderr: exp.error };
        const explicit = exp.target;
        const msgRaw = f.one("--message")?.trim();
        const inferredReply = isPosInt(msgRaw) ? msgRaw : undefined;
        let deliver: DeliveryTarget | null = null;
        if (explicit && explicit.person) {
          // Explicit private-chat target: only inherit inferredReply if it matches current private chat.
          const sameAsCurrent = chatCtx.defaultDeliver?.person === explicit.person.trim();
          deliver = normalizeDeliveryTarget({
            person: explicit.person,
            replyTo: explicit.replyTo ?? (sameAsCurrent ? inferredReply : undefined),
          });
        } else if (explicit && (explicit.chat || explicit.topic)) {
          // Explicit group/topic target: inherit current group chat if only --deliver-topic was passed.
          const targetChat = explicit.chat ?? chatCtx.defaultDeliver?.chat;
          const sameChat = Boolean(targetChat && chatCtx.defaultDeliver?.chat === targetChat.trim());
          const sameTopic = !explicit.topic || chatCtx.defaultDeliver?.topic === explicit.topic.trim();
          deliver = normalizeDeliveryTarget({
            chat: targetChat,
            topic: explicit.topic,
            replyTo: explicit.replyTo ?? (sameChat && sameTopic ? inferredReply : undefined),
          });
        } else if (chatCtx.defaultDeliver) {
          deliver = normalizeDeliveryTarget({ ...chatCtx.defaultDeliver, replyTo: explicit?.replyTo ?? inferredReply });
        }
        const c = openCase(root, { title: f.one("--title") ?? "", chat, place: f.one("--place"), by: f.one("--by"), message: f.one("--message"),
          thread: f.one("--thread"), next: f.one("--next"), promise: f.one("--promise"), schedule: f.one("--schedule"), space: f.one("--space"),
          deliver });
        const dest = c.deliver ? ` Адрес ответа: ${formatDeliveryTarget(c.deliver)}.` : "";
        return { exitCode: 0, stdout: `${c.id}\nОткрыто дело «${c.title}».${dest} Расписание шага — с именем «${c.id}: <шаг>», затем case set ${c.id} --schedule <auto_…>.` };
      }
      case "set": {
        const id = f.pos[0]; mine(id);
        const exp = explicitDeliveryFromFlags(f);
        if (exp.error) return { exitCode: 2, stderr: exp.error };
        const c = setCase(root, id, { state: f.one("--state"), next: f.one("--next"), thread: f.one("--thread"),
          waitingWho: f.one("--waiting-who"), waitingWhat: f.one("--waiting-what"), waitingBack: f.one("--waiting-back"),
          noWaiting: f.has("--no-waiting"), promise: f.one("--promise"), scheduleAdd: f.many("--schedule"),
          scheduleRemove: f.many("--unschedule"), space: f.one("--space"), person: f.one("--person"),
          deliver: exp.target, clearDeliver: f.has("--clear-deliver"), note: f.one("--note") });
        return { exitCode: 0, stdout: `${c.id}: ${c.state}${c.next ? `; дальше: ${c.next}` : ""}` };
      }
      case "deliver": {
        const id = f.pos[0];
        if (!id) return { exitCode: 2, stderr: `Usage: ${CASE_USAGE}` };
        mine(id);
        const text = f.one("--text");
        const textFile = f.one("--text-file");
        const files = f.many("--file").filter(Boolean);
        const cliReplyTo = f.one("--reply-to");
        if (text !== undefined && textFile !== undefined) {
          return { exitCode: 2, stderr: "cannot combine --text and --text-file; pass one of them" };
        }
        if (textFile === "-") return { exitCode: 2, stderr: "--text-file cannot be stdin (-); pass an absolute file path or --text" };
        if (!text?.trim() && !textFile?.trim()) {
          return { exitCode: 2, stderr: "deliver requires --text <TEXT> or --text-file <PATH>" };
        }
        if (cliReplyTo !== undefined && !isPosInt(cliReplyTo)) {
          return { exitCode: 2, stderr: `--reply-to must be a positive integer message ID, got: ${cliReplyTo}` };
        }
        // Require absolute paths for --text-file and --file so server cwd never alters path resolution.
        if (textFile && !isAbsolute(textFile)) {
          return { exitCode: 2, stderr: `--text-file must be an absolute path, got: ${textFile}` };
        }
        for (const fp of files) {
          if (!isAbsolute(fp)) return { exitCode: 2, stderr: `--file must be an absolute path, got: ${fp}` };
        }
        // Non-coordinator, non-terminal threads may only pass --text-file inside their own project folder.
        if (textFile && !terminal && !coordinator(ctx.threadId!)) {
          const projDir = await callerProjectDir(bb.sdk, ctx.projectId);
          if (!projDir || !isInsideDir(projDir, textFile)) {
            return { exitCode: 1, stderr: "text_file_outside_project: --text-file должен находиться внутри папки текущего разговора/проекта (или передай текст через --text)" };
          }
        }

        // Atomically acquire an in-flight lease and validate closed / delivered / uncertain guards under withLock.
        const acquired = acquireDeliveryLease(root, id, { force: f.has("--force"), byThread: ctx.threadId });
        if (!acquired.ok) return { exitCode: 1, stderr: acquired.message };

        const c = acquired.card;
        const d = c.deliver!;
        const targetText = formatDeliveryTarget(d);
        try {
          const sendArgs = ["telegram-projects", "send"];
          if (d.person) {
            sendArgs.push("--person", d.person);
          } else {
            if (d.chat) sendArgs.push("--chat", d.chat);
            if (d.topic) sendArgs.push("--topic", d.topic);
          }
          const replyTo = (cliReplyTo ?? d.replyTo)?.trim();
          if (isPosInt(replyTo)) sendArgs.push("--reply-to", replyTo!);
          if (textFile?.trim()) sendArgs.push("--text-file", textFile);
          else if (text !== undefined) sendArgs.push("--text", text);
          for (const fp of files) sendArgs.push("--file", fp);

          const res = await sendBridge(sendArgs, ctx);
          let parsed: { sent?: boolean; messageId?: number; note?: string } | null = null;
          try { parsed = res.stdout.trim() ? JSON.parse(res.stdout.trim()) : null; } catch { parsed = null; }

          // Case A: clean confirmed send.
          if (res.exitCode === 0 && parsed?.sent === true) {
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

          const reason = (res.stderr || parsed?.note || res.stdout || "bridge_send_failed").trim();

          // Case B: partial/incomplete send where bridge already delivered part (sent:true with exitCode!=0).
          if (parsed?.sent === true) {
            recordDelivery(root, c.id, {
              at: new Date().toISOString(),
              ...(typeof parsed.messageId === "number" ? { messageId: parsed.messageId } : {}),
              target: targetText,
              ...(ctx.threadId ? { byThread: ctx.threadId } : {}),
              uncertain: true,
              note: reason,
            });
            return { exitCode: res.exitCode || 1, stderr: reason };
          }

          // Case C: uncertain outcome (send_outcome_unknown or execFile timeout) — block blind retries without --force.
          if (res.timedOut || /send_outcome_unknown/i.test(reason)) {
            const uncertainReason = res.timedOut ? `send_timeout: ${reason || "превышено время ожидания моста — сообщение могло дойти"}` : reason;
            recordDelivery(root, c.id, {
              at: new Date().toISOString(),
              target: targetText,
              ...(ctx.threadId ? { byThread: ctx.threadId } : {}),
              uncertain: true,
              note: uncertainReason,
            });
            return { exitCode: res.exitCode || 1, stderr: uncertainReason };
          }

          // Case D: clean refusal/failure before send — do not mark delivered, keep card undelivered.
          recordDeliveryFailure(root, c.id, reason);
          return { exitCode: res.exitCode || 1, stderr: reason };
        } finally {
          releaseDeliveryLease(root, c.id, acquired.lease.nonce);
        }
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
