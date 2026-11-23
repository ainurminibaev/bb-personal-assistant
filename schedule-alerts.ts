import type { BbPluginApi } from "@get-bb/plugin-sdk";
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { z } from "zod";
import { readOwnerThread } from "./owner-contact";

/**
 * The guest guard never refuses a schedule (owner, 23.11). When one conversation passes the thresholds it writes
 * one line next to itself (.bb/hooks/guest-thread-guard-alerts.jsonl); here every new "over" line becomes one
 * message to the owner: through the assistant's conversation where there is one, otherwise a BB notification.
 */
export interface Alert { at: number; conversation: string; state: "over" | "below"; pending: number; perMinute: number }

export function newAlerts(text: string, seen: number): { alerts: Alert[]; lines: number } {
  const lines = text.split("\n").filter(Boolean);
  const alerts: Alert[] = [];
  // A file that was cleared or rotated starts over.
  for (const line of lines.slice(seen > lines.length ? 0 : seen)) {
    try {
      const a = JSON.parse(line) as Alert;
      if (a.state === "over" && typeof a.conversation === "string") alerts.push(a);
    } catch { /* a torn line is skipped */ }
  }
  return { alerts, lines: lines.length };
}

export function alertText(title: string, a: Alert): string {
  return `Владельцу в Telegram от платформы: в разговоре «${title}» ожидают ${a.pending} расписаний` +
    (a.perMinute > 1 ? `, за последнюю минуту поставлено ${a.perMinute}` : "") +
    ". Ничего не запрещено — проверь, нужно ли столько; снять лишние может сам разговор (bb automation delete).";
}

export async function deliverScheduleAlerts(bb: BbPluginApi, roots: readonly string[]): Promise<number> {
  let sent = 0;
  for (const root of roots) {
    const file = join(root, ".bb", "hooks", "guest-thread-guard-alerts.jsonl");
    if (!existsSync(file)) continue;
    const key = `schedule-alerts:${root}`;
    const seen = (await bb.storage.kv.get<number>(key)) ?? 0;
    const { alerts, lines } = newAlerts(readFileSync(file, "utf8"), lines0(seen));
    for (const a of alerts) {
      const thread = await bb.sdk.threads.get({ threadId: a.conversation }).catch(() => null) as { title?: string | null } | null;
      const text = alertText(thread?.title ?? a.conversation, a);
      const owner = await readOwnerThread(bb.sdk);
      if (owner) await bb.sdk.threads.send({ threadId: owner, input: [{ type: "text", text }] } as never);
      else await bb.sdk.plugins.callRpc({ pluginId: "push-notifications", method: "notifications.send",
        input: { title: "Расписания в гостевом разговоре", body: text }, outputSchema: z.unknown() });
      sent++;
    }
    await bb.storage.kv.set(key, lines);
  }
  return sent;
}

function lines0(n: number) { return Number.isInteger(n) && n >= 0 ? n : 0; }
