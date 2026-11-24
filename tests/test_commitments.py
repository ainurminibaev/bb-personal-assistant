"""Commitments between people and the daily digest of a group (С91, С92): invented chats, people and messages."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "deploy" / "vallis-commitments"
FAKE = ROOT / "tests" / "fake_history.py"
CHAT = "-1009990001111"          # invented group
OTHER = "-1009990002222"


def message(number, date, author, author_id, text, chat=CHAT, topic=0, reply=None, **more):
    m = {"chat": int(chat), "chatTitle": "Команда «Ромашка»", "topic": topic, "message": number, "date": date,
         "author": author, "authorId": author_id, "text": text,
         "link": f"https://t.me/c/{chat[4:]}/{str(topic) + '/' if topic else ''}{number}", **more}
    if reply:
        m["replyTo"] = reply
    return m


DAY = [
    message(101, "2026-03-10T07:05:00.000Z", "Анна Лебедева", 9001, "Борис, пришли смету по складу до четверга"),
    message(102, "2026-03-10T07:06:10.000Z", "Борис Орлов", 9002, "Ок, сделаю к четвергу", reply=101),
    message(103, "2026-03-10T09:30:00.000Z", "Вера Сомова", 9003, "Я посмотрю договор с подрядчиком"),
    message(104, "2026-03-10T12:00:00.000Z", "Анна Лебедева", 9001, "Решили: релиз переносим на 20 марта", topic=7),
    message(105, "2026-03-10T13:00:00.000Z", "Глеб Юдин", 9004, "", transcript="может быть гляну логи вечером"),
    message(201, "2026-03-10T10:00:00.000Z", "Дина Котова", 9005, "Обновлю прайс до пятницы", chat=OTHER),
]
NEXT_DAY = [
    message(110, "2026-03-11T08:00:00.000Z", "Вера Сомова", 9003, "По договору вопрос снят, подрядчик сам исправил", reply=103),
    message(111, "2026-03-11T08:30:00.000Z", "Борис Орлов", 9002, "Смету пришлю в пятницу, не успеваю к четвергу"),
]


class Commitments(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.history = self.tmp / "history.json"
        self.calls = self.tmp / "calls.jsonl"
        self.history.write_text(json.dumps(DAY + NEXT_DAY, ensure_ascii=False), encoding="utf-8")
        self.env = {**os.environ, "VALLIS_COMMITMENTS_DIR": str(self.tmp / "store"),
                    "VALLIS_COMMITMENTS_HISTORY": f"{sys.executable} {FAKE}",
                    "FAKE_HISTORY": str(self.history), "FAKE_HISTORY_CALLS": str(self.calls)}
        self.run_tool("set", "--timezone", "Europe/Moscow", "--language", "ru")
        self.run_tool("chats", "add", CHAT)

    def run_tool(self, *args, ok=True, env=None):
        done = subprocess.run([sys.executable, str(TOOL), *map(str, args)], capture_output=True, text=True,
                              env={**self.env, **(env or {})})
        if ok:
            self.assertEqual(done.returncode, 0, done.stderr)
        return done

    def out(self, *args):
        return json.loads(self.run_tool(*args).stdout)

    def add(self, *args):
        return self.out("add", "--chat", CHAT, *args)["id"]

    def first_day(self):
        """What the agent writes down after reading the first day."""
        smeta = self.add("--who", "Борис Орлов", "--who-id", 9002, "--for", "Анна Лебедева", "--what", "прислать смету по складу",
                         "--due", "2026-03-12", "--message", 102, "--date", "2026-03-10")
        dogovor = self.add("--who", "Вера Сомова", "--what", "посмотреть договор с подрядчиком", "--message", 103,
                           "--date", "2026-03-10")
        self.add("--who", "Анна Лебедева", "--what", "релиз переносим на 20 марта", "--kind", "decision",
                 "--message", 104, "--topic", 7, "--date", "2026-03-10")
        logs = self.add("--who", "Глеб Юдин", "--what", "глянуть логи", "--unsure", "--message", 105, "--date", "2026-03-10")
        return smeta, dogovor, logs

    def digest(self, *args):
        return Path(self.out("render", *args)["digest"]).read_text(encoding="utf-8")

    # ── reading the day ──────────────────────────────────────────────────────────────────────────────────────────

    def test_day_is_read_in_one_call_and_only_this_chat(self):
        report = self.out("collect", "--date", "2026-03-10")
        self.assertEqual([r["chat"] for r in report], [CHAT])
        self.assertEqual(report[0]["messages"], 5)
        self.assertEqual(report[0]["title"], "Команда «Ромашка»")
        text = Path(report[0]["transcript"]).read_text(encoding="utf-8")
        self.assertIn("[03-10 10:05 #101] Анна Лебедева (9001): Борис, пришли смету по складу до четверга", text)
        self.assertIn("#102 ↩101] Борис Орлов", text)                    # the answer points at the request
        self.assertIn("== topic 7 ==", text)
        self.assertIn("[voice] может быть гляну логи вечером", text)       # a voice message is read by its transcript
        self.assertNotIn("прайс", text)                                    # another chat is not mixed in
        self.assertEqual(len(self.calls.read_text().splitlines()), 1)      # one request to the archive, no model call

    def test_long_day_is_read_whole_by_pages(self):
        many = [message(1000 + n, f"2026-03-10T{6 + n // 60:02d}:{n % 60:02d}:00.000Z", "Анна Лебедева", 9001, f"сообщение {n}")
                for n in range(450)]
        self.history.write_text(json.dumps(many, ensure_ascii=False), encoding="utf-8")
        report = self.out("collect", "--date", "2026-03-10")
        self.assertEqual(report[0]["messages"], 450)
        lines = [l for l in Path(report[0]["transcript"]).read_text(encoding="utf-8").splitlines() if l.startswith("[")]
        self.assertEqual(len(lines), 450)
        self.assertTrue(lines[0].endswith("сообщение 0") and lines[-1].endswith("сообщение 449"))
        self.assertEqual(len(self.calls.read_text().splitlines()), 3)

    def test_private_chat_is_never_collected(self):
        self.assertIn("private chats are not collected", self.run_tool("chats", "add", "9001", ok=False).stderr)
        self.assertIn("private chats are not collected", self.run_tool("collect", "--chat", "9001", ok=False).stderr)

    def test_chat_outside_the_list_is_not_read(self):
        done = self.run_tool("collect", "--chat", OTHER, "--date", "2026-03-10", ok=False)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("is not in the list", done.stderr)
        self.assertFalse(self.calls.exists())

    def test_archive_refusal_is_said_in_words(self):
        done = self.run_tool("collect", ok=False, env={"FAKE_HISTORY_FAIL": "telegram_conversation_required"})
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("owner's Telegram conversation", done.stderr)

    def test_missed_day_is_picked_up_next_time(self):
        self.out("collect", "--date", "2026-03-10")
        self.first_day()
        self.digest("--date", "2026-03-10")
        late = message(120, "2026-03-11T19:30:00.000Z", "Анна Лебедева", 9001, "Завтра созвон в 11")   # 22:30 local, after a 19:00 digest
        self.history.write_text(json.dumps(DAY + NEXT_DAY + [late], ensure_ascii=False), encoding="utf-8")
        # No run on 11.03; the run of 12.03 starts after the last message of the delivered digest.
        report = self.out("collect", "--date", "2026-03-12")
        text = Path(report[0]["transcript"]).read_text(encoding="utf-8")
        self.assertEqual(report[0]["messages"], 3)
        self.assertIn("#110", text)
        self.assertIn("#120", text)
        self.assertNotIn("#105", text)                                     # already in the digest of 10.03

    # ── the list ─────────────────────────────────────────────────────────────────────────────────────────────────

    def test_promise_with_a_due_date_gets_into_the_list(self):
        self.out("collect", "--date", "2026-03-10")
        smeta, _, _ = self.first_day()
        item = next(i for i in self.out("list", "--json") if i["id"] == smeta)
        self.assertEqual((item["who"], item["for"], item["due"], item["state"]),
                         ("Борис Орлов", "Анна Лебедева", "2026-03-12", "open"))
        self.assertEqual(item["source"]["link"], f"https://t.me/c/{CHAT[4:]}/102")
        self.assertNotIn("Ок, сделаю", json.dumps(item, ensure_ascii=False))   # the chat's text is not copied

    def test_reading_the_day_twice_does_not_double(self):
        first = self.first_day()
        second = self.first_day()
        self.assertEqual(first, second)
        self.assertEqual(len(self.out("list", "--all", "--json")), 4)

    def test_decision_is_kept_by_date_but_is_not_open(self):
        self.first_day()
        self.assertEqual([i["what"] for i in self.out("list", "--json")],
                         ["прислать смету по складу", "посмотреть договор с подрядчиком", "глянуть логи"])
        self.assertIn("### Договорённости дня\n- **3.** Анна Лебедева: релиз переносим на 20 марта", self.digest("--date", "2026-03-10"))

    # ── the digest ───────────────────────────────────────────────────────────────────────────────────────────────

    def test_digest_of_the_day(self):
        self.out("collect", "--date", "2026-03-10")
        self.first_day()
        text = self.digest("--date", "2026-03-10", "--comment", "Обсуждали склад и перенос релиза.")
        self.assertIn("# Поручения и договорённости — 2026-03-10", text)
        self.assertIn("## За день\nОбсуждали склад и перенос релиза.", text)
        self.assertIn("## Команда «Ромашка» · сообщений за день: 5", text)
        self.assertIn("### Новое за день\n- **1.** Борис Орлов: прислать смету по складу — для Анна Лебедева — срок 2026-03-12 — "
                      f"[сообщение](https://t.me/c/{CHAT[4:]}/102)", text)
        self.assertIn("- **2.** Вера Сомова: посмотреть договор с подрядчиком — без срока", text)
        self.assertIn("### Уточнить: поручение ли это\n- **4.** Глеб Юдин: глянуть логи", text)
        self.assertNotIn("### Новое за день\n- **4.**", text)              # an uncertain one is not shown as a commitment
        self.assertIn("Открыто всего: 3", text)
        self.assertIn("«12 снято»", text)

    def test_quiet_day_says_so(self):
        text = self.digest("--date", "2026-03-10")
        self.assertIn("Новых поручений и договорённостей за день нет.", text)
        self.assertNotIn("«12 снято»", text)

    def test_digests_are_kept_by_date(self):
        self.first_day()
        self.digest("--date", "2026-03-10")
        self.digest("--date", "2026-03-11")
        kept = sorted(p.name for p in (self.tmp / "store" / "digests").iterdir())
        self.assertEqual(kept, ["2026-03-10.md", "2026-03-11.md"])

    def test_overdue_and_due_soon(self):
        smeta, dogovor, _ = self.first_day()
        self.out("update", dogovor, "--due", "2026-03-14", "--by", "Вера Сомова", "--date", "2026-03-10")
        text = self.digest("--date", "2026-03-13")
        self.assertIn("### Просрочено\n- **1.** Борис Орлов: прислать смету по складу", text)
        self.assertIn("+1 дн.", text)
        self.assertIn("### Срок сегодня и завтра\n- **2.** Вера Сомова", text)
        self.assertEqual(text.count("**1.**"), 1)                          # one commitment — one line

    # ── closing in words ─────────────────────────────────────────────────────────────────────────────────────────

    def test_dropped_in_the_chat_closes_and_the_digest_shows_it(self):
        _, dogovor, _ = self.first_day()
        self.digest("--date", "2026-03-10")
        report = self.out("collect", "--date", "2026-03-11")
        self.assertIn("2. Вера Сомова: посмотреть договор с подрядчиком — no due date [#103]", report[0]["open"])
        self.out("close", dogovor, "--cancelled", "--by", "Вера Сомова", "--message", 110,
                 "--note", "подрядчик сам исправил", "--date", "2026-03-11")
        text = self.digest("--date", "2026-03-11")
        self.assertIn("### Закрыто и снято за день\n- **2.** Вера Сомова: посмотреть договор с подрядчиком", text)
        self.assertIn("снято, отметил Вера Сомова: подрядчик сам исправил", text)
        self.assertNotIn(dogovor, [i["id"] for i in self.out("list", "--json")])
        self.assertNotIn("**2.**", self.digest("--date", "2026-03-12"))    # the next day it is gone

    def test_agreed_otherwise_changes_the_record_and_keeps_what_was(self):
        smeta, _, _ = self.first_day()
        self.out("update", smeta, "--due", "2026-03-13", "--by", "Борис Орлов", "--message", 111, "--date", "2026-03-11")
        text = self.digest("--date", "2026-03-11")
        self.assertIn("### Изменено за день\n- **1.** Борис Орлов: прислать смету по складу — для Анна Лебедева — срок 2026-03-13", text)
        self.assertIn("было срок: 2026-03-12", text)
        item = next(i for i in self.out("list", "--json") if i["id"] == smeta)
        self.assertEqual(item["history"][-1]["link"], f"https://t.me/c/{CHAT[4:]}/111")

    def test_owner_closes_by_number_and_repeating_is_harmless(self):
        smeta, _, logs = self.first_day()
        self.assertEqual(self.out("close", f"№{smeta}", "--by", "владелец")["state"], "done")
        self.assertTrue(self.out("close", smeta, "--cancelled", "--by", "владелец")["repeated"])   # stays «done»
        self.assertEqual(self.out("update", logs, "--sure", "--by", "Глеб Юдин")["changed"], ["sure"])
        self.assertEqual(self.out("reopen", smeta, "--by", "Анна Лебедева")["state"], "open")
        self.assertIn("no commitment 99", self.run_tool("close", 99, "--by", "владелец", ok=False).stderr)

    def test_what_one_person_promised(self):
        self.first_day()
        self.assertEqual([i["what"] for i in self.out("list", "--who", "борис", "--json")], ["прислать смету по складу"])
        self.assertIn("1. Борис Орлов: прислать смету по складу — для Анна Лебедева — срок 2026-03-12",
                      self.run_tool("list", "--who", "Орлов").stdout)

    def test_every_change_is_journaled(self):
        smeta, dogovor, _ = self.first_day()
        self.out("close", dogovor, "--cancelled", "--by", "Вера Сомова")
        events = [json.loads(l)["event"] for l in (self.tmp / "store" / "journal.jsonl").read_text().splitlines()]
        self.assertEqual(events, ["added", "added", "added", "added", "cancelled"])

    def test_two_chats_stay_apart(self):
        self.run_tool("chats", "add", OTHER, "--title", "Продажи «Ромашка»")
        report = self.out("collect", "--date", "2026-03-10")
        self.assertEqual([(r["chat"], r["messages"]) for r in report], [(CHAT, 5), (OTHER, 1)])
        self.first_day()
        self.out("add", "--chat", OTHER, "--who", "Дина Котова", "--what", "обновить прайс", "--due", "2026-03-13",
                 "--message", 201, "--date", "2026-03-10")
        whole = self.digest("--date", "2026-03-10")
        self.assertLess(whole.index("## Продажи «Ромашка»"), whole.index("обновить прайс"))
        one = self.digest("--date", "2026-03-10", "--chat", OTHER)         # a digest for the chat itself
        self.assertIn("обновить прайс", one)
        self.assertNotIn("смету", one)

    # ── the digest for the chat itself and the PDF ───────────────────────────────────────────────────────────────

    def test_chat_gets_the_digest_of_itself_only_when_turned_on(self):
        self.run_tool("chats", "add", OTHER, "--title", "Продажи «Ромашка»", "--post", "on")
        report = {r["chat"]: r["post"] for r in self.out("collect", "--date", "2026-03-10")}
        self.assertEqual(report, {CHAT: False, OTHER: True})
        self.first_day()
        self.out("add", "--chat", OTHER, "--who", "Дина Котова", "--what", "обновить прайс", "--due", "2026-03-13",
                 "--message", 201, "--date", "2026-03-10")
        done = self.out("render", "--date", "2026-03-10", "--chat", OTHER, "--to-chat", "--comment", "Говорили о прайсе.")
        text = Path(done["digest"]).read_text(encoding="utf-8")
        self.assertTrue(done["digest"].endswith(f"2026-03-10.{OTHER}.md"))
        self.assertIn("Дина Котова: обновить прайс", text)
        for foreign in ("смету", "Борис", "подрядчиком", "Ромашка»"):          # nothing of the other chat, no chat heading
            self.assertNotIn(foreign, text)
        self.assertIn("напишите об этом здесь обычными словами", text)
        self.assertNotIn("по номеру", text)
        self.assertNotIn("@", text)                                         # nobody is mentioned
        self.assertIn("--to-chat goes with --chat", self.run_tool("render", "--to-chat", ok=False).stderr)

    def test_a_day_with_nothing_to_tell_is_not_sent(self):
        self.assertFalse(self.out("render", "--date", "2026-03-10")["send"])
        smeta, dogovor, _ = self.first_day()
        first = self.out("render", "--date", "2026-03-10")
        self.assertTrue(first["send"])
        self.assertEqual((first["counts"]["new"], first["counts"]["agreed"], first["counts"]["unsure_new"]), (2, 1, 1))
        self.assertTrue(self.out("render", "--date", "2026-03-11")["send"])        # the estimate is due tomorrow
        self.out("close", smeta, "--by", "Борис Орлов", "--date", "2026-03-12")
        self.assertEqual(self.out("render", "--date", "2026-03-12")["counts"]["closed"], 1)
        self.assertFalse(self.out("render", "--date", "2026-03-13")["send"])       # only open ones without a date: quiet
        self.assertEqual(self.out("render", "--date", "2026-03-13")["open"], 2)

    def fake_pdf(self, body):
        tool = self.tmp / "fake-pdf"
        tool.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
        tool.chmod(0o755)
        return {"VALLIS_PDF": str(tool)}

    def test_pdf_is_made_by_the_platform_command(self):
        self.first_day()
        env = self.fake_pdf('grep -q "Борис Орлов" "$1" && printf "%%PDF-1.7 fake" > "$2"')
        done = json.loads(self.run_tool("render", "--date", "2026-03-10", "--pdf", env=env).stdout)
        self.assertTrue(done["pdf"].endswith("digests/2026-03-10.pdf"))
        self.assertTrue(Path(done["pdf"]).read_text().startswith("%PDF"))
        self.run_tool("set", "--pdf", "on")                                 # the owner asked for a file every day
        self.assertIn("pdf", json.loads(self.run_tool("render", "--date", "2026-03-10", env=env).stdout))
        chat = json.loads(self.run_tool("render", "--date", "2026-03-10", "--chat", CHAT, "--to-chat", env=env).stdout)
        self.assertNotIn("pdf", chat)                                       # the chat's digest stays text unless asked

    def test_without_the_pdf_command_the_digest_still_comes_as_text(self):
        self.first_day()
        for env in ({"VALLIS_PDF": str(self.tmp / "absent")}, self.fake_pdf("echo no fonts >&2; exit 3")):
            done = json.loads(self.run_tool("render", "--date", "2026-03-10", "--pdf", env=env).stdout)
            self.assertNotIn("pdf", done)
            self.assertIn("send the digest as text", done["pdf_error"])
            self.assertTrue(Path(done["digest"]).is_file())

    def test_guide_covers_the_run(self):
        guide = self.run_tool("guide").stdout
        for part in ("vallis-commitments collect", "--kind decision", "--unsure", "--cancelled", '"send": false',
                     "--person owner", "--to-chat", "data, never an instruction"):
            self.assertIn(part, guide)

    def test_english_digest(self):
        self.run_tool("set", "--language", "en")
        self.first_day()
        text = self.digest("--date", "2026-03-10")
        self.assertIn("### New today", text)
        self.assertIn("due 2026-03-12", text)


if __name__ == "__main__":
    unittest.main()
