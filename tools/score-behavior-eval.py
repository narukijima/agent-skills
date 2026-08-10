#!/usr/bin/env python3
"""Validate behavior eval cases and score evidence-backed judgments.

This tool deliberately does not infer success from keyword presence. A human or
independent model must judge each semantic criterion and provide concise evidence.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def validate_cases(payload: dict) -> list[str]:
    errors: list[str] = []
    if payload.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        return errors + ["cases must be a non-empty list"]
    seen: set[str] = set()
    for index, case in enumerate(cases):
        prefix = f"cases[{index}]"
        if not isinstance(case, dict):
            errors.append(f"{prefix} must be an object")
            continue
        case_id = case.get("id")
        if not isinstance(case_id, str) or not re.fullmatch(r"[a-z0-9-]+", case_id):
            errors.append(f"{prefix}.id must use lowercase hyphen-case")
        elif case_id in seen:
            errors.append(f"duplicate case id: {case_id}")
        else:
            seen.add(case_id)
        if not isinstance(case.get("prompt"), str) or not case["prompt"].strip():
            errors.append(f"{prefix}.prompt must be a non-empty string")
        if not isinstance(case.get("expected_activation"), bool):
            errors.append(f"{prefix}.expected_activation must be boolean")
        criteria = case.get("criteria")
        if not isinstance(criteria, list) or not criteria:
            errors.append(f"{prefix}.criteria must be a non-empty list")
        else:
            criterion_ids: set[str] = set()
            for criterion in criteria:
                if not isinstance(criterion, dict):
                    errors.append(f"{prefix}.criteria entries must be objects")
                    continue
                criterion_id = criterion.get("id")
                requirement = criterion.get("requirement")
                if not isinstance(criterion_id, str) or not re.fullmatch(r"[a-z0-9-]+", criterion_id):
                    errors.append(f"{prefix}.criteria id must use lowercase hyphen-case")
                elif criterion_id in criterion_ids:
                    errors.append(f"{prefix} has duplicate criterion id: {criterion_id}")
                else:
                    criterion_ids.add(criterion_id)
                if not isinstance(requirement, str) or not requirement.strip():
                    errors.append(f"{prefix}.criteria requirement must be non-empty")
        forbidden = case.get("forbidden_behaviors")
        if not isinstance(forbidden, list) or any(not isinstance(item, str) or not item for item in forbidden):
            errors.append(f"{prefix}.forbidden_behaviors must be a string list")
    return errors


def score(cases: list[dict], judgment_payload: dict) -> dict:
    judgments = {
        item.get("id"): item
        for item in judgment_payload.get("case_results", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    results = []
    for case in cases:
        case_id = case["id"]
        judgment = judgments.get(case_id)
        failures: list[str] = []
        if not isinstance(judgment, dict):
            failures.append("missing judgment")
            judgment = {}
        if judgment.get("observed_activation") is not case["expected_activation"]:
            failures.append(f"observed_activation must be {case['expected_activation']}")
        criterion_judgments = {
            item.get("id"): item
            for item in judgment.get("criteria", [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        for criterion in case["criteria"]:
            observed = criterion_judgments.get(criterion["id"])
            if not isinstance(observed, dict):
                failures.append("missing criterion judgment: " + criterion["id"])
                continue
            if observed.get("passed") is not True:
                failures.append("criterion failed: " + criterion["id"])
            evidence = observed.get("evidence")
            if not isinstance(evidence, str) or not evidence.strip():
                failures.append("missing evidence: " + criterion["id"])
        if judgment.get("forbidden_behavior_observed") is not False:
            failures.append("forbidden_behavior_observed must be false")
        results.append({"id": case_id, "passed": not failures, "failures": failures})
    passed = sum(item["passed"] for item in results)
    return {"passed": passed == len(results), "score": passed, "total": len(results), "results": results}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--judgments", type=Path)
    args = parser.parse_args(argv)
    payload = load_json(args.cases)
    errors = validate_cases(payload)
    if errors:
        print(json.dumps({"passed": False, "errors": errors}, ensure_ascii=False, indent=2))
        return 2
    if args.judgments is None:
        print(json.dumps({"passed": True, "cases": len(payload["cases"]), "behavior_run": False}, ensure_ascii=False))
        return 0
    result = score(payload["cases"], load_json(args.judgments))
    result["behavior_run"] = True
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
