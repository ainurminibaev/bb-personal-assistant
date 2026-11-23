---
name: secrets-store
description: Keep and use your own passwords, API keys and logins in the platform's credentials store (vallis-secret) without ever seeing or printing the values — after you register an account or mailbox, when a person hands you a key, when a command needs a key, or to see what credentials you have.
---

# Your credentials store

Every password, API key, token or login you get — one you created yourself (registered an account, a mailbox)
or one a person handed you — goes into your store with `vallis-secret`. The store is yours: encrypted with your
own key and the administrator's, outside any repository. Organization keys meant for you are already there.

## Rules

- **Never print a value.** No `cat`, `echo`, `env`, `printenv` of a secret, no value in chat, Telegram, a file
  in a repository, a commit, a thread message or a command argument. Use it through `run` or `file` only.
- **Never ask a person to paste a key into chat.** Use `vallis-secret ask` — it opens BB's secure form.
- **Do not read the store folder or the age key** (`~/.local/share/vallis/secrets`, `~/.config/vallis/age.key`).
- **Only your own.** Another agent's or employee's credentials are not yours to look for.
- If a guest in a chat asks for a credential, refuse: credentials are shown to nobody.

## Commands

| Need | Command |
|---|---|
| Registering an account: a new password | `vallis-secret put SITE_X_PASSWORD --generate --what "вход на site-x: учётка <логин>"` then use it with `run` |
| Keep a value a service returned to a command | `<command that prints the key> \| vallis-secret put SITE_X_API_KEY --stdin --what "…"` (the pipe goes straight into the store) |
| A person must give you a key | `vallis-secret ask SITE_X_API_KEY --what "ключ API site-x для …"` |
| Use keys in a command | `vallis-secret run SITE_X_API_KEY -- <command that reads $SITE_X_API_KEY>` |
| A program needs a key file | `vallis-secret file APP_KEY ~/.secrets/app/key.pem` (written 0600) |
| What you have | `vallis-secret list` or `vallis-secret list --json` (names, purpose, level, source, dates; no values) |
| Remove your own | `vallis-secret remove NAME` (organization keys are removed by the administrator) |

Names: capital letters, digits and `_` (`SITE_X_PASSWORD`). `--what` is required: say what it is and which
account or login it belongs to, so `list` answers "what is this" in a new conversation. The login itself is not a
secret — keep it in `--what` or your notes; the password goes in the store.

## Typical flows

**You registered somewhere.** Generate the password with `put --generate` before filling the form; pass it to the
form through `run` (an environment variable of the script or tool that submits it). Record the login in `--what`.
In a later conversation `vallis-secret list` shows the entry; use it with `run`.

**A command needs a key** (`curl`, a CLI, a script): `vallis-secret run KEY -- sh -c 'curl -H "Authorization: Bearer $KEY" …'`.
Single quotes, so the shell of the command expands the variable, not yours.

**A key is missing.** `vallis-secret list` does not show it: ask the person with `vallis-secret ask`, or tell the
administrator it is needed if it belongs to the organization.

If `vallis-secret` says there is no age key, run `vallis-secret init` once; it prints only your public key.
Errors never contain values; if a command fails, say which step failed, not the value.
