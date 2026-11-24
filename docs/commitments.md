# Commitments between people and the daily digest

People promise each other things in group chats. The platform keeps them in one list and tells the owner once a day
what appeared, what was closed and what is late — without a model call per message and without writing to the chats.

- **Command** `vallis-commitments` (`deploy/vallis-commitments`, tools.yml line `commitments`): reads a day of a group
  from the bridge's conversation archive, keeps the list, renders the digest. `--help` lists the commands,
  `guide` prints the procedure of the daily run.
- **Skill** `commitments` (`.bb/skills/commitments`): how the owner turns a group on, asks about the list and corrects
  it in words. It comes only to the owner's conversation.
- **Daily run**: one scheduled turn in a subthread of the owner's conversation — `collect`, the agent writes down
  new commitments and closes those the chat closed in words, `render`, the digest goes to the owner.

## What is kept

One record per commitment: number, chat, who, for whom, what, due, kind (promise, accepted request, agreement),
whether it is certain, state (open, done, cancelled; an agreement is only noted), the link to the message, the task
in the tracker if there is one, the next step, and the history of changes with who said it and where.
The text of the chat is not copied: only the wording of the commitment and the link.

Folder `~/.local/share/vallis/commitments` of the agent's account (`VALLIS_COMMITMENTS_DIR`): `config.json` (chats,
time zone, language), `ledger.json`, `journal.jsonl`, `transcripts/<date>/<chat>.txt`, `digests/<date>.md`.

## Boundaries

- Only groups named by the owner; a private chat cannot be added.
- The archive is read through `bb telegram-projects search-history`, so the bridge decides what the calling
  conversation may see; the command works from the owner's conversation and its subthreads.
- A group's or a person's conversation cannot run the command or read its folder (guest thread guard): the list
  covers every chat.
- The digest goes to the owner. A group gets a digest only when the owner turned it on for that group
  (`chats add <chat> --post on`), and only of itself, without mentions. The digest of several chats never goes to
  a chat or to another person. Writing to people about their commitments is a later stage.
- PDF: `render --pdf` (or `set --pdf on` for every day) calls `vallis-pdf`; where it is missing or fails, the answer
  says so and the digest goes as text.
- A day with nothing new, closed, changed, overdue or due is not sent (`"send": false`).

## Check

`python3 -m unittest tests.test_commitments` — an invented chat: a promise with a due date gets into the list, the
day is read in one request (three for 450 messages), «вопрос снят» closes, «решили иначе» changes and keeps what was,
a missed day is picked up, two chats stay apart, a private chat is refused.
