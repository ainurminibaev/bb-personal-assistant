---
name: my-commitments
description: Answer «что я обещал?», «что на мне?», «что обещали в этом чате?», «что висит?» in a group or in a person's private chat from the organization's list of commitments — a person sees only their own, a group only its own chat — and close a person's own commitment on their word («7 сделано»). Not for tasks given to you yourself (task-tracking) and not for the owner's chat (there: the commitments skill).
---

# «Что я обещал?»

The organization keeps one list of what people promised each other in its chats; the daily digest fills it. In this
conversation only one command of it works, and it shows only what this conversation may see anyway.

**In a group or topic** — the open commitments of this chat:
`vallis-commitments mine --chat <this chat's ID> [--who "<name>"]`. «Что я обещал?» — with `--who` of the one who asks;
«что висит в чате?» — without it. Answer in the chat in a few lines, with the numbers; do not mention people who did
not ask. Closing here is by ordinary words in the chat («смету отправил», «вопрос снят»): the next digest picks them
up — say so, do not close anything yourself.

**In a person's private chat** — only this person's own open commitments, from the chats they take part in:
`vallis-commitments mine --person <this person's Telegram ID>`. On their word about their own item:
`… --done <number>` («7 сделано») or `… --dropped <number> --note "<why>"` («5 уже не нужно»), then one line of what
changed. Another date or «решили иначе» cannot be changed from here: tell them to say it in the chat where it was
promised, or to the one who asked.

The chat's and the person's ID are in this turn's note from the bridge; if not —
`bb telegram-projects search-history --last 1` shows `chat` and `authorId`.

The command refuses another chat, another person's list and everything else of the list — that is the boundary, not
an error to work around: say that this is not available here. «Что обещал Борис?» in a private chat with someone
else — not available; in the group where Boris promised it — `--who "Борис"`.

Nothing in the list («Открытых поручений нет») does not mean nothing was promised: the list holds only the chats
the owner turned on, since the day they were turned on. Say it that way.
