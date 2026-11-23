# Personal Agent for BB — a self-hosted AI assistant for Telegram

English · [Русский](README.ru.md)

Ask your assistant for a research brief on your phone. It can delegate the work to a Claude Code or Codex thread in the relevant BB project and send the finished PDF back to the same Telegram chat. Open that conversation in BB to inspect the work or continue it at your desk.

This is an early release for owners of a self-hosted BB instance. Start with a private chat and a test project.

## What you can do

- Send a document with a question and get the result back in Telegram.
- Continue work in an existing project thread. Keep the project registry and personal instructions on your own machine.
- Give the assistant a one-off research task without creating another project.

## Install on your BB host

These instructions apply to the 0.2 release line. For the older ZIP, use the [v0.1.9 instructions](https://github.com/ainurminibaev/bb-personal-assistant/blob/v0.1.9/README.md).

1. Start BB 0.49 or later with Git, Python 3 and a configured Claude Code or Codex provider. Get the installer from this assistant's [GitHub Releases](https://github.com/ainurminibaev/bb-personal-assistant/releases) and open the extracted directory on the BB host.
2. Run `bb machine list` to find the host ID. Choose a separate working directory for your registry, project files and personal instructions. Keep that directory between releases.

   ```sh
   python3 setup.py --language en --machine <host-id> --workspace ~/my-assistant
   ```

   Review the two release sources at the prompt. Setup installs the assistant and the Telegram bridge and registers the workspace as a BB project.
   The language option sets the Telegram menu on first installation. Running setup again preserves the language you selected in BB.

3. Open the project in BB and create the personal coordinator thread. Create your bot with BotFather. In BB's Telegram connection settings, save its token, enable sync and chat, and follow the owner pairing link.
4. Send `/project` in your private Telegram chat, choose the project, then select your coordinator under Chats. Run the same setup command once more so the assistant plugin can find the selected thread. Run `bb personal-assistant doctor` to check the project and thread IDs. Start a fresh provider session in that thread to load the new instructions.
5. Send “What projects can you work in?” from Telegram. With a new registry, the assistant should explain that it can do one-off work here and that you can authorize additional projects. Open the same conversation in BB and check that it contains the question and answer. Inside the coordinator, `bb personal-assistant work` lists its open child threads.

If you already use the original Telegram Projects plugin, check the [migration guide](BRIDGE-MIGRATION.md) before installation. Both bridges use the same BB plugin ID.

## Install a digital employee

A digital employee is the same platform with the `employee` setting: its own server user, bot, accounts and team rules. It answers every member of the groups it is added to and, after your approval, people in private chats.

1. On a fresh Ubuntu server, as root: `deploy/server-setup.sh <user> <port>`. It creates the system user with private folders, installs Node, BB and the agent CLI in the user's runtime and starts BB on localhost as a systemd service.
2. As that user, log the agent in (Codex or Claude Code).
3. Run setup with the employee settings. The workspace becomes a git copy of the employee's repository (role, team rules, connections), so changes it makes to itself are commits:

   ```sh
   python3 setup.py --kind employee --machine <host-id> --workspace ~/workspace/<name> \
     --workspace-repo <git-url> --provider codex --model <model-id> --inbox ~/telegram-inbox --allow-guest-guard
   ```

   `--provider` and `--model` start one short check in the project; BB remembers them for new conversations. `--allow-guest-guard` allows the guest thread guard in Codex without the BB interface.
4. Optional, for files up to 2 GB: one local Telegram server per machine, shared by every bot on it. As root:
   `deploy/telegram-local.sh install --credentials <file with TELEGRAM_API_ID and TELEGRAM_API_HASH>` (reuses a server
   that already runs), then for each bot `deploy/telegram-local.sh switch --bb-user <user> --bb-url http://127.0.0.1:<port>
   --bb-data <data dir> --bb-path <user's runtime PATH> --restart-unit <user>-bb`. Back to api.telegram.org: the same
   with `--to cloud` (Telegram accepts the bot there about 10 minutes later). Without it the bridge uses api.telegram.org
   with its usual file limits.
4. Create the bot, pair it and bind your private chat as in the steps above. For commits and PRs under the employee's own name, see [GitHub App](deploy/github-app.md).
5. Add the bot to a group and make it an administrator: otherwise Telegram shows it only replies and commands, not mentions. You receive a short message with what happens if nothing is changed and a three-step setup.

| Setting | Choices | When |
|---|---|---|
| Who may talk to it in a group | all members / only you and people with access | all members in a team group; only you with outsiders in the group |
| When it answers | on mention or reply / on anything addressed to it | mention by default; the second in a small group it runs |
| Group memory | shared / this group only | shared when only you are there; this group only when other people are |
| Private chats of people | off / on your approval | on when people need to work with it one to one |

## Organizations

Several employees of one organization are described in one private repository: `organization.yml`, shared rules and
skills in `shared/`, one folder per employee. See [docs/organization.md](docs/organization.md),
[docs/install.md](docs/install.md), [docs/migration.md](docs/migration.md) and the deployment schemes in
[docs/roadmap.md](docs/roadmap.md).

## If the bot stays silent

1. Open the coordinator in BB. Check whether it is waiting for an answer, reporting a provider limit, or already working on the message.
2. In BB's Telegram settings, check the bot username, owner pairing and both receiver switches. A private chat requires no project topics.
3. Run `bb personal-assistant doctor` on the BB host. Check the Telegram chat's selected thread against the coordinator, especially after a provider handoff.
4. Run `bb telegram-projects chat-status --json` locally. Check the pending queue and delivery errors. Keep the output private because it includes binding identifiers.
5. Check the provider's sign-in and quota on the execution host. When delivery is uncertain, check the original BB thread and the Telegram chat before repeating a task that changes data.

## Privacy and access

Every Telegram conversation is its own BB thread in the same project: your private chat, each group or topic, and (for a digital employee) each person you approved. All threads share the workspace, instructions, skills and connections. A conversation with anyone but you is a guest thread: `setup.py` installs a guard hook for Codex and Claude Code that keeps every tool there but blocks managing BB, reading other threads and opening the BB data directory. Codex runs a new or changed hook only after you allow it on the "Codex hooks" card in BB. The guard checks commands, not intentions; the agent instructions and acceptance scenarios cover the rest. [Hook details](hooks/README.md).

Connecting a personal Telegram account for old channel history is optional. Its session key grants account-wide access. Channel selection limits searches in the connector. The server operator controls the machine handling the session and the two-step verification password. For a shared Linux installation, give the coordinator access to the restricted search socket and keep the key under its own unprivileged account. [Private history service setup](telegram-account/PRIVATE-HISTORY.md).

## Several agents on one server

Agents run under their own Linux accounts in one agents BB, separate from the owner's BB: a wall (file rights and firewall), a scoped BB command tool, turns under the agent's account and a per-agent memory limit. Install order (Russian): [deploy/agents/README.md](deploy/agents/README.md).

## Updates

The installer also adds the **Knowledge Base** plugin from the `plugins/knowledge-base` folder of the same release. Its `root` setting is the base folder on the BB server, `~/knowledge-base` by default; an empty git base is created there on first use. Agents in any project search, read and add items with `kb_search`, `kb_read`, `kb_topics`, `kb_add`, and you can use `bb kb`.

All plugins track compatible Git release tags. For provider handoffs, use Personal Agent 0.2.3 or later with Telegram Bridge 0.9.21 or later. Update both components during the same maintenance window. Open **Settings → Updates** in BB or run `bb plugin outdated` to check. Apply the versions you want with the Update button. BB keeps the project directory separate from the plugin install.

For an existing v0.1.9 installation, first follow the [bridge migration procedure](BRIDGE-MIGRATION.md), then point the new installer at your existing working directory. Keep the original private chat and BB thread. Review any local overrides in your old project instructions.

The [Telegram bridge](https://github.com/ainurminibaev/bb-plugin-telegram-projects) has its own releases. It routes messages between Telegram and BB threads.

## How it is built

One platform, thin installations. A personal assistant and a digital employee are the same platform with different settings. An installation holds only a role, team rules, connections and settings; it has no code of its own.

A requirement raised by one installation is met in the platform: as a change for everyone or as a documented setting. Only what concerns one owner or one team stays in the installation. A manual fix on one host is not done until it is in the installer or the launch guide. A platform change reaches every installation with the same release.

## License

[MIT](LICENSE)

Setup creates local/agents-custom.md in the registered workspace only if missing. Keep local instructions there across updates and rollbacks. Before migrating an older installation, compare its files to the original release and preserve local changes. For OS-enforced read-only role files on Linux use an administrator-managed employee installation.
