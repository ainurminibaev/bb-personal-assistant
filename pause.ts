import type { BbPluginApi } from "@get-bb/plugin-sdk";
import { z } from "zod";

/**
 * Pause of an employee (С94): the owner stops it with words in his private chat with it, and starts it again the same way.
 * BB's own admission checkpoint does the work (`message.dispatch`, «wait»): while paused every turn of this BB — group
 * and people's chats, schedule runs, subthreads — waits in BB's queue and runs after the start; nothing is dropped and
 * no data is touched. The owner's private chat with the bot passes, so «включи» reaches the employee.
 * Only the owner: the command runs in the owner's private chat (the bridge's binding of topic 0) or in a terminal of
 * the server (no thread — a person with access to the account).
 */
export interface PauseState { paused: boolean; since?: number; reason?: string; by?: string }

const bridgeStatus = z.object({ chatBindings: z.array(z.object({ topicId: z.number().optional(), threadId: z.string().nullish() }).passthrough()) }).passthrough();
const RECHECK_MS = 60_000;
const automationsList = z.array(z.object({ id: z.string(), name: z.string().optional(), enabled: z.boolean().optional(),
  trigger: z.object({ triggerType: z.string().optional() }).passthrough().optional(), nextRunAt: z.number().nullish(),
  lastRunThreadId: z.string().nullish(), execution: z.object({ targetThreadId: z.string().nullish() }).passthrough().optional() }).passthrough());

/** A schedule run that waited during a pause: when it first waited, which schedule, repeating or not, its period. */
export interface DelayedRun { at: number; automationId?: string; name: string; recurring: boolean; period: number | null }

/**
 * After «включи» a schedule run that waited (С94, coordinator 25.11): a repeating one that waited longer than its own
 * period is not run — its next run is already due, an old one would tell people stale things; a one-off runs, and its
 * agent gets a note that it was held by the pause since that moment, to decide whether it still matters.
 */
export function afterPause(run: DelayedRun, now: number): "skip" | "run-noted" | "run" {
  if (run.recurring && run.period !== null && now - run.at > run.period) return "skip";
  return run.recurring ? "run" : "run-noted";
}

const hhmm = (t: number) => new Date(t).toLocaleTimeString("ru-RU", { timeZone: "Europe/Moscow", hour: "2-digit", minute: "2-digit" })
  + " МСК " + new Date(t).toLocaleDateString("ru-RU", { timeZone: "Europe/Moscow", day: "2-digit", month: "2-digit" });

export function pauseControl(bb: BbPluginApi) {
  let state: PauseState = { paused: false };
  let owner: string | null = null;
  const ready = (async () => { state = (await bb.storage.kv.get<PauseState>("pause")) ?? { paused: false }; })().catch(() => {});

  async function ownerThread(): Promise<string | null> {
    const status = await bb.sdk.plugins.callRpc({ pluginId: "telegram-projects", method: "status", input: null, outputSchema: bridgeStatus })
      .catch(() => null);
    owner = status?.chatBindings.find(b => (b.topicId ?? 0) === 0 && b.threadId)?.threadId ?? null;
    return owner;
  }

  /** Which schedule a run belongs to, from the automations of its project (target thread or the run's own thread). */
  async function scheduleOf(projectId: string | undefined, threadId: string): Promise<DelayedRun> {
    const list = projectId ? await bb.sdk.plugins.callRpc({ pluginId: "automations", method: "automations_list", input: { projectId },
      outputSchema: automationsList }).catch(() => []) : [];
    const a = list.find(x => x.execution?.targetThreadId === threadId || x.lastRunThreadId === threadId);
    const recurring = a?.trigger?.triggerType === "schedule";
    const now = Date.now();
    return { at: now, automationId: a?.id, name: a?.name ?? "расписание", recurring,
      period: recurring && a?.nextRunAt && a.nextRunAt > now ? a.nextRunAt - now : null };
  }

  /** The decision for one dispatch: pass the owner's private chat, let everything else wait. */
  async function decide(ctx: { thread: { id: string }; parentThreadId?: string | null; originPluginId?: string | null; project?: { id: string } }) {
    await ready;
    const fromSchedule = ctx.originPluginId === "automations";
    const key = "pause-run:" + ctx.thread.id;
    if (!state.paused) {
      if (!fromSchedule) return { action: "proceed" as const };
      const run = await bb.storage.kv.get<DelayedRun>(key);
      if (!run) return { action: "proceed" as const };
      await bb.storage.kv.delete(key);
      const what = afterPause(run, Date.now());
      if (what === "skip") {
        const skipped = (await bb.storage.kv.get<string[]>("pause-skipped")) ?? [];
        await bb.storage.kv.set("pause-skipped", [...skipped, `«${run.name}» (ждал с ${hhmm(run.at)})`]);
        return { action: "reject" as const, message: "Сотрудник был на паузе: этот запуск ждал дольше своего периода и пропущен; следующий — по расписанию." };
      }
      if (what === "run-noted")
        void bb.sdk.threads.send({ threadId: ctx.thread.id, input: [{ type: "text", visibility: "agent-only", mentions: [],
          text: `Это запуск расписания «${run.name}», задержанный паузой сотрудника с ${hhmm(run.at)}. Реши, актуален ли он сейчас; если нет — скажи людям коротко или промолчи (NO_REPLY).` }] } as never)
          .catch(() => {});
      return { action: "proceed" as const };
    }
    // The owner's chat always passes: the binding may have moved (the assistant's pair switched threads), so a thread
    // that is not the remembered one asks the bridge again; work the owner started in his chat (its subthreads) too.
    const mine = (o: string | null) => !!o && (ctx.thread.id === o || ctx.parentThreadId === o);
    if (mine(owner) || mine(await ownerThread())) return { action: "proceed" as const };
    if (fromSchedule && !(await bb.storage.kv.get<DelayedRun>(key)))
      await bb.storage.kv.set(key, await scheduleOf(ctx.project?.id, ctx.thread.id));
    return { action: "wait" as const, reason: "Сотрудник на паузе по решению владельца; сообщение будет обработано после включения.",
      sendAt: Date.now() + RECHECK_MS };
  }

  async function command(sub: "pause" | "resume" | "status", threadId: string | undefined, reason?: string) {
    await ready;
    if (sub === "status")
      return { exitCode: 0, stdout: state.paused ? `на паузе с ${new Date(state.since ?? 0).toISOString()}${state.reason ? ` — ${state.reason}` : ""}` : "работает" };
    if (threadId) {
      const o = await ownerThread();
      if (threadId !== o)
        return { exitCode: 1, stderr: "Ставить на паузу и включать может только владелец: в своей личке с ботом сотрудника или в терминале сервера." };
    }
    state = sub === "pause" ? { paused: true, since: Date.now(), reason, by: threadId ? "owner-chat" : "terminal" } : { paused: false };
    await bb.storage.kv.set("pause", state);
    return { exitCode: 0, stdout: sub === "pause"
      ? "Пауза: в группах, личках людей и по расписаниям не отвечаю; сообщения ждут в очереди, данные целы. Личка владельца работает — «включи», чтобы продолжить."
      : "Включено: отложенные сообщения и расписания идут по очереди." };
  }

  /** One line to the owner a little after «включи», when runs were skipped; called by the plugin's minute schedule. */
  async function report(send: (text: string) => Promise<unknown>) {
    await ready;
    if (state.paused) return null;
    const skipped = (await bb.storage.kv.get<string[]>("pause-skipped")) ?? [];
    if (!skipped.length) return null;
    await bb.storage.kv.set("pause-skipped", []);
    const text = `После паузы пропущено запусков расписаний: ${skipped.length} — ${skipped.slice(0, 8).join("; ")}${skipped.length > 8 ? " и другие" : ""}. Следующие пойдут по расписанию.`;
    await send(text);
    return text;
  }

  return { decide, command, report, isPaused: () => state.paused, since: () => state.since ?? null, ready };
}
