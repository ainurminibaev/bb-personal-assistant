# Personal Agent for BB — a self-hosted AI assistant for Telegram

English · [Русский](README.ru.md)

Ask your assistant for a research brief on your phone. It can delegate the work to a Claude Code or Codex thread in the relevant BB project and send the finished PDF back to the same Telegram chat. Open that conversation in BB to inspect the work or continue it at your desk.

## What you can do

- Send a document with a question and get the result back in Telegram.
- Continue work in an existing project thread. Keep the project registry and personal instructions on your own machine.
- Give the assistant a one-off research task without creating another project.

## Install on your BB host

These instructions apply to the 0.2 release line. For the older ZIP, use the [v0.1.9 instructions](https://github.com/ainurminibaev/bb-personal-assistant/blob/v0.1.9/README.md).

1. Start BB 0.49 or later with Git, Python 3 and a configured Claude Code or Codex provider. Get the installer from this assistant's [GitHub Releases](https://github.com/ainurminibaev/bb-personal-assistant/releases) and open the extracted directory on the BB host.
2. Run `bb machine list` to find the host ID. Choose a separate working directory for your registry, project files and personal instructions. Keep that directory between releases.

   ```sh
   python3 setup.py --machine <host-id> --workspace ~/my-assistant
   ```

   Review the two release sources at the prompt. Setup installs the assistant and the Telegram bridge and registers the workspace as a BB project.
3. Open the project in BB and create the personal coordinator thread. Create your bot with BotFather. In BB's Telegram connection settings, save its token, enable sync and chat, and follow the owner pairing link.
4. Send `/project` in your private Telegram chat, choose the project, then select your coordinator under Chats. Run the same setup command once more so the assistant plugin can find the selected thread. Run `bb personal-assistant doctor` to check the project and thread IDs. Start a fresh provider session in that thread to load the new instructions.
5. Send the bot a question and open the same conversation in BB to verify delivery. Inside the coordinator, `bb personal-assistant work` lists its open child threads.

## Privacy and access

The assistant uses the files and tools available to its agent process. A group topic has a separate conversation history. Running group and personal agents as the same operating-system user can expose personal files and credentials. Use owner-only access on a personal host until you have isolated group execution.

Connecting a personal Telegram account for old channel history is optional. Its session key grants account-wide access. Channel selection limits searches in the connector. The server operator controls the machine handling the session and the two-step verification password. For a shared Linux installation, give the coordinator access to the restricted search socket and keep the key under its own unprivileged account. [Private history service setup](telegram-account/PRIVATE-HISTORY.md).

## Updates

Both plugins track compatible Git release tags. Open **Settings → Updates** in BB or run `bb plugin outdated` to check. Apply the versions you want with the Update button. BB keeps the project directory separate from the plugin install.

For an existing v0.1.9 installation, first follow the [bridge migration procedure](BRIDGE-MIGRATION.md), then point the new installer at your existing working directory. Keep the original private chat and BB thread. Review any local overrides in your old project instructions.

The [Telegram bridge](https://github.com/ainurminibaev/bb-plugin-telegram-projects) has its own releases. It routes messages between Telegram and BB threads.

## License

[MIT](LICENSE)
