---
name: tell-person
description: Pass a message or an assignment to a person on someone's request — «скажи Борису…», «передай Анне…», «напиши ему, что…», «попроси её…» — by the right way (the chat where the person is, their private chat, or a question to the requester first) and tell the requester honestly how it reached them. Use whenever you are asked to tell, remind or ask another person something; not for answering the person you are talking to.
---

# «Скажи такому-то»: reach the person and say how

You carry the requester's words to the person; you do not assign work in your own name. Every way below is the
bridge's ordinary sending (`bb telegram-projects send`, see the telegram-projects skill): the bridge decides what this
conversation may send, and its refusal is an answer for the requester, not an obstacle to get around.

## 1. Who is the person and where can they be reached

- `bb telegram-projects send --targets` — the groups this conversation may write to and the people with a private chat:
  a person without a note can be written to privately; «has not started the bot» means their private chat is closed.
- The person's Telegram ID and the groups they write in: `bb telegram-projects search-history --author "<name>" --last 3`
  (`authorId`, `chat`, `chatTitle`). Two people fit the name — ask the requester which one; never guess.

## 2. Which way — by where you were asked

**In a group or topic** (anyone who may ask you there):
- The person is in this group — write here and mention them, even if their private chat is open: the request was
  made in public and stays there. `bb telegram-projects send --text "[Борис](tg://user?id=<ID>), …"`.
- «В личку» was said — a private chat from a group is written only for the owner or for the person themself.
  Asked by someone else — write here with a mention and say why.
- The person is not in this group — you cannot reach them from here. Say so and name what works: the owner can ask
  you in their own chat, or the requester writes in a chat where the person is.

**In the owner's chat:**
- A place was named («в чате поддержки») — there, with a mention.
- No place — the person's private chat: `bb telegram-projects send --person "<name>" --text "…"`.
- Their private chat is closed — do not pick another place yourself. Ask the owner in the same answer: «У Бориса
  личка со мной не открыта. Написать ему в „<group where he is>“ с отметкой?» — and offer the link that opens it:
  `https://t.me/<your bot>` (he presses Start, the owner approves). On «да» — write in that group with a mention.
- No shared group either — «не дошло»: the link is the only way; say so.

**In another person's private chat** (they ask you to tell a third person): never the third person's private
chat. A group where both of them are: `bb telegram-projects send --chat "<group>" --text "[Борис](tg://user?id=<ID>),
по просьбе Анны: …"` — always with «по просьбе <requester>». The bridge allows it only where both are in that
group; it refuses (an older bridge refuses always) or there is no such group — say plainly that it did not go and
what works: they ask you in a shared group themselves. Do not go to the owner for permission unless they ask you to.

**A colleague agent asks you in a shared topic** — the same as a request in a group: mention the person there.

## 3. What you write

One message, readable in five seconds: who asks, what, by when if a time was named, where to answer.
«Борис, Анна просит прислать смету по складу до четверга. Ответить можно здесь.» Nothing from other conversations
beyond what the request needs; no «напоминаю» and no pressure — you pass words, the requester sets the tone.

## 4. Tell the requester how it reached them

Right after sending, one line where you were asked, by the fact of the send (`"sent":true` in its answer):
- «Передал Борису в личку.»
- «Отметил Бориса в этом чате.» / «Написал Борису в „Команде“ с отметкой — личка у него не открыта.»
- «Не дошло: личка закрыта, общего чата нет. Вот ссылка, чтобы он открыл личку: …»

Never say «передал» without a sent message; a refusal of the bridge — in plain words, with what the requester can do.
If the person may not address you in that group, their answer will not reach you by itself: say that the answer
should go to the requester directly.

## 5. When an answer or a result is expected

«Спроси и вернись с ответом», a deadline, «проследи» — it is a task you keep: the task-tracking skill (a subthread,
a return time, reminders with quiet hours). A plain «скажи» ends with the line of step 4.
