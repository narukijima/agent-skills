import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("public_repo_scan", ROOT / "tools" / "public_repo_scan.py")
public_repo_scan = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(public_repo_scan)


class PublicRepoScanTests(unittest.TestCase):
    def test_fixture_email_and_generic_ci_paths_are_allowed(self):
        content = b"fixture" + b"@" + b"example.invalid /home/runner/work/repo"
        self.assertEqual(public_repo_scan.scan_bytes(Path("fixture.txt"), content), [])

    def test_personal_identifiers_and_credentials_are_rejected_without_echoing_values(self):
        content = b"owner" + b"@" + b"private.test " + b"/Users/" + b"operator/project " + b"ghp_" + (b"A" * 24)
        findings = public_repo_scan.scan_bytes(Path("unsafe.txt"), content)
        self.assertEqual(len(findings), 3)
        rendered = " ".join(findings)
        self.assertNotIn("owner", rendered)
        self.assertNotIn("operator", rendered)
        self.assertNotIn("ghp_", rendered)

    def test_only_github_noreply_commit_addresses_are_allowed(self):
        noreply = "123+public-user" + "@" + "users.noreply.github.com"
        self.assertIsNotNone(public_repo_scan.NOREPLY.fullmatch(noreply))
        private = "owner" + "@" + "private.test"
        self.assertIsNone(public_repo_scan.NOREPLY.fullmatch(private))


if __name__ == "__main__":
    unittest.main()
