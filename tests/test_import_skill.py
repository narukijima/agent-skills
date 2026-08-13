import subprocess
import shutil
import stat
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IMPORTER = ROOT / "tools/import-skill.sh"


def temporary_source_repository(directory: str, skill_name: str, description: str):
    source = Path(directory) / "source"
    (source / "tools").mkdir(parents=True)
    shutil.copy2(IMPORTER, source / "tools/import-skill.sh")
    shutil.copytree(ROOT / "skills" / skill_name, source / "skills" / skill_name)
    skill_file = source / "skills" / skill_name / "SKILL.md"
    skill_file.chmod(skill_file.stat().st_mode | stat.S_IWUSR)
    lines = skill_file.read_text(encoding="utf-8").splitlines()
    description_index = next(
        index for index, line in enumerate(lines) if line.startswith("description:")
    )
    lines[description_index] = f"description: {description}"
    skill_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.email", "fixture@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.name", "fixture"], check=True)
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-qm", "fixture"], check=True)
    skill_file.chmod(0o444)
    return source, source / "tools/import-skill.sh"


class ImportSkillTests(unittest.TestCase):
    def test_importer_parses_with_macos_bash_32(self):
        completed = subprocess.run(
            ["/bin/bash", "-n", str(IMPORTER)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_import_reads_metadata_version_and_preserves_license(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "agent-directory"
            completed = subprocess.run(
                ["/bin/bash", str(IMPORTER), "ai-native-design", "--target", str(target)],
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
            skill = (imported / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("status: active", skill)
            self.assertIn('aliases: ["ai native design", "ai ui"]', skill)
            self.assertIn('source_version: "0.2.1"', upstream)
            self.assertIn('import_mode: "vendored-copy"', upstream)
            self.assertIn('frontmatter_projection: "agent-directory-v1"', upstream)

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

    def test_import_rejects_description_longer_than_agent_directory_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            tracked = ROOT / "skills/ai-native-design/SKILL.md"
            before = tracked.read_bytes()
            target = Path(directory) / "agent-directory"
            source, importer = temporary_source_repository(directory, "ai-native-design", "x" * 201)
            completed = subprocess.run(
                ["bash", str(importer), "ai-native-design", "--target", str(target)],
                cwd=source,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("longer than 200 characters", completed.stderr)
            self.assertFalse(target.exists())
            self.assertEqual(tracked.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
