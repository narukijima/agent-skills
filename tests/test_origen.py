import base64
import binascii
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib


REPO_ROOT = Path(__file__).resolve().parents[1]
ORIGEN = REPO_ROOT / "skills/origen/scripts/origen.py"


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

request = json.load(sys.stdin)
payload = base64.b64decode(request["payload"])
signature = hashlib.sha256(b"origen-test-provider:" + payload).hexdigest()
proof = request.get("proof", {})
if request["operation"] == "sign":
    response = {
        "provider": "origen-test-provider",
        "key_id": "test-key-1",
        "algorithm": "test-sha256",
        "signature": signature,
        "provider_version": "test-1",
        "key_protection": "external-service",
        "dependency_provenance": "generated test fixture",
        "reproducible_install": "python stdlib fixture",
        "request_scope": request.get("request_scope"),
    }
elif request["operation"] == "verify":
    response = {
        "verified": proof.get("signature") == signature,
        "provider": "origen-test-provider",
        "key_id": "test-key-1",
        "algorithm": "test-sha256",
        "provider_version": "test-1",
        "key_protection": "external-service",
    }
else:
    raise SystemExit(2)
json.dump(response, sys.stdout)
""",
            encoding="utf-8",
        )
        self.provider_command = f"{sys.executable} {self.provider}"

    def tearDown(self):
        self.temporary.cleanup()

    def run_origen(self, *args, expected=0):
        completed = subprocess.run(
            [sys.executable, str(ORIGEN), *map(str, args)],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            expected,
            msg=f"stdout={completed.stdout}\nstderr={completed.stderr}",
        )
        stream = completed.stdout if expected == 0 else completed.stderr
        return json.loads(stream)

    def resign_evidence(self, record, *, legacy=False):
        statement = {key: value for key, value in record.items() if key != "proof"}
        payload = json.dumps(
            statement, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        request = {
            "operation": "sign",
            "request_scope": "sign-canonical-evidence-only",
            "payload": base64.b64encode(payload).decode("ascii"),
            "payload_sha256": "unused-by-test-provider",
        }
        completed = subprocess.run(
            [sys.executable, str(self.provider)],
            input=json.dumps(request),
            stdout=subprocess.PIPE,
            text=True,
            check=True,
        )
        proof = json.loads(completed.stdout)
        if legacy:
            proof = {key: proof[key] for key in ("provider", "key_id", "algorithm", "signature")}
        record["proof"] = proof
        return record

    def capture_root(self, asset):
        evidence = self.root / f"{asset.name}.root.json"
        result = self.run_origen(
            "root",
            asset,
            "--creator-id",
            "creator:test",
            "--origin-id",
            "origin:test",
            "--timestamp",
            "2026-08-23T00:00:00Z",
            "--sign-command",
            self.provider_command,
            "--evidence",
            evidence,
        )
        return evidence, result

    def make_jpeg_adapter(self, *, missing_guarantee=None, fail=False, strict=False, content_status="unknown"):
        adapter = self.root / f"adapter-{missing_guarantee or 'complete'}.py"
        guarantees = [
            "decoded-content",
            "clean-container-rebuild",
            "metadata-policy-applied",
            "provenance-inspected",
            "output-validated",
        ]
        if strict:
            guarantees.extend((
                "human-origin-inputs-only",
                "deterministic-transformation",
                "content-origin-mapped",
            ))
        if missing_guarantee:
            guarantees.remove(missing_guarantee)
        adapter.write_text(
            """import json
from pathlib import Path
import sys

request = json.load(sys.stdin)
FAIL
Path(request["output_path"]).write_bytes(b"\\xff\\xd8\\xff\\xd9")
json.dump({
    "status": "rebuilt",
    "tool": "origen-test/jpeg-adapter",
    "version": "1.0.0",
    "media_type": "image/jpeg",
    "guarantees": GUARANTEES,
    "content_provenance": CONTENT_STATUS,
    "dependency_provenance": "test fixture source",
    "reproducible_install": "python stdlib test fixture",
}, sys.stdout)
""".replace("GUARANTEES", repr(guarantees)).replace("CONTENT_STATUS", repr(content_status)).replace(
                "FAIL", "raise SystemExit(9)" if fail else ""
            ),
            encoding="utf-8",
        )
        return f"{sys.executable} {adapter}"

    def write_source_map(self, path, *, source, evidence, kind="text", operations=None, transformation=None):
        value = {
            "schema_version": "origen-source-map/1",
            "kind": kind,
            "sources": [{
                "source_id": "root",
                "asset": str(source),
                "evidence": str(evidence),
            }],
        }
        if kind == "text":
            value["operations"] = operations or []
        else:
            value["primary_source_id"] = "root"
            value["transformation"] = transformation or {"op": "identity"}
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    @staticmethod
    def png_chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", binascii.crc32(kind + data) & 0xFFFFFFFF)
        )

    def make_png(self, path, extra_chunks=(), pixel=0x7F):
        signature = b"\x89PNG\r\n\x1a\n"
        ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
        raw = b"\x00" + bytes([pixel])
        chunks = [self.png_chunk(b"IHDR", ihdr)]
        chunks.extend(self.png_chunk(kind, data) for kind, data in extra_chunks)
        chunks.extend((self.png_chunk(b"IDAT", zlib.compress(raw)), self.png_chunk(b"IEND", b"")))
        path.write_bytes(signature + b"".join(chunks))

    def test_root_captures_signed_hash_but_is_not_publish_ready(self):
        asset = self.root / "human.md"
        asset.write_text("Human source\n", encoding="utf-8")
        evidence, result = self.capture_root(asset)

        self.assertEqual(result["status"], "root-captured")
        self.assertFalse(result["publish_ready"])
        record = json.loads(evidence.read_text(encoding="utf-8"))
        self.assertEqual(record["origin"]["creator_id"], "creator:test")
        self.assertEqual(record["evidence_type"], "human-root")

        verified = self.run_origen(
            "verify",
            asset,
            "--evidence",
            evidence,
            "--verify-command",
            self.provider_command,
        )
        self.assertEqual(verified["status"], "verified")
        self.assertFalse(verified["publish_ready"])

    def test_empty_human_identity_is_rejected_before_signing(self):
        asset = self.root / "human.txt"
        asset.write_text("human\n", encoding="utf-8")
        rejected = self.run_origen(
            "root",
            asset,
            "--creator-id",
            "",
            "--origin-id",
            "origin:test",
            "--sign-command",
            self.provider_command,
            "--evidence",
            self.root / "root.json",
            expected=1,
        )
        self.assertEqual(rejected["error"]["code"], "IDENTITY_REQUIRED")

    def test_signing_provider_with_agent_readable_key_mode_is_rejected(self):
        unsafe_provider = self.root / "unsafe-provider.py"
        unsafe_provider.write_text(
            self.provider.read_text(encoding="utf-8").replace(
                '"key_protection": "external-service"',
                '"key_protection": "agent-readable-file"',
            ),
            encoding="utf-8",
        )
        asset = self.root / "human.txt"
        asset.write_text("human\n", encoding="utf-8")
        rejected = self.run_origen(
            "root",
            asset,
            "--creator-id",
            "creator:test",
            "--origin-id",
            "origin:test",
            "--sign-command",
            f"{sys.executable} {unsafe_provider}",
            "--evidence",
            self.root / "root.json",
            expected=1,
        )
        self.assertEqual(rejected["error"]["code"], "UNSAFE_SIGNING_PROVIDER")

    def test_json_finalization_is_canonical_and_links_signed_root(self):
        human = self.root / "human.txt"
        human.write_text("human\n", encoding="utf-8")
        root_evidence, _ = self.capture_root(human)
        source = self.root / "ai.json"
        source.write_text('{"z": 1, "a": "e\\u0301"}\r\n', encoding="utf-8")
        final = self.root / "final.json"
        evidence = self.root / "final.origen.json"

        result = self.run_origen(
            "finalize",
            source,
            "--output",
            final,
            "--evidence",
            evidence,
            "--source-kind",
            "ai-output",
            "--transformation",
            "edited from signed root",
            "--root-evidence",
            root_evidence,
            "--sign-command",
            self.provider_command,
            "--verify-command",
            self.provider_command,
            "--timestamp",
            "2026-08-23T01:00:00Z",
        )
        self.assertTrue(result["publish_ready"])
        self.assertEqual(result["guarantee_level"], "standard")
        self.assertEqual(result["structural_provenance"], "clean")
        self.assertEqual(result["content_provenance"], "unknown")
        self.assertEqual(final.read_text(encoding="utf-8"), '{"a":"é","z":1}\n')
        record = json.loads(evidence.read_text(encoding="utf-8"))
        self.assertEqual(record["event"]["source_kind"], "ai-output")
        self.assertIsNotNone(record["lineage"]["root_evidence_digest"])

        ready = self.run_origen(
            "prepublish",
            final,
            "--evidence",
            evidence,
            "--root-evidence",
            root_evidence,
            "--verify-command",
            self.provider_command,
        )
        self.assertEqual(ready["status"], "publish-ready")
        self.assertTrue(ready["chain_verified"])

    def test_prepublish_requires_linked_root_evidence(self):
        human = self.root / "human.txt"
        human.write_text("human\n", encoding="utf-8")
        root_evidence, _ = self.capture_root(human)
        source = self.root / "draft.txt"
        source.write_text("draft\n", encoding="utf-8")
        final = self.root / "final.txt"
        evidence = self.root / "final.json"
        self.run_origen(
            "finalize",
            source,
            "--output",
            final,
            "--evidence",
            evidence,
            "--source-kind",
            "ai-output",
            "--transformation",
            "rewrite",
            "--root-evidence",
            root_evidence,
            "--sign-command",
            self.provider_command,
            "--verify-command",
            self.provider_command,
        )

        rejected = self.run_origen(
            "prepublish",
            final,
            "--evidence",
            evidence,
            "--verify-command",
            self.provider_command,
            expected=1,
        )
        self.assertEqual(rejected["error"]["code"], "LINEAGE_INCOMPLETE")
        self.assertFalse(rejected["publish_ready"])

    def test_derivative_chain_verifies_root_and_parent(self):
        human = self.root / "human.txt"
        human.write_text("human\n", encoding="utf-8")
        root_evidence, _ = self.capture_root(human)

        source_a = self.root / "draft-a.txt"
        source_a.write_text("derivative a\n", encoding="utf-8")
        final_a = self.root / "final-a.txt"
        evidence_a = self.root / "final-a.json"
        self.run_origen(
            "finalize",
            source_a,
            "--output",
            final_a,
            "--evidence",
            evidence_a,
            "--source-kind",
            "ai-output",
            "--transformation",
            "create derivative A",
            "--root-evidence",
            root_evidence,
            "--sign-command",
            self.provider_command,
            "--verify-command",
            self.provider_command,
        )

        source_b = self.root / "draft-b.txt"
        source_b.write_text("derivative b\n", encoding="utf-8")
        final_b = self.root / "final-b.txt"
        evidence_b = self.root / "final-b.json"
        self.run_origen(
            "finalize",
            source_b,
            "--output",
            final_b,
            "--evidence",
            evidence_b,
            "--source-kind",
            "ai-output",
            "--transformation",
            "create derivative B from A",
            "--root-evidence",
            root_evidence,
            "--parent-evidence",
            evidence_a,
            "--sign-command",
            self.provider_command,
            "--verify-command",
            self.provider_command,
        )

        ready = self.run_origen(
            "prepublish",
            final_b,
            "--evidence",
            evidence_b,
            "--root-evidence",
            root_evidence,
            "--parent-evidence",
            evidence_a,
            "--verify-command",
            self.provider_command,
        )
        self.assertTrue(ready["chain_verified"])

    def test_tampered_final_asset_is_rejected(self):
        source = self.root / "draft.txt"
        source.write_text("draft\n", encoding="utf-8")
        final = self.root / "final.txt"
        evidence = self.root / "final.json"
        self.run_origen(
            "finalize",
            source,
            "--output",
            final,
            "--evidence",
            evidence,
            "--source-kind",
            "human-edit",
            "--transformation",
            "line ending normalization",
            "--sign-command",
            self.provider_command,
        )
        final.write_text("tampered\n", encoding="utf-8")

        rejected = self.run_origen(
            "prepublish",
            final,
            "--evidence",
            evidence,
            "--verify-command",
            self.provider_command,
            expected=1,
        )
        self.assertEqual(rejected["error"]["code"], "ASSET_MISMATCH")

    def test_tampered_evidence_signature_is_rejected(self):
        source = self.root / "draft.txt"
        source.write_text("draft\n", encoding="utf-8")
        final = self.root / "final.txt"
        evidence = self.root / "final.json"
        self.run_origen(
            "finalize",
            source,
            "--output",
            final,
            "--evidence",
            evidence,
            "--source-kind",
            "ai-output",
            "--transformation",
            "rewrite",
            "--sign-command",
            self.provider_command,
        )
        record = json.loads(evidence.read_text(encoding="utf-8"))
        record["event"]["transformations"] = ["misrepresented"]
        evidence.write_text(json.dumps(record), encoding="utf-8")

        rejected = self.run_origen(
            "verify",
            final,
            "--evidence",
            evidence,
            "--verify-command",
            self.provider_command,
            expected=1,
        )
        self.assertEqual(rejected["error"]["code"], "SIGNATURE_INVALID")

    def test_png_is_rebuilt_and_generic_metadata_is_not_inherited(self):
        source = self.root / "source.png"
        self.make_png(source, extra_chunks=((b"tEXt", b"Comment\x00external tool"),))
        final = self.root / "final.png"
        evidence = self.root / "final.json"
        self.run_origen(
            "finalize",
            source,
            "--output",
            final,
            "--evidence",
            evidence,
            "--source-kind",
            "external-tool",
            "--transformation",
            "trusted PNG container rebuild",
            "--sign-command",
            self.provider_command,
        )

        inspection = self.run_origen("inspect", final)
        self.assertEqual(inspection["provenance_status"], "clean")
        self.assertEqual(inspection["structural_provenance"], "clean")
        self.assertEqual(inspection["content_provenance"], "unknown")
        self.assertNotIn("tEXt", inspection["chunks"])
        self.assertTrue(final.exists())

    def test_embedded_c2pa_is_not_silently_removed(self):
        source = self.root / "credentialed.png"
        self.make_png(source, extra_chunks=((b"caBX", b"signed-content-credential"),))

        rejected = self.run_origen(
            "finalize",
            source,
            "--output",
            self.root / "final.png",
            "--evidence",
            self.root / "final.json",
            "--source-kind",
            "ai-output",
            "--transformation",
            "prepare final",
            "--sign-command",
            self.provider_command,
            expected=1,
        )
        self.assertEqual(rejected["error"]["code"], "PROVENANCE_REQUIRES_POLICY")

    def test_unsupported_jpeg_requires_explicit_trusted_adapter(self):
        source = self.root / "source.jpg"
        source.write_bytes(b"\xff\xd8\xff\xd9")

        rejected = self.run_origen(
            "finalize",
            source,
            "--output",
            self.root / "final.jpg",
            "--evidence",
            self.root / "final.json",
            "--source-kind",
            "external-tool",
            "--transformation",
            "prepare final",
            "--sign-command",
            self.provider_command,
            expected=1,
        )
        self.assertEqual(rejected["error"]["code"], "TRUSTED_ADAPTER_REQUIRED")

    def test_external_trusted_adapter_can_finalize_a_supported_family(self):
        source = self.root / "source.jpg"
        source.write_bytes(b"\xff\xd8\xff\xd9")
        final = self.root / "final.jpg"
        evidence = self.root / "final.json"
        result = self.run_origen(
            "finalize",
            source,
            "--output",
            final,
            "--evidence",
            evidence,
            "--source-kind",
            "external-tool",
            "--transformation",
            "test-only trusted JPEG rebuild",
            "--adapter-command",
            self.make_jpeg_adapter(),
            "--sign-command",
            self.provider_command,
        )
        self.assertTrue(result["publish_ready"])
        record = json.loads(evidence.read_text(encoding="utf-8"))
        self.assertEqual(record["inspection"]["provenance_status"], "verified-by-adapter")
        self.assertRegex(record["toolchain"]["executable_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(record["guarantee"]["content_provenance"], "unknown")
        ready = self.run_origen(
            "prepublish",
            final,
            "--evidence",
            evidence,
            "--verify-command",
            self.provider_command,
        )
        self.assertTrue(ready["publish_ready"])

    def test_external_adapter_missing_a_guarantee_is_rejected(self):
        source = self.root / "source.jpg"
        source.write_bytes(b"\xff\xd8\xff\xd9")
        rejected = self.run_origen(
            "finalize",
            source,
            "--output",
            self.root / "final.jpg",
            "--evidence",
            self.root / "final.json",
            "--source-kind",
            "external-tool",
            "--transformation",
            "test adapter",
            "--adapter-command",
            self.make_jpeg_adapter(missing_guarantee="output-validated"),
            "--sign-command",
            self.provider_command,
            expected=1,
        )
        self.assertEqual(rejected["error"]["code"], "ADAPTER_GUARANTEE_MISSING")

    def test_binary_extension_without_matching_magic_is_ambiguous(self):
        source = self.root / "fake.mp4"
        source.write_bytes(b"\x00\x01not-an-mp4")
        inspection = self.run_origen("inspect", source)
        self.assertEqual(inspection["findings"][0]["code"], "MEDIA_TYPE_UNCONFIRMED")
        rejected = self.run_origen(
            "finalize",
            source,
            "--output",
            self.root / "final.mp4",
            "--evidence",
            self.root / "final.json",
            "--source-kind",
            "external-tool",
            "--transformation",
            "prepare final",
            "--adapter-command",
            self.make_jpeg_adapter(),
            "--sign-command",
            self.provider_command,
            expected=1,
        )
        self.assertEqual(rejected["error"]["code"], "INPUT_AMBIGUOUS")

    def test_invisible_text_character_is_detected_and_rejected(self):
        source = self.root / "hidden.txt"
        source.write_text("visible\u200bhidden\n", encoding="utf-8")
        inspection = self.run_origen("inspect", source)
        self.assertEqual(inspection["structural_provenance"], "detected")
        self.assertIn("TEXT_INVISIBLE_CHARACTER", {item["code"] for item in inspection["findings"]})

        rejected = self.run_origen(
            "finalize",
            source,
            "--output",
            self.root / "final.txt",
            "--evidence",
            self.root / "final.json",
            "--source-kind",
            "ai-output",
            "--transformation",
            "normalize",
            "--sign-command",
            self.provider_command,
            expected=1,
        )
        self.assertEqual(rejected["error"]["code"], "TEXT_INVISIBLE_CHARACTER")
        self.assertFalse(rejected["publish_ready"])

    def test_leading_bom_is_removed_then_final_structure_is_clean(self):
        source = self.root / "bom.txt"
        source.write_bytes(b"\xef\xbb\xbfsafe\r\n")
        inspection = self.run_origen("inspect", source)
        self.assertEqual(inspection["structural_provenance"], "detected")
        final = self.root / "final.txt"
        evidence = self.root / "final.json"
        result = self.run_origen(
            "finalize",
            source,
            "--output",
            final,
            "--evidence",
            evidence,
            "--source-kind",
            "ai-output",
            "--transformation",
            "BOM and line-ending normalization",
            "--sign-command",
            self.provider_command,
        )
        self.assertEqual(final.read_bytes(), b"safe\n")
        self.assertEqual(result["structural_provenance"], "clean")
        self.assertEqual(result["content_provenance"], "unknown")

    def test_large_multibyte_text_is_not_misclassified_by_prefix_truncation(self):
        source = self.root / "large.md"
        source.write_text("記" * 30000 + "\n", encoding="utf-8")
        inspection = self.run_origen("inspect", source)
        self.assertEqual(inspection["asset"]["media_type"], "text/markdown")
        self.assertEqual(inspection["findings"], [])
        result = self.run_origen(
            "finalize",
            source,
            "--output",
            self.root / "large-final.md",
            "--evidence",
            self.root / "large-final.json",
            "--source-kind",
            "human-edit",
            "--transformation",
            "canonical UTF-8 build",
            "--sign-command",
            self.provider_command,
        )
        self.assertTrue(result["publish_ready"])
        self.assertEqual(result["structural_provenance"], "clean")

    def test_active_html_is_detected_and_requires_a_sanitizing_adapter(self):
        source = self.root / "active.html"
        source.write_text('<p onclick="x()">safe</p><script>alert(1)</script>\n', encoding="utf-8")
        inspection = self.run_origen("inspect", source)
        self.assertEqual(inspection["structural_provenance"], "detected")
        codes = {item["code"] for item in inspection["findings"]}
        self.assertIn("ACTIVE_SCRIPT", codes)
        self.assertIn("ACTIVE_EVENT_HANDLER", codes)
        rejected = self.run_origen(
            "finalize",
            source,
            "--output",
            self.root / "final.html",
            "--evidence",
            self.root / "final.json",
            "--source-kind",
            "external-tool",
            "--transformation",
            "sanitize active content",
            "--sign-command",
            self.provider_command,
            expected=1,
        )
        self.assertEqual(rejected["error"]["code"], "TRUSTED_ADAPTER_REQUIRED")

    def test_standard_ai_text_never_claims_content_level_clean(self):
        source = self.root / "ai.txt"
        source.write_text("AI-generated wording\n", encoding="utf-8")
        result = self.run_origen(
            "finalize",
            source,
            "--output",
            self.root / "final.txt",
            "--evidence",
            self.root / "final.json",
            "--source-kind",
            "ai-output",
            "--transformation",
            "canonical UTF-8 rebuild",
            "--sign-command",
            self.provider_command,
        )
        self.assertTrue(result["publish_ready"])
        self.assertEqual(result["guarantee_level"], "standard")
        self.assertEqual(result["structural_provenance"], "clean")
        self.assertEqual(result["content_provenance"], "unknown")
        self.assertFalse(result["root_verified"])

    def test_legacy_v1_final_can_be_verified_but_not_prepublished(self):
        source = self.root / "legacy.txt"
        source.write_text("legacy\n", encoding="utf-8")
        final = self.root / "final.txt"
        evidence = self.root / "final.json"
        self.run_origen(
            "finalize",
            source,
            "--output",
            final,
            "--evidence",
            evidence,
            "--source-kind",
            "human-edit",
            "--transformation",
            "normalize",
            "--sign-command",
            self.provider_command,
        )
        record = json.loads(evidence.read_text(encoding="utf-8"))
        record["schema_version"] = "origen-evidence/1"
        record.pop("guarantee")
        record.pop("toolchain")
        record.pop("source_mapping", None)
        self.resign_evidence(record, legacy=True)
        evidence.write_text(json.dumps(record), encoding="utf-8")

        verified = self.run_origen(
            "verify",
            final,
            "--evidence",
            evidence,
            "--verify-command",
            self.provider_command,
        )
        self.assertEqual(verified["guarantee_level"], "legacy_standard")
        rejected = self.run_origen(
            "prepublish",
            final,
            "--evidence",
            evidence,
            "--verify-command",
            self.provider_command,
            expected=1,
        )
        self.assertEqual(rejected["error"]["code"], "EVIDENCE_UPGRADE_REQUIRED")

    def test_strict_text_rejects_ai_wording_outside_human_source_map(self):
        human = self.root / "human.txt"
        human.write_text("Human words.\n", encoding="utf-8")
        root_evidence, _ = self.capture_root(human)
        source_map = self.write_source_map(
            self.root / "map.json",
            source=human,
            evidence=root_evidence,
            operations=[{"op": "slice", "source_id": "root", "start": 0, "end": len("Human words.\n")}],
        )
        proposed = self.root / "proposed.txt"
        proposed.write_text("Human words. AI added this.\n", encoding="utf-8")
        rejected = self.run_origen(
            "finalize",
            proposed,
            "--output",
            self.root / "final.txt",
            "--evidence",
            self.root / "final.json",
            "--guarantee-level",
            "strict_origin",
            "--source-map",
            source_map,
            "--source-kind",
            "human-edit",
            "--transformation",
            "Human-source selection",
            "--root-evidence",
            root_evidence,
            "--verify-command",
            self.provider_command,
            "--sign-command",
            self.provider_command,
            expected=1,
        )
        self.assertEqual(rejected["error"]["code"], "STRICT_CONTENT_MISMATCH")

    def test_strict_media_rejects_generated_pixels_as_human_origin(self):
        human = self.root / "human.png"
        self.make_png(human, pixel=0x11)
        root_evidence, _ = self.capture_root(human)
        source_map = self.write_source_map(
            self.root / "media-map.json",
            source=human,
            evidence=root_evidence,
            kind="media",
            transformation={"op": "identity"},
        )
        generated = self.root / "generated.png"
        self.make_png(generated, pixel=0xEE)
        rejected = self.run_origen(
            "finalize",
            generated,
            "--output",
            self.root / "final.png",
            "--evidence",
            self.root / "final.json",
            "--guarantee-level",
            "strict_origin",
            "--source-map",
            source_map,
            "--source-kind",
            "human-edit",
            "--transformation",
            "identity rebuild",
            "--root-evidence",
            root_evidence,
            "--verify-command",
            self.provider_command,
            "--sign-command",
            self.provider_command,
            expected=1,
        )
        self.assertEqual(rejected["error"]["code"], "STRICT_CONTENT_MISMATCH")

    def test_signed_human_png_identity_rebuild_passes_strict_origin(self):
        human = self.root / "human.png"
        self.make_png(human, extra_chunks=((b"tEXt", b"Comment\x00human camera"),), pixel=0x22)
        root_evidence, _ = self.capture_root(human)
        source_map = self.write_source_map(
            self.root / "media-map.json",
            source=human,
            evidence=root_evidence,
            kind="media",
            transformation={"op": "identity"},
        )
        final = self.root / "final.png"
        evidence = self.root / "final.json"
        result = self.run_origen(
            "finalize",
            human,
            "--output",
            final,
            "--evidence",
            evidence,
            "--guarantee-level",
            "strict_origin",
            "--source-map",
            source_map,
            "--source-kind",
            "captured-original",
            "--transformation",
            "trusted PNG container rebuild",
            "--root-evidence",
            root_evidence,
            "--verify-command",
            self.provider_command,
            "--sign-command",
            self.provider_command,
        )
        self.assertTrue(result["publish_ready"])
        self.assertEqual(result["structural_provenance"], "clean")
        self.assertEqual(result["content_provenance"], "verified_clean")
        inspection = self.run_origen("inspect", final)
        self.assertNotIn("tEXt", inspection["chunks"])

    def test_human_text_derivative_passes_strict_origin_and_source_map_recheck(self):
        human = self.root / "human.txt"
        human.write_text("Alpha\nBeta\n", encoding="utf-8")
        root_evidence, _ = self.capture_root(human)
        source_map = self.write_source_map(
            self.root / "map.json",
            source=human,
            evidence=root_evidence,
            operations=[
                {"op": "slice", "source_id": "root", "start": 6, "end": 10},
                {"op": "separator", "value": "\n\n"},
                {"op": "slice", "source_id": "root", "start": 0, "end": 5},
            ],
        )
        proposed = self.root / "proposed.txt"
        proposed.write_text("Beta\n\nAlpha\n", encoding="utf-8")
        final = self.root / "final.txt"
        evidence = self.root / "final.json"
        result = self.run_origen(
            "finalize",
            proposed,
            "--output",
            final,
            "--evidence",
            evidence,
            "--guarantee-level",
            "strict_origin",
            "--source-map",
            source_map,
            "--source-kind",
            "human-edit",
            "--transformation",
            "move signed Human spans",
            "--root-evidence",
            root_evidence,
            "--verify-command",
            self.provider_command,
            "--sign-command",
            self.provider_command,
        )
        self.assertTrue(result["publish_ready"])
        self.assertEqual(result["guarantee_level"], "strict_origin")
        self.assertEqual(result["content_provenance"], "verified_clean")
        self.assertTrue(result["root_verified"])
        ready = self.run_origen(
            "prepublish",
            final,
            "--evidence",
            evidence,
            "--root-evidence",
            root_evidence,
            "--source-map",
            source_map,
            "--verify-command",
            self.provider_command,
        )
        self.assertEqual(ready["content_provenance"], "verified_clean")
        self.assertTrue(ready["publish_ready"])

    def test_strict_origin_rejects_incomplete_source_map(self):
        human = self.root / "human.txt"
        human.write_text("Human\n", encoding="utf-8")
        root_evidence, _ = self.capture_root(human)
        source_map = self.write_source_map(
            self.root / "map.json",
            source=human,
            evidence=root_evidence,
            operations=[],
        )
        rejected = self.run_origen(
            "finalize",
            human,
            "--output",
            self.root / "final.txt",
            "--evidence",
            self.root / "final.json",
            "--guarantee-level",
            "strict_origin",
            "--source-map",
            source_map,
            "--source-kind",
            "human-edit",
            "--transformation",
            "select source",
            "--root-evidence",
            root_evidence,
            "--verify-command",
            self.provider_command,
            "--sign-command",
            self.provider_command,
            expected=1,
        )
        self.assertEqual(rejected["error"]["code"], "SOURCE_MAP_INCOMPLETE")

    def test_external_toolchain_failure_never_becomes_publish_ready(self):
        source = self.root / "source.jpg"
        source.write_bytes(b"\xff\xd8\xff\xd9")
        rejected = self.run_origen(
            "finalize",
            source,
            "--output",
            self.root / "final.jpg",
            "--evidence",
            self.root / "final.json",
            "--source-kind",
            "external-tool",
            "--transformation",
            "rebuild",
            "--adapter-command",
            self.make_jpeg_adapter(fail=True),
            "--sign-command",
            self.provider_command,
            expected=1,
        )
        self.assertEqual(rejected["error"]["code"], "PROVIDER_FAILED")
        self.assertFalse(rejected["publish_ready"])

    def test_detected_content_level_provenance_is_not_called_clean(self):
        source = self.root / "source.jpg"
        source.write_bytes(b"\xff\xd8\xff\xd9")
        rejected = self.run_origen(
            "finalize",
            source,
            "--output",
            self.root / "final.jpg",
            "--evidence",
            self.root / "final.json",
            "--source-kind",
            "ai-output",
            "--transformation",
            "rebuild",
            "--adapter-command",
            self.make_jpeg_adapter(content_status="detected"),
            "--sign-command",
            self.provider_command,
            expected=1,
        )
        self.assertEqual(rejected["error"]["code"], "CONTENT_PROVENANCE_DETECTED")
        self.assertEqual(rejected["content_provenance"], "unknown")

    def test_unknown_binary_is_fail_closed(self):
        source = self.root / "unknown.bin"
        source.write_bytes(b"\x00\x01\x02\x03")
        rejected = self.run_origen(
            "finalize",
            source,
            "--output",
            self.root / "final.bin",
            "--evidence",
            self.root / "final.json",
            "--source-kind",
            "external-tool",
            "--transformation",
            "prepare final",
            "--sign-command",
            self.provider_command,
            expected=1,
        )
        self.assertEqual(rejected["error"]["code"], "UNSUPPORTED_FORMAT")

    def test_duplicate_json_keys_are_rejected(self):
        source = self.root / "duplicate.json"
        source.write_text('{"a":1,"a":2}\n', encoding="utf-8")
        rejected = self.run_origen("inspect", source, expected=1)
        self.assertEqual(rejected["error"]["code"], "INVALID_JSON")

    def test_existing_output_is_never_overwritten(self):
        source = self.root / "draft.txt"
        source.write_text("draft\n", encoding="utf-8")
        final = self.root / "final.txt"
        final.write_text("keep\n", encoding="utf-8")
        rejected = self.run_origen(
            "finalize",
            source,
            "--output",
            final,
            "--evidence",
            self.root / "final.json",
            "--source-kind",
            "human-edit",
            "--transformation",
            "normalize",
            "--sign-command",
            self.provider_command,
            expected=1,
        )
        self.assertEqual(rejected["error"]["code"], "OUTPUT_EXISTS")
        self.assertEqual(final.read_text(encoding="utf-8"), "keep\n")


if __name__ == "__main__":
    unittest.main()
