---
name: agent-mail
description: Get the agent its own mailbox through a mail service API and read its incoming mail — to register in an external system, receive a confirmation code or a link. Use when a task needs an address of the agent itself, not a person's.
---

# The agent's own mail

Use a mailbox of your own only when the task needs one: a registration in an external system, a confirmation code,
a service that writes to the agent. Do not create mailboxes in advance or "just in case", and do not use a person's
address for this.

## The key of the mail service

The platform works with AgentMail (API documentation: https://docs.agentmail.to). The key is a setting of the
organization or of the owner, never a value in a repository or in a chat:

- the `AGENTMAIL_API_KEY` variable of the environment you run in, or
- the file `~/.secrets/agentmail.key` of your installation (a closed folder, mode 700, file 600).

No key — say so in one line and ask the owner to add it; do not look for keys anywhere else.

## Create a mailbox and read mail

Work with the service API directly by its documentation: create an inbox with a clear username (the agent's name
and the purpose), then list the inbox messages and read the one you wait for. Poll calmly (every 10–20 seconds,
a few minutes at most) and stop when the letter has come. Take only what the task needs from it: the code or the link.

## Where the new login goes

- The address and every password you create go to `~/.secrets/accounts/<service>.json` (folder 700, file 600):
  service, address, login, password, date, purpose.
- In your working notes write only that the account exists and where its login is stored — no values.

## Do not

- Do not show the key, passwords or codes in a chat, a thread title, a commit or a document; a code is used, not quoted.
- Do not create a second mailbox for the same purpose: check `~/.secrets/accounts/` first.
- Do not subscribe the mailbox to newsletters and do not use it to write to people on your own.
