import { closeSync, existsSync, mkdirSync, openSync, readFileSync, renameSync, statSync, unlinkSync, writeFileSync } from "node:fs";
import { join } from "node:path";

/**
 * Case card (С65, С70): the memory of a tracked task on disk, next to the installation, not in a tracker or a chat.
 * local/cases/<id>.md — fields in a short head and the last journal lines; local/cases/index.md — one line per case.
 * Written only through `bb personal-assistant case …`: the card and its index line change together, under a lock,
 * through a temporary file and a rename, so two subthreads never overwrite each other and a reader never sees half.
 * Owner, 24.11 (by the coordinator): cases live only in local/ of the installation; closed lines stay 90 days in the
 * index, then move to index-archive.md; a chat is restarted by the owner or an organization administrator.
 */

export const STATES = ["open", "waiting", "decision", "done_unverified", "undelivered", "closed"] as const;
export type CaseState = typeof STATES[number];

export interface DeliveryTarget {
  chat?: string;           // target group chatId or title (for --chat)
  topic?: string;          // target forum topicId or name (for --topic)
  person?: string;         // target private chat: userId, "owner", or name (for --person)
  replyTo?: string;        // source messageId to reply to (for --reply-to)
}

export interface DeliveryRecord {
  at: string;              // ISO timestamp of confirmed delivery
  messageId?: number;      // Telegram messageId returned by the bridge
  target: string;          // human-readable destination
  byThread?: string;       // BB threadId that ran the delivery
}

export interface Card {
  id: string;
  title: string;
  state: CaseState;
  opened: string;
  since: string;
  chat: string;            // the conversation the case belongs to (see chatKey in server.ts)
  place: string;           // where it was asked, in words: «группа «…», тема …»
  by: string;              // who asked
  message: string;         // the asking message, if known
  thread: string;          // the case's subthread
  next: string;            // the next step in words
  waiting: { who?: string; what?: string; back?: string } | null;
  schedules: string[];     // BB schedules of this case
  promises: string[];      // what was promised to people, with dates
  places: string[];        // links to spaces (tracker task, document, branch)
  people: string[];
  deliver: DeliveryTarget | null;    // promised answer destination (step 1 mycelium)
  delivered: DeliveryRecord | null;  // confirmed delivery receipt from the bridge
  result: string;
  journal: string[];       // last movements, newest last
}

const JOURNAL_KEEP = 10;
const ARCHIVE_DAYS = 90;
const FIELDS: (keyof Card)[] = ["id", "title", "state", "opened", "since", "chat", "place", "by", "message", "thread",
  "next", "waiting", "schedules", "promises", "places", "people", "deliver", "delivered", "result"];

export function normalizeDeliveryTarget(d: DeliveryTarget | null | undefined): DeliveryTarget | null {
  if (!d) return null;
  const chat = d.chat?.trim() || undefined;
  const topic = d.topic?.trim() || undefined;
  const person = d.person?.trim() || undefined;
  const replyTo = d.replyTo?.trim() || undefined;
  if (!chat && !topic && !person && !replyTo) return null;
  // Never store both person and chat/topic: person targets a private chat, chat/topic targets a group.
  if (person) return { person, ...(replyTo ? { replyTo } : {}) };
  return { ...(chat ? { chat } : {}), ...(topic ? { topic } : {}), ...(replyTo ? { replyTo } : {}) };
}

export function formatDeliveryTarget(d: DeliveryTarget | null | undefined): string {
  if (!d) return "";
  const parts: string[] = [];
  if (d.person) parts.push(`личка: ${d.person}`);
  else {
    if (d.chat) parts.push(`чат ${d.chat}`);
    if (d.topic) parts.push(`тема ${d.topic}`);
  }
  if (d.replyTo) parts.push(`ответ на #${d.replyTo}`);
  return parts.join(", ");
}

export const casesDir = (root: string) => join(root, "local", "cases");

/** The card file: a head of `key: <JSON>` lines between `---`, then the journal. JSON values are valid YAML too. */
export function serialize(card: Card): string {
  const head = FIELDS.map(k => `${k}: ${JSON.stringify(card[k] ?? null)}`).join("\n");
  return `---\n${head}\n---\n${card.journal.slice(-JOURNAL_KEEP).join("\n")}\n`;
}

export function parse(text: string): Card {
  const m = /^---\n([\s\S]*?)\n---\n?([\s\S]*)$/.exec(text);
  if (!m) throw new Error("not a case card");
  const card = { journal: [] } as unknown as Card & Record<string, unknown>;
  for (const line of m[1].split("\n")) {
    const i = line.indexOf(": ");
    if (i > 0) card[line.slice(0, i)] = JSON.parse(line.slice(i + 2));
  }
  card.journal = m[2].split("\n").filter(Boolean);
  for (const k of ["schedules", "promises", "places", "people"] as const) card[k] = Array.isArray(card[k]) ? card[k] : [];
  // Backward compatibility with existing case cards on disk written before 0.4.14.
  card.deliver = card.deliver && typeof card.deliver === "object" ? normalizeDeliveryTarget(card.deliver as DeliveryTarget) : null;
  card.delivered = card.delivered && typeof card.delivered === "object" ? card.delivered : null;
  return card;
}

const clean = (s: string) => (s ?? "").replace(/[|\n\r]+/g, " ").trim();

/** The index line: found with grep without reading cards. */
export function indexLine(c: Card): string {
  const next = c.waiting?.who ? `ждём: ${c.waiting.who}${c.waiting.back ? ` до ${c.waiting.back}` : ""}` : c.next;
  return [c.id, c.state, c.place || c.chat, c.by, c.title, next, c.since.slice(0, 10), c.chat].map(clean).join(" | ");
}

export interface IndexRow { id: string; state: string; place: string; by: string; title: string; next: string; date: string; chat: string }

export function parseIndex(text: string): IndexRow[] {
  return text.split("\n").filter(l => l.includes(" | ")).map(l => {
    const [id, state, place, by, title, next, date, chat] = l.split(" | ").map(s => s.trim());
    return { id, state, place, by, title, next, date, chat: chat ?? "" };
  });
}

export function slug(title: string): string {
  const map: Record<string, string> = { а: "a", б: "b", в: "v", г: "g", д: "d", е: "e", ё: "e", ж: "zh", з: "z", и: "i", й: "i", к: "k", л: "l", м: "m",
    н: "n", о: "o", п: "p", р: "r", с: "s", т: "t", у: "u", ф: "f", х: "h", ц: "c", ч: "ch", ш: "sh", щ: "sch", ы: "y", э: "e", ю: "yu", я: "ya" };
  const s = title.toLowerCase().split("").map(ch => map[ch] ?? ch).join("").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  return s.split("-").slice(0, 4).join("-") || "delo";
}

const stamp = (now: Date) => now.toISOString().slice(0, 16).replace("T", " ");

/** One writer at a time: an exclusive lock file; a lock older than 30 s is a crashed writer and is taken over. */
function withLock<T>(dir: string, fn: () => T): T {
  mkdirSync(dir, { recursive: true, mode: 0o700 });
  const lock = join(dir, ".lock");
  for (let i = 0; ; i++) {
    try { closeSync(openSync(lock, "wx")); break; } catch (e) {
      if ((e as NodeJS.ErrnoException).code !== "EEXIST") throw e;
      try { if (Date.now() - statSync(lock).mtimeMs > 30_000) unlinkSync(lock); } catch { /* gone meanwhile */ }
      if (i > 200) throw new Error("the cases folder is busy, try again");
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 25);
    }
  }
  try { return fn(); } finally { try { unlinkSync(lock); } catch { /* already gone */ } }
}

function atomicWrite(path: string, text: string) {
  const tmp = `${path}.${process.pid}.tmp`;
  writeFileSync(tmp, text, { mode: 0o600 });
  renameSync(tmp, path);
}

export function readCard(root: string, id: string): Card {
  if (!/^[a-z0-9-]+$/.test(id)) throw new Error(`no such case: ${id}`);
  const path = join(casesDir(root), `${id}.md`);
  if (!existsSync(path)) throw new Error(`no such case: ${id}`);
  return parse(readFileSync(path, "utf8"));
}

export function readIndex(root: string): IndexRow[] {
  const path = join(casesDir(root), "index.md");
  return existsSync(path) ? parseIndex(readFileSync(path, "utf8")) : [];
}

/** Write the card and its index line together; closed lines older than 90 days move to index-archive.md. */
function save(root: string, card: Card, now: Date) {
  const dir = casesDir(root);
  atomicWrite(join(dir, `${card.id}.md`), serialize(card));
  const path = join(dir, "index.md");
  const lines = existsSync(path) ? readFileSync(path, "utf8").split("\n").filter(l => l.includes(" | ")) : [];
  const rest = lines.filter(l => l.split(" | ")[0].trim() !== card.id);
  const old = (l: string) => {
    const r = parseIndex(l)[0];
    return r.state === "closed" && now.getTime() - new Date(r.date).getTime() > ARCHIVE_DAYS * 86_400_000;
  };
  const archived = rest.filter(old);
  if (archived.length) {
    const ap = join(dir, "index-archive.md");
    atomicWrite(ap, (existsSync(ap) ? readFileSync(ap, "utf8") : "") + archived.join("\n") + "\n");
  }
  atomicWrite(path, [...rest.filter(l => !old(l)), indexLine(card)].join("\n") + "\n");
}

export interface OpenArgs { title: string; chat: string; place?: string; by?: string; message?: string; thread?: string;
  next?: string; promise?: string; schedule?: string; space?: string; deliver?: DeliveryTarget | null }

export function openCase(root: string, a: OpenArgs, now = new Date()): Card {
  if (!a.title?.trim()) throw new Error("--title is required");
  return withLock(casesDir(root), () => {
    const base = `c-${now.toISOString().slice(0, 10).replace(/-/g, "")}-${slug(a.title)}`;
    let id = base;
    for (let n = 2; existsSync(join(casesDir(root), `${id}.md`)); n++) id = `${base}-${n}`;
    const card: Card = { id, title: a.title.trim(), state: "open", opened: now.toISOString(), since: now.toISOString(),
      chat: a.chat, place: a.place ?? "", by: a.by ?? "", message: a.message ?? "", thread: a.thread ?? "",
      next: a.next ?? "", waiting: null, schedules: a.schedule ? [a.schedule] : [], promises: a.promise ? [a.promise] : [],
      places: a.space ? [a.space] : [], people: a.by ? [a.by] : [], deliver: normalizeDeliveryTarget(a.deliver),
      delivered: null, result: "", journal: [`${stamp(now)} принято`] };
    save(root, card, now);
    return card;
  });
}

export interface SetArgs { state?: string; next?: string; thread?: string; waitingWho?: string; waitingWhat?: string;
  waitingBack?: string; noWaiting?: boolean; promise?: string; scheduleAdd?: string[]; scheduleRemove?: string[];
  space?: string; person?: string; deliver?: DeliveryTarget | null; clearDeliver?: boolean; note?: string; result?: string }

export function setCase(root: string, id: string, a: SetArgs, now = new Date()): Card {
  return withLock(casesDir(root), () => {
    const c = readCard(root, id);
    if (a.state) {
      if (!(STATES as readonly string[]).includes(a.state)) throw new Error(`state is one of: ${STATES.join(", ")}`);
      if (a.state === "closed") throw new Error("close a case with `case close`: it also removes its schedules");
      c.state = a.state as CaseState;
    }
    if (a.next !== undefined) c.next = a.next;
    if (a.thread) c.thread = a.thread;
    if (a.noWaiting) c.waiting = null;
    if (a.waitingWho || a.waitingWhat || a.waitingBack) {
      c.waiting = { ...(c.waiting ?? {}), ...(a.waitingWho ? { who: a.waitingWho } : {}),
        ...(a.waitingWhat ? { what: a.waitingWhat } : {}), ...(a.waitingBack ? { back: a.waitingBack } : {}) };
      if (c.state === "open") c.state = "waiting";
    }
    if (a.promise) c.promises.push(a.promise);
    for (const s of a.scheduleAdd ?? []) if (!c.schedules.includes(s)) c.schedules.push(s);
    if (a.scheduleRemove?.length) c.schedules = c.schedules.filter(s => !a.scheduleRemove!.includes(s));
    if (a.space && !c.places.includes(a.space)) c.places.push(a.space);
    if (a.person && !c.people.includes(a.person)) c.people.push(a.person);
    if (a.clearDeliver) c.deliver = null;
    else if (a.deliver) {
      const prev = c.deliver ?? {};
      const merged: DeliveryTarget = a.deliver.person
        ? { person: a.deliver.person, replyTo: a.deliver.replyTo !== undefined ? a.deliver.replyTo : prev.replyTo }
        : (a.deliver.chat !== undefined || a.deliver.topic !== undefined)
          ? { chat: a.deliver.chat !== undefined ? a.deliver.chat : prev.chat,
              topic: a.deliver.topic !== undefined ? a.deliver.topic : prev.topic,
              replyTo: a.deliver.replyTo !== undefined ? a.deliver.replyTo : prev.replyTo }
          : { ...prev, ...a.deliver };
      c.deliver = normalizeDeliveryTarget(merged);
    }
    if (a.result !== undefined) c.result = a.result;
    c.since = now.toISOString();
    c.journal.push(`${stamp(now)} ${a.note ?? describe(a)}`);
    c.journal = c.journal.slice(-JOURNAL_KEEP);
    save(root, c, now);
    return c;
  });
}

function describe(a: SetArgs): string {
  const parts: string[] = [];
  if (a.state) parts.push(`состояние ${a.state}`);
  if (a.waitingWho) parts.push(`ждём ${a.waitingWho}`);
  if (a.scheduleAdd?.length) parts.push(`расписание ${a.scheduleAdd.join(", ")}`);
  if (a.promise) parts.push(`обещано: ${a.promise}`);
  if (a.deliver) parts.push(`адрес ответа: ${formatDeliveryTarget(a.deliver) || "обновлён"}`);
  if (a.clearDeliver) parts.push("адрес ответа очищен");
  if (a.next) parts.push(`дальше: ${a.next}`);
  return parts.join("; ") || "изменено";
}

/** Record a bridge-confirmed delivery in the case card and its journal. */
export function recordDelivery(root: string, id: string, rec: DeliveryRecord, now = new Date()): Card {
  return withLock(casesDir(root), () => {
    const c = readCard(root, id);
    c.delivered = rec;
    if (c.state === "undelivered") c.state = "open";
    c.since = now.toISOString();
    const msg = typeof rec.messageId === "number" ? `, msg ${rec.messageId}` : "";
    c.journal.push(`${stamp(now)} доставлено (${rec.target}${msg})`);
    c.journal = c.journal.slice(-JOURNAL_KEEP);
    save(root, c, now);
    return c;
  });
}

/** Record a failed/refused delivery attempt without marking the case delivered. */
export function recordDeliveryFailure(root: string, id: string, reason: string, now = new Date()): Card {
  return withLock(casesDir(root), () => {
    const c = readCard(root, id);
    if (c.state !== "closed") c.state = "undelivered";
    c.since = now.toISOString();
    const short = clean(reason).slice(0, 160) || "ошибка отправки";
    c.journal.push(`${stamp(now)} доставка не прошла: ${short}`);
    c.journal = c.journal.slice(-JOURNAL_KEEP);
    save(root, c, now);
    return c;
  });
}

/** Close: the card says closed with its result; the schedules to remove are returned to the caller. */
export function closeCase(root: string, id: string, result: string, now = new Date()): { card: Card; schedules: string[] } {
  return withLock(casesDir(root), () => {
    const c = readCard(root, id);
    const schedules = c.schedules;
    c.state = "closed";
    c.result = result || c.result;
    c.waiting = null;
    c.schedules = [];
    c.since = now.toISOString();
    c.journal.push(`${stamp(now)} закрыто${result ? `: ${result}` : ""}`);
    c.journal = c.journal.slice(-JOURNAL_KEEP);
    save(root, c, now);
    return { card: c, schedules };
  });
}

/** Cases of one conversation; open only unless asked. */
export function listCases(root: string, chat: string | null, withClosed = false): IndexRow[] {
  return readIndex(root).filter(r => (chat === null || r.chat === chat) && (withClosed || r.state !== "closed"));
}

export function findCases(root: string, chat: string | null, words: string): IndexRow[] {
  const ws = words.toLowerCase().split(/\s+/).filter(Boolean);
  return readIndex(root).filter(r => (chat === null || r.chat === chat)
    && ws.every(w => `${r.title} ${r.next} ${r.by} ${r.place}`.toLowerCase().includes(w)));
}

/**
 * Every open case has its next step on the calendar or a person it waits for with a date; its schedules exist and
 * are on; a promised or awaited date has not passed unnoticed.
 */
export function checkCases(root: string, chat: string | null, liveSchedules: ReadonlySet<string>, now = new Date()): string[] {
  const out: string[] = [];
  for (const r of listCases(root, chat)) {
    const c = readCard(root, r.id);
    const gone = c.schedules.filter(s => !liveSchedules.has(s));
    if (gone.length) out.push(`${c.id} «${c.title}»: расписания нет или выключено — ${gone.join(", ")}; поставь следующий шаг заново`);
    const alive = c.schedules.length > gone.length;
    if (!alive && !c.waiting?.back) out.push(`${c.id} «${c.title}»: нет ни расписания следующего шага, ни ожидания с датой`);
    if (c.waiting?.back && new Date(c.waiting.back).getTime() < now.getTime())
      out.push(`${c.id} «${c.title}»: срок ожидания ${c.waiting.back} прошёл — напомни или реши`);
  }
  return out;
}

/** One line for a new conversation's instructions, read from disk synchronously; nothing when no case is open. */
export function openCasesHint(root: string | undefined): string | null {
  if (!root) return null;
  try {
    if (!readIndex(root).some(r => r.state !== "closed")) return null;
  } catch { return null; }
  return "У этой установки есть открытые дела (карточки на диске). В начале разговора выполни `bb personal-assistant case list --here` — "
    + "это дела этого чата; по делу, о котором речь, читай его карточку (`case show <id>`), не переспрашивай решённое и не повторяй "
    + "сделанное (журнал карточки); обещание с прошедшим сроком — выполни или честно напиши о задержке; затем `case check --here`.";
}

