import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IMPORTER = ROOT / "tools/import-skill.sh"


class ImportSkillTests(unittest.TestCase):
    def test_import_reads_metadata_version_and_preserves_license(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "agent-directory"
            completed = subprocess.run(
                ["bash", str(IMPORTER), "ai-native-design", "--target", str(target)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            imported = target / "skills/ai-native-design"
            self.assertTrue((imported / "LICENSE.txt").is_file())
            self.assertTrue((imported / "references/source-strategy.md").is_file())
            upstream = (imported / "agents/upstream.yaml").read_text(encoding="utf-8")
            self.assertIn('source_version: "0.2.0"', upstream)
            self.assertIn('import_mode: "vendored-copy"', upstream)

    def test_import_refuses_to_overwrite_existing_skill(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "agent-directory"
            subprocess.run(
                ["bash", str(IMPORTER), "ai-native-design", "--target", str(target)],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            completed = subprocess.run(
                ["bash", str(IMPORTER), "ai-native-design", "--target", str(target)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("refusing to overwrite", completed.stderr)


if __name__ == "__main__":
    unittest.main()
