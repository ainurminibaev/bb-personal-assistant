import type { BbPluginApi } from "@get-bb/plugin-sdk";
import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

/** The server's speech service router (deploy/speech): Codex first, local models as the fallback. */
export const VOICE_TO_TEXT = process.env.BB_VOICE_TO_TEXT || "/usr/local/bin/voice-to-text";
export const SERVER_SPEECH_ID = "server-speech";

const EXT: Record<string, string> = { "audio/ogg": ".ogg", "audio/webm": ".webm", "audio/mp4": ".m4a", "audio/mpeg": ".mp3",
  "audio/wav": ".wav", "audio/x-wav": ".wav" };

/** One transcription through the router; a failure or an empty text throws (BB then reports it as usual). */
export async function transcribeWithServerSpeech(audio: Blob & { name?: string }, signal?: AbortSignal,
  router = VOICE_TO_TEXT): Promise<string> {
  const dir = await mkdtemp(join(tmpdir(), "bb-speech-"));
  const ext = (audio.name && /\.[a-z0-9]{2,5}$/i.exec(audio.name)?.[0]) || EXT[audio.type.split(";")[0]] || ".ogg";
  const file = join(dir, "voice" + ext);
  try {
    await writeFile(file, Buffer.from(await audio.arrayBuffer()), { mode: 0o600 });
    const text = await new Promise<string>((resolve, reject) =>
      execFile(router, [file], { timeout: 600_000, maxBuffer: 1 << 20, signal },
        (error, stdout) => error ? reject(new Error("speech_service_failed")) : resolve(String(stdout).trim())));
    if (!text) throw new Error("speech_service_empty");
    return text;
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}

/**
 * BB's own voice transcription (`bb voice transcribe`, the BB microphone, the bridge without a router path) goes
 * through the server's speech service when it is installed; the installer selects this service for voice.
 */
export function registerServerSpeech(bb: BbPluginApi) {
  const ai = (bb as unknown as { experimental_aiServices?: { register(d: unknown): { dispose(): void } } }).experimental_aiServices;
  if (!ai) return;
  ai.register({
    id: SERVER_SPEECH_ID,
    displayName: "Служба речи сервера",
    transcribe: (audio: File, options: { signal: AbortSignal }) => transcribeWithServerSpeech(audio, options.signal),
    status: async () => existsSync(VOICE_TO_TEXT) ? { ready: true as const }
      : { ready: false as const, message: "the server speech service is not installed (deploy/speech/install.sh)" },
  });
}
