import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

/**
 * С116: what kind of conversation this is — the owner's own or a guest one (a group, a person's private chat).
 * The same decision the guest guard makes, from BB's and the bridge's records, never from a signature in the turn:
 * the guard next to the project knows the BB data folder (setup.py writes it into guest-thread-guard.json).
 */
export type ConversationKind = "owner" | "guest";

export function conversationKind(guard: string, folder: string | undefined, threadId: string | undefined,
                                 run: (args: string[]) => string = args => execFileSync("python3", args, { encoding: "utf8", timeout: 5000 })): ConversationKind | null {
  if (!folder || !threadId || !existsSync(guard)) return null;
  let dataDir = "";
  try { dataDir = JSON.parse(readFileSync(join(folder, ".bb", "hooks", "guest-thread-guard.json"), "utf8")).dataDir ?? ""; }
  catch { return null; }
  if (!dataDir) return null;
  try {
    const out = run([guard, "--thread", threadId, dataDir]).trim();
    return out === "owner" || out === "guest" ? out : null;
  } catch { return null; }
}

export function kindLine(kind: ConversationKind | null): string | null {
  if (kind === "owner") return "Вид разговора: свой разговор владельца — здесь о своём устройстве и возможностях отвечаешь владельцу по существу (С43, С116).";
  if (kind === "guest") return "Вид разговора: гостевой (группа, тема или личка не владельца) — о своём устройстве здесь не рассказываешь (С116).";
  return null;
}
