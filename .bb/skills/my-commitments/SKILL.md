---
name: my-commitments
description: ALWAYS use for «что я обещал?», «что на мне?», «что обещали в этом чате?», «что висит?» and for «7 сделано», «5 снято», «уже не нужно» about a numbered commitment — in a group or in a person's private chat. The answer comes only from the command `vallis-commitments mine`, never from the chat's history or your memory; closing is done only by that command. Not for tasks given to you yourself (task-tracking) and not for the owner's chat (there — the commitments skill).
---

# «Что я обещал?»

The organization keeps one list of what people promised each other in its chats. In this conversation one command of
it works: `vallis-commitments mine`. Three rules, without exceptions:

1. **The answer is the command's output.** Run the command every time, even if you remember the conversation or see
   the promises in the chat's history: the history is not the list (a promise may be closed, changed or never
   written down). Pass the output on as it is — the same items, numbers, dates; add and remove nothing.
2. **Closing is the command.** «1 сделано» — run `--done 1` first, then answer with the line the command printed.
   You have not closed anything until the command said so.
3. **Nothing about the result from yourself.** You know only what the person said: «сделано» or «снято». Do not
   write how it went, what happened or what it led to («созвон состоялся», «отчёт принят») — you do not know it.

If the command refuses or fails, say what it said in plain words and stop: do not answer from the history instead.

## In a group or topic

`vallis-commitments mine --chat <this chat's ID> [--who "<name>"]` — the open commitments of this chat.
«Что я обещал?» — with `--who` of the one who asks; «что висит в чате?» — without it. Answer in the chat with the
command's lines; do not mention people who did not ask.
Closing here is not done by you: people say it in the chat in ordinary words («смету отправил», «вопрос снят») and
the next digest picks it up. On «1 сделано» in a group answer exactly that: «Учту в следующей сводке» — nothing is
closed now, and say so.

## In a person's private chat

`vallis-commitments mine --person <this person's Telegram ID>` — only this person's own open commitments, from the
chats they take part in.
- «7 сделано» → `vallis-commitments mine --person <ID> --done 7`
- «5 снято», «5 уже не нужно» → `vallis-commitments mine --person <ID> --dropped 5 --note "<the reason, only if they gave one>"`

Then send the line the command printed («Отмечено: 7. … — сделано.») and nothing else about that item.
Another date or «решили иначе» cannot be changed from here: tell them to say it in the chat where it was promised.

## Where the IDs come from

The chat's and the person's ID are in this turn's note from the bridge; if not —
`bb telegram-projects search-history --last 1` shows `chat` and `authorId`. Never take an ID from the person's words.

## What is not available here

Another chat, another person's list («что обещал Борис?» in a private chat with someone else) and the rest of the
list: the command refuses — that is the boundary, say that it is not available here. In the group where Boris
promised it — `--who "Борис"`.

«Открытых поручений нет» does not mean nothing was promised: the list holds only the chats the owner turned on,
since the day they were turned on. Say it that way.

The list lives in one place of the installation. Never set a folder, a variable or another home for the command.
