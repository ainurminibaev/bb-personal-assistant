import type { BbPluginApi } from "@get-bb/plugin-sdk";
import { z } from "zod";

/**
 * How any thread of the assistant's BB reaches the owner in Telegram: through the conversation of the
 * personal assistant that is active right now (the bridge's private chat, topic 0).
 *
 * BB asks for the instruction synchronously at thread start, so the thread ID is kept in memory only:
 * read from the bridge at load and refreshed in the background; nothing is written to disk. If a `tell`
 * misses after a switch of the pair, `bb personal-assistant owner-thread` asks the bridge at that moment.
 */
const bridgeStatus = z.object({
  chatBindings: z.array(z.object({ topicId: z.number().optional(), threadId: z.string().nullish() }).passthrough()),
}).passthrough();

export function ownerContactText(threadId: string): string {
  return [
    "Как связаться с владельцем: нужно сообщить ему что-то или спросить его в Telegram — передай это действующему",
    `разговору личного помощника: \`bb thread tell ${threadId}\`. Первая строка — «Владельцу в Telegram от <проект и`,
    "тред>», один вопрос на сообщение. Ответ придёт в твой тред. Если tell не прошёл, свежий адрес даёт",
    "`bb personal-assistant owner-thread`.",
  ].join(" ");
}

/** The active assistant conversation, or null: not an assistant installation, no bridge, no private chat. */
export async function readOwnerThread(sdk: BbPluginApi["sdk"]): Promise<string | null> {
  try {
    const settings = await sdk.plugins.getSettings({ pluginId: "telegram-projects" }) as { values?: Record<string, unknown> };
    if ((settings.values?.installKind ?? "assistant") !== "assistant") return null;
    const status = await sdk.plugins.callRpc({ pluginId: "telegram-projects", method: "status", input: null, outputSchema: bridgeStatus });
    return status.chatBindings.find(b => b.topicId === 0 && b.threadId)?.threadId ?? null;
  } catch {
    return null; // the bridge is disabled, removed or not ready: no instruction
  }
}

export function ownerContact(bb: BbPluginApi, enabled: () => boolean, everyMs = 60_000) {
  let current: string | null = null;
  let running: Promise<string | null> | null = null;
  const refresh = () => running ??= readOwnerThread(bb.sdk)
    .then(id => (current = enabled() ? id : null))
    .finally(() => { running = null; });
  void refresh();
  const timer = setInterval(() => void refresh(), everyMs);
  timer.unref?.();
  bb.experimental_signal?.addEventListener?.("abort", () => clearInterval(timer));
  return {
    refresh,
    /** The instruction for one thread at its start; the assistant's own conversation gets none. */
    instructions(threadId: string): string | null {
      void refresh();
      return enabled() && current && current !== threadId ? ownerContactText(current) : null;
    },
  };
}
