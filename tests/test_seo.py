import importlib.util
import io
import json
import re
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/seo"
SCRIPT = SKILL / "scripts/seo_evidence.py"
SPEC = importlib.util.spec_from_file_location("seo_evidence", SCRIPT)
seo_evidence = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(seo_evidence)


class SeoSkillContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.skill_text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        cls.reference_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((SKILL / "references").glob("*.md"))
        )
        cls.contract = cls.skill_text + "\n" + cls.reference_text

    def test_required_self_contained_files_exist(self):
        required = {
            "SKILL.md",
            "LICENSE.txt",
            "agents/openai.yaml",
            "references/source-policy.md",
            "references/technical-search.md",
            "references/search-quality.md",
            "references/measurement.md",
            "references/structured-data.md",
            "references/programmatic-seo.md",
            "references/ai-search.md",
            "scripts/seo_evidence.py",
        }
        actual = {
            str(path.relative_to(SKILL))
            for path in SKILL.rglob("*")
            if path.is_file()
        }
        self.assertTrue(required.issubset(actual), required - actual)

    def test_conditional_reference_links_resolve(self):
        links = re.findall(r"`(references/[^`]+\.md)`", self.skill_text)
        expected = {
            "references/source-policy.md",
            "references/technical-search.md",
            "references/search-quality.md",
            "references/measurement.md",
            "references/structured-data.md",
            "references/programmatic-seo.md",
            "references/ai-search.md",
        }
        self.assertEqual(set(links), expected)
        for link in links:
            self.assertTrue((SKILL / link).is_file(), link)

    def test_protocol_evidence_and_completion_contracts_exist(self):
        for phrase in (
            "Observe → Measure → Diagnose → Fix → Verify",
            "confirmed",
            "likely",
            "hypothesis",
            "unsupported",
            "severityとconfidenceを独立",
            "verified implementation",
            "pending external recrawl",
            "search crawler、training crawler、user-triggered fetch",
            "field / lab",
            "Schema.org validity",
            "rich-result eligibility",
            "Unique utility",
        ):
            self.assertIn(phrase, self.contract)

    def test_frontmatter_catalog_and_agent_metadata(self):
        frontmatter = re.match(r"^---\n(.*?)\n---\n", self.skill_text, re.S).group(1)
        top_level = {
            match.group(1)
            for line in frontmatter.splitlines()
            if (match := re.match(r"^([a-z][a-z0-9-]*):", line))
        }
        self.assertEqual(top_level, {"name", "description", "license", "metadata"})
        self.assertIn('claudagt.version: "0.2.0"', frontmatter)
        catalog = (ROOT / "skills/SKILLS.md").read_text(encoding="utf-8")
        metadata = (SKILL / "agents/openai.yaml").read_text(encoding="utf-8")
        self.assertIn("[`seo`](seo/SKILL.md)", catalog)
        self.assertIn("$seo", metadata)
        self.assertIn("allow_implicit_invocation: true", metadata)

    def test_publisher_import_preserves_self_contained_skill_and_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "agent-directory"
            completed = subprocess.run(
                ["/bin/bash", str(ROOT / "tools/import-skill.sh"), "seo", "--target", str(target)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            imported = target / "skills/seo"
            for relative in (
                "LICENSE.txt",
                "references/source-policy.md",
                "scripts/seo_evidence.py",
                "agents/upstream.yaml",
            ):
                self.assertTrue((imported / relative).is_file(), relative)
            projected = (imported / "SKILL.md").read_text(encoding="utf-8")
            upstream = (imported / "agents/upstream.yaml").read_text(encoding="utf-8")
            self.assertIn("status: active", projected)
            self.assertIn('source_version: "0.2.0"', upstream)
            self.assertIn('import_mode: "vendored-copy"', upstream)


class SeoEvidenceToolTests(unittest.TestCase):
    def test_static_extraction_does_not_claim_rendered_schema_absence(self):
        html = """<!doctype html><html lang="ja"><head>
        <title> Example </title><link rel="canonical" href="/example">
        <script>window.injectLater = 'application/ld+json';</script>
        </head><body><h1>Heading</h1><a href="/next#part">Next</a></body></html>"""
        parser = seo_evidence.PageSignalParser("https://example.com/example")
        parser.feed(html)
        result = parser.result()
        self.assertEqual(result["structured_data"]["status"], "not_observed_in_static_html")
        self.assertIn("does not establish absence", result["structured_data"]["limitation"])
        self.assertEqual(result["canonical"], ["https://example.com/example"])
        self.assertEqual(result["internal_links"], ["https://example.com/next"])

    def test_static_extraction_reports_jsonld_without_claiming_eligibility(self):
        html = """<script type="application/ld+json">
        {"@context":"https://schema.org","@type":"Product","name":"Example"}
        </script>"""
        parser = seo_evidence.PageSignalParser("https://example.com/product")
        parser.feed(html)
        result = parser.result()["structured_data"]
        self.assertEqual(result["status"], "observed_in_static_html")
        self.assertEqual(result["json_ld_blocks"][0]["types"], ["Product"])
        self.assertIn("eligibility", result["limitation"])

    def test_inventory_finds_required_deterministic_signals(self):
        result = seo_evidence.audit_inventory(
            [
                {
                    "url": "https://example.com/",
                    "status": 403,
                    "crawler_role": "search",
                    "scope": "site",
                    "expected_indexable": True,
                },
                {
                    "url": "https://example.com/a",
                    "status": 200,
                    "expected_indexable": True,
                    "canonical": "https://example.com/b",
                    "expected_canonical": "https://example.com/a",
                },
                {
                    "url": "https://example.com/old",
                    "status": 301,
                    "final_url": "https://example.com/new",
                    "in_sitemap": True,
                    "meta_robots": "noindex, follow",
                    "canonical": "https://example.com/preferred",
                },
            ]
        )
        by_code = {item["code"]: item for item in result["signals"]}
        self.assertEqual(by_code["SEARCH_CRAWLER_BLOCKED"]["severity_candidate"], "Critical")
        self.assertEqual(by_code["SEARCH_CRAWLER_BLOCKED"]["evidence_state"], "observed")
        self.assertNotIn("confidence", by_code["SEARCH_CRAWLER_BLOCKED"])
        for code in ("CANONICAL_MISMATCH", "SITEMAP_REDIRECT", "SITEMAP_NOINDEX", "SITEMAP_NON_CANONICAL"):
            self.assertIn(code, by_code)
        self.assertTrue(all(item["needs_diagnosis"] for item in result["signals"]))

    def test_inventory_only_compares_canonical_when_expectation_is_known(self):
        result = seo_evidence.audit_inventory(
            [{"url": "https://example.com/a", "status": 200, "canonical": "https://example.com/b"}]
        )
        self.assertEqual(result["signals"], [])

    def test_cli_fail_threshold_and_jsonl_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "url": "https://example.com/",
                        "status": 403,
                        "crawler_role": "search",
                        "scope": "site",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with redirect_stdout(io.StringIO()):
                self.assertEqual(seo_evidence.main(["audit-inventory", "--input", str(path)]), 0)
                self.assertEqual(
                    seo_evidence.main(
                        ["audit-inventory", "--input", str(path), "--fail-on", "Critical"]
                    ),
                    1,
                )


if __name__ == "__main__":
    unittest.main()
