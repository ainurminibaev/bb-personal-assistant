---
name: task-tracking
description: Keep a task you were given until its result is checked and handed over — status on request and every 30 minutes, reminders to people with escalation, a daily check of open tasks. Use when a request waits for someone, has a deadline or is delegated; skip for a request answered in the same turn.
---

# Keep a task to its result

## When a request becomes a tracked task

Answer in the same turn when you can. Only a request that **waits** (for a person, a file, a decision), **has a deadline** or is **delegated** becomes a tracked task. Before opening one, check your subthreads (`bb thread list --parent-self`): a request with the same goal continues the open task, a finished one is reopened in its own subthread.

Open it as a subthread of this conversation (`bb thread spawn --parent-self`). Its first message holds the conditions: expected result, how to check it is done, deadline if known, who asked and where statuses go (by default this chat or topic). Ask about the deadline only if it changes priority or promises; suggest a realistic one. Title the subthread with its state in plain words — «ждём ответа Георгия до 18:00», «готово, проверено». No codes.

The subthread is the task's whole state: its first message — conditions; its last status — what is done, what goes on, what is left, who we wait for and the next step; its journal — what was already sent. Read only it when you return to the task, not the whole conversation.

## Returning to the task

For the next step set a one-off schedule on the subthread: `bb automation create --project <this project> --name "<task>: <step>" --in <30m|2h|1d> --target-thread <subthread> --reasoning low --prompt "<what to check>"` (`--at <ISO time>` for an exact moment). When an answer comes earlier, cancel or move it. On each run read the subthread, compare with the answers received, then act. Never repeat a reminder its journal already shows.

## Status

- On request («что с задачей?») — answer at once, in the same place.
- Every 30 minutes while a task is open longer than 30 minutes: one recurring schedule on **this conversation's thread** (`bb automation create --project <this project> --name "Статус поручений" --cron "*/30 * * * *" --timezone <IANA zone> --target-thread <this thread> --reasoning low --prompt "<status of open tasks>"`). Set it when the first task passes 30 minutes; remove it when the last open task closes. With no open task there is no schedule and no turn. One message per run, a summary of all open tasks: done / going on / left / waiting for whom, next step. If nothing changed since the last status, say so in one line.
- A status run is cheap: read the open subthreads' last statuses only.

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

## Closing

A question, a partial answer or a sent request keep the task open. Close it only after checking the result against its condition and handing it to whoever asked — or when they accept a partial result or cancel. Then: cancel its schedules, give the subthread the title «готово, проверено» (or «отменено»), and remove the 30-minute status schedule if no other task is open here.

## Daily check

Once a day (one recurring schedule in the owner's conversation) go through open task subthreads: a stale status, a passed return time without a run, a deadline at risk, a lost next step. Restore the next step, remind or escalate — without repeating what the journals show was already done.
