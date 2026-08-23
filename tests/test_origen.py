import base64
import binascii
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zlib


REPO_ROOT = Path(__file__).resolve().parents[1]
ORIGEN = REPO_ROOT / "skills/origen/scripts/origen.py"
ORIGEN_ENGINE = REPO_ROOT / "skills/origen/scripts/origen_engine.py"


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_engine_module():
    spec = importlib.util.spec_from_file_location("origen_engine_test_module", ORIGEN_ENGINE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    sys.path.insert(0, str(ORIGEN_ENGINE.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


class OrigenTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.provider = self.root / "provider.py"
        self.provider.write_text(
            """import base64
import hashlib
import json
import os
import sys

request = json.load(sys.stdin)
operation = request["operation"]
identity = {"root-key": "human-root-service", "final-key": "final-build-service", "final-key-v2": "final-build-service-v2"}
verifiers = {"root-key": {"public_key": "ed25519:test-root"}, "final-key": {"public_key": "ed25519:test-final"}, "final-key-v2": {"verifier_ref": "did:key:test-final-v2"}}

if operation == "health":
    json.dump({"healthy": True}, sys.stdout)
elif operation == "capabilities":
    json.dump({"operations": ["authorize_root", "verify_authorization", "sign", "verify", "get_public_key", "timestamp", "verify_timestamp"]}, sys.stdout)
elif operation == "get_public_key":
    key_id = request["key_id"]
    json.dump({"key_id": key_id, "algorithm": "Ed25519", "verifier": verifiers[key_id]}, sys.stdout)
elif operation == "authorize_root":
    boundary_type = os.environ.get("ORIGEN_TEST_AUTH_TYPE", "trusted_ingest")
    subject = request["subject_sha256"]
    receipt = "auth:" + boundary_type + ":capture-1:" + subject
    json.dump({"boundary_type": boundary_type, "boundary_id": "capture-1", "subject_sha256": subject, "receipt": receipt}, sys.stdout)
elif operation == "verify_authorization":
    expected = "auth:" + request["boundary_type"] + ":" + request["boundary_id"] + ":" + request["subject_sha256"]
    json.dump({"verified": request["receipt"] == expected}, sys.stdout)
elif operation == "timestamp":
    subject = request["subject_sha256"]
    json.dump({"provider_id": "time-provider", "provider_identity": "test-rfc3161", "protocol": "RFC3161-test", "trusted_time": "2026-08-23T00:00:01Z", "receipt": "tsa:" + subject}, sys.stdout)
elif operation == "verify_timestamp":
    json.dump({"verified": request["receipt"] == "tsa:" + request["subject_sha256"]}, sys.stdout)
elif operation == "sign":
    payload = base64.b64decode(request["payload"])
    key_id = request["key_id"]
    signature = hashlib.sha256(b"ed25519-test:" + key_id.encode() + b":" + payload).hexdigest()
    response = {"provider_id": "sign-provider", "key_id": key_id, "algorithm": "Ed25519", "signer_identity": identity[key_id], "signature": signature}
    if request.get("role") == "root-attestor":
        statement = json.loads(payload)
        response["authorization_receipt_digest"] = statement["authorization"]["receipt_digest"]
    json.dump(response, sys.stdout)
elif operation == "verify":
    payload = base64.b64decode(request["payload"])
    key_id = request["key_id"]
    expected = hashlib.sha256(b"ed25519-test:" + key_id.encode() + b":" + payload).hexdigest()
    json.dump({"verified": request["signature"] == expected, "provider_id": "sign-provider", "key_id": key_id, "algorithm": "Ed25519", "signer_identity": identity[key_id]}, sys.stdout)
else:
    raise SystemExit(9)
""",
            encoding="utf-8",
        )
        self.registry = self.root / "providers.json"
        self.write_registry()
        self.config = self.root / ".origen" / "config.json"
        setup = self.run_origen(
            "setup", "--provider-registry", self.registry,
            "--root-signer", "default-root", "--final-signer", "default-final",
            "--timestamp-provider", "default", "--config", self.config,
            cwd=REPO_ROOT,
        )
        self.assertEqual(setup["self_test"], "passed")

    def tearDown(self):
        self.temporary.cleanup()

    def provider_entry(self, provider_identity):
        runtime = Path(sys.executable).resolve()
        return {
            "executable": str(runtime),
            "arguments": [str(self.provider)],
            "expected_executable_sha256": sha256(runtime),
            "expected_script_sha256": {str(self.provider): sha256(self.provider)},
            "expected_resource_sha256": {},
            "provider_identity": provider_identity,
            "inherit_environment": ["ORIGEN_TEST_AUTH_TYPE"],
            "version": "test-1",
            "dependency_provenance": "test fixture",
            "reproducible_install": "python stdlib fixture",
        }

    def registry_value(self):
        return {
            "schema_version": "origen-provider-registry/1",
            "providers": {
                "sign-provider": self.provider_entry("test-sign-service"),
                "time-provider": self.provider_entry("test-rfc3161"),
            },
            "signers": {
                "default-root": {
                    "provider": "sign-provider", "key_id": "root-key", "algorithm": "Ed25519",
                    "signer_identity": "human-root-service", "verifier": {"public_key": "ed25519:test-root"},
                    "root_authorization": {"accepted_boundaries": ["trusted_ingest", "explicit_authorization", "pre_authorized_workflow"]},
                },
                "default-final": {
                    "provider": "sign-provider", "key_id": "final-key", "algorithm": "Ed25519",
                    "signer_identity": "final-build-service", "verifier": {"public_key": "ed25519:test-final"},
                },
            },
            "timestamp_providers": {"default": {"provider": "time-provider", "provider_identity": "test-rfc3161", "protocol": "RFC3161"}},
            "builders": {},
            "inspectors": {},
        }

    def write_registry(self, mutate=None):
        value = self.registry_value()
        if mutate:
            mutate(value)
        self.registry.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
        return value

    def update_config_policy(self, **values):
        config = json.loads(self.config.read_text(encoding="utf-8"))
        config.setdefault("policy", {}).update(values)
        self.config.write_text(json.dumps(config, sort_keys=True), encoding="utf-8")

    def run_origen(self, *args, expected=0, cwd=None, env=None):
        completed = subprocess.run(
            [sys.executable, str(ORIGEN), *map(str, args)], cwd=cwd or self.root,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False, env=env,
        )
        self.assertEqual(completed.returncode, expected, msg=f"stdout={completed.stdout}\nstderr={completed.stderr}")
        stream = completed.stdout if expected == 0 else completed.stderr
        self.assertTrue(stream.strip())
        return json.loads(stream)

    def capture_root(self, asset=None, evidence=None, env=None):
        asset = asset or self.root / "human.txt"
        if not asset.exists():
            asset.write_text("Human source\n", encoding="utf-8")
        evidence = evidence or self.root / "root.json"
        result = self.run_origen(
            "root", asset, "--creator-id", "creator:test", "--origin-id", "origin:test",
            "--evidence", evidence, env=env,
        )
        return evidence, result

    def finalize(self, source, root_evidence, bundle=None, **extra):
        bundle = bundle or self.root / "publish-bundle"
        args = ["finalize", source, "--bundle", bundle, "--root-evidence", root_evidence]
        for name in ("source_kind", "guarantee_level", "instruction_actor", "publication_profile", "json_schema_id", "source_map", "parent_evidence"):
            if name in extra:
                args += ["--" + name.replace("_", "-"), extra[name]]
        return bundle, self.run_origen(*args, expected=extra.get("expected", 0))

    @staticmethod
    def png_chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data) & 0xFFFFFFFF)

    def make_png(self, path, *, extra=()):
        header = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
        chunks = [self.png_chunk(b"IHDR", header)]
        chunks.extend(self.png_chunk(kind, data) for kind, data in extra)
        chunks.extend([self.png_chunk(b"IDAT", zlib.compress(b"\x00\x00")), self.png_chunk(b"IEND", b"")])
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"".join(chunks))

    def test_setup_writes_minimal_alias_config_and_self_tests(self):
        config = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(config["schema_version"], "origen-config/1")
        self.assertEqual(config["root_signer"], "default-root")
        self.assertEqual(config["final_signer"], "default-final")
        self.assertEqual(config["timestamp_provider"], "default")
        self.assertNotIn("private_key", self.config.read_text(encoding="utf-8"))
        rejected = self.run_origen(
            "setup", "--provider-registry", self.registry, "--root-signer", "same",
            "--final-signer", "same", "--timestamp-provider", "default",
            "--config", self.root / "bad.json", expected=1, cwd=REPO_ROOT,
        )
        self.assertEqual(rejected["error"]["code"], "ROLE_ALIAS_COLLISION")

    def test_minimal_root_finalize_verify_and_prepublish_flow(self):
        root_evidence, root_result = self.capture_root()
        root = json.loads(root_evidence.read_text(encoding="utf-8"))
        self.assertEqual(root_result["root_assurance_level"], "trusted_time")
        self.assertEqual(root["schema_version"], "origen-evidence/4")
        self.assertEqual(root["authorization"]["boundary_type"], "trusted_ingest")
        self.assertEqual(root["identities"]["signer"]["algorithm"], "Ed25519")
        self.assertEqual(root["identities"]["signer"]["verifier"]["public_key"], "ed25519:test-root")
        draft = self.root / "draft.txt"
        draft.write_text("AI output is allowed in STANDARD.\n", encoding="utf-8")
        bundle, result = self.finalize(draft, root_evidence)
        self.assertTrue(result["publish_ready"])
        final = json.loads((bundle / "evidence.json").read_text(encoding="utf-8"))
        self.assertEqual(final["identities"]["signer"]["role"], "final-attestor")
        self.assertIsNone(final["authorization"])
        verified = self.run_origen("verify", "--bundle", bundle, "--root-evidence", root_evidence)
        ready = self.run_origen("prepublish", "--bundle", bundle, "--root-evidence", root_evidence)
        self.assertTrue(verified["verified"] and ready["verified"])

    def test_human_root_accepts_nonmanual_authorization_and_rejects_unknown_boundary(self):
        env = dict(os.environ, ORIGEN_TEST_AUTH_TYPE="pre_authorized_workflow")
        evidence, _ = self.capture_root(evidence=self.root / "preauthorized.json", env=env)
        record = json.loads(evidence.read_text(encoding="utf-8"))
        self.assertEqual(record["authorization"]["boundary_type"], "pre_authorized_workflow")
        bad_env = dict(os.environ, ORIGEN_TEST_AUTH_TYPE="untrusted_agent_path")
        rejected = self.run_origen(
            "root", self.root / "human.txt", "--creator-id", "creator:test", "--origin-id", "origin:test",
            "--evidence", self.root / "rejected.json", expected=1, env=bad_env,
        )
        self.assertEqual(rejected["error"]["code"], "ROOT_AUTHORIZATION_REJECTED")

    def test_authorization_and_trusted_timestamp_tamper_fail_closed(self):
        root_evidence, _ = self.capture_root()
        draft = self.root / "draft.txt"
        draft.write_text("draft\n", encoding="utf-8")
        for field, code in (("authorization_receipt", "ROOT_AUTHORIZATION_TAMPERED"), ("timestamp_receipt", "TIMESTAMP_RECEIPT_TAMPERED")):
            record = json.loads(root_evidence.read_text(encoding="utf-8"))
            record["proof"][field] += "tampered"
            path = self.root / f"tampered-{field}.json"
            path.write_text(json.dumps(record), encoding="utf-8")
            _, rejected = self.finalize(draft, path, bundle=self.root / f"bundle-{field}", expected=1)
            self.assertEqual(rejected["error"]["code"], code)

    def test_provider_registry_is_pinned_and_arbitrary_cli_provider_is_not_accepted(self):
        self.write_registry(lambda value: value["providers"]["sign-provider"].update(expected_executable_sha256="0" * 64))
        human = self.root / "human.txt"
        human.write_text("Human\n", encoding="utf-8")
        rejected = self.run_origen("root", human, "--creator-id", "x", "--origin-id", "y", "--evidence", self.root / "x.json", expected=1)
        self.assertEqual(rejected["error"]["code"], "EXECUTABLE_HASH_MISMATCH")
        completed = subprocess.run(
            [sys.executable, str(ORIGEN), "root", "x", "--creator-id", "x", "--origin-id", "y", "--evidence", "z", "--signer-id", "other"],
            cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
        )
        self.assertNotEqual(completed.returncode, 0)

    def test_registry_can_add_rotated_key_without_breaking_old_evidence(self):
        root_evidence, _ = self.capture_root()
        draft = self.root / "draft.txt"
        draft.write_text("draft\n", encoding="utf-8")
        bundle, _ = self.finalize(draft, root_evidence)
        self.write_registry(lambda value: value["signers"].update({
            "default-final-v2": {
                "provider": "sign-provider", "key_id": "final-key-v2", "algorithm": "Ed25519",
                "signer_identity": "final-build-service-v2", "verifier": {"verifier_ref": "did:key:test-final-v2"},
            }
        }))
        result = self.run_origen("verify", "--bundle", bundle, "--root-evidence", root_evidence)
        self.assertTrue(result["verified"])

    def test_policy_digest_change_rejects_old_evidence(self):
        root_evidence, _ = self.capture_root()
        draft = self.root / "draft.txt"
        draft.write_text("draft\n", encoding="utf-8")
        bundle, _ = self.finalize(draft, root_evidence)
        self.update_config_policy(publisher_handoff_policy={"publication_representations": ["canonical-bytes"], "allowed_transport_metadata": []})
        rejected = self.run_origen("verify", "--bundle", bundle, "--root-evidence", root_evidence, expected=1)
        self.assertEqual(rejected["error"]["code"], "POLICY_DIGEST_MISMATCH")

    def test_secure_snapshot_rejects_symlink_and_path_swap(self):
        human = self.root / "human.txt"
        human.write_text("Human\n", encoding="utf-8")
        link = self.root / "link.txt"
        link.symlink_to(human)
        rejected = self.run_origen("root", link, "--creator-id", "x", "--origin-id", "y", "--evidence", self.root / "x.json", expected=1)
        self.assertEqual(rejected["error"]["code"], "SYMLINK_REJECTED")
        module = load_engine_module()
        target = self.root / "target.bin"
        replacement = self.root / "replacement.bin"
        target.write_bytes(b"A" * 4096)
        replacement.write_bytes(b"B" * 4096)
        real_open = module.os.open
        swapped = False
        def swapping_open(path, flags, *args, **kwargs):
            nonlocal swapped
            if not swapped and Path(path) == target:
                swapped = True
                replacement.rename(target)
            return real_open(path, flags, *args, **kwargs)
        with module.SnapshotStore() as store, mock.patch.object(module.os, "open", side_effect=swapping_open):
            with self.assertRaises(module.OrigenError) as caught:
                store.capture(target, label="path swap", maximum=8192)
        self.assertEqual(caught.exception.code, "PATH_SWAPPED")

    def test_atomic_bundle_never_overwrites_and_tamper_is_rejected(self):
        root_evidence, _ = self.capture_root()
        draft = self.root / "draft.txt"
        draft.write_text("draft\n", encoding="utf-8")
        bundle, _ = self.finalize(draft, root_evidence)
        _, rejected = self.finalize(draft, root_evidence, bundle=bundle, expected=1)
        self.assertEqual(rejected["error"]["code"], "OUTPUT_EXISTS")
        (bundle / "asset").write_text("tampered\n", encoding="utf-8")
        rejected = self.run_origen("verify", "--bundle", bundle, "--root-evidence", root_evidence, expected=1)
        self.assertEqual(rejected["error"]["code"], "ASSET_MISMATCH")

    def test_concurrent_finalize_has_one_atomic_winner(self):
        root_evidence, _ = self.capture_root()
        draft = self.root / "race.txt"
        draft.write_text("race\n", encoding="utf-8")
        bundle = self.root / "race-bundle"
        command = [sys.executable, str(ORIGEN), "finalize", str(draft), "--bundle", str(bundle), "--root-evidence", str(root_evidence)]
        processes = [subprocess.Popen(command, cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        results = [process.communicate(timeout=10) + (process.returncode,) for process in processes]
        self.assertEqual(sorted(item[2] for item in results), [0, 1])
        loser = next(json.loads(stderr) for _, stderr, code in results if code == 1)
        self.assertEqual(loser["error"]["code"], "OUTPUT_EXISTS")

    def test_provider_timeout_and_output_are_bounded(self):
        original = self.provider.read_text(encoding="utf-8")
        self.update_config_policy(resource_limits={"subprocess_stdout_bytes": 1024})
        self.provider.write_text("import sys\nsys.stdout.write('x' * 10000)\n", encoding="utf-8")
        self.write_registry()
        human = self.root / "human.txt"
        human.write_text("Human\n", encoding="utf-8")
        rejected = self.run_origen("root", human, "--creator-id", "x", "--origin-id", "y", "--evidence", self.root / "bomb.json", expected=1)
        self.assertEqual(rejected["error"]["code"], "PROVIDER_OUTPUT_LIMIT")

        self.provider.write_text("import time\ntime.sleep(1)\n", encoding="utf-8")
        self.write_registry()
        self.update_config_policy(resource_limits={"subprocess_timeout_seconds": 0.05})
        rejected = self.run_origen("root", human, "--creator-id", "x", "--origin-id", "y", "--evidence", self.root / "slow.json", expected=1)
        self.assertEqual(rejected["error"]["code"], "PROVIDER_TIMEOUT")
        self.provider.write_text(original, encoding="utf-8")

    def test_duplicate_and_noncurrent_evidence_are_rejected_before_verification(self):
        root_evidence, _ = self.capture_root()
        draft = self.root / "draft.txt"
        draft.write_text("draft\n", encoding="utf-8")
        bundle, _ = self.finalize(draft, root_evidence)
        evidence_path = bundle / "evidence.json"
        text = evidence_path.read_text(encoding="utf-8")
        evidence_path.write_text(text.replace('"schema_version":', '"schema_version":"origen-evidence/4","schema_version":', 1), encoding="utf-8")
        rejected = self.run_origen("verify", "--bundle", bundle, "--root-evidence", root_evidence, expected=1)
        self.assertEqual(rejected["error"]["code"], "INVALID_JSON")

        bundle2, _ = self.finalize(draft, root_evidence, bundle=self.root / "old-schema")
        record = json.loads((bundle2 / "evidence.json").read_text(encoding="utf-8"))
        record["schema_version"] = "origen-evidence/3"
        (bundle2 / "evidence.json").write_text(json.dumps(record), encoding="utf-8")
        rejected = self.run_origen("verify", "--bundle", bundle2, "--root-evidence", root_evidence, expected=1)
        self.assertEqual(rejected["error"]["code"], "UNSUPPORTED_EVIDENCE_SCHEMA")

    def test_invisible_text_and_c2pa_markers_remain_fail_closed(self):
        root_evidence, _ = self.capture_root()
        hidden = self.root / "hidden.txt"
        hidden.write_text("visible\u200bhidden\n", encoding="utf-8")
        _, rejected = self.finalize(hidden, root_evidence, bundle=self.root / "hidden-bundle", expected=1)
        self.assertEqual(rejected["error"]["code"], "TEXT_INVISIBLE_CHARACTER")
        marked = self.root / "marked.txt"
        marked.write_text("-----BEGIN C2PA MANIFEST-----\nfixture\n-----END C2PA MANIFEST-----\n", encoding="utf-8")
        inspection = self.run_origen("inspect", marked)
        self.assertIn("TEXT-structured-C2PA", inspection["c2pa_markers"])

    def test_strict_origin_rebuilds_only_signed_human_source(self):
        human = self.root / "human.txt"
        human.write_text("Alpha\nBeta\n", encoding="utf-8")
        root_evidence, _ = self.capture_root(human)
        source_map = self.root / "map.json"
        source_map.write_text(json.dumps({
            "schema_version": "origen-source-map/2", "kind": "text", "instruction_actor": "ai",
            "sources": [{"source_id": "root", "asset": str(human), "evidence": str(root_evidence)}],
            "operations": [
                {"op": "slice", "source_id": "root", "start": 6, "end": 11, "boundary": "line"},
                {"op": "separator", "value": "\n"},
                {"op": "slice", "source_id": "root", "start": 0, "end": 6, "boundary": "line"},
            ],
        }), encoding="utf-8")
        composed = self.root / "composed.txt"
        self.run_origen("strict-compose", "--source-map", source_map, "--root-evidence", root_evidence, "--output", composed)
        bundle, _ = self.finalize(composed, root_evidence, guarantee_level="strict_origin", source_map=source_map, source_kind="human-edit")
        ready = self.run_origen("prepublish", "--bundle", bundle, "--root-evidence", root_evidence, "--source-map", source_map)
        self.assertTrue(ready["verified"])
        proposal = self.root / "proposal.txt"
        proposal.write_text("AI wording outside the signed source.\n", encoding="utf-8")
        _, rejected = self.finalize(proposal, root_evidence, bundle=self.root / "bad-strict", guarantee_level="strict_origin", source_map=source_map, expected=1)
        self.assertEqual(rejected["error"]["code"], "STRICT_CONTENT_MISMATCH")

    def test_builtin_markdown_and_png_canonicalization_remain_available(self):
        root_evidence, _ = self.capture_root()
        markdown = self.root / "safe.md"
        markdown.write_text("# Safe\r\n\r\nText.\r\n", encoding="utf-8")
        bundle, _ = self.finalize(markdown, root_evidence, bundle=self.root / "markdown", publication_profile="markdown-safe")
        self.assertEqual((bundle / "asset").read_text(encoding="utf-8"), "# Safe\n\nText.\n")
        png = self.root / "human.png"
        self.make_png(png, extra=((b"tEXt", b"Comment\x00capture"),))
        png_root, _ = self.capture_root(png, evidence=self.root / "png-root.json")
        source_map = self.root / "png-map.json"
        source_map.write_text(json.dumps({
            "schema_version": "origen-source-map/2", "kind": "media", "instruction_actor": "tool",
            "sources": [{"source_id": "root", "asset": str(png), "evidence": str(png_root)}],
            "primary_source_id": "root", "transformation": {"op": "identity", "parameters": {}},
        }), encoding="utf-8")
        png_bundle, _ = self.finalize(png, png_root, bundle=self.root / "png-bundle", guarantee_level="strict_origin", source_map=source_map, source_kind="captured-original")
        inspection = self.run_origen("inspect", png_bundle / "asset")
        self.assertNotIn("tEXt", inspection["chunks"])

    def test_private_key_material_is_never_part_of_cli_or_evidence(self):
        root_evidence, _ = self.capture_root()
        text = root_evidence.read_text(encoding="utf-8") + self.config.read_text(encoding="utf-8") + self.registry.read_text(encoding="utf-8")
        self.assertNotIn("private_key", text)
        self.assertNotIn("secret_key", text)
        help_text = subprocess.run([sys.executable, str(ORIGEN), "root", "--help"], stdout=subprocess.PIPE, text=True, check=True).stdout
        self.assertNotIn("keychain", help_text.lower())
        self.assertNotIn("private", help_text.lower())


if __name__ == "__main__":
    unittest.main()
