import base64
import binascii
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import struct
import zipfile
import zlib


REPO_ROOT = Path(__file__).resolve().parents[1]
ORIGEN = REPO_ROOT / "skills/origen/scripts/origen.py"
ORIGEN_ENGINE = REPO_ROOT / "skills/origen/scripts/origen_engine.py"


def load_engine_module():
    spec = importlib.util.spec_from_file_location("origen_engine_test_module", ORIGEN_ENGINE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class OrigenTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.provider = self.root / "provider.py"
        self.provider.write_text(
            """import base64
import hashlib
import json
import sys

tool_id = sys.argv[1]
request = json.load(sys.stdin)
operation = request["operation"]
if operation == "authorize_root":
    json.dump({"authorization_receipt_digest": hashlib.sha256(b"human-approved").hexdigest()}, sys.stdout)
elif operation == "timestamp":
    subject = request["subject_sha256"]
    json.dump({
        "provider": "test-tsa",
        "protocol": "RFC3161-test-fixture",
        "trusted_time": "2026-08-23T00:00:01Z",
        "receipt": "tsa:" + subject,
    }, sys.stdout)
elif operation == "verify_timestamp":
    expected = "tsa:" + request["subject_sha256"]
    json.dump({"verified": request["receipt"] == expected}, sys.stdout)
elif operation == "sign":
    payload = base64.b64decode(request["payload"])
    expected = request["expected"]
    signature = hashlib.sha256(b"v3-test:" + payload).hexdigest()
    response = {
        "provider": "test-attestor",
        "key_id": expected["key_id"],
        "algorithm": expected["algorithm"],
        "signer_identity": expected["identity"],
        "signature": signature,
    }
    if expected["role"] == "root-attestor":
        response["authorization_receipt_digest"] = hashlib.sha256(b"human-approved").hexdigest()
    json.dump(response, sys.stdout)
elif operation == "verify":
    payload = base64.b64decode(request["payload"])
    signer = request["expected_signer"]
    signature = hashlib.sha256(b"v3-test:" + payload).hexdigest()
    json.dump({
        "verified": request["proof"].get("signature") == signature,
        "key_id": signer["key_id"],
        "algorithm": signer["algorithm"],
        "signer_identity": signer["identity"],
    }, sys.stdout)
else:
    raise SystemExit(9)
""",
            encoding="utf-8",
        )
        self.policy = self.root / "policy.json"
        self.write_policy()

    def tearDown(self):
        self.temporary.cleanup()

    def tool(self, tool_id, **extra):
        runtime = Path(sys.executable).resolve()
        value = {
            "executable": str(runtime),
            "arguments": [str(self.provider), tool_id],
            "expected_executable_sha256": sha256(runtime),
            "expected_script_sha256": {str(self.provider): sha256(self.provider)},
            "expected_resource_sha256": {},
            "provider": "test-attestor" if tool_id != "tsa" else "test-tsa",
            "version": "test-1",
            "dependency_provenance": "test fixture",
            "reproducible_install": "python stdlib fixture",
        }
        value.update(extra)
        return value

    def tool_script(self, tool_id, script, **extra):
        value = self.tool(tool_id, **extra)
        value["arguments"] = [str(script), tool_id]
        value["expected_script_sha256"] = {str(script): sha256(script)}
        return value

    def policy_value(self):
        return {
            "schema_version": "origen-trust-policy/1",
            "policy_id": "test-production",
            "policy_version": "1.0.0",
            "mode": "production",
            "root_required": True,
            "human_origin_claim": True,
            "allowed_media_types": ["text/plain", "text/markdown", "application/json", "image/png"],
            "approved_signers": {
                "root-signer": self.tool(
                    "root-signer", role="root-attestor", key_id="root-key", algorithm="test-sha256",
                    signer_identity="human-root-service", agent_invocable=False,
                ),
                "final-signer": self.tool(
                    "final-signer", role="final-attestor", key_id="final-key", algorithm="test-sha256",
                    signer_identity="final-build-service", agent_invocable=True,
                ),
            },
            "approved_verifiers": {
                "verifier": self.tool("verifier", provider="test-verifier", verifier_identity="test-verifier"),
            },
            "approved_builders": {},
            "approved_inspectors": {},
            "approved_timestamp_providers": {"tsa": self.tool("tsa")},
            "approved_key_ids": ["root-key", "final-key"],
            "approved_algorithms": ["test-sha256"],
            "creator_key_map": {
                "creator:test": {"key_id": "root-key", "signer_identity": "human-root-service"},
            },
            "resource_limits": {},
            "environment_policy": {
                "network": "deny", "approved_path": [str(Path(sys.executable).parent)],
                "allowed_variables": {}, "sandbox_contract": "test-no-network-sandbox",
            },
            "publisher_handoff_policy": {
                "publication_representations": ["canonical-bytes"],
                "allowed_transport_metadata": ["content-type"],
            },
            "slice_boundary_policy": {
                "allowed": ["grapheme", "token", "word", "line", "paragraph"],
                "advanced_code_point": False, "allow_letter_synthesis": False,
            },
            "c2pa_policy": {"action": "detach"},
            "publication_profiles": {
                "markdown-safe": {"front_matter": "forbid", "raw_html": "forbid", "comments": "forbid"},
            },
            "approved_json_schemas": {},
            "external_manifest_policy": "reject-unless-approved-inspector",
        }

    def write_policy(self, mutate=None):
        value = self.policy_value()
        if mutate:
            mutate(value)
        self.policy.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        return value

    def run_origen(self, *args, expected=0):
        completed = subprocess.run(
            [sys.executable, str(ORIGEN), *map(str, args)], cwd=REPO_ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
        )
        self.assertEqual(completed.returncode, expected, msg=f"stdout={completed.stdout}\nstderr={completed.stderr}")
        stream = completed.stdout if expected == 0 else completed.stderr
        self.assertTrue(stream.strip(), msg=f"empty JSON stream; stdout={completed.stdout!r} stderr={completed.stderr!r}")
        try:
            return json.loads(stream)
        except json.JSONDecodeError:
            self.fail(f"non-JSON output: {stream!r}")

    @staticmethod
    def png_chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data) & 0xFFFFFFFF)

    def make_png(self, path, *, width=1, height=1, extra=()):
        header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
        raw = b"".join(b"\x00" + b"\x00" * width for _ in range(height))
        chunks = [self.png_chunk(b"IHDR", header)]
        chunks.extend(self.png_chunk(kind, data) for kind, data in extra)
        chunks.extend([self.png_chunk(b"IDAT", zlib.compress(raw)), self.png_chunk(b"IEND", b"")])
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"".join(chunks))

    def capture_root(self, asset=None, **overrides):
        asset = asset or self.root / "human.txt"
        if not asset.exists():
            asset.write_text("Human source\n", encoding="utf-8")
        evidence = overrides.get("evidence", self.root / "root.json")
        args = [
            "root", asset, "--creator-id", overrides.get("creator_id", "creator:test"),
            "--origin-id", "origin:test", "--signer-id", overrides.get("signer_id", "root-signer"),
            "--verifier-id", "verifier", "--timestamp-provider-id", "tsa",
            "--policy", self.policy, "--evidence", evidence,
            "--timestamp", overrides.get("timestamp", "1999-01-01T00:00:00Z"),
        ]
        return evidence, self.run_origen(*args, expected=overrides.get("expected", 0))

    def finalize(self, source, root_evidence, *, expected=0, **extra):
        bundle = extra.get("bundle", self.root / "publish-bundle")
        args = [
            "finalize", source, "--bundle", bundle, "--policy", self.policy,
            "--signer-id", extra.get("signer_id", "final-signer"), "--verifier-id", "verifier",
            "--timestamp-provider-id", "tsa", "--root-evidence", root_evidence,
            "--source-kind", extra.get("source_kind", "ai-output"),
            "--guarantee-level", extra.get("guarantee_level", "standard"),
            "--transformation", "canonical build", "--instruction-actor", extra.get("instruction_actor", "ai"),
            "--publication-representation", "canonical-bytes",
        ]
        if extra.get("source_map"):
            args += ["--source-map", extra["source_map"]]
        if extra.get("builder_id"):
            args += ["--builder-id", extra["builder_id"]]
        if extra.get("inspector_id"):
            args += ["--inspector-id", extra["inspector_id"]]
        if extra.get("publication_profile"):
            args += ["--publication-profile", extra["publication_profile"]]
        if extra.get("json_schema_id"):
            args += ["--json-schema-id", extra["json_schema_id"]]
        return bundle, self.run_origen(*args, expected=expected)

    def test_production_root_finalize_and_atomic_prepublish_receipt(self):
        human = self.root / "human.txt"
        human.write_text("Human source\n", encoding="utf-8")
        root_evidence, root_result = self.capture_root(human)
        self.assertEqual(root_result["root_assurance_level"], "trusted_time")
        root_record = json.loads(root_evidence.read_text(encoding="utf-8"))
        self.assertEqual(root_record["schema_version"], "origen-evidence/3")
        self.assertEqual(root_record["created_at"], "1999-01-01T00:00:00Z")
        self.assertEqual(root_record["timestamp"]["trusted_time"], "2026-08-23T00:00:01Z")
        self.assertNotEqual(root_record["created_at"], root_record["timestamp"]["trusted_time"])

        draft = self.root / "draft.txt"
        draft.write_text("AI-generated content is allowed in STANDARD.\n", encoding="utf-8")
        bundle, result = self.finalize(draft, root_evidence)
        self.assertTrue(result["publish_ready"])
        self.assertEqual(set(path.name for path in bundle.iterdir()), {"asset", "evidence.json", "receipt.json"})
        evidence = json.loads((bundle / "evidence.json").read_text(encoding="utf-8"))
        self.assertEqual(evidence["assurance"]["content_signals"]["state"], "unknown")
        self.assertFalse(evidence["assurance"]["derivation"]["no_unmapped_generated_content"])
        ready = self.run_origen(
            "prepublish", "--bundle", bundle, "--policy", self.policy, "--verifier-id", "verifier",
            "--timestamp-provider-id", "tsa", "--root-evidence", root_evidence,
        )
        self.assertTrue(ready["verified"])
        self.assertTrue(ready["publisher_must_rehash_upload"])
        self.assertTrue(ready["publisher_must_not_transform"])

    def test_production_rejects_arbitrary_commands_and_unapproved_ids(self):
        human = self.root / "human.txt"
        human.write_text("Human\n", encoding="utf-8")
        rejected = self.run_origen(
            "root", human, "--creator-id", "creator:test", "--origin-id", "origin:test",
            "--signer-id", "root-signer", "--verifier-id", "verifier", "--timestamp-provider-id", "tsa",
            "--policy", self.policy, "--evidence", self.root / "root.json",
            "--sign-command", "/tmp/arbitrary-signer", expected=1,
        )
        self.assertEqual(rejected["error"]["code"], "COMMAND_OVERRIDE_FORBIDDEN")
        rejected = self.run_origen(
            "root", human, "--creator-id", "creator:test", "--origin-id", "origin:test",
            "--signer-id", "unknown", "--verifier-id", "verifier", "--timestamp-provider-id", "tsa",
            "--policy", self.policy, "--evidence", self.root / "root.json", expected=1,
        )
        self.assertEqual(rejected["error"]["code"], "SIGNER_NOT_APPROVED")
        rejected = self.run_origen(
            "root", human, "--creator-id", "creator:test", "--origin-id", "origin:test",
            "--signer-id", "root-signer", "--verifier-id", "unknown", "--timestamp-provider-id", "tsa",
            "--policy", self.policy, "--evidence", self.root / "root.json", expected=1,
        )
        self.assertEqual(rejected["error"]["code"], "VERIFIER_NOT_APPROVED")

    def test_executable_key_role_and_creator_mismatches_fail_closed(self):
        self.write_policy(lambda p: p["approved_signers"]["root-signer"].update(expected_executable_sha256="0" * 64))
        _, rejected = self.capture_root(expected=1)
        self.assertEqual(rejected["error"]["code"], "EXECUTABLE_HASH_MISMATCH")

        self.write_policy(lambda p: p["approved_signers"]["root-signer"].update(key_id="not-approved"))
        _, rejected = self.capture_root(expected=1)
        self.assertEqual(rejected["error"]["code"], "KEY_ID_NOT_APPROVED")

        self.write_policy()
        _, rejected = self.capture_root(signer_id="final-signer", expected=1)
        self.assertEqual(rejected["error"]["code"], "ATTESTOR_ROLE_MISMATCH")
        _, rejected = self.capture_root(creator_id="creator:other", expected=1)
        self.assertEqual(rejected["error"]["code"], "CREATOR_KEY_MAPPING_MISMATCH")

    def test_backdated_local_time_is_not_trusted_and_timestamp_tamper_is_rejected(self):
        root_evidence, _ = self.capture_root(timestamp="1980-01-01T00:00:00Z")
        record = json.loads(root_evidence.read_text(encoding="utf-8"))
        self.assertEqual(record["created_at"], "1980-01-01T00:00:00Z")
        self.assertEqual(record["assurance"]["root"]["assurance_level"], "trusted_time")
        self.assertNotEqual(record["created_at"], record["timestamp"]["trusted_time"])
        record["proof"]["timestamp_receipt"] += "tampered"
        root_evidence.write_text(json.dumps(record), encoding="utf-8")
        draft = self.root / "draft.txt"
        draft.write_text("draft\n", encoding="utf-8")
        _, rejected = self.finalize(draft, root_evidence, expected=1)
        self.assertEqual(rejected["error"]["code"], "TIMESTAMP_RECEIPT_TAMPERED")

    def test_symlink_asset_and_symlink_evidence_are_rejected(self):
        human = self.root / "human.txt"
        human.write_text("Human\n", encoding="utf-8")
        link = self.root / "human-link.txt"
        link.symlink_to(human)
        _, rejected = self.capture_root(link, expected=1)
        self.assertEqual(rejected["error"]["code"], "SYMLINK_REJECTED")

        root_evidence, _ = self.capture_root(human)
        evidence_link = self.root / "root-link.json"
        evidence_link.symlink_to(root_evidence)
        draft = self.root / "draft.txt"
        draft.write_text("draft\n", encoding="utf-8")
        _, rejected = self.finalize(draft, evidence_link, expected=1)
        self.assertEqual(rejected["error"]["code"], "SYMLINK_REJECTED")

    def test_path_swap_and_hardlink_mutation_are_rejected_by_snapshot_layer(self):
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
            with self.assertRaises(module.V3Error) as caught:
                store.capture(target, label="path swap fixture", maximum=8192)
        self.assertEqual(caught.exception.code, "PATH_SWAPPED")

        original = self.root / "hardlink.bin"
        alias = self.root / "hardlink-alias.bin"
        original.write_bytes(b"C" * (2 * 1024 * 1024))
        os.link(original, alias)
        real_read = module.os.read
        mutated = False

        def mutating_read(fd, size):
            nonlocal mutated
            chunk = real_read(fd, size)
            if chunk and not mutated:
                mutated = True
                with alias.open("r+b") as stream:
                    stream.seek(1024 * 1024)
                    stream.write(b"D" * 1024)
                    stream.flush()
                    os.fsync(stream.fileno())
            return chunk

        with module.SnapshotStore() as store, mock.patch.object(module.os, "read", side_effect=mutating_read):
            with self.assertRaises(module.V3Error) as caught:
                store.capture(original, label="hardlink mutation fixture", maximum=3 * 1024 * 1024)
        self.assertEqual(caught.exception.code, "INPUT_MUTATED")

    def test_atomic_bundle_never_overwrites_and_partial_bundle_is_rejected(self):
        root_evidence, _ = self.capture_root()
        draft = self.root / "draft.txt"
        draft.write_text("draft\n", encoding="utf-8")
        bundle, _ = self.finalize(draft, root_evidence)
        _, rejected = self.finalize(draft, root_evidence, bundle=bundle, expected=1)
        self.assertEqual(rejected["error"]["code"], "OUTPUT_EXISTS")
        partial = self.root / "partial-bundle"
        partial.mkdir()
        (partial / "asset").write_text("partial\n", encoding="utf-8")
        rejected = self.run_origen(
            "prepublish", "--bundle", partial, "--policy", self.policy, "--verifier-id", "verifier",
            "--timestamp-provider-id", "tsa", "--root-evidence", root_evidence, expected=1,
        )
        self.assertEqual(rejected["error"]["code"], "INVALID_BUNDLE")

    def test_concurrent_finalize_has_one_atomic_winner(self):
        root_evidence, _ = self.capture_root()
        draft = self.root / "race.txt"
        draft.write_text("race\n", encoding="utf-8")
        bundle = self.root / "race-bundle"
        command = [
            sys.executable, str(ORIGEN), "finalize", str(draft), "--bundle", str(bundle),
            "--policy", str(self.policy), "--signer-id", "final-signer", "--verifier-id", "verifier",
            "--timestamp-provider-id", "tsa", "--root-evidence", str(root_evidence),
            "--source-kind", "ai-output", "--guarantee-level", "standard",
            "--transformation", "canonical build", "--instruction-actor", "ai",
            "--publication-representation", "canonical-bytes",
        ]
        processes = [subprocess.Popen(command, cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        results = [process.communicate(timeout=10) + (process.returncode,) for process in processes]
        self.assertEqual(sorted(item[2] for item in results), [0, 1])
        loser = next(json.loads(stderr) for stdout, stderr, code in results if code == 1)
        self.assertEqual(loser["error"]["code"], "OUTPUT_EXISTS")
        self.assertEqual(set(path.name for path in bundle.iterdir()), {"asset", "evidence.json", "receipt.json"})

    def test_policy_digest_and_development_evidence_cannot_cross_production_gate(self):
        root_evidence, _ = self.capture_root()
        draft = self.root / "draft.txt"
        draft.write_text("draft\n", encoding="utf-8")
        bundle, _ = self.finalize(draft, root_evidence)
        self.write_policy(lambda p: p["publisher_handoff_policy"].update(allowed_transport_metadata=[]))
        rejected = self.run_origen(
            "prepublish", "--bundle", bundle, "--policy", self.policy, "--verifier-id", "verifier",
            "--timestamp-provider-id", "tsa", "--root-evidence", root_evidence, expected=1,
        )
        self.assertEqual(rejected["error"]["code"], "POLICY_DIGEST_MISMATCH")

        self.write_policy(lambda p: p.update(mode="development"))
        dev_human = self.root / "dev-human.txt"
        dev_human.write_text("Development Human\n", encoding="utf-8")
        dev_root, _ = self.capture_root(dev_human, evidence=self.root / "dev-root.json")
        dev_draft = self.root / "dev.txt"
        dev_draft.write_text("development\n", encoding="utf-8")
        dev_bundle, _ = self.finalize(dev_draft, dev_root, bundle=self.root / "dev-bundle")
        self.write_policy()
        rejected = self.run_origen(
            "prepublish", "--bundle", dev_bundle, "--policy", self.policy, "--verifier-id", "verifier",
            "--timestamp-provider-id", "tsa", "--root-evidence", dev_root, expected=1,
        )
        self.assertEqual(rejected["error"]["code"], "DEVELOPMENT_EVIDENCE_REJECTED")

    def test_strict_compose_rebuilds_from_sources_and_records_ai_instruction_actor(self):
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
        result = self.run_origen(
            "strict-compose", "--source-map", source_map, "--root-evidence", root_evidence,
            "--output", composed, "--policy", self.policy, "--verifier-id", "verifier",
            "--timestamp-provider-id", "tsa",
        )
        self.assertEqual(result["source_mapping"]["rebuilt_output_sha256"], sha256(composed))
        bundle, _ = self.finalize(
            composed, root_evidence, guarantee_level="strict_origin", source_map=source_map,
            source_kind="human-edit", instruction_actor="ai",
        )
        evidence = json.loads((bundle / "evidence.json").read_text(encoding="utf-8"))
        self.assertTrue(evidence["assurance"]["derivation"]["no_unmapped_generated_content"])
        self.assertEqual(evidence["actors"]["instruction_actor"], "ai")
        ready = self.run_origen(
            "prepublish", "--bundle", bundle, "--policy", self.policy, "--verifier-id", "verifier",
            "--timestamp-provider-id", "tsa", "--root-evidence", root_evidence, "--source-map", source_map,
        )
        self.assertTrue(ready["verified"])

    def test_grapheme_split_and_letter_synthesis_are_rejected(self):
        cases = (
            ("split", "a\u20ddxy\n", [{"op": "slice", "source_id": "root", "start": 0, "end": 1, "boundary": "grapheme"}], "SLICE_BOUNDARY_VIOLATION"),
            ("letters", "a b\n", [
                {"op": "slice", "source_id": "root", "start": 0, "end": 1, "boundary": "grapheme"},
                {"op": "slice", "source_id": "root", "start": 2, "end": 3, "boundary": "grapheme"},
            ], "LETTER_SYNTHESIS_FORBIDDEN"),
        )
        for name, source_text, operations, code in cases:
            human = self.root / f"{name}-human.txt"
            human.write_text(source_text, encoding="utf-8")
            root_evidence, _ = self.capture_root(human, evidence=self.root / f"{name}-root.json")
            source_map = self.root / f"{name}.json"
            source_map.write_text(json.dumps({
                "schema_version": "origen-source-map/2", "kind": "text", "instruction_actor": "ai",
                "sources": [{"source_id": "root", "asset": str(human), "evidence": str(root_evidence)}],
                "operations": operations,
            }), encoding="utf-8")
            rejected = self.run_origen(
                "strict-compose", "--source-map", source_map, "--root-evidence", root_evidence,
                "--output", self.root / f"{name}.txt", "--policy", self.policy, "--verifier-id", "verifier",
                "--timestamp-provider-id", "tsa", expected=1,
            )
            self.assertEqual(rejected["error"]["code"], code)

    def test_c2pa_text_markers_and_malformed_wrapper_are_detected(self):
        cases = {
            "structured.md": "-----BEGIN C2PA MANIFEST-----\nabc\n-----END C2PA MANIFEST-----\n",
            "variation.txt": "plain\ufe0ftext\n",
            "html.html": '<script type="application/c2pa">x</script>',
            "external.html": '<link rel="c2pa-manifest" href="x.c2pa">',
            "image.svg": '<svg><metadata c2pa:manifest="x"/></svg>',
        }
        self.write_policy(lambda p: p["allowed_media_types"].extend(["text/html", "image/svg+xml"]))
        for name, content in cases.items():
            path = self.root / name
            path.write_text(content, encoding="utf-8")
            args = ["inspect", path, "--policy", self.policy]
            if path.suffix == ".md":
                args += ["--publication-profile", "markdown-safe"]
            result = self.run_origen(*args)
            self.assertTrue(result["c2pa_markers"], name)
        malformed = self.root / "malformed.txt"
        malformed.write_text("-----BEGIN C2PA MANIFEST-----\n", encoding="utf-8")
        rejected = self.run_origen("inspect", malformed, "--policy", self.policy, expected=1)
        self.assertEqual(rejected["error"]["code"], "MALFORMED_C2PA_TEXT_WRAPPER")

    def test_oversized_file_and_excessive_png_dimensions_are_rejected(self):
        self.write_policy(lambda p: p["resource_limits"].update(input_file_bytes=4))
        oversized = self.root / "big.txt"
        oversized.write_text("12345", encoding="utf-8")
        rejected = self.run_origen("inspect", oversized, "--policy", self.policy, expected=1)
        self.assertEqual(rejected["error"]["code"], "FILE_TOO_LARGE")

    def test_excessive_source_map_operations_are_rejected(self):
        self.write_policy(lambda p: p["resource_limits"].update(operation_count=1))
        human = self.root / "human.txt"
        human.write_text("Alpha\nBeta\n", encoding="utf-8")
        root_evidence, _ = self.capture_root(human)
        source_map = self.root / "many-ops.json"
        source_map.write_text(json.dumps({
            "schema_version": "origen-source-map/2", "kind": "text", "instruction_actor": "tool",
            "sources": [{"source_id": "root", "asset": str(human), "evidence": str(root_evidence)}],
            "operations": [
                {"op": "slice", "source_id": "root", "start": 0, "end": 6, "boundary": "line"},
                {"op": "slice", "source_id": "root", "start": 6, "end": 11, "boundary": "line"},
            ],
        }), encoding="utf-8")
        rejected = self.run_origen(
            "strict-compose", "--source-map", source_map, "--root-evidence", root_evidence,
            "--output", self.root / "many.txt", "--policy", self.policy, "--verifier-id", "verifier",
            "--timestamp-provider-id", "tsa", expected=1,
        )
        self.assertEqual(rejected["error"]["code"], "OPERATION_COUNT_EXCEEDED")

    def test_duplicate_evidence_key_and_unsupported_v3_combination_are_rejected(self):
        root_evidence, _ = self.capture_root()
        draft = self.root / "draft.txt"
        draft.write_text("draft\n", encoding="utf-8")
        bundle, _ = self.finalize(draft, root_evidence)
        evidence_path = bundle / "evidence.json"
        text = evidence_path.read_text(encoding="utf-8")
        evidence_path.write_text(text.replace('"schema_version":', '"schema_version":"origen-evidence/3","schema_version":', 1), encoding="utf-8")
        rejected = self.run_origen(
            "prepublish", "--bundle", bundle, "--policy", self.policy, "--verifier-id", "verifier",
            "--timestamp-provider-id", "tsa", "--root-evidence", root_evidence, expected=1,
        )
        self.assertEqual(rejected["error"]["code"], "INVALID_JSON")

        # Recreate and assert cross-field validation runs before cryptographic verification.
        bundle2, _ = self.finalize(draft, root_evidence, bundle=self.root / "bundle2")
        record = json.loads((bundle2 / "evidence.json").read_text(encoding="utf-8"))
        record["assurance"]["derivation"]["no_unmapped_generated_content"] = True
        (bundle2 / "evidence.json").write_text(json.dumps(record), encoding="utf-8")
        rejected = self.run_origen(
            "prepublish", "--bundle", bundle2, "--policy", self.policy, "--verifier-id", "verifier",
            "--timestamp-provider-id", "tsa", "--root-evidence", root_evidence, expected=1,
        )
        self.assertEqual(rejected["error"]["code"], "INVALID_EVIDENCE")

    def install_media_tools(self):
        builder = self.root / "builder.py"
        builder.write_text(
            """import json
from pathlib import Path
import sys
request = json.load(sys.stdin)
Path(request["output_path"]).write_bytes(b"\\xff\\xd8\\xff\\xd9")
json.dump({"status": "built", "builder_id": "media-builder"}, sys.stdout)
""",
            encoding="utf-8",
        )
        inspector = self.root / "inspector.py"
        inspector.write_text(
            """import json
import sys
request = json.load(sys.stdin)
coverage = {key: "covered" for key in request["required_coverage"]}
coverage.update({
    "file_type": "valid", "container_validity": "valid",
    "mime_extension_consistency": "consistent", "metadata": "not_present",
    "c2pa": "not_present", "exif_xmp_iptc": "not_present",
    "active_content": "not_present", "embedded_files": "not_present",
    "external_references": "not_present", "decodability": "decodable",
    "resource_limits": "within_limits", "policy_coverage": "covered",
})
json.dump({
    "status": "inspected", "inspector_id": "media-inspector", "coverage": coverage,
    "content_signals": {"state": "not_detected", "checks": ["test-detector:not_detected"]},
    "operation_validated": True, "source_bindings_validated": True, "output_validated": True,
}, sys.stdout)
""",
            encoding="utf-8",
        )
        def mutate(policy):
            policy["allowed_media_types"].append("image/jpeg")
            policy["approved_builders"]["media-builder"] = self.tool_script(
                "media-builder", builder, builder_identity="test-media-builder"
            )
            policy["approved_inspectors"]["media-inspector"] = self.tool_script(
                "media-inspector", inspector, inspector_identity="independent-media-inspector"
            )
        self.write_policy(mutate)
        return builder, inspector

    def media_source_map(self, source, evidence, transformation):
        source_map = self.root / f"map-{len(list(self.root.glob('map-*.json')))}.json"
        source_map.write_text(json.dumps({
            "schema_version": "origen-source-map/2", "kind": "media", "instruction_actor": "ai",
            "sources": [{"source_id": "root", "asset": str(source), "evidence": str(evidence)}],
            "primary_source_id": "root", "transformation": transformation,
        }), encoding="utf-8")
        return source_map

    def test_typed_crop_resize_pass_and_unsafe_content_parameters_fail(self):
        self.install_media_tools()
        human = self.root / "human.jpg"
        human.write_bytes(b"\xff\xd8\xff\xd9")
        root_evidence, _ = self.capture_root(human)
        for index, transformation in enumerate((
            {"op": "crop", "parameters": {"x": 0, "y": 0, "width": 1, "height": 1}},
            {"op": "resize", "parameters": {"width": 1, "height": 1, "filter": "nearest"}},
        )):
            source_map = self.media_source_map(human, root_evidence, transformation)
            bundle, result = self.finalize(
                human, root_evidence, bundle=self.root / f"media-bundle-{index}",
                guarantee_level="strict_origin", source_map=source_map, source_kind="captured-original",
                builder_id="media-builder", inspector_id="media-inspector",
            )
            self.assertTrue(result["publish_ready"])
            evidence = json.loads((bundle / "evidence.json").read_text(encoding="utf-8"))
            self.assertEqual(evidence["assurance"]["content_signals"]["state"], "unknown")

        invalid = (
            ({"op": "overlay-signed-asset", "parameters": {"source_id": "unsigned"}}, "UNSIGNED_CONTENT_RESOURCE"),
            ({"op": "crop", "parameters": {"url": "https://example.invalid/mask"}}, "UNSAFE_OPERATION_PARAMETER"),
            ({"op": "resize", "parameters": {"base64": "data:image/png;base64,AAAA"}}, "UNSAFE_OPERATION_PARAMETER"),
            ({"op": "add-signed-subtitle", "parameters": {"subtitle": "unsigned words"}}, "UNSAFE_OPERATION_PARAMETER"),
            ({"op": "overlay-signed-asset", "parameters": {"logo": "unsigned.png"}}, "UNSAFE_OPERATION_PARAMETER"),
        )
        for transformation, code in invalid:
            source_map = self.media_source_map(human, root_evidence, transformation)
            _, rejected = self.finalize(
                human, root_evidence, bundle=self.root / f"reject-{code}-{len(list(self.root.glob('reject-*')))}",
                guarantee_level="strict_origin", source_map=source_map, source_kind="captured-original",
                builder_id="media-builder", inspector_id="media-inspector", expected=1,
            )
            self.assertEqual(rejected["error"]["code"], code)

    def test_arbitrary_builder_and_inspector_ids_are_rejected(self):
        self.install_media_tools()
        human = self.root / "human.jpg"
        human.write_bytes(b"\xff\xd8\xff\xd9")
        root_evidence, _ = self.capture_root(human)
        source_map = self.media_source_map(human, root_evidence, {"op": "crop", "parameters": {"x": 0, "y": 0, "width": 1, "height": 1}})
        _, rejected = self.finalize(
            human, root_evidence, guarantee_level="strict_origin", source_map=source_map,
            source_kind="captured-original", builder_id="arbitrary", inspector_id="media-inspector", expected=1,
        )
        self.assertEqual(rejected["error"]["code"], "BUILDER_NOT_APPROVED")
        _, rejected = self.finalize(
            human, root_evidence, guarantee_level="strict_origin", source_map=source_map,
            source_kind="captured-original", builder_id="media-builder", inspector_id="arbitrary", expected=1,
        )
        self.assertEqual(rejected["error"]["code"], "INSPECTOR_NOT_APPROVED")

    def test_provider_stdout_bomb_and_timeout_are_bounded(self):
        bomb = self.root / "bomb.py"
        bomb.write_text("import sys\nsys.stdout.write('x' * 100000)\n", encoding="utf-8")
        self.write_policy(lambda p: (
            p["resource_limits"].update(subprocess_stdout_bytes=1024),
            p["approved_signers"].update({"root-signer": self.tool_script(
                "root-signer", bomb, role="root-attestor", key_id="root-key", algorithm="test-sha256",
                signer_identity="human-root-service", agent_invocable=False,
            )}),
        ))
        _, rejected = self.capture_root(expected=1)
        self.assertEqual(rejected["error"]["code"], "PROVIDER_OUTPUT_LIMIT")

        sleeper = self.root / "sleep.py"
        sleeper.write_text("import time\ntime.sleep(2)\n", encoding="utf-8")
        self.write_policy(lambda p: (
            p["resource_limits"].update(subprocess_timeout_seconds=0.1),
            p["approved_signers"].update({"root-signer": self.tool_script(
                "root-signer", sleeper, role="root-attestor", key_id="root-key", algorithm="test-sha256",
                signer_identity="human-root-service", agent_invocable=False,
            )}),
        ))
        _, rejected = self.capture_root(expected=1)
        self.assertEqual(rejected["error"]["code"], "PROVIDER_TIMEOUT")

    def test_source_swap_and_final_rebuilt_digest_mismatch_are_rejected(self):
        human = self.root / "human.txt"
        human.write_text("Alpha\n", encoding="utf-8")
        root_evidence, _ = self.capture_root(human)
        source_map = self.root / "map.json"
        source_map.write_text(json.dumps({
            "schema_version": "origen-source-map/2", "kind": "text", "instruction_actor": "human",
            "sources": [{"source_id": "root", "asset": str(human), "evidence": str(root_evidence)}],
            "operations": [{"op": "slice", "source_id": "root", "start": 0, "end": 6, "boundary": "line"}],
        }), encoding="utf-8")
        composed = self.root / "composed.txt"
        self.run_origen(
            "strict-compose", "--source-map", source_map, "--root-evidence", root_evidence,
            "--output", composed, "--policy", self.policy, "--verifier-id", "verifier", "--timestamp-provider-id", "tsa",
        )
        bundle, _ = self.finalize(
            composed, root_evidence, guarantee_level="strict_origin", source_map=source_map,
            source_kind="human-edit",
        )
        human.write_text("Swapped\n", encoding="utf-8")
        rejected = self.run_origen(
            "prepublish", "--bundle", bundle, "--policy", self.policy, "--verifier-id", "verifier",
            "--timestamp-provider-id", "tsa", "--root-evidence", root_evidence, "--source-map", source_map, expected=1,
        )
        self.assertEqual(rejected["error"]["code"], "SOURCE_ASSET_MISMATCH")

        human.write_text("Alpha\n", encoding="utf-8")
        (bundle / "asset").write_text("tampered\n", encoding="utf-8")
        rejected = self.run_origen(
            "prepublish", "--bundle", bundle, "--policy", self.policy, "--verifier-id", "verifier",
            "--timestamp-provider-id", "tsa", "--root-evidence", root_evidence, "--source-map", source_map, expected=1,
        )
        self.assertEqual(rejected["error"]["code"], "ASSET_MISMATCH")

    def test_png_dimensions_decompression_and_embedded_c2pa_are_fail_closed(self):
        self.write_policy(lambda p: p["resource_limits"].update(width=8, height=8, pixel_count=64, decoded_bytes=80))
        wide = self.root / "wide.png"
        self.make_png(wide, width=9, height=1)
        rejected = self.run_origen("inspect", wide, "--policy", self.policy, expected=1)
        self.assertEqual(rejected["error"]["code"], "PNG_DIMENSIONS_EXCEEDED")

        bomb = self.root / "bomb.png"
        self.make_png(bomb, width=8, height=8)
        self.write_policy(lambda p: p["resource_limits"].update(width=8, height=8, pixel_count=64, decoded_bytes=16))
        rejected = self.run_origen("inspect", bomb, "--policy", self.policy, expected=1)
        self.assertEqual(rejected["error"]["code"], "PNG_DECOMPRESSION_LIMIT")

        credentialed = self.root / "credentialed.png"
        self.make_png(credentialed, extra=((b"caBX", b"manifest"),))
        self.write_policy()
        inspection = self.run_origen("inspect", credentialed, "--policy", self.policy)
        self.assertIn("PNG-caBX", inspection["c2pa_markers"])

    def test_binary_c2pa_carriers_and_external_manifest_are_detected(self):
        def mutate(policy):
            policy["allowed_media_types"].extend(["audio/wav", "application/pdf", "application/octet-stream"])
        self.write_policy(mutate)
        riff = self.root / "carrier.wav"
        riff.write_bytes(b"RIFF" + struct.pack("<I", 12) + b"WAVE" + b"c2pa" + b"\x00" * 8)
        pdf = self.root / "carrier.pdf"
        pdf.write_bytes(b"%PDF-1.7\n1 0 obj<</Type/EmbeddedFile/C2PA true>>endobj\n%%EOF")
        external = self.root / "manifest.c2pa"
        external.write_bytes(b"external manifest")
        self.assertIn("RIFF-C2PA", self.run_origen("inspect", riff, "--policy", self.policy)["c2pa_markers"])
        self.assertIn("PDF-embedded-C2PA", self.run_origen("inspect", pdf, "--policy", self.policy)["c2pa_markers"])
        self.assertIn("external-C2PA-manifest", self.run_origen("inspect", external, "--policy", self.policy)["c2pa_markers"])

    def test_archive_bomb_and_json_nonfinite_values_are_rejected(self):
        self.write_policy(lambda p: (
            p["allowed_media_types"].append("application/zip"),
            p["resource_limits"].update(compression_ratio=2, archive_entry_count=2, decoded_bytes=1024),
        ))
        archive = self.root / "bomb.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
            output.writestr("payload.txt", "A" * 10000)
        rejected = self.run_origen("inspect", archive, "--policy", self.policy, expected=1)
        self.assertEqual(rejected["error"]["code"], "ARCHIVE_BOMB")

        self.write_policy()
        for name, text in (("nan.json", '{"x":NaN}'), ("inf.json", '{"x":Infinity}')):
            path = self.root / name
            path.write_text(text, encoding="utf-8")
            rejected = self.run_origen("inspect", path, "--policy", self.policy, expected=1)
            self.assertEqual(rejected["error"]["code"], "INVALID_JSON")

        unknown = self.root / "unknown.bin"
        unknown.write_bytes(b"\x00\x01\x02")
        self.write_policy(lambda p: p["allowed_media_types"].append("application/octet-stream"))
        root_evidence, _ = self.capture_root()
        _, rejected = self.finalize(unknown, root_evidence, expected=1)
        self.assertEqual(rejected["error"]["code"], "UNSUPPORTED_FORMAT")

    def test_multi_root_and_human_addition_require_independent_signed_sources(self):
        root_a = self.root / "a.txt"
        root_b = self.root / "b.txt"
        root_a.write_text("Alpha\n", encoding="utf-8")
        root_b.write_text("Beta\n", encoding="utf-8")
        evidence_a, _ = self.capture_root(root_a, evidence=self.root / "a-root.json")
        evidence_b, _ = self.capture_root(root_b, evidence=self.root / "b-root.json")
        source_map = self.root / "multi.json"
        source_map.write_text(json.dumps({
            "schema_version": "origen-source-map/2", "kind": "text", "instruction_actor": "mixed",
            "sources": [
                {"source_id": "a", "asset": str(root_a), "evidence": str(evidence_a)},
                {"source_id": "b", "asset": str(root_b), "evidence": str(evidence_b)},
            ],
            "operations": [
                {"op": "slice", "source_id": "a", "start": 0, "end": 6, "boundary": "line"},
                {"op": "slice", "source_id": "b", "start": 0, "end": 5, "boundary": "line"},
            ],
        }), encoding="utf-8")
        output = self.root / "multi.txt"
        result = self.run_origen(
            "strict-compose", "--source-map", source_map, "--root-evidence", evidence_a,
            "--output", output, "--policy", self.policy, "--verifier-id", "verifier", "--timestamp-provider-id", "tsa",
        )
        self.assertEqual(len(result["source_mapping"]["sources"]), 2)

        unsigned = self.root / "unsigned.txt"
        unsigned.write_text("Unsigned addition\n", encoding="utf-8")
        source_map.write_text(source_map.read_text(encoding="utf-8").replace(str(root_b), str(unsigned)), encoding="utf-8")
        rejected = self.run_origen(
            "strict-compose", "--source-map", source_map, "--root-evidence", evidence_a,
            "--output", self.root / "rejected.txt", "--policy", self.policy, "--verifier-id", "verifier",
            "--timestamp-provider-id", "tsa", expected=1,
        )
        self.assertEqual(rejected["error"]["code"], "SOURCE_ASSET_MISMATCH")

    def test_proof_toolchain_policy_tamper_and_noncurrent_schema_are_rejected(self):
        root_evidence, _ = self.capture_root()
        draft = self.root / "draft.txt"
        draft.write_text("draft\n", encoding="utf-8")
        for field in ("proof", "toolchain", "policy"):
            bundle, _ = self.finalize(draft, root_evidence, bundle=self.root / f"tamper-{field}")
            evidence_path = bundle / "evidence.json"
            record = json.loads(evidence_path.read_text(encoding="utf-8"))
            if field == "proof":
                record["proof"]["signature"] = "tampered"
            elif field == "toolchain":
                record["toolchain"]["unicode_database_version"] = "0.0"
            else:
                record["policy"]["digest"] = "0" * 64
            evidence_path.write_text(json.dumps(record), encoding="utf-8")
            rejected = self.run_origen(
                "prepublish", "--bundle", bundle, "--policy", self.policy, "--verifier-id", "verifier",
                "--timestamp-provider-id", "tsa", "--root-evidence", root_evidence, expected=1,
            )
            expected_codes = {"proof": "SIGNATURE_INVALID", "toolchain": "SIGNATURE_INVALID", "policy": "POLICY_DIGEST_MISMATCH"}
            self.assertEqual(rejected["error"]["code"], expected_codes[field])

        old_bundle = self.root / "old-schema-bundle"
        old_bundle.mkdir()
        (old_bundle / "asset").write_text("old\n", encoding="utf-8")
        (old_bundle / "evidence.json").write_text(json.dumps({"schema_version": "origen-evidence/2"}), encoding="utf-8")
        (old_bundle / "receipt.json").write_text("{}", encoding="utf-8")
        rejected = self.run_origen(
            "prepublish", "--bundle", old_bundle, "--policy", self.policy, "--verifier-id", "verifier", expected=1,
        )
        self.assertEqual(rejected["error"]["code"], "UNSUPPORTED_EVIDENCE_SCHEMA")

    def test_adapter_background_mutation_does_not_change_final_snapshot(self):
        builder, _ = self.install_media_tools()
        builder.write_text(
            """import json
from pathlib import Path
import subprocess
import sys
request = json.load(sys.stdin)
output = Path(request["output_path"])
output.write_bytes(b"\\xff\\xd8\\xff\\xd9")
subprocess.Popen([sys.executable, "-c", "import pathlib,time,sys;time.sleep(.2);pathlib.Path(sys.argv[1]).write_bytes(b'changed')", str(output)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
json.dump({"status": "built", "builder_id": "media-builder"}, sys.stdout)
""",
            encoding="utf-8",
        )
        # Repin after changing the approved builder fixture.
        inspector = self.root / "inspector.py"
        def mutate(policy):
            policy["allowed_media_types"].append("image/jpeg")
            policy["approved_builders"]["media-builder"] = self.tool_script("media-builder", builder, builder_identity="test-media-builder")
            policy["approved_inspectors"]["media-inspector"] = self.tool_script("media-inspector", inspector, inspector_identity="independent-media-inspector")
        self.write_policy(mutate)
        human = self.root / "human.jpg"
        human.write_bytes(b"\xff\xd8\xff\xd9")
        root_evidence, _ = self.capture_root(human)
        source_map = self.media_source_map(human, root_evidence, {"op": "crop", "parameters": {"x": 0, "y": 0, "width": 1, "height": 1}})
        bundle, _ = self.finalize(
            human, root_evidence, guarantee_level="strict_origin", source_map=source_map,
            source_kind="captured-original", builder_id="media-builder", inspector_id="media-inspector",
        )
        time.sleep(0.4)
        self.assertEqual((bundle / "asset").read_bytes(), b"\xff\xd8\xff\xd9")

    def test_provider_not_detected_is_not_clean_and_detected_is_rejected(self):
        _, inspector = self.install_media_tools()
        inspector.write_text(
            inspector.read_text(encoding="utf-8").replace('"state": "not_detected"', '"state": "detected"'),
            encoding="utf-8",
        )
        builder = self.root / "builder.py"
        def mutate(policy):
            policy["allowed_media_types"].append("image/jpeg")
            policy["approved_builders"]["media-builder"] = self.tool_script("media-builder", builder, builder_identity="test-media-builder")
            policy["approved_inspectors"]["media-inspector"] = self.tool_script("media-inspector", inspector, inspector_identity="independent-media-inspector")
        self.write_policy(mutate)
        human = self.root / "human.jpg"
        human.write_bytes(b"\xff\xd8\xff\xd9")
        root_evidence, _ = self.capture_root(human)
        source_map = self.media_source_map(human, root_evidence, {"op": "crop", "parameters": {"x": 0, "y": 0, "width": 1, "height": 1}})
        _, rejected = self.finalize(
            human, root_evidence, guarantee_level="strict_origin", source_map=source_map,
            source_kind="captured-original", builder_id="media-builder", inspector_id="media-inspector", expected=1,
        )
        self.assertEqual(rejected["error"]["code"], "CONTENT_SIGNAL_DETECTED")

    def test_builtin_json_requires_pinned_shape_and_builds_canonical_bytes(self):
        schema = self.root / "shape.json"
        schema.write_text(json.dumps({
            "schema_version": "origen-json-shape/1",
            "shape": {
                "type": "object",
                "required": ["name", "count"],
                "additional_properties": False,
                "properties": {
                    "name": {"type": "string"},
                    "count": {"type": "integer"},
                },
            },
        }), encoding="utf-8")
        self.write_policy(lambda p: p["approved_json_schemas"].update({
            "record-v1": {"path": str(schema), "sha256": sha256(schema)},
        }))
        root_evidence, _ = self.capture_root()
        source = self.root / "source.json"
        source.write_text('{"name":"e\\u0301","count":1}\n', encoding="utf-8")
        bundle, _ = self.finalize(source, root_evidence, json_schema_id="record-v1")
        self.assertEqual((bundle / "asset").read_bytes(), '{"count":1,"name":"é"}\n'.encode())

        invalid = self.root / "invalid.json"
        invalid.write_text('{"name":"x","count":1,"extra":true}\n', encoding="utf-8")
        _, rejected = self.finalize(
            invalid, root_evidence, bundle=self.root / "invalid-json-bundle",
            json_schema_id="record-v1", expected=1,
        )
        self.assertEqual(rejected["error"]["code"], "JSON_SCHEMA_MISMATCH")

    def test_strict_final_rejects_ai_wording_and_nonprimary_media_input(self):
        human = self.root / "human.txt"
        human.write_text("Alpha\n", encoding="utf-8")
        root_evidence, _ = self.capture_root(human)
        source_map = self.root / "map.json"
        source_map.write_text(json.dumps({
            "schema_version": "origen-source-map/2", "kind": "text", "instruction_actor": "ai",
            "sources": [{"source_id": "root", "asset": str(human), "evidence": str(root_evidence)}],
            "operations": [{"op": "slice", "source_id": "root", "start": 0, "end": 6, "boundary": "line"}],
        }), encoding="utf-8")
        proposal = self.root / "proposal.txt"
        proposal.write_text("Alpha plus AI wording outside the signed source map\n", encoding="utf-8")
        _, rejected = self.finalize(
            proposal, root_evidence, guarantee_level="strict_origin", source_map=source_map,
            source_kind="ai-output", expected=1,
        )
        self.assertEqual(rejected["error"]["code"], "STRICT_CONTENT_MISMATCH")

        self.install_media_tools()
        signed = self.root / "human.jpg"
        signed.write_bytes(b"\xff\xd8\xff\xd9")
        jpeg_root, _ = self.capture_root(signed, evidence=self.root / "jpeg-root.json")
        generated = self.root / "generated.jpg"
        generated.write_bytes(b"\xff\xd8\xff\xe0\xff\xd9")
        media_map = self.media_source_map(signed, jpeg_root, {"op": "identity", "parameters": {}})
        _, rejected = self.finalize(
            generated, jpeg_root, guarantee_level="strict_origin", source_map=media_map,
            source_kind="captured-original", builder_id="media-builder", inspector_id="media-inspector",
            expected=1,
        )
        self.assertEqual(rejected["error"]["code"], "STRICT_CONTENT_MISMATCH")

    def test_production_root_requires_guarded_attestor_and_trusted_timestamp(self):
        self.write_policy(lambda p: p["approved_signers"]["root-signer"].update(agent_invocable=True))
        _, rejected = self.capture_root(expected=1)
        self.assertEqual(rejected["error"]["code"], "ROOT_ATTESTOR_EXPOSED")

        self.write_policy()
        human = self.root / "human.txt"
        human.write_text("Human\n", encoding="utf-8")
        rejected = self.run_origen(
            "root", human, "--creator-id", "creator:test", "--origin-id", "origin:test",
            "--signer-id", "root-signer", "--verifier-id", "verifier",
            "--policy", self.policy, "--evidence", self.root / "no-tsa-root.json", expected=1,
        )
        self.assertEqual(rejected["error"]["code"], "TRUSTED_TIMESTAMP_REQUIRED")

    def test_parent_lineage_is_signed_and_prepublish_requires_matching_parent(self):
        root_evidence, _ = self.capture_root()
        first_draft = self.root / "first.txt"
        first_draft.write_text("first generation\n", encoding="utf-8")
        parent_bundle, _ = self.finalize(first_draft, root_evidence, bundle=self.root / "parent-bundle")
        parent_evidence = parent_bundle / "evidence.json"

        second_draft = self.root / "second.txt"
        second_draft.write_text("second generation\n", encoding="utf-8")
        child_bundle, _ = self.finalize(
            second_draft, root_evidence, bundle=self.root / "child-bundle",
        )
        # Re-run with an explicit parent so the child signs the full lineage.
        chained_bundle = self.root / "chained-bundle"
        args = [
            "finalize", second_draft, "--bundle", chained_bundle, "--policy", self.policy,
            "--signer-id", "final-signer", "--verifier-id", "verifier", "--timestamp-provider-id", "tsa",
            "--root-evidence", root_evidence, "--parent-evidence", parent_evidence,
            "--source-kind", "human-edit", "--guarantee-level", "standard",
            "--transformation", "canonical build", "--instruction-actor", "human",
            "--publication-representation", "canonical-bytes",
        ]
        self.run_origen(*args)
        chained = json.loads((chained_bundle / "evidence.json").read_text(encoding="utf-8"))
        self.assertEqual(chained["lineage"]["parent_asset_id"], json.loads(parent_evidence.read_text(encoding="utf-8"))["asset"]["id"])

        rejected = self.run_origen(
            "prepublish", "--bundle", chained_bundle, "--policy", self.policy, "--verifier-id", "verifier",
            "--timestamp-provider-id", "tsa", "--root-evidence", root_evidence, expected=1,
        )
        self.assertEqual(rejected["error"]["code"], "LINEAGE_INCOMPLETE")
        rejected = self.run_origen(
            "prepublish", "--bundle", chained_bundle, "--policy", self.policy, "--verifier-id", "verifier",
            "--timestamp-provider-id", "tsa", "--root-evidence", root_evidence,
            "--parent-evidence", child_bundle / "evidence.json", expected=1,
        )
        self.assertEqual(rejected["error"]["code"], "LINEAGE_MISMATCH")
        ready = self.run_origen(
            "prepublish", "--bundle", chained_bundle, "--policy", self.policy, "--verifier-id", "verifier",
            "--timestamp-provider-id", "tsa", "--root-evidence", root_evidence,
            "--parent-evidence", parent_evidence,
        )
        self.assertTrue(ready["verified"])

    def test_invisible_characters_type_confusion_and_large_multibyte_text_stay_guarded(self):
        root_evidence, _ = self.capture_root()
        hidden = self.root / "hidden.txt"
        hidden.write_text("visible\u200btext\n", encoding="utf-8")
        _, rejected = self.finalize(hidden, root_evidence, bundle=self.root / "hidden-bundle", expected=1)
        self.assertEqual(rejected["error"]["code"], "TEXT_INVISIBLE_CHARACTER")

        self.write_policy(lambda p: p["allowed_media_types"].append("audio/mpeg"))
        ambiguous = self.root / "fake.mp3"
        ambiguous.write_bytes(b"\x00\x01\x02\x03")
        inspection = self.run_origen("inspect", ambiguous, "--policy", self.policy)
        self.assertIn("MEDIA_TYPE_UNCONFIRMED", [item["code"] for item in inspection["findings"]])
        self.assertEqual(inspection["structural_provenance"], "unknown")

        self.write_policy()
        large = self.root / "large.txt"
        large.write_bytes(("a" * 65535).encode("utf-8") + "é".encode("utf-8") + ("b" * 4096).encode("utf-8") + b"\n")
        inspection = self.run_origen("inspect", large, "--policy", self.policy)
        self.assertEqual(inspection["asset"]["media_type"], "text/plain")
        self.assertEqual(inspection["findings"], [])

    def test_builtin_markdown_profile_and_png_identity_rebuild(self):
        root_evidence, _ = self.capture_root()
        markdown = self.root / "safe.md"
        markdown.write_text("# Safe\r\n\r\nHuman-readable text.\r\n", encoding="utf-8")
        bundle, _ = self.finalize(
            markdown, root_evidence, publication_profile="markdown-safe",
            bundle=self.root / "markdown-bundle",
        )
        self.assertEqual((bundle / "asset").read_text(encoding="utf-8"), "# Safe\n\nHuman-readable text.\n")

        active = self.root / "active.md"
        active.write_text("<script>alert(1)</script>\n", encoding="utf-8")
        _, rejected = self.finalize(
            active, root_evidence, publication_profile="markdown-safe",
            bundle=self.root / "active-markdown-bundle", expected=1,
        )
        self.assertEqual(rejected["error"]["code"], "FINAL_INSPECTION_FAILED")

        png = self.root / "human.png"
        self.make_png(png, extra=((b"tEXt", b"Comment\x00human camera"),))
        png_root, _ = self.capture_root(png, evidence=self.root / "png-root.json")
        source_map = self.media_source_map(png, png_root, {"op": "identity", "parameters": {}})
        png_bundle, _ = self.finalize(
            png, png_root, guarantee_level="strict_origin", source_map=source_map,
            source_kind="captured-original", bundle=self.root / "png-bundle",
        )
        inspection = self.run_origen("inspect", png_bundle / "asset", "--policy", self.policy)
        self.assertNotIn("tEXt", inspection["chunks"])


if __name__ == "__main__":
    unittest.main()
