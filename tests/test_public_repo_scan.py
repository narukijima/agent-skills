import importlib.util
import subprocess
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("public_repo_scan", ROOT / "tools" / "public_repo_scan.py")
public_repo_scan = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(public_repo_scan)


class PublicRepoScanTests(unittest.TestCase):
    def test_current_public_product_has_no_owner_agent_active_state_files(self):
        present_files = [path for path in public_repo_scan.tracked_files() if path.is_file()]
        self.assertEqual(public_repo_scan.scan_owner_agent_state_files(present_files), [])

    def test_public_product_root_rejects_owner_agent_project_and_state_files(self):
        findings = public_repo_scan.scan_owner_agent_state_files(
            [Path("README.md"), Path("PROJECT.md"), Path("STATE.md")]
        )
        self.assertEqual(len(findings), 2)
        self.assertTrue(any(finding.startswith("PROJECT.md:") for finding in findings))
        self.assertTrue(any(finding.startswith("STATE.md:") for finding in findings))

    def test_nested_consumer_project_state_is_not_the_public_product_root(self):
        paths = [Path("fixtures/consumer/PROJECT.md"), Path("fixtures/consumer/STATE.md")]
        self.assertEqual(public_repo_scan.scan_owner_agent_state_files(paths), [])

    def test_fixture_email_and_generic_ci_paths_are_allowed(self):
        content = b"fixture" + b"@" + b"example.invalid /home/" + b"runner/work/repo"
        self.assertEqual(public_repo_scan.scan_bytes(Path("fixture.txt"), content), [])

    def test_personal_identifiers_and_credentials_are_rejected_without_echoing_values(self):
        content = (
            b"owner"
            + b"@"
            + b"private.test "
            + b"/"
            + b"Users/operator/project "
            + b"gh"
            + b"p_"
            + (b"A" * 24)
        )
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

    def test_force_pushed_unreachable_base_falls_back_to_scanning_new_head(self):
        noreply = b"123+public-user" + b"@" + b"users.noreply.github.com"
        log_row = b"a" * 40 + b"\0" + noreply + b"\0" + noreply + b"\0"
        missing_base = subprocess.CalledProcessError(128, ("git", "log"))
        with mock.patch.object(public_repo_scan, "git", side_effect=(missing_base, log_row)) as git_mock:
            self.assertEqual(public_repo_scan.scan_commits("old..new"), [])
        self.assertEqual(git_mock.call_args_list[1].args[1], "new")


if __name__ == "__main__":
    unittest.main()
