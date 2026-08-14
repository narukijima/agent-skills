import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools/check-runtime-permission-boundary.py"
SPEC = importlib.util.spec_from_file_location("runtime_permission_boundary", TOOL)
runtime_permission_boundary = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(runtime_permission_boundary)


class RuntimePermissionBoundaryTests(unittest.TestCase):
    def test_current_repository_has_no_runtime_permission_policy(self):
        self.assertEqual(runtime_permission_boundary.scan_repository(ROOT), [])

    def test_provider_modes_and_generic_shell_approval_are_rejected(self):
        fixtures = (
            "claude " + "--permission-mode accept" + "Edits",
            "codex " + "--sandbox workspace" + "-write",
            "shell実行前に必ずユーザー承認",
            "allowed" + "-tools: Bash",
        )
        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                self.assertTrue(runtime_permission_boundary.scan_text(Path("fixture.md"), fixture))

    def test_domain_authorization_language_is_allowed(self):
        text = (
            "Bind platform, account, operation, credential fingerprint, content source, budget, "
            "caller, and schedule. Reconcile unknown provider state before retry."
        )
        self.assertEqual(runtime_permission_boundary.scan_text(Path("skill.md"), text), [])


if __name__ == "__main__":
    unittest.main()
