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

## If the bot stays silent

1. Open the coordinator in BB. Check whether it is waiting for an answer, reporting a provider limit, or already working on the message.
2. In BB's Telegram settings, check the bot username, owner pairing and both receiver switches. A private chat requires no project topics.
3. Run `bb personal-assistant doctor` on the BB host. Check the Telegram chat's selected thread against the coordinator, especially after a provider handoff.
4. Run `bb telegram-projects chat-status --json` locally. Check the pending queue and delivery errors. Keep the output private because it includes binding identifiers.
5. Check the provider's sign-in and quota on the execution host. When delivery is uncertain, check the original BB thread and the Telegram chat before repeating a task that changes data.

## Privacy and access

The assistant uses the files and tools available to its agent process. A group topic has a separate conversation history. Running group and personal agents as the same operating-system user can expose personal files and credentials. For participant access, configure a separate BB worker for the topic and bind it again. Starting with Telegram Bridge personal.28, participant jobs require this binding. The personal bridge keeps the bot token and sends the reply back to the original Telegram message. Owner-only local operation remains available. [Group worker deployment](https://github.com/ainurminibaev/bb-plugin-telegram-projects/blob/v0.9.1/docs/isolated-groups.md).

Use a separate provider login for the worker. Giving it a separate project directory alone still lets processes running as the same OS user read that user’s provider credentials. Before admitting participants, test a file in the intended topic and reply directly to the returned PDF with a correction. Check that the revised PDF arrives in the same topic.

Connecting a personal Telegram account for old channel history is optional. Its session key grants account-wide access. Channel selection limits searches in the connector. The server operator controls the machine handling the session and the two-step verification password. For a shared Linux installation, give the coordinator access to the restricted search socket and keep the key under its own unprivileged account. [Private history service setup](telegram-account/PRIVATE-HISTORY.md).

## Updates

Both plugins track compatible Git release tags. For provider handoffs, use Personal Agent 0.2.3 or later with Telegram Bridge 0.9.21 or later. Update both components during the same maintenance window. Open **Settings → Updates** in BB or run `bb plugin outdated` to check. Apply the versions you want with the Update button. BB keeps the project directory separate from the plugin install.

For an existing v0.1.9 installation, first follow the [bridge migration procedure](BRIDGE-MIGRATION.md), then point the new installer at your existing working directory. Keep the original private chat and BB thread. Review any local overrides in your old project instructions.

The [Telegram bridge](https://github.com/ainurminibaev/bb-plugin-telegram-projects) has its own releases. It routes messages between Telegram and BB threads.

## License

[MIT](LICENSE)
