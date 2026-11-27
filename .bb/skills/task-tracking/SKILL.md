---
name: task-tracking
description: Keep a task you were given until its result is checked and handed over — status on request and every 30 minutes, reminders to people with escalation, a daily check of open tasks. Use when a request waits for someone, has a deadline or is delegated; skip for a request answered in the same turn.
---

# Keep a task to its result

## When a request becomes a tracked task

Answer in the same turn when you can. Only a request that **waits** (for a person, a file, a decision), **has a deadline** or is **delegated** becomes a tracked task. Before opening one, check your subthreads (`bb thread list --parent-self`): a request with the same goal continues the open task, a finished one is reopened in its own subthread.

First run `bb status` and `bb thread show <this thread> --json`: they give this thread's and project's IDs and its `providerId` and model, used below. Every `bb automation create` with `--prompt` needs `--provider <providerId> --model <model>`. Open the task as a subthread of this conversation: `bb thread spawn --parent-self --parent-notifications stuck --project <project> --title "⏳ <state in plain words>" --prompt "<conditions>"` — always `stuck`: its routine runs must not wake this conversation (that turn would carry the whole conversation), only failures and questions do. A delayed first message (`--send-at`) is not a schedule: use `bb automation create` for every return. In the owner's Telegram chat, a subthread that may need the owner's answer (a question with options, an approval) is started through the bridge's delegation instead (telegram-projects skill, `spawnDelegated`): the bridge then brings its questions to the owner as buttons and its result back; a plain spawn is not watched by the bridge. Its first message holds the conditions: expected result, how to check it is done, deadline if known, who asked and where statuses go (by default this chat or topic). Ask about the deadline only if it changes priority or promises; suggest a realistic one. Title the subthread with its state in plain words; while the task is open the title starts with ⏳ — «⏳ ждём ответа Георгия до 18:00». No codes. Right after the spawn mark it as a task: `bb thread-cleanup task <subthread> open` — the daily thread cleanup never archives an open task, and after a week without movement tells the owner instead (if the command is missing, the cleanup is not installed: skip it). Keep ⏳ on every rename until the task closes.

**Open its case card** in the same step, without asking anyone — a request with a deadline («итог завтра в 10:00»), a wait or a hand-over is a case; a deadline named by a participant who may assign you tasks is set as the case's schedule without asking the owner (only a person without that right needs the owner's consent): `bb personal-assistant case open --title "<what, in plain words>" --by "<who asked>" --place "<where: chat, topic>" [--message <source msgId>] --thread <subthread> --next "<next step>" [--promise "<what you promised, to whom, by when>"] [--deliver-chat <id|title> --deliver-topic <id|name> | --deliver-person <id|owner>] [--deliver-reply-to <msgId>]`. It prints the case id and stores the promised answer destination (by default this chat/topic, plus `--message` as reply-to when numeric). The card lives on disk next to the installation and survives a restart of this conversation, a switch of the model and the archive of the subthread; it is the task's memory, the subthread is its workshop. Write to it only through `case …`, one line per event: `case set <id> --waiting-who … --waiting-what … --waiting-back <ISO>` when you wait for someone, `--next …` when the next step changes, `--promise …` for every new promise, `--note …` for anything else that happened. Without the command (an installation without it) keep the state in the subthread as below.

The subthread is the task's whole state: its first message — conditions; its last status — what is done, what goes on, what is left, who we wait for and the next step; its journal — what was already sent. Read only it when you return to the task, not the whole conversation.

Never promise a status, a reminder or a return you have not scheduled: create the schedule first, check it with `bb automation list --project <project>`, then tell the person when to expect it. If a command fails, read its error and run it again corrected.

## Returning to the task

For the next step set a one-off schedule on the subthread and tie it to the case: name it `<case id>: <step>` and right after creating it run `case set <id> --schedule <automation id>` (`--unschedule <id>` when you remove or replace it). A reminder that is not in the card is lost on a restart. `bb automation create --project <this project> --name "<case id>: <step>" --in <30m|2h|1d> --target-thread <subthread> --provider <providerId> --model <model> --reasoning low --prompt "<what to check>"` (`--at <ISO time>` for an exact moment). When an answer comes earlier, cancel or move it. On each run read the subthread, compare with the answers received, then act. Never repeat a reminder its journal already shows.

A run of a schedule cannot create a new schedule (BB refuses: «Threads executing an automation run cannot create automations»), but it can change its own. So the next check of a case is moved, not created: `bb automation update <the case's schedule> --at <time>` (or `--cron`, or pause), the same schedule from the case card. No schedule on the case and you are in a run — record `case set <id> --note "следующая проверка не поставлена"` and tell the person who gave the task in one line.

## Status

- On request («что с задачей?») — answer at once, in the same place.
- Every 30 minutes while a task is open longer than 30 minutes: one recurring schedule on **the task's subthread**, not on this conversation's thread — a run there reads only the task's own state, a run here would carry the whole conversation (measured: ~125k input tokens against ~25k). `bb automation create --project <project> --name "Статус: <task>" --cron "*/30 * * * *" --timezone <IANA zone> --target-thread <subthread> --provider <providerId> --model <model> --reasoning low --prompt "<status of this task>"`. Set it when the task passes 30 minutes; remove it when the task closes. With no open task there is no schedule and no turn.
- The run posts the status itself where the task was given: `bb telegram-projects send --topic "<topic>" --text "…"` for a group topic, `--person <person>` for a person's chat, `--person owner` for the owner's chat. One message per run: done / going on / left / waiting for whom, next step. If nothing changed since the last status, say so in one line. Several open tasks in one place: each posts its own short line.

## Reminders to people

Ask the person where they are (their chat or a mention in the group). Choose the interval by urgency, weekday, their working hours and the deadline. Defaults, unless the team's rules in the working folder set others:

| Case | Interval |
|---|---|
| Blocking or critical | 15–30 min |
| Urgent, working day | 1 h |
| Urgent, weekend | 2 h |
| Not urgent, working day | 1 working day |
| Not urgent, weekend | next working day |

Quiet hours 21:00–09:00 in the person's time zone, unless the team says otherwise. **Check quiet hours and the weekday right before sending**, not only when scheduling — the task may have outlived a night; if it is quiet now, move the reminder to the next allowed time.

After three reminders without an answer, or when the deadline is about to be missed, escalate to the person who asked or to the owner: what answer is missing, from whom, by when it is needed, what can be done now. Escalate at once if the deadline is about to slip, even before three reminders.

## Delivering the result

When you promised to come back with an answer later, deliver the finished result through the case card: `bb personal-assistant case deliver <id> --text "<result>"` (or `--text-file <path>`, optional `--file <path>` and `--reply-to <msgId>`). It sends the answer to the destination saved in the card via the standard bridge (`bb telegram-projects send`, with all bridge rights and audience checks intact), records the bridge confirmation (`messageId`, timestamp) in the card journal, and blocks accidental duplicate sends after a restart (pass `--force` only if a new send is genuinely required). If the bridge refuses, the card stays open as `undelivered` with the refusal reason in its journal — never work around a bridge refusal. When `case deliver` sent the answer into the **current** chat (especially a private chat), finish the turn with `NO_REPLY` so the person does not receive a duplicate message.

## Closing

A question, a partial answer or a sent request keep the task open. Close it only after checking the result against its condition and handing it to whoever asked (via `case deliver <id> --text "…"` when returning to a saved chat/topic) — or when they accept a partial result or cancel. Then: `case close <id> --result "<what was handed over, where>"` — it prints the schedules to remove and the cleanup mark; run them. Cancel its schedules, give the subthread the title «готово, проверено» (or «отменено») without ⏳, and mark it closed: `bb thread-cleanup task <subthread> closed` — only then may the thread cleanup archive it, and remove the 30-minute status schedule if no other task is open here.

## A new conversation

When this conversation is new (a restart, a switch of the model, a new thread for the same chat) and its instructions say there are open cases, first run `bb personal-assistant case list --here`: these are the cases of this chat. On a message about one of them read only its card (`case show <id>`) — do not ask again what is decided, do not repeat what its journal shows done. A promise whose time has passed: do it now or tell the person plainly that it is late and when it will be. Then `case check --here` and put back any missing next step.

## Daily check

Once a day (one recurring schedule in the owner's conversation) add to the summary one line of your spend — `bb personal-assistant usage --today` (today and the model window — the logs are read only where they grew since the last time; say plainly if the window is past 80 %), run `bb personal-assistant case check --all` and go through open task subthreads: a stale status, a passed return time without a run, a deadline at risk, a lost next step. Restore the next step, remind or escalate — without repeating what the journals show was already done.
