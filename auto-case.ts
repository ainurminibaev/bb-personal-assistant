import type { BbPluginApi } from "@get-bb/plugin-sdk";
import { z } from "zod";
import { chatOf } from "./case-command";
import { openCase, readCard, readIndex, setCase } from "./cases";

/**
 * A case opens without the agent's will (С65, coordinator 25.11). Once a minute the platform plugin looks at the
 * schedules created in the last day: one created by a Telegram conversation (or its subthread) that no case card holds
 * becomes a case — opened by the plugin, the schedule recorded in it, and the creating thread told the case's id.
 * A schedule named «<case id>: <step>» joins that case. Schedules not made from a Telegram chat (the installation's own:
 * cleanup, checks) stay as they are. Nothing runs a model.
 */
const automations = z.array(z.object({ id: z.string(), name: z.string().optional(), enabled: z.boolean().optional(),
  createdByThreadId: z.string().nullish(), createdAt: z.number().nullish(), nextRunAt: z.number().nullish(),
  trigger: z.object({ triggerType: z.string().optional(), runAt: z.number().nullish() }).passthrough().optional() }).passthrough());

const SEEN = "auto-case:seen";
const DAY = 86_400_000;

const when = (t: number | null | undefined) => t
  ? new Date(t).toLocaleString("ru-RU", { timeZone: "Europe/Moscow", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) + " МСК"
  : "по расписанию";

export async function autoCases(bb: BbPluginApi, root: string | undefined, now = Date.now()): Promise<string[]> {
  if (!root) return [];
  const seen = new Set((await bb.storage.kv.get<string[]>(SEEN)) ?? []);
  const held = new Set(readIndex(root).flatMap(r => { try { return readCard(root, r.id).schedules; } catch { return []; } }));
  const opened: string[] = [];
  const projects = (await bb.sdk.projects.list({})) as { id: string }[];
  for (const p of projects) {
    const list = await bb.sdk.plugins.callRpc({ pluginId: "automations", method: "automations_list", input: { projectId: p.id },
      outputSchema: automations }).catch(() => []);
    for (const a of list) {
      if (seen.has(a.id) || a.enabled === false || !a.createdByThreadId || (a.createdAt ?? now) < now - DAY) continue;
      seen.add(a.id);
      if (held.has(a.id)) continue;
      const chat = await chatOf(bb.sdk, a.createdByThreadId).catch(() => "");
      if (!chat.startsWith("tg:")) continue;   // not made from a Telegram chat: the installation's own schedule
      const named = /^(c-\d{8}-[a-z0-9-]+):/.exec(a.name ?? "")?.[1];
      let id: string;
      if (named && readIndex(root).some(r => r.id === named)) {
        setCase(root, named, { scheduleAdd: [a.id], note: `расписание «${a.name}» привязано само` });
        id = named;
      } else {
        const card = openCase(root, { title: (a.name ?? "Поручение").replace(/^c-[^:]+:\s*/, "").slice(0, 120), chat,
          thread: a.createdByThreadId, schedule: a.id, next: `запуск ${when(a.nextRunAt ?? a.trigger?.runAt)}` });
        id = card.id;
      }
      opened.push(id);
      void bb.sdk.threads.send({ threadId: a.createdByThreadId, input: [{ type: "text", visibility: "agent-only", mentions: [],
        text: `Расписание «${a.name ?? a.id}» привязано к делу ${id} (открыто само). Веди его: \`bb personal-assistant case show ${id}\`, `
          + `\`case set ${id} …\` при каждом событии, \`case close ${id} --result …\` когда итог доставлен. Людям об этом писать не нужно: ответь ровно NO_REPLY.` }] } as never)
        .catch(() => {});
    }
  }
  await bb.storage.kv.set(SEEN, [...seen].slice(-2000));
  return opened;
}
