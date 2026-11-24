# Installing an assistant or an employee

Run the installer from the extracted platform release, on the BB host, with the BB CLI on `PATH`. A clean server is
prepared first with `deploy/server-setup.sh`.

## Personal assistant

```
python3 setup.py --machine <BB machine ID> --workspace <private folder> --language en
```

Then pair the owner with the bot as described in [README.md](../README.md).

## Employee from an organization repository

```
python3 setup.py --machine <BB machine ID> --organization <git URL> --employee <name> \
  --workspace <folder for the copy> --provider codex
```

- The copy contains the root files, `shared/` and `employees/<name>/` only; the BB project is created in
  `employees/<name>` and named after the employee (or `--name`).
- The installer prints one line per difference between `employee.yml` / `organization.yml` and the installation.
- Adding the next employee: a folder in `employees/`, its name in `organization.yml`, push, and the same command with
  the new name and its own `--workspace`.

## Employee with its own repository

```
python3 setup.py --machine <BB machine ID> --kind employee --workspace-repo <git URL> --workspace <empty folder>
```

This is the single-employee case and keeps working.

## Update

Run the same command again. With `--organization`, the copy is updated with `git pull --ff-only`, skill links are
refreshed and the differences are printed again; a change in `shared/` reaches the employee this way. If the employee
has local commits that are not pushed, the installer stops and asks to push them first.

## Release candidates and mirrors

The installer expects the platform and the bridge from their release sources. To install from an already installed
release candidate or a local mirror, add `--keep-installed-sources`: the installed sources are kept as they are and only
their enabled state is checked.

## Rollback

Platform: reinstall the previous release through BB Settings → Updates. Organization copy:
`git -C <copy> reset --hard <previous commit>` and the same setup command. Memory, conversations and sign-ins are not in
the copy and are not touched by either.

## Tools

On the first run the installer sets up every line of `tools.yml` that this installation gets (pictures, context-mode,
the rules-compatibility skill and hook, the browser, the agent's own mail skill) and prints the state of each line:
installed, excluded, needs_login, needs_setup or error, with what to do. Nothing is skipped silently. The same later:
`python3 setup.py tools status [--json] --workspace <BB project folder>`. A plugin that this server cannot read from
its source can be installed from a copy: `--tool-source <id>=<path or git source>`. Format and levels:
[tools.md](tools.md).

An assistant gets the knowledge base plugin; an employee does not. An assistant's BB also tells every thread how to
reach the owner through the active assistant conversation (`ownerContact` setting of the platform plugin).

## Server services

Speech, the quick decision service and PDF are one per server and are set up by their own installers in `deploy/`
(as root, once per server); the assistant's or employee's setup uses them.

- **Voice of the bridge.** When the speech service is installed (`/usr/local/bin/voice-to-text`), setup points the
  bridge at it (`voiceRouterPath`); a working custom router is kept.
- **Speech router update.** A new release's router (formats, retries) without touching models, ports or units:
  `sudo deploy/speech/install.sh router`.
- **Quick decision key from another server.** The key goes from that server's service straight into this one, with no
  copy on disk: `ssh <server> 'sudo cat /var/lib/bb-decide/openai.key' | sudo deploy/decide/install.sh
  --openai-key-from - --openai-consumers <accounts>`. See [decide.md](decide.md).
- **PDF for one account.** `deploy/pdf/install.sh --prefix <home>/vallis-pdf` installs into that account's folder
  without root (a stand account, a second employee on the same server); put `<prefix>/bin` on that agent's PATH. The
  server-wide default is `/opt/vallis-pdf` with commands in `/usr/local/bin`.
- **Credentials store.** `vallis-secret` with `age` and `sops` comes with setup from their pinned releases; no system
  package is needed.
- **After a plugin update.** `bb personal-assistant tools` checks the tools again when its saved check is older than the
  loaded plugin; no BB restart is needed for the platform plugin's background work.

## Google Drive, Docs and Sheets

As the agent's account, with the OAuth app keys in its credentials store (`GOOGLE_OAUTH_CLIENT_ID`,
`GOOGLE_OAUTH_CLIENT_SECRET`): `deploy/google/google-connect link --email <agent's address>` prints the consent link.
A person signs in to the agent's Google account, presses «Allow» and sends back the address of the localhost page it
opens; put it in a file and run `deploy/google/google-connect finish <file>` at once (the code lives minutes). The
servers are workspace-mcp 2.5.0 and mcp-google-sheets 0.6.3, as on the owner's installation; `uv` must be on the
agent's PATH. If the app is in testing mode, the agent's address must be in its test users, and
`http://localhost:8765/` an allowed redirect URI of the client. Restart BB for running conversations.

## A second employee on the same server

- `deploy/server-setup.sh <user> <port>` with a free port pair: `<port>` for the server, `<port>+1` for BB's host
  daemon (the first BB keeps 38886/38887).
- Without access to the organization's GitHub yet: give `--organization` a local copy (a git bundle cloned bare into
  the employee's home) and switch the copy's origin to GitHub when the access is given.
- The server's local Telegram Bot API (one per server): the employee's account gets only passage (`--x`) on its data
  folders and read access to its own bot's folder (ACL with defaults), never the group of that server; set
  `telegramApiRoot` and `telegramLocalRoot` of its bridge like the first employee's.
- Codex login: its own login is the default; a copy of another employee's `auth.json` works only on the owner's word
  and both must be checked after the next token refresh (about 8 days after the last one).
- Mail: an inbox of its own (AgentMail); the key goes into its credentials store.

## A confirmation code for the owner

`bb personal-assistant code-to-owner [--from <sender>] [--minutes N]` sends a code from the agent's own mailbox to the
owner's private chat only; the agent never sees it, a guest conversation cannot run it (С85).
