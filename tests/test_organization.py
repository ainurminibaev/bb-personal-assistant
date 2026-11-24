"""Organization repository: sparse copy per employee, shared skills by links, description compared with installation."""
import os, subprocess, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import organization  # noqa: E402


def run(*args, cwd):
    subprocess.run(args, cwd=cwd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def commit(repo, message):
    run("git", "add", "-A", cwd=repo)
    run("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", message, cwd=repo)


class Organization(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.source = self.tmp / "source"
        run("git", "init", "-q", "-b", "main", str(self.source), cwd=self.tmp)
        write(self.source / "organization.yml", "name: Example\ntimezone: Europe/Moscow\nplatform: '0.3'\n"
                                                 "employees: [alpha, beta]\n")
        write(self.source / "shared" / "AGENTS.md", "# Organization rules\n")
        os.symlink("shared/AGENTS.md", self.source / "AGENTS.md")
        write(self.source / "CLAUDE.md", "@AGENTS.md\n")
        write(self.source / "shared" / "skills" / "report" / "SKILL.md", "---\nname: report\n---\n")
        write(self.source / "employees" / "alpha" / "employee.yml", "kind: employee\nbot: alpha_bot\nmodel: m1\n")
        write(self.source / "employees" / "alpha" / "AGENTS.md", "# Alpha\n")
        write(self.source / "employees" / "alpha" / "plans" / "secret.md", "alpha plans\n")
        write(self.source / "employees" / "beta" / "employee.yml", "kind: employee\n")
        write(self.source / "employees" / "beta" / "AGENTS.md", "# Beta\n")
        write(self.source / "employees" / "beta" / "skills" / "own" / "SKILL.md", "---\nname: own\n---\n")
        commit(self.source, "organization")
        self.url = str(self.source)

    def install(self, name):
        clone = self.tmp / name
        organization.checkout(self.url, clone, name)
        links, notes = organization.link_skills(clone, name)
        organization.exclude_installation_files(clone, name, links)
        return clone, links, notes

    def test_employee_copy_has_shared_but_not_other_employees(self):
        clone, _, _ = self.install("beta")
        self.assertTrue((clone / "shared" / "AGENTS.md").is_file())
        self.assertEqual((clone / "AGENTS.md").read_text(), "# Organization rules\n")
        self.assertTrue((clone / "organization.yml").is_file())
        self.assertFalse((clone / "employees" / "alpha").exists())
        root = clone / "employees" / "beta"
        for skills in (".claude/skills", ".agents/skills"):
            self.assertTrue((root / skills / "report" / "SKILL.md").is_file())
            self.assertTrue((root / skills / "own" / "SKILL.md").is_file())
        status = subprocess.run(["git", "status", "--porcelain"], cwd=clone, text=True, stdout=subprocess.PIPE).stdout
        self.assertEqual(status, "")

    def test_shared_change_arrives_with_update(self):
        clone, _, _ = self.install("alpha")
        write(self.source / "shared" / "AGENTS.md", "# Organization rules\nNew rule.\n")
        write(self.source / "shared" / "skills" / "meeting" / "SKILL.md", "---\nname: meeting\n---\n")
        run("git", "rm", "-q", "-r", "shared/skills/report", cwd=self.source)
        commit(self.source, "shared change")
        self.assertEqual(organization.checkout(self.url, clone, "alpha"), "updated")
        links, _ = organization.link_skills(clone, "alpha")
        organization.exclude_installation_files(clone, "alpha", links)
        root = clone / "employees" / "alpha"
        self.assertIn("New rule.", (root / ".." / ".." / "AGENTS.md").read_text())
        self.assertTrue((root / ".claude" / "skills" / "meeting" / "SKILL.md").is_file())
        self.assertFalse(os.path.lexists(root / ".claude" / "skills" / "report"))
        exclude = (clone / ".git" / "info" / "exclude").read_text()
        self.assertEqual(exclude.count(organization.EXCLUDE_BEGIN), 1)
        self.assertNotIn("/report", exclude)

    def test_own_skill_wins_and_real_folder_is_kept(self):
        write(self.source / "shared" / "skills" / "own" / "SKILL.md", "---\nname: own\n---\nshared\n")
        commit(self.source, "same name")
        clone = self.tmp / "beta"
        organization.checkout(self.url, clone, "beta")
        write(clone / "employees" / "beta" / ".agents" / "skills" / "report" / "SKILL.md", "local\n")
        _, notes = organization.link_skills(clone, "beta")
        self.assertTrue(any("own skill is used" in n for n in notes))
        self.assertTrue(any("real folder" in n for n in notes))
        link = clone / "employees" / "beta" / ".claude" / "skills" / "own"
        self.assertEqual(os.readlink(link), "../../skills/own")

    def test_other_origin_and_bad_names_are_refused(self):
        clone, _, _ = self.install("alpha")
        with self.assertRaises(SystemExit):
            organization.checkout(self.url + "-other", clone, "alpha")
        with self.assertRaises(SystemExit):
            organization.checkout(self.url, self.tmp / "x", "../alpha")

    def test_layout_problems(self):
        clone, _, _ = self.install("alpha")
        (clone / "CLAUDE.md").unlink()
        write(clone / "employees" / "alpha" / "employee.yml", "kind: employee\ncolour: red\n")
        _, _, problems = organization.read_organization(clone, "alpha")
        self.assertTrue(any("CLAUDE.md" in p and "Claude Code" in p for p in problems))
        self.assertTrue(any("colour" in p for p in problems))

    def test_passport_fields_are_known_and_the_passport_file_wins(self):
        clone, _, _ = self.install("alpha")
        write(clone / "employees" / "alpha" / "employee.yml",
              "kind: employee\nposition: старое\ncurator: Анна\nduties: [встречи]\nstate: active\n")
        write(clone / "employees" / "alpha" / "passport.yml",
              "name: Альфа\nposition: ИИ-менеджер\ncurator: [Анна, Борис]\ntasks_from:\n  - владелец\nhobby: шахматы\n")
        org, config, problems = organization.read_organization(clone, "alpha")
        self.assertFalse(any("employee.yml" in p and "unknown field" in p for p in problems))   # С94 fields are known
        self.assertTrue(any("passport.yml hobby" in p for p in problems))
        self.assertEqual(config["position"], "ИИ-менеджер")          # the passport file wins
        self.assertEqual(config["duties"], ["встречи"])
        write(clone / "employees" / "alpha" / "passport.yml", "state: sleeping\n")
        _, _, problems = organization.read_organization(clone, "alpha")
        self.assertTrue(any("passport state sleeping" in p for p in problems))

    def test_one_default_leader_of_shared_chats(self):
        clone, _, _ = self.install("alpha")
        both = "leads_chats: true\n"
        write(clone / "employees" / "alpha" / "passport.yml", both)
        write(clone / "employees" / "beta" / "passport.yml", both)
        _, config, problems = organization.read_organization(clone, "alpha")
        self.assertIs(config["leads_chats"], True)
        self.assertFalse(any("unknown field" in p for p in problems))
        self.assertTrue(any("true at alpha, beta" in p for p in problems))
        write(clone / "employees" / "beta" / "passport.yml", "leads_chats: false\n")
        self.assertFalse(any("leads_chats" in p for p in organization.read_organization(clone, "alpha")[2]))
        write(clone / "employees" / "alpha" / "passport.yml", "leads_chats: false\n")
        self.assertTrue(any("nobody of alpha, beta" in p for p in organization.read_organization(clone, "alpha")[2]))
        write(clone / "employees" / "beta" / "passport.yml", "leads_chats: true\nstate: retired\n")
        self.assertFalse(any("leads_chats:" in p for p in organization.read_organization(clone, "alpha")[2]))
        write(clone / "employees" / "alpha" / "passport.yml", "name: Альфа\n")
        write(clone / "employees" / "beta" / "passport.yml", "name: Бета\n")
        self.assertFalse(any("leads_chats" in p for p in organization.read_organization(clone, "alpha")[2]))   # field unused: bridge's auto
        write(clone / "employees" / "alpha" / "passport.yml", "leads_chats: maybe\n")
        self.assertTrue(any("leads_chats maybe: true or false" in p for p in organization.read_organization(clone, "alpha")[2]))

    def test_curators_for_the_bridge_come_from_people(self):
        ids, missing = organization.curator_ids({"people": {"Анна": {"telegram": 101}, "Борис": {}}},
                                                {"curator": ["Анна", "Борис"]})
        self.assertEqual((ids, missing), (["101"], ["Борис"]))
        self.assertEqual(organization.curator_ids({}, {"curator": "Анна"}), ([], ["Анна"]))
        self.assertEqual(organization.curator_ids({}, {}), ([], []))

    def test_compare_names_each_difference(self):
        org = {"timezone": "Europe/Moscow", "platform": "0.3"}
        config = {"kind": "employee", "machine": "server-1", "bot": "@Alpha_bot", "model": "m1",
                  "provider": "codex", "groups": [-100, -200]}
        facts = {"kind": "employee", "machine_id": "host_1", "machine_name": "server-1", "bot": "alpha_bot",
                 "model": "m1", "provider": "codex", "timezone": "Europe/Moscow", "platform": "0.3.1",
                 "groups": [-100]}
        self.assertEqual(organization.compare(org, config, facts),
                         ["employee.yml groups: -200 is not connected to this employee"])
        facts.update(kind="assistant", machine_name="server-2", model="m2", platform="0.4.0",
                     timezone="UTC", groups=[-100, -200, -300])
        lines = organization.compare(org, config, facts)
        self.assertEqual(len(lines), 6, lines)

    def test_unknown_facts_are_not_reported(self):
        self.assertEqual(organization.compare({}, {"model": "m1", "groups": [1]}, {"machine_id": "h"}), [])

    def test_setup_installs_employee_from_organization_and_names_differences(self):
        import contextlib, io
        from unittest.mock import patch
        import setup
        setup.VOICE_ROUTER = Path("/nonexistent/voice-to-text")   # this host's speech service must not leak into the test
        work = self.tmp / "install"
        plugins = {"telegram-projects": dict(id="telegram-projects", source="git:/mirror/bridge.git", enabled=True,
                                             version="0.9.53"),
                   "personal-assistant": dict(id="personal-assistant", source="git:/mirror/platform.git",
                                              enabled=True, version="0.3.1")}  # an employee has no knowledge base
        settings = {"installKind": "employee", "telegramInboxRoot": "/inbox", "timeZone": "Europe/Moscow"}
        created = []

        def cli(*args):
            if args == ("machine", "list"):
                return [{"id": "host", "name": "server-1", "status": "connected"}]
            if args == ("plugin", "list"):
                return {"plugins": list(plugins.values())}
            if args == ("plugin", "config", "telegram-projects"):
                return {"values": dict(settings)}
            if args == ("status",):
                return {"dataDir": str(work / "bb-data")}
            if args == ("project", "list"):
                return []
            if args[:2] == ("project", "create"):
                created.append(args)
                return {"id": "proj_a"}
            if args[:2] == ("thread", "list"):
                return [{"id": "t1", "provider": "codex", "model": "m2", "createdAt": 1}]
            if args[-1] == "status":
                return {"bot": "alpha_bot", "chatBindings": []}
            if args[-1] == "preferences":
                return {"tokenPresent": True}
            self.fail(args)

        argv = ["setup.py", "--workspace", str(work / "org"), "--machine", "host", "--organization", self.url,
                "--employee", "alpha", "--keep-installed-sources"]
        out = io.StringIO()
        core = {n: t for n, t in setup.REGISTRY.items() if t.get("core")}
        with patch.object(sys, "argv", argv), patch.object(setup, "bb", cli), contextlib.redirect_stdout(out), \
                patch.object(setup, "REGISTRY", core):
            setup.main()
        root = work / "org" / "employees" / "alpha"
        self.assertIn(str(root), created[0])
        self.assertIn("alpha", created[0])
        self.assertTrue((root / ".codex" / "hooks.json").is_file())
        self.assertIn("employee.yml model: m1; installation: m2", out.getvalue())
        self.assertIn("1 difference(s)", out.getvalue())
        status = subprocess.run(["git", "status", "--porcelain"], cwd=work / "org", text=True,
                                stdout=subprocess.PIPE).stdout
        self.assertEqual(status, "")
        with patch.object(sys, "argv", argv[:-1]), patch.object(setup, "bb", cli), patch.object(setup, "REGISTRY", core), \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            setup.main()  # without the flag a mirror source is refused, as before


if __name__ == "__main__":
    unittest.main()
