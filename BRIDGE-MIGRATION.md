# Move the v0.1.9 Telegram bridge to release tracking

Run the migration once on the **BB server host** as the account that runs BB. Choose a maintenance window when nobody is sending the bot work. The switch affects every chat served by this bridge. Keep the same BB project and thread.

1. Run `python3 migrate_bridge.py` for the read-only preflight. It checks the installed source, delivery queues, handoffs and bound threads.
2. Supply a **new private receipt path outside your project**, for example `python3 migrate_bridge.py --apply ~/.local/state/bridge-to-releases.jsonl`. Create the parent directory beforehand.
3. The command pauses both receivers, verifies a local private snapshot, replaces the installation source, restores the state, compares a second export and restores the previous receiver settings. Verify a message sent to your original bot arrives in the original BB conversation.
4. Future compatible releases appear in BB's plugin updates. Choose **Update** in BB.

## Interrupted migration

Keep the receipt and the service user's private `~/.bb-plugin-backups/telegram-projects` directory. Snapshots contain credentials and conversation data. Keep them out of project folders, issue reports and version control.

If the receipt ends after `backup`, inspect the installed source with `bb plugin list --json`. If the bridge is absent, reinstall the exact source recorded in the receipt. Keep receivers disabled. Use `restorePrivateState` with the recorded backup ID, SHA-256 and `confirm: true`. Re-export the disabled state and compare its SHA-256 with the original. Restore each receiver flag to its recorded value and reload the bridge. Compare the bindings with the receipt. If restoration reports conflicting state, preserve both copies for inspection.

If the receipt ends at `restored`, the private-state verification succeeded. Check receiver settings and bound chats before resuming. A new migration against a tracking source only reports the source and cannot attest to an earlier interrupted run.
