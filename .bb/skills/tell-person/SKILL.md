---
name: tell-person
description: Pass a message or an assignment to a person on someone's request — «скажи Борису…», «передай Анне…», «напиши ему, что…», «попроси её…» — by the right way (for the owner — their private chat first even when asked in a group, else an allowed shared chat where they are, or an explicitly named place; for non-owners — only an allowed shared chat where both are present) and tell the requester honestly how it reached them. Use whenever you are asked to tell, remind or ask another person something; not for answering the person you are talking to.
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

## 2. Which way — by who asked and what place was named

**Owner's request (in the owner's private chat OR in a group/topic):**
- **Explicitly named place** («в чате разработчиков», «в этой группе», «в личку») — always takes priority; send only
  there (`bb telegram-projects send --text "[Борис](tg://user?id=<ID>), …"` for the current group/topic so topic is
  kept, `--chat "<group>"` for another group, or `--person "<name>"` for «в личку»). If that explicitly named place
  is closed, refused, or the person is not in it — do **not** silently switch to another place; report the exact
  reason and offer the available alternative (e.g. ask whether to post in `<group>` or give `https://t.me/<your bot>`).
- **No place named** — **use the person's private chat first** whenever it is open and allowed in `send --targets`,
  **including when the owner's request came from a group**: `bb telegram-projects send --person "<name>" --text "…"`.
- **Private chat is closed or ordinary sending to it is unavailable** (when no place was named):
  - If the message contains **private or sensitive details** — do **not** carry them into a shared group without
    permission; stop, inform the owner that private chat is closed, ask whether to write in `<group>`, and offer
    `https://t.me/<your bot>`.
  - Otherwise — send to a suitable allowed shared group where the person is a member (prefer the **source group** if
    the person is in it, otherwise a **working group** where they are), with a mention.
- Neither private chat nor an allowed shared group is available — report «не дошло» honestly with the exact reason
  and offer the link `https://t.me/<your bot>` (the person presses Start, the owner approves).

**Non-owner's request in a group or topic** (another participant who may ask you there):
- Never write to a third person's private chat on a non-owner's request. If a non-owner explicitly asks to message a
  third person privately («напиши Борису в личку»), refuse honestly — do not silently post a private request into a
  public group.
- Otherwise, write only to an allowed shared group where both the requester and the recipient are present (prefer
  this group if the person is in it, using `bb telegram-projects send --text …` to keep the topic), with a mention
  and signed «по просьбе <requester>».
- If the person is not in any allowed shared group (or the bridge refuses) — refuse honestly and say what works.

**In another person's private chat** (someone who is not the owner asks you to tell a third person):
1. Never the third person's private chat.
2. An allowed shared group where both of them are: `bb telegram-projects send --chat "<group>" --text "[Борис](tg://user?id=<ID>),
   по просьбе Анны: …"` — always signed «по просьбе <requester>».
3. The bridge refuses, or there is no such group — **refuse honestly and offer the shared chat**, in one message:
   «Отсюда передать Борису не могу: из личного разговора я пишу только вам. Напишите мне в общем чате, где он есть, —
   я отмечу его там.» And stop there.

**Do not ask the owner.** This refusal is the rule itself, not a right you lack: the rule «не хватает права — иди к
владельцу» does not apply to passing words between people, and the owner is not disturbed for every «скажи
такому-то». The only case for a question to the owner is when the person asks for access in so many words («попроси
владельца разрешить мне писать Борису», «дайте мне доступ») — then one question with buttons, as for any access.

**A colleague agent asks you in a shared topic** — the same as a non-owner request in a group: write in an allowed
shared group where the person is, with a mention and signed «по просьбе <agent>».

## 3. What you write

One message, readable in five seconds: who asks, what, by when if a time was named, where to answer.
«Борис, Анна просит прислать смету по складу до четверга. Ответить можно здесь.» Nothing from other conversations
beyond what the request needs; never carry private information into a group without permission; no «напоминаю» and
no pressure — you pass words, the requester sets the tone.

## 4. Tell the requester how it reached them

Right after sending, one line where you were asked, by the fact of the send (`"sent":true` in its answer):
- «Передал Борису в личку.»
- «Отметил Бориса в этом чате — личка у него не открыта.» / «Написал Борису в „Команде“ с отметкой — личка у него не открыта.»
- «Отметил Бориса в „Отделе разработчиков“, как просили.»
- «Не дошло: личка закрыта, общего чата нет. Вот ссылка, чтобы он открыл личку: …»

Never say «передал» without a sent message; a refusal of the bridge — in plain words, with what the requester can do.
If the person may not address you in that group, their answer will not reach you by itself: say that the answer
should go to the requester directly.

## 5. When an answer or a result is expected

«Спроси и вернись с ответом», a deadline, «проследи» — it is a task you keep: the task-tracking skill (a subthread,
a return time, reminders with quiet hours). A plain «скажи» ends with the line of step 4.
