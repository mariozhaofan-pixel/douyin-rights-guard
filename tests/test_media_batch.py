import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import case_manager as cm
import media_batch as mb
import media_probe as mp


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "case"
        cm.init_case(str(self.root))
        self.root, self.case = cm.load_case(str(self.root))
        self.case["works"] = [{"work_id": "w1", "url": "https://www.douyin.com/video/123", "title": "Synthetic original"}]
        cm.atomic_json(self.root / "case.json", self.case)
        self.add_media("original.mp4", "original_video", work_id="w1")

    def tearDown(self):
        self.temp.cleanup()

    def add_media(self, filename, kind, **bindings):
        path = self.root / "02_originals" / filename
        path.write_bytes(filename.encode())
        ledger = cm.manifest(self.root)
        ledger["evidence"].append({"path": path.relative_to(self.root).as_posix(), "sha256": cm.sha256(path), "kind": kind, **bindings})
        cm.atomic_json(self.root / cm.MANIFEST, ledger)

    def candidates(self, count=1, local=False):
        self.case["candidates"] = [{"candidate_id": f"c{i}", "platform": "douyin", "video_id": str(1000+i),
            "canonical_url": f"https://www.douyin.com/video/{1000+i}", "original_work_id": "w1", "submission_status": "not_submitted",
            "manual_review": {"status": "pending"}, "uploader_id": "uploader"} for i in range(count)]
        cm.atomic_json(self.root / "case.json", self.case)
        if local:
            for i in range(count):
                self.add_media(f"candidate{i}.mp4", "candidate_video", candidate_id=f"c{i}")

    def args(self, **kwargs):
        return SimpleNamespace(**({"root": str(self.root), "candidate": [], "download": False, "resume_platform": [],
            "retry_failed": False, "limit": 20, "interval": 1.0, "mirror": True, "max_frames": 720, "kuaishou_public": False} | kwargs))

    @staticmethod
    def compare(args):
        folder = Path(args.out)
        folder.mkdir(parents=True)
        (folder / "comparison.json").write_text('{"decision":"human_review_required"}', encoding="utf-8")
        (folder / "contact-sheet.jpg").write_bytes(b"synthetic")

    def test_offline_default_does_not_fetch_or_change_reviews(self):
        self.candidates()
        before = (self.root / "case.json").read_bytes()
        with patch.object(mp, "download") as fetch:
            report = mb.run_batch(self.args())
        fetch.assert_not_called()
        self.assertEqual(report["counts"], {"candidate_media_needed": 1})
        self.assertEqual(before, (self.root / "case.json").read_bytes())
        self.assertFalse(report["network_submission_performed"])

    def test_cached_jobs_do_not_consume_limit_and_next_batch_progresses(self):
        self.candidates(3, local=True)
        before = (self.root / "case.json").read_bytes()
        with patch.object(mp, "compare", side_effect=self.compare) as compare:
            first = mb.run_batch(self.args(limit=1))
            second = mb.run_batch(self.args(limit=1))
        self.assertEqual(compare.call_count, 2)
        self.assertEqual(first["counts"]["prepared_human_review_required"], 1)
        self.assertEqual(second["counts"], {"reused_comparison": 1, "prepared_human_review_required": 1, "deferred_by_limit": 1})
        self.assertEqual(before, (self.root / "case.json").read_bytes())

    def test_changed_comparison_file_is_not_reused(self):
        self.candidates(local=True)
        with patch.object(mp, "compare", side_effect=self.compare) as compare:
            first = mb.run_batch(self.args())
            (self.root / first["results"][0]["report"]).write_text("changed")
            second = mb.run_batch(self.args())
        self.assertEqual(compare.call_count, 2)
        self.assertEqual(second["counts"], {"prepared_human_review_required": 1})

    def test_cookie_failure_stops_remaining_and_persists(self):
        self.candidates(4)
        with patch.object(mp, "download", side_effect=mp.MediaError("[preflight/cookie_required] stopped")) as fetch:
            first = mb.run_batch(self.args(download=True))
            second = mb.run_batch(self.args(download=True))
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(first["counts"], {"preparation_failed": 2, "platform_download_stopped": 2})
        self.assertEqual(second["counts"], {"platform_download_stopped": 4})

    def test_cookie_streak_persists_across_single_job_runs(self):
        self.candidates(4)
        with patch.object(mp, "download", side_effect=mp.MediaError("[preflight/cookie_required] stopped")) as fetch:
            mb.run_batch(self.args(download=True, limit=1))
            second = mb.run_batch(self.args(download=True, limit=1))
            mb.run_batch(self.args(download=True, limit=1))
        self.assertEqual(fetch.call_count, 2)
        self.assertIn("douyin", second["platform_stops"])

    def test_saved_download_does_not_survive_target_change(self):
        file = self.root / "04_candidates/candidate.mp4"
        file.write_bytes(b"synthetic")
        item = {"download": {"file": "04_candidates/candidate.mp4", "sha256": cm.sha256(file),
                              "source_url": "https://www.douyin.com/video/1000"}}
        self.assertEqual(mb.saved_download(self.root, item, "https://www.douyin.com/video/1000"), file)
        self.assertIsNone(mb.saved_download(self.root, item, "https://www.douyin.com/video/1001"))

    def test_public_backend_unknown_failure_stops_platform(self):
        self.candidates(2)
        for candidate in self.case["candidates"]:
            candidate["platform"] = "kuaishou"
            candidate["canonical_url"] = cm.canonical_url("kuaishou", candidate["video_id"])
        cm.atomic_json(self.root / "case.json", self.case)
        with patch.object(mp, "download", side_effect=mp.MediaError("页面显示验证")) as fetch:
            report = mb.run_batch(self.args(download=True, kuaishou_public=True))
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(report["counts"], {"preparation_failed": 1, "platform_download_stopped": 1})

    def test_access_rejected_stops_immediately_and_no_raw_error_logged(self):
        self.candidates(2)
        with patch.object(mp, "download", side_effect=mp.MediaError("[preflight/access_rejected] private-token-example")) as fetch:
            report = mb.run_batch(self.args(download=True))
        self.assertEqual(fetch.call_count, 1)
        self.assertNotIn("private-token-example", json.dumps(report))

    def test_withdrawn_submitted_and_own_candidates_are_skipped(self):
        self.candidates(3, local=True)
        self.case["candidates"][0]["submission_status"] = "unknown"
        self.case["candidates"][1]["platform"] = "kuaishou"
        self.case["platform_scope"]["active_platforms"] = ["douyin", "bilibili"]
        self.case["account_roles"] = {"douyin": {"known_own_account_ids": ["uploader"]}}
        cm.atomic_json(self.root / "case.json", self.case)
        with patch.object(mp, "download") as fetch, patch.object(mp, "compare") as compare:
            report = mb.run_batch(self.args(download=True))
        fetch.assert_not_called()
        compare.assert_not_called()
        self.assertEqual(report["counts"], {"submission_state_requires_followup": 1, "inactive_platform": 1, "own_or_whitelisted_account": 1})

    def test_corrupt_original_evidence_blocks_fetch(self):
        self.candidates()
        (self.root / "02_originals/original.mp4").write_bytes(b"changed")
        with patch.object(mp, "download") as fetch:
            report = mb.run_batch(self.args(download=True))
        fetch.assert_not_called()
        self.assertEqual(report["counts"], {"original_media_missing_or_ambiguous": 1})


if __name__ == "__main__":
    unittest.main()
