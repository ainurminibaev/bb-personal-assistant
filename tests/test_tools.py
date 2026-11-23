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
