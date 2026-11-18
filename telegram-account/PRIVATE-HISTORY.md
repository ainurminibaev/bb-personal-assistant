# Private Telegram history on Linux

The optional connector lets the personal coordinator search the channels its owner selected. The Telegram session has account-wide access. Keep the key under a dedicated OS account. Every process running as the coordinator can use its history socket. Run group agents and their children under other unprivileged accounts, without sudo, access to the BB control plane or the coordinator's credentials. A root agent can cross OS user boundaries.

## Provision on the server

1. Use separate unprivileged accounts for the private coordinator (`pa-coordinator`) and the session keeper (`pa-history`). The server administrator owns the code in `/opt/personal-agent-history`. Copy the files in this directory there and install the locked requirements in `/opt/personal-agent-history/venv` with `pip install --require-hashes -r requirements.txt`. The keeper and coordinator must have read-only access to the code and interpreter. Install qrencode on the server.
2. Create `/var/lib/personal-agent-history` owned by `pa-history:pa-history`, mode 0700. Place the numeric coordinator UID (from `id -u pa-coordinator`) and the positive Telegram owner ID (from the paired bridge's status) in root-owned `/etc/personal-agent-history.env`, as `COORDINATOR_UID=<uid>` and `PA_TELEGRAM_OWNER_ID=<id>`. Protect this file with mode 0600. Update it and re-pair the session if ownership changes.
3. Run the following from a trusted server administrator terminal, substituting the verified Telegram owner ID. Register your own Telegram application at [my.telegram.org](https://my.telegram.org). Enter its API ID and hash in the trusted terminal during configure. From your computer, forward the port with `ssh -N -L 18341:127.0.0.1:18341 your-server`. Open the temporary link printed by connect, scan the QR code with Telegram, verify the account and select the channels you want to search. The user authenticates directly with the keeper. The coordinator never receives the key.

   ```sh
   sudo -u pa-history env PA_TELEGRAM_STATE=/var/lib/personal-agent-history PA_TELEGRAM_OWNER_ID=<id> /opt/personal-agent-history/venv/bin/python -EsB /opt/personal-agent-history/account.py configure
   sudo -u pa-history env PA_TELEGRAM_STATE=/var/lib/personal-agent-history PA_TELEGRAM_OWNER_ID=<id> /opt/personal-agent-history/venv/bin/python -EsB /opt/personal-agent-history/account.py connect
   ```

4. Install the two unit files from `systemd/` under `/etc/systemd/system/`, then run `systemctl daemon-reload` and `systemctl enable --now personal-agent-history.socket`. A short-lived service handles each request. The socket checks file permissions and the service checks the UID reported by the Linux kernel.
5. Under the coordinator account, run `python3 /opt/personal-agent-history/history_client.py channels`, followed by `search <channel-id> <words>` with the same client. Under a group worker account both calls must be denied. Under both accounts verify that opening the keeper's session file fails.

Record the chosen connector in the personal coordinator workspace, in `reference/telegram-user-tools.md`. For this installation the client command is `python3 /opt/personal-agent-history/history_client.py`. List channels with `channels` and search with `search CHANNEL_ID WORDS`. If the client reports a denied or revoked connection, repair that connection before retrying. Keep credentials, login links and session files outside project instructions.

To revoke the connection, run the same keeper command as in step 3 with `disconnect`. If you migrate from a session previously readable by the agent, pair a fresh session, verify the new connection, then revoke the former session in Telegram's Devices settings. Verify which other services use that old session before revoking it. Removing a local file cannot invalidate a key previously copied elsewhere.
