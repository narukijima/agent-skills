import tempfile
import threading
import unittest
from pathlib import Path

from tests.sns_api_helpers import core, make_manifest, signed
from sns_api_lib import ledger


class LedgerTests(unittest.TestCase):
    def setUp(self): self.temp = tempfile.TemporaryDirectory(); core._WORKSPACE = (Path(self.temp.name), "test")
    def tearDown(self): core._WORKSPACE = None; self.temp.cleanup()

    def test_platform_is_part_of_uniqueness_key(self):
        x_path = Path(self.temp.name) / "x.json"; make_manifest(x_path)
        x = signed(x_path); first = ledger.reserve_attempt(x)
        other = dict(x); other.update(platform="facebook", operation="publish.text", account_type="page")
        second = ledger.reserve_attempt(other)
        self.assertNotEqual(first, second)

    def test_identical_payload_different_content_id_is_duplicate(self):
        path = Path(self.temp.name) / "m.json"; make_manifest(path); value = signed(path); ledger.reserve_attempt(value); ledger.record_result(1, "failed")
        other = dict(value); other["content_id"] = "other"; other["approval_id"] = "new"
        with self.assertRaises(core.ApiFailure) as raised: ledger.reserve_attempt(other)
        self.assertEqual(raised.exception.code, "DUPLICATE")

    def test_same_caption_with_different_media_is_a_distinct_intent(self):
        first_path = Path(self.temp.name) / "first.json"; second_path = Path(self.temp.name) / "second.json"
        common = {"platform": "threads", "operation": "publish.image", "account_type": "threads-user"}
        make_manifest(first_path, **common, payload={"text": "same", "assets": [{"kind": "remote", "url": "https://cdn.example/one.jpg", "mime": "image/jpeg"}]})
        make_manifest(second_path, **common, content_id="content-2", approval_id="approval-2",
                      payload={"text": "same", "assets": [{"kind": "remote", "url": "https://cdn.example/two.jpg", "mime": "image/jpeg"}]})
        first = ledger.reserve_attempt(signed(first_path)); ledger.record_result(first, "failed")
        second = ledger.reserve_attempt(signed(second_path))
        self.assertNotEqual(first, second)

    def test_provider_checkpoint_is_audited_without_tokens(self):
        path = Path(self.temp.name) / "m.json"; make_manifest(path); intent = ledger.reserve_attempt(signed(path))
        ledger.update_provider_state(intent, {"provider_id": "c1", "provider_status": "ready", "access_token": "do-not-store", "client_secret": "also-no"})
        row = ledger.get_intent("x", "42", "content-1")
        self.assertNotIn("access_token", row["provider_state"]); self.assertNotIn("client_secret", row["provider_state"])

    def test_only_same_manifest_with_explicit_provider_resume_can_resume_submitted(self):
        path = Path(self.temp.name) / "m.json"; make_manifest(path); value = signed(path); value["_allow_resume"] = True
        intent = ledger.reserve_attempt(value); ledger.update_provider_state(intent, {"container_id": "c1"}); ledger.record_result(intent, "submitted")
        self.assertEqual(ledger.reserve_attempt(value), intent)
        denied = dict(value); denied["_allow_resume"] = False
        with self.assertRaises(core.ApiFailure) as duplicate: ledger.reserve_attempt(denied)
        self.assertEqual(duplicate.exception.code, "DUPLICATE")

    def test_manual_resolve_requires_unknown_and_records_reason(self):
        path = Path(self.temp.name) / "m.json"; make_manifest(path); intent = ledger.reserve_attempt(signed(path)); row = ledger.get_intent("x", "42", "content-1")
        ledger.manual_resolve(row, "confirmed_absent", "out-of-band timeline inspection", None)
        self.assertEqual(ledger.get_intent("x", "42", "content-1")["status"], "confirmed_absent")
        with self.assertRaises(core.ApiFailure): ledger.manual_resolve(ledger.get_intent("x", "42", "content-1"), "confirmed_absent", "again", None)

    def test_concurrent_reservation_has_one_winner(self):
        path = Path(self.temp.name) / "m.json"; make_manifest(path); value = signed(path); outcomes = []
        def run():
            try: ledger.reserve_attempt(value); outcomes.append("ok")
            except core.ApiFailure: outcomes.append("refused")
        threads = [threading.Thread(target=run) for _ in range(2)]
        for item in threads: item.start()
        for item in threads: item.join()
        self.assertEqual(sorted(outcomes), ["ok", "refused"])


if __name__ == "__main__": unittest.main()
