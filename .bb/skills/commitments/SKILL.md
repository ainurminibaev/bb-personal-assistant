---
name: commitments
description: Keep one list of what people promised each other in group chats and send the owner a daily digest of commitments and agreements — read once a day from the conversation archive, nothing is sent to the chats. Use when the owner asks for a digest of a group, asks who promised what or what is overdue, or drops and corrects a commitment in words («12 снято», «решили иначе»). Not for tasks given to you yourself — that is task-tracking.
---

# Commitments between people and the daily digest

People promise each other things in chats: «пришлю к пятнице», «посмотрю договор». You keep them in one list —
who, for whom, what, due, where it was said, state — and once a day tell the owner what appeared, what was closed
and what is late. The list is not a second tracker: a commitment that has a task in the tracker keeps only the link
(`--tracker`), and its status is read there.

Everything goes through `vallis-commitments` (`--help` lists the commands). It works only in the owner's
conversation and its subthreads: the list covers every chat, so a group's or a person's conversation does not read it.

## Turning it on for a group

Only on the owner's word, and only for groups you are in: private chats are never read.

1. Find the group's chat ID: `bb telegram-projects send --targets`. `vallis-commitments chats add <chat ID> --title "<title>"`.
   Once per installation: `vallis-commitments set --timezone <IANA zone> --language <ru|en>`.
2. The group must keep its conversation archive (the bridge's `searchableHistory`, on for an employee's groups).
   Check that the day is readable: `vallis-commitments collect --chat <chat ID>` — zero messages on a busy day means
   the archive is off for this group: tell the owner what to turn on instead of setting a schedule.
3. One subthread for the daily run, so that a run does not carry this whole conversation:
   `bb thread spawn --parent-self --parent-notifications stuck --project <project> --title "Сводка поручений" --prompt "This thread makes the daily digest of commitments. Wait for the scheduled run."`
4. One recurring schedule on that subthread, at the end of the working day unless the owner named a time:
   `bb automation create --project <project> --name "Сводка поручений" --cron "0 19 * * 1-5" --timezone <zone> --target-thread <subthread> --provider <providerId> --model <model> --reasoning low --prompt "Daily digest of commitments: run vallis-commitments guide and follow it."`
   Check it with `bb automation list --project <project>`, then tell the owner in the same message: which group, at what
   time the digest comes, and that people's words «сделал», «вопрос снят», «решили иначе» in the chat close and change
   the records by themselves at the next digest.

A second group is one more `chats add`: the same subthread and schedule read all listed groups in one run.
Turning off: `vallis-commitments chats remove <chat ID>`; with no groups left, remove the schedule. The records stay.

## The daily run

`vallis-commitments guide` prints the whole procedure — what counts as a commitment, how dates are read, how words
in the chat close and change records, when the digest is sent. The scheduled run follows it in one turn. The owner
asks for a digest now («сведи сегодня») — do the same here, for the day so far.

## The owner speaks about the list

- «Что обещали в <группе>?», «что обещал Борис?», «что просрочено?» — `vallis-commitments list [--chat <chat ID>] [--who <name>]`; answer in a few lines,
  with numbers. `vallis-commitments render` gives the full digest of today without reading the chat again.
- «12 снято», «7 сделано», «5 — срок до пятницы», «3 не поручение» — `close <number> [--cancelled] --by "<owner's name>"`,
  `update <number> --due <date> --by …`, `update <number> --sure` for a confirmed uncertain one. Several numbers in one
  message — each its own command; answer with one line of what changed.
- «Это ещё не сделано» about a closed one — `reopen <number> --by …`.
- «Присылай PDF» — `vallis-commitments set --pdf on`: the daily run sends the file with a three-line summary.
  «Пришли отчёт за сегодня файлом» — `vallis-commitments render --pdf` and send it here.
- «Сводку по этому чату — в сам чат» — `vallis-commitments chats add <chat ID> --post on` (`--post off` stops it):
  from the next run the group gets the digest of itself only, without mentions. Tell the owner that the people of
  the group will see it.

- «Присылай сводку и Анне» — `vallis-commitments recipients add <her Telegram ID> --name "<name>"` (`recipients
  remove` stops it). She gets a digest of the chats she writes in herself and nothing of the others — the command
  checks it at every run; tell the owner which chats that is now (`render --for <ID>` names them). Her private chat
  with you must be open: otherwise the run tells you it did not reach her.
- People ask «что я обещал?» in their group or private chat themselves: those conversations have their own skill
  and see only their own chat or their own promises.

Do not write to people about their commitments, and do not post a digest to a group the owner did not turn on in
this conversation: by default the digest goes to the owner only. The digest of several chats never goes to a chat
or to another person: a person may see only the chats they are in themself.
