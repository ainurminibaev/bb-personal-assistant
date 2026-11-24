"""Platform tools: selection by the description, MCP connections, and one state for every line of the list."""
import json, os, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tools  # noqa: E402

REGISTRY = tools.load()


class FakeAccount:
    def __init__(self, home, programs=()):
        self.name, self.home, self.other, self.programs = "agent", Path(home), False, set(programs)

    def which(self, program):
        return f"/bin/{program}" if program in self.programs else None

    def run(self, *args, **kwargs):
        raise AssertionError(f"unexpected command {args}")

    def give(self, path):
        pass


class Selection(unittest.TestCase):
    def test_every_line_is_known_and_core_has_a_source(self):
        self.assertIn("telegram-projects", REGISTRY)
        for name, tool in REGISTRY.items():
            self.assertIn(tool["kind"], tools.TOOL_KINDS, name)
            self.assertTrue(tool.get("what"), name)
            if tool["kind"] == "plugin":
                self.assertTrue(tool.get("source"), name)

    def test_commitments_is_a_plain_command_of_the_package(self):
        tool = dict(REGISTRY["commitments"])
        self.assertTrue((tools.PACKAGE / tool["path"]).is_file())
        self.assertEqual(sorted(tool["installs"]), ["assistant", "employee"])
        with tempfile.TemporaryDirectory() as home:
            home = Path(home)
            bin_dir = home / ".local" / "bin"
            bin_dir.mkdir(parents=True)
            env = dict(os.environ, HOME=str(home), PATH=f"{bin_dir}:{os.environ['PATH']}",
                       VALLIS_COMMITMENTS_DIR=str(home / "store"))
            account = FakeAccount(home)
            account.which = lambda p: __import__("shutil").which(p, path=env["PATH"])
            account.run = lambda *a, **k: subprocess.run(list(a), env=env, text=True, capture_output=True)
            with patch.object(tools, "_cli_dir", lambda a: bin_dir):
                row = tools.tool_state("commitments", tool, (True, "platform", ""), account, {}, home, {})
                self.assertEqual(row["state"], "needs_setup")
                self.assertIsNone(tools.install_cli(tool, account))
                self.assertTrue((bin_dir / "vallis-commitments").is_file())
                self.assertFalse((home / ".config" / "vallis").exists())      # no keys, nothing initialized
                row = tools.tool_state("commitments", tool, (True, "platform", ""), account, {}, home, {})
                self.assertEqual(row["state"], "installed", row)

    def test_bridge_source_matches_the_migration(self):
        from migrate_bridge import TRACKING
        self.assertEqual(REGISTRY["telegram-projects"]["source"], TRACKING)

    def test_owner_long_recordings_are_not_in_the_platform(self):
        text = tools.REGISTRY.read_text(encoding="utf-8")
        self.assertNotIn("local-transcribe", text)

    def test_general_by_default_and_exclusions(self):
        decisions, problems = tools.select(REGISTRY, "employee")
        self.assertEqual(problems, [])
        self.assertTrue(decisions["image-gen"][0])
        self.assertFalse(decisions["knowledge-base"][0])  # the owner's library is the assistant's only
        organization = {"tools": {"exclude": ["image-gen", "telegram-projects", "nothing"]}}
        decisions, problems = tools.select(REGISTRY, "employee", organization)
        self.assertEqual(decisions["image-gen"], (False, "organization", "excluded in organization.yml"))
        self.assertTrue(decisions["telegram-projects"][0])
        self.assertTrue(any("telegram-projects is the base" in p for p in problems))
        self.assertTrue(any("nothing is not in the platform list" in p for p in problems))
        employee = {"tools": {"add": ["image-gen"], "exclude": ["context-mode"]}}
        decisions, _ = tools.select(REGISTRY, "employee", organization, employee)
        self.assertEqual(decisions["image-gen"], (True, "employee", ""))  # the employee's own line wins
        self.assertEqual(decisions["context-mode"][:2], (False, "employee"))

    def test_thread_cleanup_comes_to_every_kind_and_installs_from_its_folder(self):
        for kind in ("assistant", "employee", "agents"):
            self.assertTrue(tools.select(REGISTRY, kind)[0]["thread-cleanup"][0], kind)
        calls = []
        self.assertIsNone(tools.install_tool("thread-cleanup", REGISTRY["thread-cleanup"], None, {}, lambda *a: calls.append(a), {}))
        self.assertIn("--subdirectory", calls[0])
        self.assertEqual(calls[0][calls[0].index("--subdirectory") + 1], "plugins/thread-cleanup")

    def test_agents_get_no_bridge(self):
        decisions, _ = tools.select(REGISTRY, "agents")
        self.assertFalse(decisions["telegram-projects"][0])
        self.assertTrue(decisions["personal-assistant"][0])
        self.assertTrue(decisions["image-gen"][0])


class Mcp(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.organization = {"mcp": {
            "tasks": {"url": "https://tasks.example.com/mcp", "token_env": "TASKS_TOKEN", "employees": ["alpha"]},
            "wiki": {"url": "https://wiki.example.com/mcp", "employees": "all"},
            "other": {"url": "https://other.example.com/mcp", "employees": ["beta"]},
        }}

    def test_named_employees_get_organization_servers(self):
        servers, problems = tools.mcp_servers(self.organization, {}, "alpha")
        self.assertEqual(problems, [])
        self.assertEqual(sorted(servers), ["tasks", "wiki"])
        self.assertEqual(tools.write_mcp(self.root, servers), {})
        claude = json.loads((self.root / ".mcp.json").read_text())["mcpServers"]
        self.assertEqual(claude["tasks"]["headers"], {"Authorization": "Bearer ${TASKS_TOKEN}"})
        codex = (self.root / ".codex" / "config.toml").read_text()
        self.assertIn('bearer_token_env_var = "TASKS_TOKEN"', codex)
        rows = {r["id"]: r for r in tools.mcp_state(self.root, servers, {})}
        self.assertEqual(rows["mcp:tasks"]["state"], "needs_login")
        self.assertEqual(rows["mcp:tasks"]["level"], "organization")
        self.assertEqual(rows["mcp:wiki"]["state"], "installed")
        # A server removed from the description leaves both files; own entries stay.
        own = json.loads((self.root / ".mcp.json").read_text())
        own["mcpServers"]["mine"] = {"type": "http", "url": "https://mine"}
        (self.root / ".mcp.json").write_text(json.dumps(own))
        (self.root / ".codex" / "config.toml").write_text('[mcp_servers.mine]\nurl = "https://mine"\n' + codex)
        tools.write_mcp(self.root, {"wiki": servers["wiki"]})
        claude = json.loads((self.root / ".mcp.json").read_text())["mcpServers"]
        self.assertEqual(sorted(claude), ["mine", "wiki"])
        codex = (self.root / ".codex" / "config.toml").read_text()
        self.assertNotIn("tasks", codex)
        self.assertIn("mine", codex)

    def test_values_do_not_belong_in_the_description(self):
        employee = {"mcp": {"crm": {"url": "https://crm.example.com/mcp", "token": "abc"},
                            "bad": {"url": "ftp://x"}}}
        servers, problems = tools.mcp_servers({}, employee, "alpha")
        self.assertIn("crm", servers)
        self.assertTrue(any("token not used" in p for p in problems))
        self.assertTrue(any("bad: url" in p for p in problems))
        _, problems = tools.mcp_servers({"mcp": {"x": {"url": "https://x"}}}, {}, "alpha")
        self.assertTrue(any("employees is missing" in p for p in problems))

    def test_file_kept_in_git_is_not_changed(self):
        (self.root / ".mcp.json").write_text('{"mcpServers": {}}\n')
        subprocess.run(["git", "add", ".mcp.json"], cwd=self.root, check=True)
        servers, _ = tools.mcp_servers(self.organization, {}, "alpha")
        errors = tools.write_mcp(self.root, servers)
        self.assertIn("kept in git", errors["tasks"])
        self.assertEqual((self.root / ".mcp.json").read_text(), '{"mcpServers": {}}\n')
        rows = {r["id"]: r for r in tools.mcp_state(self.root, servers, errors)}
        self.assertEqual(rows["mcp:tasks"]["state"], "error")


class States(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.account = FakeAccount(self.home)

    def state(self, name, plugins=None, errors=None, decision=(True, "platform", ""), bb_home=None):
        return tools.tool_state(name, REGISTRY[name], decision, self.account, plugins or {}, bb_home or self.home,
                                errors or {})

    def test_plugin_states(self):
        self.assertEqual(self.state("image-gen")["state"], "needs_setup")
        self.assertEqual(self.state("image-gen", errors={"image-gen": "no access"})["state"], "error")
        installed = {"image-gen": {"id": "image-gen", "enabled": True}}
        self.assertEqual(self.state("image-gen", installed)["state"], "needs_login")  # no Codex login
        (self.home / ".codex").mkdir()
        (self.home / ".codex" / "auth.json").write_text("{}")
        self.assertEqual(self.state("image-gen", installed)["state"], "installed")
        disabled = {"image-gen": {"id": "image-gen", "enabled": False}}
        self.assertEqual(self.state("image-gen", disabled)["action"], "bb plugin enable image-gen")
        self.assertEqual(self.state("image-gen", decision=(False, "organization", "excluded"))["state"], "excluded")

    def browser(self, data_dir, script=None):
        """runtime/chrome in a fake BB data folder: a script standing in for the browser."""
        runtime = data_dir / tools.BROWSER_HOST_DATA / "runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        if script is not None:
            (runtime / "chrome").write_text("#!/bin/sh\n" + script)
            (runtime / "chrome").chmod(0o755)
        return runtime

    def browser_state(self, plugins, data_dir):
        with patch.object(tools, "BROWSER_NAMES", ()):
            return tools.tool_state("browser", REGISTRY["browser"], (True, "platform", ""), self.account,
                                    plugins, self.home, {}, data_dir)

    def test_plugin_called_otherwise_and_its_browser_must_start(self):
        running = {"browser-automation": {"id": "browser-automation", "enabled": True}}
        data = self.home / "bb-data"
        self.assertEqual(self.browser_state({}, data)["state"], "needs_setup")  # no plugin
        row = self.browser_state(running, data)
        self.assertEqual((row["state"], row["reason"]), ("needs_setup", "no browser on this server"))
        self.browser(data, "echo 'chrome: error while loading shared libraries: libnss3.so: cannot open' >&2; exit 127\n")
        row = self.browser_state(running, data)
        self.assertEqual(row["state"], "needs_setup")
        self.assertIn("missing libraries: libnss3.so", row["reason"])
        self.browser(data, "echo 'Google Chrome for Testing 158.0.8127.36'\n")
        self.assertEqual(self.browser_state(running, data)["state"], "installed")

    def test_browser_install_checks_the_sum_and_keeps_a_working_browser(self):
        tool = REGISTRY["browser"]
        data = self.home / "bb-data"
        self.browser(data)
        def fetch(url, path):
            self.assertIn(tool["runtime"]["chrome_for_testing"]["version"], url)
            Path(path).write_bytes(b"not the pinned archive")
        with patch.object(tools, "BROWSER_NAMES", ()), patch("platform.machine", return_value="x86_64"), \
                patch("platform.system", return_value="Linux"):
            error = tools.install_browser(tool, data, fetch=fetch)
            self.assertIn("checksum", error)
            self.assertFalse((data / tools.BROWSER_HOST_DATA / "runtime" / "chrome").exists())
            self.assertFalse((data / tools.BROWSER_HOST_DATA / "runtime" / "chrome-for-testing").exists())
            self.browser(data, "echo 'Chromium 141'\n")  # one already put by hand (another build) is recognised
            self.assertIsNone(tools.install_browser(tool, data, fetch=lambda *a: self.fail("downloaded again")))
        self.assertIsNone(tools.install_browser({"kind": "plugin"}, data))  # a line without a runtime

    def test_skill_and_hook(self):
        self.assertEqual(self.state("agent-compat")["state"], "needs_setup")
        self.assertEqual(self.state("agent-compat", {"shared-guides": {}})["state"], "installed")
        for d in tools.SKILL_DIRS:
            (self.home / d / "agent-compat").mkdir(parents=True)
            (self.home / d / "agent-compat" / "SKILL.md").write_text("---\nname: agent-compat\n---\n")
        self.assertEqual(self.state("agent-compat")["state"], "installed")
        self.assertEqual(self.state("claude-md-guard")["state"], "needs_setup")
        (self.home / ".claude" / "hooks").mkdir(parents=True)
        (self.home / ".claude" / "hooks" / "claude_md_guard.py").write_text("")
        (self.home / ".claude" / "settings.json").write_text('{"hooks": {"PreToolUse": [{"hooks": '
                                                              '[{"command": "python3 claude_md_guard.py"}]}]}}')
        self.assertEqual(self.state("claude-md-guard")["state"], "installed")

    def test_mail_needs_its_key(self):
        platform = {"personal-assistant": {"id": "personal-assistant", "enabled": True}}
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENTMAIL_API_KEY", None)
            row = self.state("agent-mail", platform)
            self.assertEqual(row["state"], "needs_setup")
            self.assertIn("AGENTMAIL_API_KEY", row["reason"])
            (self.home / ".secrets").mkdir()
            (self.home / ".secrets" / "agentmail.key").write_text("k")
            self.assertEqual(self.state("agent-mail", platform)["state"], "installed")
        self.assertEqual(self.state("agent-mail")["reason"], "the personal-assistant plugin is not installed")

    def test_service_reads_its_own_state(self):
        tool = dict(REGISTRY["speech"], state_file=str(self.home / "state.json"))
        row = tools.tool_state("speech", tool, (True, "platform", ""), self.account, {}, self.home, {})
        self.assertEqual(row["state"], "needs_setup")
        (self.home / "state.json").write_text(json.dumps({"state": "needs_setup", "reason": "no network: model",
                                                         "profiles": ["ru"]}))
        row = tools.tool_state("speech", tool, (True, "platform", ""), self.account, {}, self.home, {})
        self.assertEqual((row["state"], row["reason"]), ("needs_setup", "no network: model"))
        (self.home / "state.json").write_text(json.dumps({"state": "installed", "profiles": ["ru"]}))
        self.account.programs.add("voice-to-text")
        checked = []

        def systemctl(args, **kwargs):
            checked.append(args[-1])
            return subprocess.CompletedProcess(args, 0)
        with patch.object(tools.subprocess, "run", systemctl):
            row = tools.tool_state("speech", tool, (True, "platform", ""), self.account, {}, self.home, {})
        self.assertEqual(row["state"], "installed")
        self.assertEqual(checked, ["bb-speech-ru.socket"])  # only the profiles the service has
        self.account.programs.discard("voice-to-text")      # status from a PATH without /usr/local/bin
        with patch.object(tools.subprocess, "run", systemctl):
            row = tools.tool_state("speech", tool, (True, "platform", ""), self.account, {}, self.home, {})
        self.assertEqual(row["state"], "installed")

    def test_pdf_status_is_what_really_starts(self):
        tool = dict(REGISTRY["pdf"], check_commands=["vallis-pdf"])
        self.account.programs.add("vallis-pdf")
        said = {}
        self.account.run = lambda *args, **kw: subprocess.CompletedProcess(args, 0, json.dumps(said, ensure_ascii=False), "")
        state = lambda: tools.tool_state("pdf", tool, (True, "platform", ""), self.account, {}, self.home, {})
        said.update({"PDF": True, "чтение PDF": False, "схема": False})
        row = state()
        self.assertEqual((row["state"], row["reason"]), ("needs_setup", "PDF: стоит; чтение PDF: не стоит; схема: не стоит"))
        said["чтение PDF"] = True                          # the scheme needs the account's browser: not required
        row = state()
        self.assertEqual((row["state"], row["reason"]), ("installed", "PDF: стоит; чтение PDF: стоит; схема: не стоит"))
        self.assertEqual(REGISTRY["pdf"]["check_run"], ["vallis-pdf", "--check"])
        self.assertIn("vallis-pdf", REGISTRY["pdf"]["check_commands"])        # an account install (--prefix) counts
        self.account.run = lambda *args, **kw: subprocess.CompletedProcess(args, 1, "", "boom")
        self.assertEqual(state()["state"], "error")

    def test_a_broken_check_is_an_error_row(self):
        self.account.programs.add("context-mode")
        self.account.programs.add("codex")
        row = self.state("context-mode")
        self.assertEqual(row["state"], "error")

    def test_report_lists_every_line_with_one_state(self):
        decisions, _ = tools.select(REGISTRY, "employee")
        rows = tools.states(REGISTRY, decisions, self.account, {}, self.home, {})
        self.assertEqual([r["id"] for r in rows], list(REGISTRY))
        for row in rows:
            self.assertIn(row["state"], tools.STATES)
            self.assertEqual(set(row), {"id", "what", "kind", "level", "state", "reason", "action"})
        data = tools.report("employee", rows, [], "alpha")
        self.assertEqual(data["format"], 1)
        json.dumps(data)


if __name__ == "__main__":
    unittest.main()


class Secrets(unittest.TestCase):
    """The credentials store line (С37): description without values, MCP keys from the store, installation and state."""

    def setUp(self):
        self.organization = {"secrets": {"admin": ["age1admin"], "keys": {
            "TASKS_TOKEN": {"what": "трекер", "to": ["alpha"]},
            "MAIL_KEY": {"what": "почта", "to": "all"},
            "OTHER_KEY": {"what": "чужой", "to": ["beta"]}}}}
        self.employee = {"secrets": {"keys": {"SITE_KEY": {"what": "сайт"}}}}

    def test_description_names_keys_without_values(self):
        keys, admins, problems = tools.described_secrets(self.organization, self.employee, "alpha")
        self.assertEqual(problems, [])
        self.assertEqual(admins, ["age1admin"])
        self.assertEqual(keys, {"TASKS_TOKEN": ("organization", "трекер"), "MAIL_KEY": ("organization", "почта"),
                                "SITE_KEY": ("employee", "сайт")})
        bad = {"secrets": {"keys": {"X_KEY": {"what": "x", "value": "abc"}, "lower": {"what": "y"},
                                    "Y_KEY": {"what": "y"}}, "admin": ["nope"]}}
        _, _, problems = tools.described_secrets(bad, {"secrets": {"keys": {"Z": {"what": "z", "to": "all"}}}}, "alpha")
        text = "\n".join(problems)
        self.assertIn("X_KEY: value not used", text)
        self.assertIn("keys.lower", text)
        self.assertIn("Y_KEY: to is missing", text)
        self.assertIn("secrets.admin", text)
        self.assertIn("to belongs to organization.yml", text)

    def test_mcp_key_from_the_store(self):
        root = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        servers = {"tasks": ("organization", "https://tasks.example.com/mcp", "TASKS_TOKEN")}
        self.assertEqual(tools.write_mcp(root, servers, {"TASKS_TOKEN"}), {})
        entry = json.loads((root / ".mcp.json").read_text())["mcpServers"]["tasks"]
        self.assertEqual(entry["headersHelper"], "vallis-secret header TASKS_TOKEN")
        self.assertNotIn("headers", entry)
        row = tools.mcp_state(root, servers, {}, {"TASKS_TOKEN"})[0]
        self.assertEqual(row["state"], "needs_login")
        self.assertIn("Claude Code reads TASKS_TOKEN from the store", row["reason"])

    def account(self, home, bin_dir):
        account = FakeAccount(home)
        # a sops already on this machine's PATH would be kept by install_cli: the test downloads its own
        rest = ":".join(d for d in os.environ["PATH"].split(":") if d and not os.path.exists(os.path.join(d, "sops")))
        env = dict(os.environ, HOME=str(home), PATH=f"{bin_dir}:{rest}",
                   VALLIS_AGE_KEY=str(home / ".config/vallis/age.key"),
                   VALLIS_SECRETS_DIR=str(home / ".local/share/vallis/secrets"),
                   VALLIS_ADMIN_RECIPIENTS=str(home / ".config/vallis/admin.recipients"))
        account.which = lambda p: __import__("shutil").which(p, path=env["PATH"])
        account.run = lambda *a, **k: subprocess.run(list(a), env=env, text=True, capture_output=True)
        return account

    @unittest.skipUnless(__import__("shutil").which("age-keygen"), "age is needed")
    def test_install_and_state(self):
        import hashlib, shutil
        home = Path(tempfile.mkdtemp())
        bin_dir = home / "bin"
        fake = home / "fake-sops"
        fake.write_text(f"#!/bin/sh\n[ \"$1\" = --version ] && echo 'sops 3.13.3' && exit 0\n"
                        f"exec {sys.executable} {Path(__file__).resolve().parent / 'fake_sops.py'} \"$@\"\n")
        digest = hashlib.sha256(fake.read_bytes()).hexdigest()
        tool = dict(REGISTRY["secrets-store"])
        arch = tools.ARCH[os.uname().machine]
        tool["download"] = {"sops": {"version": "v3.13.3", "url": fake.as_uri().replace(".", ".", 1) + "?{arch}",
                                     "sha256": {arch: digest}}}
        tool["download"]["sops"]["url"] = fake.as_uri()
        account = self.account(home, bin_dir)
        with patch.object(tools, "_cli_dir", lambda a: bin_dir):
            row = tools.tool_state("secrets-store", tool, (True, "platform", ""), account, {}, home, {})
            self.assertEqual(row["state"], "needs_setup")
            self.assertIsNone(tools.install_cli(tool, account, ["age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"]))
            self.assertTrue((bin_dir / "vallis-secret").is_file() and (bin_dir / "sops").is_file())
            self.assertIn("age1qqq", (home / ".config/vallis/admin.recipients").read_text())
            (home / ".config/vallis/admin.recipients").unlink()
            # once the account has its key, age is not needed to work: the state does not ask for it
            no_age = self.account(home, bin_dir)
            no_age.which = lambda p, w=account.which: None if p in ("age", "age-keygen") else w(p)
            row = tools.tool_state("secrets-store", tool, (True, "platform", ""), no_age, {}, home, {})
            self.assertEqual(row["state"], "installed", row)
            row = tools.tool_state("secrets-store", tool, (True, "platform", ""), account, {}, home, {})
            self.assertEqual((row["state"], row["reason"]), ("installed", "keys in the store: 0"))
            put = account.run("vallis-secret", "put", "SITE_KEY", "--generate", "--what", "сайт")
            self.assertEqual(put.returncode, 0, put.stderr)
            rows = {r["id"]: r["state"] for r in tools.secret_rows(
                account, {"SITE_KEY": ("employee", "сайт"), "MAIL_KEY": ("organization", "почта")})}
            self.assertEqual(rows, {"secret:SITE_KEY": "installed", "secret:MAIL_KEY": "needs_setup"})
            # a wrong pinned sum leaves nothing behind
            (bin_dir / "sops").unlink()
            tool["download"]["sops"]["sha256"] = {arch: "0" * 64}
            self.assertIn("does not match", tools.install_cli(tool, account))
            self.assertFalse((bin_dir / "sops").exists())
        shutil.rmtree(home, ignore_errors=True)

    def test_pinned_sops_for_both_architectures(self):
        sops = REGISTRY["secrets-store"]["download"]["sops"]
        self.assertEqual(sorted(sops["sha256"]), ["amd64", "arm64"])
        self.assertTrue(sops["url"].startswith("https://github.com/getsops/sops/releases/download/"))
        self.assertIn(sops["version"], sops["url"])


class LeakCheck(unittest.TestCase):
    def test_the_hook_finds_the_installed_command_and_warns_without_it(self):
        repo = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        program = repo / "bin" / "vallis-secret"
        program.parent.mkdir()
        program.write_text("#!/bin/sh\necho checked \"$@\"\n")
        program.chmod(0o755)
        self.assertIsNone(tools.install_leak_check(repo, program))
        hook = repo / ".git" / "hooks" / "pre-commit"
        env = {"PATH": "/usr/bin:/bin", "HOME": str(repo)}
        done = subprocess.run(["sh", str(hook)], env=env, capture_output=True, text=True)
        self.assertEqual(done.stdout.strip(), "checked leaks --git-staged")
        program.unlink()
        done = subprocess.run(["sh", str(hook)], env=env, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0)
        self.assertIn("NOT checked", done.stderr)
        (repo / ".git" / "hooks" / "pre-commit").write_text("#!/bin/sh\nmy own\n")
        self.assertIn("someone's own", tools.install_leak_check(repo, program))

    def test_accesses_for_self_awareness(self):
        account = FakeAccount(tempfile.mkdtemp(), programs={"vallis-secret"})
        listed = {"secrets": [{"name": "SITE_PASSWORD", "what": "вход на сайт", "level": "agent", "source": "agent",
                               "state": "present", "created": "2026-11-23"}]}
        account.run = lambda *a, **k: subprocess.CompletedProcess(a, 0, json.dumps(listed), "")
        rows = tools.access_rows(account)
        self.assertEqual(rows, [{"name": "SITE_PASSWORD", "what": "вход на сайт", "level": "agent",
                                 "source": "agent", "state": "present"}])
        self.assertEqual(tools.report("employee", [], [], "alpha", accesses=rows)["accesses"], rows)
        self.assertEqual(tools.access_rows(FakeAccount(tempfile.mkdtemp())), [])


class PinnedArchive(unittest.TestCase):
    """A pinned tar.gz download (age): its named programs land next to the command; a wrong sum installs nothing."""

    def test_archive_programs_next_to_the_command(self):
        import hashlib, io, tarfile
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w:gz") as tar:
                for name in ("age/age", "age/age-keygen", "age/LICENSE"):
                    data = b"#!/bin/sh\necho v1.3.2\n"
                    info = tarfile.TarInfo(name); info.size = len(data); tar.addfile(info, io.BytesIO(data))
            archive = d / "age.tar.gz"; archive.write_bytes(buf.getvalue())
            arch = tools.ARCH.get(os.uname().machine)
            spec = {"version": "v1.3.2", "version_args": ["--version"], "url": archive.as_uri(),
                    "files": ["age/age", "age/age-keygen"], "sha256": {arch: hashlib.sha256(buf.getvalue()).hexdigest()}}
            bin_dir = d / "bin"
            (d / "cmd").write_text("#!/bin/sh\nexit 0\n")

            class Acc:
                home = d
                def which(self, p): return str(bin_dir / p) if (bin_dir / p).exists() else None
                def run(self, *a, **k): return subprocess.CompletedProcess(a, 0, "", "")
                def give(self, p): pass
            tool = {"download": {"age": spec}, "path": "cmd", "requires": ["age", "age-keygen"]}
            with patch.object(tools, "_cli_dir", lambda account: bin_dir), patch.object(tools, "PACKAGE", d):
                self.assertIsNone(tools.install_cli(tool, Acc()))
                self.assertTrue((bin_dir / "age").exists() and (bin_dir / "age-keygen").exists())
                self.assertFalse((bin_dir / "LICENSE").exists())
                self.assertEqual(list(bin_dir.glob(".age-*")), [])               # the archive is not kept
                (bin_dir / "age").unlink(); (bin_dir / "age-keygen").unlink()
                spec["sha256"][arch] = "0" * 64
                self.assertIn("sha256", tools.install_cli(tool, Acc()))
                self.assertFalse((bin_dir / "age").exists())
class Spaces(unittest.TestCase):
    """Where the agent works (С53): spaces refer to tools, MCP, connections, folders and groups; one row each."""

    def setUp(self):
        self.clone = Path(tempfile.mkdtemp())
        (self.clone / "shared" / "knowledge").mkdir(parents=True)
        (self.clone / "shared" / "knowledge" / "where.md").write_text("# Где что лежит\n")
        self.organization = {"connections": {"github": {"org": "example"}}, "spaces": {
            "tasks": {"what": "задачник команды", "kind": "tracker", "via": "mcp:tracker", "to": "all",
                      "access": "write", "map": "shared/knowledge/where.md#задачник"},
            "knowledge": {"what": "знания команды", "kind": "knowledge", "via": "folder:shared/knowledge", "to": "all",
                          "access": "write"},
            "drive": {"what": "диск организации", "kind": "drive", "via": "tool:google-workspace", "to": ["alpha"],
                      "access": "read", "map": "shared/knowledge/drive.md"},
            "repos": {"what": "репозитории", "kind": "repositories", "via": "connections:github", "to": "all"},
            "other": {"what": "чужое", "kind": "other", "via": "groups", "to": ["beta"]}}}
        self.employee = {"groups": [-100, -200], "spaces": {
            "exclude": ["repos"],
            "chats": {"what": "чаты и группы", "kind": "chats", "via": "groups", "access": "write"}}}

    def test_description(self):
        spaces, problems = tools.described_spaces(self.organization, self.employee, "alpha")
        self.assertEqual(problems, [])
        self.assertEqual(sorted(spaces), ["chats", "drive", "knowledge", "tasks"])
        self.assertEqual(spaces["drive"]["access"], "read")
        self.assertEqual(spaces["chats"]["level"], "employee")
        bad = {"spaces": {"x": {"what": "x", "kind": "nope", "via": "groups", "to": "all"},
                          "y": {"what": "y", "kind": "drive", "via": "https://drive", "to": "all"},
                          "z": {"what": "z", "kind": "drive", "via": "folder:../etc", "to": "all"},
                          "w": {"what": "w", "kind": "drive", "via": "groups"},
                          "v": {"what": "v", "kind": "drive", "via": "groups", "to": "all", "token": "abc"}}}
        spaces, problems = tools.described_spaces(bad, {"spaces": {"exclude": ["none"]}}, "alpha")
        text = "\n".join(problems)
        for part in ("spaces.x:", "spaces.y:", "inside the organization repository", "w: to is missing",
                     "v: token not used", "none is not a space"):
            self.assertIn(part, text)
        self.assertEqual(sorted(spaces), ["v"])

    def test_rows_take_the_state_of_what_they_refer_to(self):
        spaces, _ = tools.described_spaces(self.organization, self.employee, "alpha")
        tool_rows = [{"id": "mcp:tracker", "state": "installed", "action": ""},
                     {"id": "google-workspace", "state": "needs_login", "reason": "no login: ~/.secrets/x",
                      "action": "log in"}]
        rows = {r["id"]: r for r in tools.space_rows(spaces, tool_rows, self.clone, self.organization, self.employee)}
        self.assertEqual(rows["tasks"]["state"], "ready")
        self.assertEqual(rows["knowledge"]["state"], "ready")
        self.assertEqual((rows["drive"]["state"], rows["drive"]["reason"]), ("needs_login", "нужен вход"))
        self.assertEqual((rows["chats"]["state"], rows["chats"]["reason"]), ("ready", "групп: 2"))
        for row in rows.values():  # words without paths or addresses: a guest sees what and reason
            self.assertNotIn("/", row["reason"] + row["what"])
        tool_rows[1]["state"] = "installed"
        rows = {r["id"]: r for r in tools.space_rows(spaces, tool_rows, self.clone, self.organization, self.employee)}
        self.assertEqual((rows["drive"]["state"], rows["drive"]["reason"]), ("needs_setup", "нет описания, что где лежит"))
        rows = {r["id"]: r for r in tools.space_rows(spaces, [], self.clone, self.organization, {"spaces": {}})}
        self.assertEqual(rows["tasks"]["reason"], "подключение не описано")
        self.assertEqual(rows["chats"]["reason"], "групп нет")

    def test_report_carries_spaces(self):
        data = tools.report("employee", [], [], "alpha", [{"id": "tasks"}])
        self.assertEqual(data["format"], 1)
        self.assertEqual(data["spaces"], [{"id": "tasks"}])
        self.assertEqual(tools.report("agents", [], [])["spaces"], [])


class CatalogLines(unittest.TestCase):
    """С87: with the tool catalog on, a line's MCP process goes to .bb/tool-sources.json, not to the engines."""

    def test_catalog_on_only_when_installed_and_enabled(self):
        self.assertFalse(tools.catalog_on({}))
        self.assertTrue(tools.catalog_on({"tool-catalog": {"id": "tool-catalog", "enabled": True}}))
        self.assertFalse(tools.catalog_on({"tool-catalog": {"id": "tool-catalog", "enabled": False}}))

    def test_written_with_mode_600_beside_other_sources_and_removed_when_unselected(self):
        root = Path(tempfile.mkdtemp())
        (root / ".bb").mkdir()
        (root / ".bb" / "tool-sources.json").write_text(json.dumps({"mcpServers": {"drive-old": {"command": "x"}}}))
        spec = {"level": "organization", "command": ["vallis-secret", "run", "K", "--", "uvx", "pkg"], "env": {"A": "b"}}
        self.assertEqual(tools.write_catalog(root, {"google-workspace": spec}), {})
        path = root / ".bb" / "tool-sources.json"
        data = json.loads(path.read_text())["mcpServers"]
        self.assertEqual(data["google-workspace"], {"command": "vallis-secret", "args": ["run", "K", "--", "uvx", "pkg"], "env": {"A": "b"}})
        self.assertIn("drive-old", data)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(tools.write_catalog(root, {}), {})
        self.assertEqual(set(json.loads(path.read_text())["mcpServers"]), {"drive-old"})

    def test_a_source_of_its_own_is_not_overwritten(self):
        root = Path(tempfile.mkdtemp())
        (root / ".bb").mkdir()
        (root / ".bb" / "tool-sources.json").write_text(json.dumps({"mcpServers": {"google-workspace": {"command": "mine"}}}))
        errors = tools.write_catalog(root, {"google-workspace": {"command": ["x"], "env": {}}})
        self.assertIn("already has its own", errors["google-workspace"])
        self.assertEqual(json.loads((root / ".bb" / "tool-sources.json").read_text())["mcpServers"]["google-workspace"],
                         {"command": "mine"})


class McpProcess(unittest.TestCase):
    """A tools.yml line of kind mcp (С51): written into both settings files, keys from the store, login state."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.tool = REGISTRY["google-workspace"]

    def test_off_by_default_and_added_by_the_description(self):
        decisions, _ = tools.select(REGISTRY, "employee")
        self.assertFalse(decisions["google-workspace"][0])
        decisions, _ = tools.select(REGISTRY, "employee", {"tools": {"add": ["google-workspace"]}})
        self.assertEqual(decisions["google-workspace"], (True, "organization", ""))
        processes = tools.mcp_processes(REGISTRY, decisions, FakeAccount(self.home))
        spec = processes["google-workspace"]
        self.assertEqual(spec["command"][:5], ["vallis-secret", "run", "GOOGLE_OAUTH_CLIENT_ID",
                                               "GOOGLE_OAUTH_CLIENT_SECRET", "--"])
        self.assertIn("workspace-mcp==2.5.0", spec["command"])
        self.assertEqual(spec["env"]["WORKSPACE_MCP_CREDENTIALS_DIR"], str(self.home / ".secrets/google-workspace"))

    def test_written_without_values_next_to_described_servers(self):
        root = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        decisions, _ = tools.select(REGISTRY, "employee", {}, {"tools": {"add": ["google-workspace"]}})
        processes = tools.mcp_processes(REGISTRY, decisions, FakeAccount(self.home))
        servers = {"wiki": ("organization", "https://wiki.example.com/mcp", None), **processes}
        self.assertEqual(tools.write_mcp(root, servers), {})
        claude = json.loads((root / ".mcp.json").read_text())["mcpServers"]
        self.assertEqual(claude["google-workspace"]["type"], "stdio")
        self.assertEqual(claude["google-workspace"]["command"], "vallis-secret")
        import tomllib
        codex = tomllib.loads((root / ".codex" / "config.toml").read_text())["mcp_servers"]
        self.assertEqual(codex["google-workspace"]["command"], "vallis-secret")
        self.assertIn("workspace-mcp==2.5.0", codex["google-workspace"]["args"])
        self.assertIn("WORKSPACE_MCP_CREDENTIALS_DIR", codex["google-workspace"]["env"])
        self.assertEqual(codex["wiki"]["url"], "https://wiki.example.com/mcp")
        self.assertNotIn("CLIENT_SECRET\" =", (root / ".codex" / "config.toml").read_text())
        rows = tools.mcp_state(root, servers, {})
        self.assertEqual([r["id"] for r in rows], ["mcp:wiki"])  # the line has its own row
        # off again: both entries go away
        tools.write_mcp(root, {"wiki": servers["wiki"]})
        self.assertNotIn("google-workspace", json.loads((root / ".mcp.json").read_text())["mcpServers"])
        self.assertNotIn("google-workspace", (root / ".codex" / "config.toml").read_text())

    def test_states(self):
        account = FakeAccount(self.home, programs={"vallis-secret"})
        row = tools.tool_state("google-workspace", self.tool, (True, "organization", ""), account, {}, self.home, {})
        self.assertEqual(row["state"], "needs_setup")
        self.assertIn("uvx", row["reason"])
        account.programs.add("uvx")
        listed = {"secrets": [{"name": "GOOGLE_OAUTH_CLIENT_ID"}]}
        account.run = lambda *a, **k: subprocess.CompletedProcess(a, 0, json.dumps(listed), "")
        row = tools.tool_state("google-workspace", self.tool, (True, "organization", ""), account, {}, self.home, {})
        self.assertEqual(row["state"], "needs_setup")
        self.assertIn("GOOGLE_OAUTH_CLIENT_SECRET", row["action"])
        listed["secrets"].append({"name": "GOOGLE_OAUTH_CLIENT_SECRET"})
        self.assertIsNone(tools.prepare_mcp(self.tool, account))
        folder = self.home / ".secrets" / "google-workspace"
        self.assertEqual(oct(folder.stat().st_mode & 0o777), "0o700")
        row = tools.tool_state("google-workspace", self.tool, (True, "organization", ""), account, {}, self.home, {})
        self.assertEqual(row["state"], "needs_login")
        (folder / "agent@example.com.json").write_text("{}")
        row = tools.tool_state("google-workspace", self.tool, (True, "organization", ""), account, {}, self.home, {})
        self.assertEqual(row["state"], "installed")
        self.assertIn("date is unknown", row["reason"])
        import datetime
        for age, state, part in ((2, "installed", "5 more day"), (6, "installed", "expires tomorrow"),
                                 (7, "needs_login", "testing mode")):
            day = (datetime.date.today() - datetime.timedelta(days=age)).isoformat()
            listed["secrets"] = [{"name": "GOOGLE_OAUTH_CLIENT_ID"}, {"name": "GOOGLE_OAUTH_CLIENT_SECRET"},
                                 {"name": "GOOGLE_WORKSPACE_LOGIN", "updated": day}]
            row = tools.tool_state("google-workspace", self.tool, (True, "organization", ""), account, {}, self.home, {})
            self.assertEqual(row["state"], state, age)
            self.assertIn(part, row["reason"])
