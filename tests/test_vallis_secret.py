"""vallis-secret: own keys, encrypt to self and the administrator, use without printing, list without values.

Runs against real sops when it is on PATH (or VALLIS_TEST_SOPS); otherwise against tests/fake_sops.py,
which keeps the same arguments and real age encryption. Needs age and age-keygen.
"""
import json, os, shutil, stat, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "deploy" / "vallis-secret"
REAL_SOPS = os.environ.get("VALLIS_TEST_SOPS") or shutil.which("sops")
CANARY = "canary-Zq7!x-not-a-real-secret"
HAVE_AGE = bool(shutil.which("age") and shutil.which("age-keygen"))


def fake_sops(folder):
    path = Path(folder) / "sops"
    path.write_text(f"#!/bin/sh\nexec {sys.executable} {ROOT / 'tests' / 'fake_sops.py'} \"$@\"\n")
    path.chmod(0o755)
    return str(path)


@unittest.skipUnless(HAVE_AGE, "age and age-keygen are needed")
class Base:
    sops = None

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.sops_bin = self.sops or fake_sops(self.tmp)
        self.alpha = self.account("alpha")
        self.beta = self.account("beta")
        self.admin = self.account("admin")

    def account(self, name):
        home = self.tmp / name
        home.mkdir()
        env = dict(os.environ, HOME=str(home), VALLIS_SOPS=self.sops_bin,
                   VALLIS_AGE_KEY=str(home / ".config/vallis/age.key"),
                   VALLIS_SECRETS_DIR=str(home / ".local/share/vallis/secrets"),
                   VALLIS_ADMIN_RECIPIENTS=str(home / ".config/vallis/admin.recipients"))
        env.pop("SOPS_AGE_KEY_FILE", None)
        done = self.call(env, "init")
        self.assertEqual(done.returncode, 0, done.stderr)
        return {"env": env, "home": home, "public": done.stdout.strip()}

    def call(self, who, *args, stdin=None):
        env = who["env"] if isinstance(who, dict) and "env" in who else who
        return subprocess.run([sys.executable, str(TOOL), *args], env=env, input=stdin,
                              capture_output=True, text=True)

    def ok(self, who, *args, stdin=None):
        done = self.call(who, *args, stdin=stdin)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn(CANARY, done.stdout + done.stderr)
        return done

    def store(self, who):
        return Path(who["env"]["VALLIS_SECRETS_DIR"])

    def put_canary(self, who, name="SITE_PASSWORD"):
        return self.ok(who, "put", name, "--stdin", "--what", "вход на тестовый сайт", stdin=CANARY + "\n")

    # --- tests ---------------------------------------------------------------------------------------------

    def test_init_makes_private_key_and_store(self):
        key = Path(self.alpha["env"]["VALLIS_AGE_KEY"])
        self.assertEqual(stat.S_IMODE(key.stat().st_mode), 0o600)
        for folder in (self.store(self.alpha), self.store(self.alpha) / "own", self.store(self.alpha) / "org"):
            self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)
        self.assertTrue(self.alpha["public"].startswith("age1"))
        again = self.ok(self.alpha, "init")
        self.assertEqual(again.stdout.strip(), self.alpha["public"], "init keeps the existing key")

    def test_put_stores_encrypted_with_open_description(self):
        self.put_canary(self.alpha)
        path = self.store(self.alpha) / "own" / "SITE_PASSWORD.json"
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        text = path.read_text()
        self.assertNotIn(CANARY, text)
        doc = json.loads(text)
        self.assertEqual(doc["what"], "вход на тестовый сайт")
        self.assertEqual(doc["source"], "agent")
        self.assertTrue(doc["value"].startswith("ENC["))

    def test_run_gives_value_to_the_command_only(self):
        self.put_canary(self.alpha)
        out = self.tmp / "seen"
        self.ok(self.alpha, "run", "SITE_PASSWORD", "--", sys.executable, "-c",
                f"import os; open({str(out)!r}, 'w').write(os.environ['SITE_PASSWORD'])")
        self.assertEqual(out.read_text(), CANARY)

    def test_generate_is_strong_and_unseen(self):
        done = self.ok(self.alpha, "put", "NEW_ACCOUNT", "--generate", "--what", "учётка, заведённая агентом")
        self.assertEqual(done.stdout.strip(), "stored NEW_ACCOUNT")
        out = self.tmp / "gen"
        self.ok(self.alpha, "run", "NEW_ACCOUNT", "--", sys.executable, "-c",
                f"import os; open({str(out)!r}, 'w').write(os.environ['NEW_ACCOUNT'])")
        value = out.read_text()
        self.assertEqual(len(value), 24)
        self.assertNotIn(value, done.stdout + done.stderr)

    def test_list_json_has_no_values(self):
        self.put_canary(self.alpha)
        self.ok(self.alpha, "put", "OTHER", "--generate", "--what", "второй")
        done = self.ok(self.alpha, "list", "--json")
        data = json.loads(done.stdout)
        names = {r["name"]: r for r in data["secrets"]}
        self.assertEqual(set(names), {"SITE_PASSWORD", "OTHER"})
        row = names["SITE_PASSWORD"]
        self.assertEqual((row["level"], row["source"], row["state"]), ("agent", "agent", "present"))
        self.assertEqual(row["what"], "вход на тестовый сайт")
        self.assertTrue(row["created"])
        self.assertNotIn("value", row)
        self.assertNotIn("ENC[", done.stdout)

    def test_other_account_cannot_decrypt_a_copy(self):
        self.put_canary(self.alpha)
        theirs = self.store(self.alpha) / "own" / "SITE_PASSWORD.json"
        shutil.copy(theirs, self.store(self.beta) / "own" / "SITE_PASSWORD.json")
        done = self.call(self.beta, "run", "SITE_PASSWORD", "--", "true")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("cannot decrypt", done.stderr)
        self.assertNotIn(CANARY, done.stdout + done.stderr)

    def test_administrator_can_decrypt(self):
        Path(self.alpha["env"]["VALLIS_ADMIN_RECIPIENTS"]).write_text("# admin\n" + self.admin["public"] + "\n")
        self.put_canary(self.alpha)
        theirs = self.store(self.alpha) / "own" / "SITE_PASSWORD.json"
        self.assertEqual(len(json.loads(theirs.read_text())["sops"]["age"]), 2)
        shutil.copy(theirs, self.store(self.admin) / "own" / "SITE_PASSWORD.json")
        out = self.tmp / "admin-seen"
        self.ok(self.admin, "file", "SITE_PASSWORD", str(out))
        self.assertEqual(out.read_text(), CANARY)
        self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o600)

    def test_import_keeps_binary_and_original(self):
        source = self.tmp / "key.pem"
        source.write_bytes(b"\x00\xffbinary " + CANARY.encode())
        self.ok(self.alpha, "import", "APP_KEY", "--file", str(source), "--what", "ключ приложения")
        self.assertTrue(source.exists())
        out = self.tmp / "restored.pem"
        self.ok(self.alpha, "file", "APP_KEY", str(out))
        self.assertEqual(out.read_bytes(), source.read_bytes())
        done = self.call(self.alpha, "run", "APP_KEY", "--", "true")
        self.assertIn("binary value", done.stderr)

    def test_organization_key_is_found_and_listed(self):
        Path(self.alpha["env"]["VALLIS_ADMIN_RECIPIENTS"]).write_text(self.admin["public"] + "\n")
        self.put_canary(self.alpha, "TRACKER_TOKEN")
        own = self.store(self.alpha) / "own" / "TRACKER_TOKEN.json"
        os.replace(own, self.store(self.alpha) / "org" / "TRACKER_TOKEN.json")
        rows = json.loads(self.ok(self.alpha, "list", "--json").stdout)["secrets"]
        self.assertEqual(rows[0]["level"], "organization")
        out = self.tmp / "org"
        self.ok(self.alpha, "file", "TRACKER_TOKEN", str(out))
        self.assertEqual(out.read_text(), CANARY)
        done = self.call(self.alpha, "remove", "TRACKER_TOKEN")
        self.assertNotEqual(done.returncode, 0, "organization keys are not removed by the agent")

    def test_bad_input_is_refused(self):
        for args, stdin in ((["put", "lower", "--generate", "--what", "x"], None),
                            (["put", "OK_NAME", "--generate", "--stdin", "--what", "x"], ""),
                            (["put", "OK_NAME", "--stdin", "--what", "x"], ""),
                            (["put", "OK_NAME", "--generate", "--what", " "], None),
                            (["run", "MISSING", "--", "true"], None),
                            (["run", "NO_SEPARATOR"], None)):
            done = self.call(self.alpha, *args, stdin=stdin)
            self.assertNotEqual(done.returncode, 0, args)

    def test_file_refuses_the_store_itself(self):
        self.put_canary(self.alpha)
        target = self.store(self.alpha) / "own" / "OTHER.json"
        done = self.call(self.alpha, "file", "SITE_PASSWORD", str(target))
        self.assertNotEqual(done.returncode, 0)

    def test_update_keeps_created(self):
        self.put_canary(self.alpha)
        path = self.store(self.alpha) / "own" / "SITE_PASSWORD.json"
        doc = json.loads(path.read_text())
        doc["created"] = "2026-01-01"
        path.write_text(json.dumps(doc))
        if self.sops:  # real sops checks the MAC of the whole file, editing an open field breaks it on purpose
            return
        self.ok(self.alpha, "put", "SITE_PASSWORD", "--generate", "--what", "новый пароль")
        self.assertEqual(json.loads(path.read_text())["created"], "2026-01-01")

    def test_ask_parses_the_form_file(self):
        bb = self.tmp / "bb"
        bb.write_text("#!/bin/sh\n# fake bb: secret request NAME --write-env PATH …\n"
                      f"printf \"%s='%s'\\n\" \"$3\" '{CANARY}' > \"$5\"\necho '{{\"ok\":true}}'\n")
        bb.chmod(0o755)
        env = dict(self.alpha["env"], BB_CLI=str(bb))
        done = self.call(env, "ask", "HANDED_KEY", "--what", "ключ, который передал человек")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn(CANARY, done.stdout + done.stderr)
        out = self.tmp / "handed"
        self.ok(self.alpha, "file", "HANDED_KEY", str(out))
        self.assertEqual(out.read_text(), CANARY)
        row = json.loads(self.ok(self.alpha, "list", "--json").stdout)["secrets"][0]
        self.assertEqual(row["source"], "handed")


class WithFakeSops(Base, unittest.TestCase):
    sops = None


@unittest.skipUnless(REAL_SOPS, "sops is not installed")
class WithRealSops(Base, unittest.TestCase):
    sops = REAL_SOPS


class DotenvParsing(unittest.TestCase):
    """The three forms BB's secret form writes (single quotes, double quotes, bare)."""

    def setUp(self):
        import importlib.machinery, importlib.util
        loader = importlib.machinery.SourceFileLoader("vallis_secret", str(TOOL))
        spec = importlib.util.spec_from_loader("vallis_secret", loader)
        self.m = importlib.util.module_from_spec(spec)
        loader.exec_module(self.m)

    def test_forms(self):
        p = self.m.parse_dotenv
        self.assertEqual(p("A='x\"y'\n", "A"), b'x"y')
        self.assertEqual(p('A="it\'s"\n', "A"), b"it's")
        self.assertEqual(p("OTHER=1\nexport A=it's\"x\n", "A"), b"it's\"x")
        with self.assertRaises(self.m.Fail):
            p("A=1\nA=2\n", "A")
        with self.assertRaises(self.m.Fail):
            p("B=1\n", "A")


if __name__ == "__main__":
    unittest.main()
