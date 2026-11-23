---
name: google-workspace
description: Work with Google Drive, Docs and Sheets of the agent's own Google account through the google-workspace MCP, and get that account logged in when the tools list says needs_login — the login is a human step with the owner's yes.
---

# Google Drive, Docs and Sheets

The `google-workspace` MCP (workspace-mcp) gives you Drive, Docs and Sheets of **your own** Google account — not
a person's. Find files, read documents, read and edit sheets with its tools. Where things lie on the drive is
described in your spaces (`bb personal-assistant tools`, the space's description file).

## State

`bb personal-assistant tools` (or `python3 setup.py tools status`) shows the `google-workspace` line:

- `installed` — work.
- `needs_setup` — a program or the key of the organization's Google application is missing: tell the owner in one
  line; the administrator gives the key (`vallis-secret grant` / `receive`). Do not look for keys anywhere else.
- `needs_login` — your Google account is not logged in: follow «Login» below.
- `error` — say the reason in one line; a revoked or expired login is logged in again the same way.

## Login (a human step)

Never log in on your own and never use a person's Google account.

1. Ask the owner in one message: «Нужен вход в мой Google-аккаунт <адрес> для диска, документов и таблиц.
   Разрешаете?» Wait for a yes.
2. Call any tool of the MCP (for example, list files): it answers with a login link and waits for Google's return
   on its local port. Open that port for the time of the login: `bb connect expose <port>` (works when this BB is
   signed in to a bb account; `bb connect shares` shows the address). The organization's Google application must
   allow that address as its return address, and the person's browser must reach it; if not, use step 5.
3. Send the link to the person who holds your account's password — in a private chat, never in a group.
   They log in **as you** and allow Drive, Docs and Sheets.
4. When the MCP reports the login, close the address (`bb connect unexpose <port>`), check with a list of
   files, and keep a copy of the login in your store: `vallis-secret import GOOGLE_WORKSPACE_LOGIN --file <the
   new file in ~/.secrets/google-workspace> --what "вход Google-аккаунта <адрес>"`.
5. **If the return through the shared address does not work:** the person logs in on their own computer with the
   same application (the administrator gives them the steps), and the resulting login file comes to you as an
   organization key file: the administrator runs `vallis-secret grant GOOGLE_WORKSPACE_LOGIN --to <your public key>
   --what "…" --out <file> --stdin < <login file>`, you run
   `vallis-secret receive <file>` and `vallis-secret file GOOGLE_WORKSPACE_LOGIN ~/.secrets/google-workspace/<account>.json`.

## Do not

- Do not show the login link in a group, and never show codes, keys or the login file's content.
- Do not share documents outside the organization or change sharing settings unless the owner asked for it.
- Do not log in a second account for the same purpose.
