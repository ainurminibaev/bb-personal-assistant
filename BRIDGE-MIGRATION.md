# Move the v0.1.9 Telegram bridge to release tracking

Run the migration once on the **BB server host** as the account that runs BB. Choose a maintenance window when nobody is sending the bot work. The switch affects every chat served by this bridge. Keep the same BB project and thread.

1. Run `python3 migrate_bridge.py` for the read-only preflight. It checks the installed source, delivery queues, handoffs and bound threads.
2. Supply a **new private receipt path outside your project**, for example `python3 migrate_bridge.py --apply ~/.local/state/bridge-to-releases.jsonl`. Create the parent directory beforehand.
3. The command pauses both receivers, verifies a local private snapshot, replaces the installation source, restores the state, checks a second private export against the original and restores the previous receiver settings. Verify a message sent to your original bot arrives in the original BB conversation.
4. Future compatible releases appear in BB's plugin updates. Choose **Update** in BB.

## If your plugin came from the original Telegram Projects repository

Check the source for `telegram-projects` in `bb plugin list --json`. The original VKirill repository and the assistant fork use the same plugin ID. BB installs one source for that ID per instance. The original v0.9.0 lacks the private-state export and restore operations used here. Keep a configured installation in place until a migration for its version is available. To try the assistant alongside it, install the release in a separate BB instance, create a new bot in BotFather and pair that bot with the new instance. Keep the original token with its current receiver.

## Interrupted migration

Keep the receipt and the service user's private `~/.bb-plugin-backups/telegram-projects` directory. Snapshots contain credentials and conversation data. Keep them out of project folders, issue reports and version control.

If the receipt ends after `backup`, inspect the installed source with `bb plugin list --json`. If the bridge is absent, reinstall the exact source recorded in the receipt. Keep receivers disabled. Use `restorePrivateState` with the recorded backup ID, SHA-256 and `confirm: true`. Re-export the disabled state and compare it with the original private snapshot. Starting with bridge personal.28, two empty worker settings, `isolatedGroupProject` and `isolatedFileHost`, may be added to the re-export. For releases with this migration fix, the installer verifies the hash of each local snapshot and compares their contents, permitting only those two additional empty settings. Any difference in a stored chat, receipt, owner setting or configured worker stops the migration. Restore each receiver flag to its recorded value and reload the bridge. Compare the bindings with the receipt. If restoration reports conflicting state, preserve both copies for inspection.

If the receipt ends at `restored`, the private-state verification succeeded. Check receiver settings and bound chats before resuming. A new migration against a tracking source only reports the source and cannot attest to an earlier interrupted run.
