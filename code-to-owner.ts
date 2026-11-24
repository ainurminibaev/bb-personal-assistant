import { execFile } from "node:child_process";
import { readFile, readdir } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";

/**
 * С85: a confirmation code from the agent's own mailbox goes to the owner only, in his private chat — the agent never
 * sees it. `bb personal-assistant code-to-owner [--from TEXT] [--minutes N]`: the newest letter of the last N minutes
 * (from or subject containing TEXT), one unambiguous code in it, sent through the bridge to the owner's private chat.
 * The agent gets only «sent» or the reason; nothing goes to a group or to another person.
 */

const API = "https://api.agentmail.to/v0";

/** One code next to a code word («код», «code», «verification», «подтвержд»); several different — none. */
export function extractCode(text: string): string | undefined {
  const flat = text.replace(/\s+/g, " ");
  const found = new Set<string>();
  const word = /(код|code|verification|подтвержд)/gi;
  for (let m = word.exec(flat); m; m = word.exec(flat)) {
    const after = flat.slice(m.index, m.index + 160);
    const c = /\b(G-\d{4,8}|\d{4,8})\b/.exec(after.slice(m[0].length));
    if (c) found.add(c[1]);
  }
  return found.size === 1 ? [...found][0] : undefined;
}

function run(cmd: string, args: string[]): Promise<string> {
  return new Promise(resolve => execFile(cmd, args, { timeout: 20_000 }, (e, out) => resolve(e ? "" : String(out))));
}

async function mailKey(): Promise<string | undefined> {
  if (process.env.AGENTMAIL_API_KEY?.trim()) return process.env.AGENTMAIL_API_KEY.trim();
  const fromStore = (await run("vallis-secret", ["run", "AGENTMAIL_API_KEY", "--", "printenv", "AGENTMAIL_API_KEY"])).trim();
  if (fromStore) return fromStore;
  const file = await readFile(join(homedir(), ".secrets", "agentmail.key"), "utf8").catch(() => "");
  return file.trim().replace(/^AGENTMAIL_API_KEY=/, "") || undefined;
}

async function inboxId(key: string): Promise<string | undefined> {
  const dir = join(homedir(), ".secrets", "accounts");
  for (const f of await readdir(dir).catch(() => [] as string[])) {
    if (!/agentmail/i.test(f) || !f.endsWith(".json")) continue;
    const id = JSON.parse(await readFile(join(dir, f), "utf8").catch(() => "{}")).inbox_id;
    if (typeof id === "string" && id) return id;
  }
  const r = await fetch(`${API}/inboxes`, { headers: { Authorization: `Bearer ${key}` } }).catch(() => undefined);
  const inboxes = r?.ok ? ((await r.json()) as { inboxes?: { inbox_id: string }[] }).inboxes : undefined;
  return inboxes?.[0]?.inbox_id;
}

type Rpc = (method: string, input: unknown) => Promise<any>;

export async function codeToOwner(argv: string[], rpc: Rpc, now = Date.now()) {
  const opt = (name: string) => { const i = argv.indexOf(name); return i >= 0 ? argv[i + 1] : undefined; };
  const from = (opt("--from") ?? "").toLowerCase();
  const minutes = Math.min(120, Math.max(1, Number(opt("--minutes") ?? 30) || 30));
  const key = await mailKey();
  if (!key) return { exitCode: 1, stderr: "Нет ключа почты агента (AGENTMAIL_API_KEY): код достать нечем." };
  const inbox = await inboxId(key);
  if (!inbox) return { exitCode: 1, stderr: "Не найден ящик агента в почтовом сервисе." };
  const H = { Authorization: `Bearer ${key}` };
  const list = await fetch(`${API}/inboxes/${encodeURIComponent(inbox)}/messages?limit=20`, { headers: H }).catch(() => undefined);
  if (!list?.ok) return { exitCode: 1, stderr: "Почтовый сервис не ответил." };
  const messages = ((await list.json()) as { messages?: any[] }).messages ?? [];
  const at = (m: any) => Date.parse(m.timestamp ?? m.created_at ?? 0) || 0;
  // Newest first whatever order the mail service lists them in: an older code is already invalid.
  const fresh = messages.filter(m => now - at(m) <= minutes * 60_000)
    .filter(m => !from || `${m.from ?? ""} ${m.subject ?? ""}`.toLowerCase().includes(from))
    .sort((a, b) => at(b) - at(a));
  const letter = fresh[0];
  if (!letter) return { exitCode: 1, stderr: `За ${minutes} мин подходящего письма нет — попросите владельца запросить код заново.` };
  const full = await fetch(`${API}/inboxes/${encodeURIComponent(inbox)}/messages/${encodeURIComponent(letter.message_id)}`,
    { headers: H }).then(r => r.ok ? r.json() : {}).catch(() => ({})) as { text?: string; html?: string; subject?: string };
  const code = extractCode(`${full.subject ?? ""} ${full.text ?? (full.html ?? "").replace(/<[^>]+>/g, " ")}`);
  if (!code) return { exitCode: 1, stderr: "В письме нет однозначного кода — владельцу ничего не отправлено." };
  // The owner's private chat: the bridge's binding of topic 0. Only there.
  const status = await rpc("status", null).catch(() => undefined);
  const threadId = status?.chatBindings?.find((b: { topicId: number; threadId?: string }) => b.topicId === 0 && b.threadId)?.threadId;
  if (!threadId) return { exitCode: 1, stderr: "Нет лички с владельцем в мосте — отправить некуда." };
  const when = new Date(Date.parse(letter.timestamp ?? letter.created_at)).toLocaleTimeString("ru-RU",
    { timeZone: "Europe/Moscow", hour: "2-digit", minute: "2-digit" });
  const deliveryId = `code-to-owner-${letter.message_id}`.replace(/[^a-zA-Z0-9_-]/g, "").slice(0, 100);
  await rpc("sendToTelegram", { threadId, topicId: 0, explicit: true, deliveryId,
    text: `Код подтверждения из моей рабочей почты: ${code} (письмо «${String(letter.subject ?? "").slice(0, 80)}», ${when} МСК).\n\nТолько вам — по вашей просьбе.` });
  return { exitCode: 0, stdout: `Код отправлен владельцу в личку (письмо ${when} МСК). Сам код в этот разговор не выводится.` };
}
