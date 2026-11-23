# Quick decision service

One service per server answers short questions about a text in seconds: a choice from a list, yes/no, a score.
Agents, the Telegram bridge (message reactions) and plugins call one command; none of them knows the provider or
needs a login of its own.

```sh
echo '{"input":"…","questions":[{"type":"choice","name":"reaction","instructions":"…",
  "choices":[{"value":"👍","description":"agreed"},{"value":"😁","description":"funny"}]}]}' | vallis-decide --timeout 6
# {"answers":[{"name":"reaction","type":"choice","choice":"😁"}],"provider":"openai-decisions","ms":420}
```

The request has the shape of the OpenAI Decisions API (`predicate`, `choice`, `score` questions). No answer
(no service, no provider ready, timeout) — exit code 1: the caller keeps its default.

## Providers

Cascade in the order of the organization file; the first ready provider answers, an error or a timeout moves on.

| Provider | Model | Credential — of the service itself |
|---|---|---|
| `openai-decisions` (default) | `gpt-6-luna` via `POST /v1/decisions` | API key: `install.sh --openai-key-from <file> --openai-consumers <accounts>`; used only for those accounts and only for that address |
| `claude` | `claude-haiku-5-5` via `claude -p` | own login: `sudo -u bb-decide -H claude` |
| `codex` | `gpt-6-luna` via `codex exec` | own login: `sudo -u bb-decide -H codex login --device-auth` |

The service learns the calling account from the kernel (Unix socket, SO_PEERCRED); the key is used only for the
accounts listed in `--openai-consumers`. Logins of the owner, employees or agents are never copied to the service.

The service keeps its own spending limit for the key (the provider's hard limit may not be available): input tokens
per month at the configured price (`DECIDE_OPENAI_MONTH_USD`, default $10; `DECIDE_OPENAI_PRICE_PER_MTOK`, default
$0.10) and calls per day (`DECIDE_OPENAI_DAY_CALLS`, default 5000) in `/etc/bb-decide/decide.env`. Over a limit the
provider is off until the period ends and the cascade goes on; the state says "monthly spending limit reached" or
"daily call limit reached". The counter (`/var/lib/bb-decide/usage.json`) holds numbers only. Without any credential the state is
`needs_setup`.

```yaml
decide:
  providers: [openai-decisions, claude, codex]
```

## Install

```sh
deploy/decide/install.sh --organization <organization repository>/organization.yml
```

A local socket (/run/bb-decide.sock) starts the service on the first request; it exits after 5 idle minutes.
The service never stores or logs the request text: the log keeps the provider, the duration and the outcome.
State: `/var/lib/bb-decide/state.json` and `setup.py tools status`. `install.sh remove [--purge]` removes it.
