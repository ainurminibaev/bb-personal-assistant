# Speech service

One speech-to-text service per server. The assistant, employees and agents of the server use the same copy of the
models; the Telegram bridge and agents call one command, `voice-to-text <audio-file> [--lang auto|ru|tt|en|ar]`.

## Order

| Order | What happens |
|---|---|
| `codex-first` (default) | Codex cloud transcription with the caller's own Codex login; on an error, empty text or no login — local models. Tatar speech returned in Latin script goes to the multilingual model |
| `local-only` | local models only, audio never leaves the server |

Local models: with both profiles the language is detected first (Russian ≥ 0.60 → Russian model, else multilingual);
with one profile — that model. `--lang tt|ar` goes straight to the multilingual model.

## Profiles

| Profile | Model | Disk | Memory while working |
|---|---|---|---|
| `ru` (default) | GigaAM v3 e2e CTC (Sber, MIT), ONNX int8 + language ID VoxLingua107 (SpeechBrain, Apache-2.0) | ~0.5 GB | up to 0.8 GB |
| `multi` | omniASR CTC 300M v2 (Apache-2.0), int8 — Tatar, English, Arabic and others | ~1.6 GB | up to 1.5 GB |

Models start on the first request (systemd socket on loopback) and exit when idle (Russian after 10 minutes,
multilingual after 2): no memory is used at rest. The first request after a pause waits for the model to load
(Russian ~4 s, multilingual ~25 s).

## Install

On the server, as root:

```sh
deploy/speech/install.sh --organization <organization repository>/organization.yml
```

The organization file chooses the profiles and the order; the language of the organization is the default
language. Options `--profiles`, `--order`, `--lang`, `--ru-port`, `--multi-port` override it.

```yaml
language: ru
speech:
  profiles: [ru, multi]
  order: codex-first
```

Models are downloaded from their original sources (Hugging Face) at pinned revisions and checked by checksum; the
Russian models are converted to ONNX once in a temporary environment that is removed afterwards. Without network,
or when a source is unreachable, the service gets the state `needs_setup` with the reason — the installer does not
fail. The state is in `/var/lib/bb-speech/state.json` and in `setup.py tools status`.

Then point the bridge at the router: setting "voice router path" = `/usr/local/bin/voice-to-text`. Agents under
their own accounts need the model ports allowed in their firewall (`install-agent.sh --allow-port`).

`install.sh status` prints the state; `install.sh remove [--purge]` removes the service (with `--purge` — models
and settings too).
