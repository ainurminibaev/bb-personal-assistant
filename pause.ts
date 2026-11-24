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

  /** The decision for one dispatch: pass the owner's private chat, let everything else wait. */
  async function decide(ctx: { thread: { id: string }; parentThreadId?: string | null }) {
    await ready;
    if (!state.paused) return { action: "proceed" as const };
    // The owner's chat always passes: the binding may have moved (the assistant's pair switched threads), so a thread
    // that is not the remembered one asks the bridge again; work the owner started in his chat (its subthreads) too.
    const mine = (o: string | null) => !!o && (ctx.thread.id === o || ctx.parentThreadId === o);
    if (mine(owner) || mine(await ownerThread())) return { action: "proceed" as const };
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

  return { decide, command, isPaused: () => state.paused, ready };
}
