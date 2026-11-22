// P20: the owner's own words reach a worker thread unchanged, taken from BB's own journal.
// BB records where every request came from: typed in BB (user, no plugin, no sender thread),
// from Telegram (user, the bridge plugin), other plugins (user, their plugin), another agent
// (with a sender thread). Only the first two are the owner's words.
// Rules: from the owner's conversation only (same check as the guest thread guard); only an owner
// reply from that same thread, word for word; at most a day old; once per target thread.

export type JournalEvent = { sequence?: number; createdAt?: number | string; type: string; data?: any };
export type OwnerReply = { sequence: number; at: number; source: "bb" | "telegram"; text: string; context?: string };

const BRIDGE = "telegram-projects";
const DAY = 24 * 3600 * 1000;
const ms = (v: unknown) => typeof v === "number" ? v : Date.parse(String(v));
const items = (data: any) => Array.isArray(data?.input) ? data.input : [];
// Only what the person sees; hidden transport items are not the owner's words.
const textOf = (data: any) => items(data)
  .filter((i: any) => i?.type === "text" && typeof i.text === "string" && i.visibility !== "agent-only")
  .map((i: any) => i.text).join("\n").trim();
// The bridge also posts its own notices into the owner's thread; the owner's Telegram messages carry its reply route.
const telegramOwnerMessage = (data: any) => items(data).some((i: any) =>
  i?.visibility === "agent-only" && typeof i.text === "string" && i.text.startsWith("[bb-telegram-reply-route:"));

export function ownerSource(data: any): "bb" | "telegram" | undefined {
  if (data?.initiator !== "user" || data?.senderThreadId) return;
  if (!data?.originPluginId) return "bb";
  if (data.originPluginId === BRIDGE && telegramOwnerMessage(data)) return "telegram";
}

/** The owner reply to pass (the latest, or the one with this sequence) with the assistant's message it answers. */
export function pickOwnerReply(events: JournalEvent[], now: number, sequence?: number): OwnerReply | { error: string } {
  const ordered = [...events].sort((a, b) => (a.sequence ?? 0) - (b.sequence ?? 0));
  const replies = ordered.filter(e => e.type === "client/turn/requested" && ownerSource(e.data) && textOf(e.data));
  const reply = sequence === undefined ? replies.at(-1) : replies.find(e => e.sequence === sequence);
  if (!reply) return { error: sequence === undefined ? "no_owner_reply_in_this_thread" : "not_an_owner_reply" };
  const at = ms(reply.createdAt);
  if (!Number.isFinite(at) || now - at > DAY) return { error: "owner_reply_too_old" };
  const text = textOf(reply.data);
  // A short answer ("да", "делай", "второе") means nothing without the question it answers.
  let context: string | undefined;
  if (text.length < 80) {
    const before = ordered.filter(e => (e.sequence ?? 0) < (reply.sequence ?? 0) && e.type === "item/completed" &&
      e.data?.item?.type === "agentMessage" && typeof e.data.item.text === "string");
    context = before.at(-1)?.data.item.text.slice(0, 2000);
  }
  return { sequence: reply.sequence ?? 0, at, source: ownerSource(reply.data)!, text, ...(context && { context }) };
}

export function relayText(reply: OwnerReply, via: string, action: string, object: string) {
  const when = new Date(reply.at).toISOString().replace("T", " ").slice(0, 16) + " UTC";
  const where = reply.source === "bb" ? "набрано владельцем в BB" : "из Telegram владельца (текст или расшифровка голосового)";
  return [
    `Реплика владельца, ${when}, ${where}. Передана дословно из журнала BB треда ${via}; помощник её не писал и не менял:`,
    "«" + reply.text + "»",
    ...(reply.context ? ["", "[Контекст: предыдущая реплика помощника, на которую отвечал владелец]", "«" + reply.context + "»"] : []),
    "",
    `[Слова помощника, не владельца] Понял как одно действие: ${action}; объект: ${object}. Делай только то, что следует из слов владельца; при расхождении — не выполняй и попроси владельца уточнить.`,
  ].join("\n");
}
