"""Every skill of the release has a front matter BB can read: a YAML error makes BB skip the skill silently,
and a conversation that is handed it gets no platform skills at all (0.4.7 my-commitments on the stand: «there: …»)."""
import unittest
from pathlib import Path

import yaml

SKILLS = Path(__file__).resolve().parent.parent / ".bb" / "skills"


class SkillFrontMatter(unittest.TestCase):
    def test_every_skill_parses_and_is_named_as_its_folder(self):
        for folder in sorted(p for p in SKILLS.iterdir() if p.is_dir()):
            with self.subTest(skill=folder.name):
                text = (folder / "SKILL.md").read_text(encoding="utf-8")
                self.assertTrue(text.startswith("---\n"), "no front matter")
                meta = yaml.safe_load(text.split("---", 2)[1])
                self.assertEqual(meta.get("name"), folder.name)
                self.assertTrue(str(meta.get("description") or "").strip(), "no description")


if __name__ == "__main__":
    unittest.main()
