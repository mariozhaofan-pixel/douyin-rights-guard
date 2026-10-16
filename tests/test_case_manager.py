"""Run with: python -m unittest discover -s tests -p test_case_manager.py"""
import argparse
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import struct
import unittest
import zlib
from unittest import mock


SPEC = importlib.util.spec_from_file_location("case_manager", Path(__file__).resolve().parents[1] / "scripts" / "case_manager.py")
cm = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cm)


class CaseManagerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rights-case-test-")
        self.root = Path(self.temporary.name) / "case"
        cm.init_case(str(self.root))
        self.root, self.case = cm.load_case(str(self.root))

    def tearDown(self):
        self.temporary.cleanup()

    def save_case(self):
        cm.atomic_json(self.root / "case.json", self.case)

    def fill_private(self):
        claimant = {"real_name": "TEST_PRIVATE_NAME", "email": "private@example.invalid", "phone": "TEST_PRIVATE_PHONE"}
        for key in cm.FILE_FIELDS:
            relative = "00_private/intake/" + key + ".png"
            path = self.root / relative
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b"synthetic-test-file")
            claimant[key] = relative
        cm.atomic_json(self.root / "00_private/claimant.json", claimant)

    def add_evidence(self, name, kind, work_id=None, candidate_id=None):
        path = self.root / "02_originals" / name
        path.write_bytes(b"SYNTHETIC " + name.encode())
        args = argparse.Namespace(file=path.relative_to(self.root).as_posix(), kind=kind,
                                  source_url="https://www.douyin.com/video/123?token=do-not-save",
                                  captured_at=None, work_id=work_id, candidate_id=candidate_id)
        return cm.evidence_add(self.root, self.case, args)

    def add_candidate(self):
        if not self.case["works"]:
            self.case["works"] = [{"work_id": "work_1", "title": "Synthetic original", "url": "https://www.douyin.com/video/123", "user_confirmed": False, "ownership_confirmed": False}]
        args = argparse.Namespace(platform="douyin", video_id="456", uploader_id="uploader_456", title="Synthetic candidate", original_id="work_1")
        return cm.candidate_add(self.root, self.case, args)

    def prepare_ready(self):
        self.fill_private()
        candidate_id = self.add_candidate()["candidate_id"]
        self.case["full_access_acknowledged"] = True
        self.case["authorizations"]["douyin"].update(status="authorized", account_id="original_account", authorized_at=cm.now())
        self.case["rights_review"].update(identity_status="user_verified", ownership_status="user_verified", reviewed_at=cm.now())
        self.case["works"][0].update(user_confirmed=True, ownership_confirmed=True)
        self.case["whitelist"].update(user_confirmed=True, confirmed_at=cm.now())
        self.case["submission_authorization"].update(user_confirmed=True, confirmed_at=cm.now(), candidate_ids=[candidate_id])
        candidate = self.case["candidates"][0]
        candidate["uploader_id_verified"] = True
        candidate["overlap_segments"] = [{"original_start": 0, "original_end": 4, "candidate_start": 1, "candidate_end": 5,
                                           "description": "Synthetic continuous shot sequence", "human_verified": True}]
        candidate["manual_review"] = {"status": "confirmed_copy", "reviewer": "user", "reviewed_at": cm.now()}
        candidate["complaint_channel_review"] = {"status": "verified", "observed_path": ["Synthetic observed rights entry", "Synthetic observed repost branch"], "infringement_entry_verified": True}
        for kind in cm.SCREENSHOT_KINDS:
            self.add_evidence(kind + ".png", kind, work_id=None if kind == "douyin_profile" else "work_1")
        self.add_evidence("original.mp4", "original_video", work_id="work_1")
        self.add_evidence("candidate.mp4", "candidate_video", candidate_id=candidate_id)
        self.save_case()
        return candidate_id

    def set_account(self, account_id, role="reporter", platform="douyin", scopes=None, own=False):
        args = argparse.Namespace(platform=platform, role=role, account_id=account_id,
                                  authorized=scopes is not None, scope=scopes, own_account=own)
        return cm.account_set(self.root, self.case, args)

    def test_account_roles_legacy_case_remains_usable_and_reports_unsplit_roles(self):
        candidate_id = self.prepare_ready()
        self.case.pop("account_roles")
        result = cm.gate(self.root, self.case, candidate_id)
        self.assertEqual(result["status"], "ready_for_submission_review")
        self.assertIn({"code": "legacy_roles_not_separated"}, result["warnings"])
        report = cm.accounts(self.case)
        self.assertIn("legacy_roles_not_separated", report["platforms"]["douyin"]["issues"])
        self.assertNotIn("account_roles", self.case)

    def test_separate_reporter_keeps_existing_author_grant_and_does_not_infer_ownership(self):
        candidate_id = self.prepare_ready()
        before_rights = copy.deepcopy(self.case["rights_review"])
        before_legacy = copy.deepcopy(self.case["authorizations"])
        self.set_account("alternate_account", scopes=["search", "report"])
        result = cm.gate(self.root, self.case, candidate_id)
        self.assertEqual(result["status"], "ready_for_submission_review")
        self.assertEqual(self.case["authorizations"], before_legacy)
        self.assertEqual(self.case["rights_review"], before_rights)
        self.assertFalse(self.case["account_roles"]["douyin"]["reporter"]["own_account"])
        self.assertNotIn("alternate_account", cm.known_own_account_ids(self.case, "douyin"))
        self.assertEqual(cm.effective_account(self.case, "douyin", "author")["account_id"], "original_account")

    def test_reporter_switch_never_inherits_old_or_legacy_report_authorization(self):
        candidate_id = self.prepare_ready()
        self.set_account("alternate_one", scopes=["search", "report"], own=True)
        before_candidates = copy.deepcopy(self.case["candidates"])
        before_submission = copy.deepcopy(self.case["submission_authorization"])
        result = self.set_account("alternate_two")
        self.assertTrue(result["account_switched"])
        reporter = cm.effective_account(self.case, "douyin", "reporter")
        self.assertEqual(reporter["status"], "pending")
        self.assertEqual(reporter["authorized_scopes"], [])
        self.assertIn("reporter_report_authorization_pending", cm.gate(self.root, self.case, candidate_id)["reasons"])
        self.assertEqual(self.case["candidates"], before_candidates)
        self.assertEqual(self.case["submission_authorization"], before_submission)
        self.assertEqual(self.case["account_roles"]["douyin"]["history"][0]["account_id"], "alternate_one")

    def test_author_identifier_is_independent_from_reporter_and_cannot_submit(self):
        candidate_id = self.prepare_ready()
        self.set_account("original_account", role="author", own=True)
        self.assertEqual(cm.effective_account(self.case, "douyin", "author")["status"], "identified")
        self.assertIn("reporter_account_missing", cm.gate(self.root, self.case, candidate_id)["reasons"])
        self.set_account("alternate_one", scopes=["search", "report"])
        self.assertEqual(cm.gate(self.root, self.case, candidate_id)["status"], "ready_for_submission_review")
        self.set_account("alternate_two", scopes=["report"])
        self.assertEqual(cm.effective_account(self.case, "douyin", "author")["account_id"], "original_account")
        self.assertEqual(cm.gate(self.root, self.case, candidate_id)["status"], "ready_for_submission_review")

    def test_search_authorization_does_not_authorize_report_and_authorization_does_not_verify_identity(self):
        candidate_id = self.prepare_ready()
        self.case["rights_review"]["identity_status"] = "pending"
        self.set_account("alternate_one", scopes=["search"], own=True)
        reasons = cm.gate(self.root, self.case, candidate_id)["reasons"]
        self.assertIn("reporter_report_authorization_pending", reasons)
        self.assertIn("identity_pending_or_blocked", reasons)
        self.assertEqual(self.case["rights_review"]["identity_status"], "pending")

    def test_all_known_own_and_author_accounts_remain_excluded_after_switch(self):
        candidate_id = self.prepare_ready()
        self.set_account("first_author", role="author")
        self.set_account("second_author", role="author")
        self.set_account("first_reporter", scopes=["report"], own=True)
        self.set_account("second_reporter", scopes=["report"], own=True)
        self.set_account("delegate_reporter", scopes=["report"])
        candidate = self.case["candidates"][0]
        expected = {"original_account", "first_author", "second_author", "first_reporter", "second_reporter"}
        self.assertEqual(cm.known_own_account_ids(self.case, "douyin"), expected)
        for account_id in expected:
            with self.subTest(account_id=account_id):
                candidate["uploader_id"] = account_id
                self.assertIn("uploader_is_authorized_own_account", cm.gate(self.root, self.case, candidate_id)["reasons"])
        candidate["uploader_id"] = "delegate_reporter"
        reasons = cm.gate(self.root, self.case, candidate_id)["reasons"]
        self.assertIn("uploader_is_reporting_account", reasons)
        self.assertNotIn("uploader_is_authorized_own_account", reasons)

    def test_own_account_exclusion_is_platform_specific(self):
        candidate_id = self.prepare_ready()
        self.set_account("uploader_456", platform="bilibili", scopes=["report"], own=True)
        self.assertEqual(cm.gate(self.root, self.case, candidate_id)["status"], "ready_for_submission_review")

    def test_same_author_reporter_warns_but_is_not_universally_forbidden(self):
        candidate_id = self.prepare_ready()
        self.set_account("original_account", scopes=["report"], own=True)
        result = cm.gate(self.root, self.case, candidate_id)
        self.assertEqual(result["status"], "ready_for_submission_review")
        self.assertIn({"code": "consider_non_author_reporter_for_search_visibility"}, result["warnings"])

    def test_channel_author_account_requirement_blocks_alternate_reporter(self):
        candidate_id = self.prepare_ready()
        self.set_account("alternate_account", scopes=["report"])
        self.case["candidates"][0]["complaint_channel_review"]["account_requirement"] = "author_account"
        self.assertIn("channel_requires_author_account_handoff", cm.gate(self.root, self.case, candidate_id)["reasons"])
        self.set_account("original_account", scopes=["report"])
        self.assertIn("channel_requires_author_account_handoff", cm.gate(self.root, self.case, candidate_id)["reasons"])

    def test_channel_identity_check_is_bound_to_current_reporter(self):
        candidate_id = self.prepare_ready()
        self.set_account("alternate_one", scopes=["report"])
        channel = self.case["candidates"][0]["complaint_channel_review"]
        channel["account_requirement"] = "verified_claimant_identity"
        self.assertIn("channel_requires_verified_reporter_identity", cm.gate(self.root, self.case, candidate_id)["reasons"])
        channel["reporter_identity_check"] = {"account_id": "alternate_one", "status": "verified", "reviewed_at": cm.now()}
        self.assertEqual(cm.gate(self.root, self.case, candidate_id)["status"], "ready_for_submission_review")
        self.set_account("alternate_two", scopes=["report"])
        self.assertIn("channel_requires_verified_reporter_identity", cm.gate(self.root, self.case, candidate_id)["reasons"])
        channel["reporter_identity_check"].update(account_id="alternate_two", reviewed_at="2026-01-01")
        self.assertIn("channel_requires_verified_reporter_identity", cm.gate(self.root, self.case, candidate_id)["reasons"])

    def test_legacy_non_douyin_login_is_not_proof_of_original_author_account(self):
        self.case["authorizations"]["bilibili"].update(status="authorized", account_id="old_bili_login", authorized_at=cm.now())
        candidate = {"platform": "bilibili", "uploader_id": "other_uploader",
                     "complaint_channel_review": {"account_requirement": "author_account"}}
        reasons, _ = cm.account_gate(self.case, candidate)
        self.assertIn("channel_requires_author_account_handoff", reasons)
        self.assertEqual(cm.effective_account(self.case, "bilibili", "author")["status"], "missing")
        self.set_account("old_bili_login", platform="bilibili", role="author")
        self.set_account("old_bili_login", platform="bilibili", scopes=["report"])
        reasons, _ = cm.account_gate(self.case, candidate)
        self.assertIn("channel_requires_author_account_handoff", reasons)

    def test_user_supplied_author_evidence_needs_no_author_login_authorization(self):
        candidate_id = self.prepare_ready()
        self.case["authorizations"] = {p: {"status": "pending", "account_id": "", "authorized_at": None} for p in cm.PLATFORMS}
        evidence = cm.manifest(self.root)
        for record in evidence["evidence"]:
            if record["kind"] in cm.SCREENSHOT_KINDS:
                record.update(provided_by="user", captured_at=None, received_at=cm.now())
        cm.atomic_json(self.root / cm.MANIFEST, evidence)
        self.set_account("original_account", role="author")
        self.set_account("alternate_account", scopes=["search", "report"])
        result = cm.gate(self.root, self.case, candidate_id)
        self.assertEqual(result["status"], "ready_for_submission_review")
        self.assertEqual(cm.accounts(self.case)["platforms"]["douyin"]["author"]["authorized_scopes"], [])
        self.case["candidates"][0]["uploader_id"] = "original_account"
        self.assertIn("uploader_is_authorized_own_account", cm.gate(self.root, self.case, candidate_id)["reasons"])

    def test_bilibili_reporter_needs_no_douyin_author_login_or_role_registration(self):
        candidate_id = self.prepare_ready()
        self.case["authorizations"]["douyin"].update(status="pending", account_id="", authorized_at=None)
        candidate = self.case["candidates"][0]
        candidate.update(platform="bilibili", video_id="BV1ab411c7mn", canonical_url=cm.canonical_url("bilibili", "BV1ab411c7mn"))
        self.set_account("bili_reporter", platform="bilibili", scopes=["report"])
        self.assertEqual(cm.gate(self.root, self.case, candidate_id)["status"], "ready_for_submission_review")

    def test_author_registration_rejects_login_authorization(self):
        for scopes in (["evidence"], ["search"], ["report"]):
            with self.subTest(scopes=scopes), self.assertRaisesRegex(cm.CaseError, "author_is_identifier_only"):
                self.set_account("author_account", role="author", scopes=scopes)

    def test_account_registration_preserves_recorded_receipts_and_claimant_file(self):
        candidate_id = self.prepare_ready()
        candidate = self.case["candidates"][0]
        candidate.update(submission_status="submitted", submitted_at=cm.now(), receipt_id="SYNTHETIC_RECEIPT")
        before_candidate = copy.deepcopy(candidate)
        before_private = (self.root / "00_private/claimant.json").read_bytes()
        self.set_account("alternate_account", scopes=["report"])
        self.assertEqual(self.case["candidates"][0], before_candidate)
        self.assertEqual((self.root / "00_private/claimant.json").read_bytes(), before_private)
        self.assertIn("submission_already_sent_unknown_or_not_cleared", cm.gate(self.root, self.case, candidate_id)["reasons"])

    def test_account_set_validates_scope_role_and_never_echoes_identifier(self):
        for role, scopes in (("author", ["report"]), ("reporter", ["evidence"]), ("reporter", [])):
            with self.subTest(role=role, scopes=scopes), self.assertRaises(cm.CaseError):
                self.set_account("test_account", role=role, scopes=scopes)
        with self.assertRaises(cm.CaseError):
            self.set_account("https://example.invalid/user?token=secret", scopes=["search"])
        result = self.set_account("LOCAL_PRIVATE_ACCOUNT", scopes=["search", "report"])
        self.assertNotIn("LOCAL_PRIVATE_ACCOUNT", json.dumps(result))
        self.assertNotIn("LOCAL_PRIVATE_ACCOUNT", json.dumps(cm.accounts(self.case)))

    def test_accounts_command_is_read_only_and_account_set_cli_records_existing_grant(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = cm.main(["account-set", "--root", str(self.root), "--platform", "douyin", "--role", "reporter",
                            "--account-id", "test_alt", "--authorized", "--scope", "search", "--scope", "report", "--own-account"])
        self.assertEqual(code, 0)
        self.assertNotIn("test_alt", output.getvalue())
        before = (self.root / "case.json").read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cm.main(["accounts", "--root", str(self.root)]), 0)
        self.assertEqual((self.root / "case.json").read_bytes(), before)

    def test_malformed_account_roles_fail_closed(self):
        candidate_id = self.prepare_ready()
        for registry in ([], {"not_a_platform": {}}, {"douyin": {"history": [None]}},
                         {"douyin": {"reporter": {"account_id": "test", "status": "authorized"}}}):
            with self.subTest(registry=registry), self.assertRaisesRegex(cm.CaseError, "invalid_account_roles"):
                self.case["account_roles"] = registry
                cm.gate(self.root, self.case, candidate_id)

    def prepare_general_report(self):
        candidate_id = self.prepare_ready()
        candidate = self.case["candidates"][0]
        candidate.update(platform="kuaishou", canonical_url=cm.canonical_url("kuaishou", candidate["video_id"]),
                         complaint_route="general_report")
        candidate["complaint_channel_review"].update(infringement_entry_verified=False, observed_path=["举报", "实际适用的普通举报分类"])
        self.set_account("ks_reporter", platform="kuaishou", scopes=["report"])
        return candidate_id

    def test_kuaishou_general_report_requires_explicit_route_and_real_observed_channel(self):
        candidate_id = self.prepare_general_report()
        candidate = self.case["candidates"][0]
        result = cm.gate(self.root, self.case, candidate_id)
        self.assertEqual(result["status"], "ready_for_submission_review")
        self.assertEqual(result["complaint_route"], "general_report")
        candidate.pop("complaint_route")
        self.assertIn("complaint_channel_not_verified", cm.gate(self.root, self.case, candidate_id)["reasons"])
        self.assertEqual(cm.gate(self.root, self.case, candidate_id)["complaint_route"], "copyright")
        candidate["complaint_route"] = "general_report"
        for value in ([], [""], None):
            candidate["complaint_channel_review"]["observed_path"] = value
            self.assertIn("complaint_channel_not_verified", cm.gate(self.root, self.case, candidate_id)["reasons"])

    def test_general_report_does_not_require_private_identity_or_author_account_screenshots(self):
        candidate_id = self.prepare_general_report()
        self.case["rights_review"]["identity_status"] = "pending"
        data = cm.manifest(self.root)
        data["evidence"] = [e for e in data["evidence"] if e["kind"] not in cm.SCREENSHOT_KINDS]
        cm.atomic_json(self.root / cm.MANIFEST, data)
        (self.root / "00_private/claimant.json").unlink()
        with mock.patch.object(cm, "intake_status", side_effect=AssertionError("ordinary report must not read private intake")):
            self.assertEqual(cm.gate(self.root, self.case, candidate_id)["status"], "ready_for_submission_review")

    def test_general_report_retains_copy_ownership_whitelist_target_and_reporter_requirements(self):
        candidate_id = self.prepare_general_report()
        original = copy.deepcopy(self.case)
        variants = [
            (lambda: self.case["candidates"][0]["manual_review"].update(status="pending"), "human_review_pending"),
            (lambda: self.case["candidates"][0].update(overlap_segments=[]), "specific_continuous_overlap_not_human_verified"),
            (lambda: self.case["works"][0].update(user_confirmed=False), "original_title_and_link_not_user_confirmed"),
            (lambda: self.case["rights_review"].update(ownership_status="pending"), "ownership_pending_or_blocked"),
            (lambda: self.case["whitelist"].update(user_confirmed=False), "whitelist_not_explicitly_confirmed"),
            (lambda: self.case["submission_authorization"].update(candidate_ids=[]), "target_not_explicitly_authorized_for_submission"),
            (lambda: self.set_account("new_ks_reporter", platform="kuaishou"), "reporter_report_authorization_pending"),
        ]
        for change, reason in variants:
            with self.subTest(reason=reason):
                self.case = copy.deepcopy(original)
                change()
                self.assertIn(reason, cm.gate(self.root, self.case, candidate_id)["reasons"])

    def test_general_report_cannot_weaken_douyin_or_bilibili_copyright_gate(self):
        candidate_id = self.prepare_ready()
        candidate = self.case["candidates"][0]
        candidate["complaint_route"] = "general_report"
        candidate["complaint_channel_review"]["infringement_entry_verified"] = False
        for platform in ("douyin", "bilibili"):
            candidate.update(platform=platform, canonical_url=cm.canonical_url(platform, candidate["video_id"]))
            reasons = cm.gate(self.root, self.case, candidate_id)["reasons"]
            self.assertIn("unsupported_complaint_route_for_platform", reasons)
            self.assertIn("complaint_channel_not_verified", reasons)

    def test_general_report_does_not_override_original_author_channel_requirement(self):
        candidate_id = self.prepare_general_report()
        self.case["candidates"][0]["complaint_channel_review"]["account_requirement"] = "author_account"
        self.assertIn("channel_requires_author_account_handoff", cm.gate(self.root, self.case, candidate_id)["reasons"])

    def test_general_report_skips_unused_corrupt_author_screenshot_but_not_original_video(self):
        candidate_id = self.prepare_general_report()
        (self.root / "02_originals/douyin_profile.png").write_bytes(b"changed")
        self.assertEqual(cm.gate(self.root, self.case, candidate_id)["status"], "ready_for_submission_review")
        (self.root / "02_originals/original.mp4").write_bytes(b"changed")
        self.assertIn("evidence_integrity_failed", cm.gate(self.root, self.case, candidate_id)["reasons"])

    def test_route_statistics_separate_general_report_and_copyright_submission_and_outcomes(self):
        self.case["candidates"] = [
            {"platform": "douyin", "submission_status": "submitted"},
            {"platform": "bilibili", "complaint_route": "copyright", "submission_status": "submitted",
             "review_result": {"status": "approved", "verified": True, "evidence_ref": "07_receipts/test-a.png"}},
            {"platform": "kuaishou", "complaint_route": "general_report", "submission_status": "submitted",
             "review_result": {"status": "approved", "verified": True, "evidence_ref": "07_receipts/test-b.png"},
             "removal_result": {"status": "removed", "verified": False, "evidence_ref": "07_receipts/not-verified.png"}},
            {"platform": "bilibili", "complaint_route": "general_report", "submission_status": "submitted"},
            {"platform": "kuaishou", "complaint_route": "general_report", "submission_status": "needs_supplement",
             "submitted_at": cm.now(), "receipt_id": "SYNTHETIC_RECEIPT"},
        ]
        before = copy.deepcopy(self.case)
        result = cm.report(self.case)
        self.assertEqual(result["totals"]["submitted"], 4)
        self.assertEqual(result["by_route"]["copyright"]["submitted"], 2)
        self.assertEqual(result["by_route"]["general_report"]["submitted"], 1)
        self.assertEqual(result["by_route"]["unknown"]["submitted"], 1)
        self.assertEqual(result["by_route"]["general_report"]["historical_submitted"], 2)
        self.assertEqual(result["by_route"]["general_report"]["review_approved"], 1)
        self.assertEqual(result["by_route"]["copyright"]["review_approved"], 1)
        self.assertEqual(result["by_route"]["general_report"]["taken_down"], 0)
        self.assertEqual(self.case, before)

    def test_candidate_route_defaults_and_cli_rejects_general_report_for_other_platforms(self):
        self.add_candidate()
        self.assertEqual(self.case["candidates"][0]["complaint_route"], "copyright")
        args = cm.parser().parse_args(["candidate-add", "--root", str(self.root), "--platform", "kuaishou",
                                      "--video-id", "new_ks", "--title", "Synthetic", "--original-id", "work_1",
                                      "--complaint-route", "general_report"])
        cm.candidate_add(self.root, self.case, args)
        self.assertEqual(self.case["candidates"][1]["complaint_route"], "general_report")
        args.platform = "bilibili"
        with self.assertRaisesRegex(cm.CaseError, "unsupported_complaint_route_for_platform"):
            cm.candidate_add(self.root, self.case, args)

    def add_browser_screenshot(self, name, kind, candidate_id, source_url=None):
        path = self.root / "04_candidates" / name
        color = bytes((sum(name.encode()) % 256, len(name) % 256, 90))
        def chunk(label, value):
            return struct.pack(">I", len(value)) + label + value + struct.pack(">I", zlib.crc32(label + value))
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
                         + chunk(b"IDAT", zlib.compress((b"\0" + color * 2) * 2)) + chunk(b"IEND", b""))
        candidate = next(c for c in self.case["candidates"] if c["candidate_id"] == candidate_id)
        args = argparse.Namespace(file=path.relative_to(self.root).as_posix(), kind=kind,
                                  source_url=source_url or candidate["canonical_url"], captured_at=None,
                                  work_id=candidate["original_work_id"], candidate_id=candidate_id)
        return cm.evidence_add(self.root, self.case, args)["evidence_id"]

    def prepare_browser_ready(self):
        candidate_id = self.prepare_ready()
        data = cm.manifest(self.root)
        data["evidence"] = [e for e in data["evidence"] if e["kind"] != "candidate_video"]
        cm.atomic_json(self.root / cm.MANIFEST, data)
        candidate = self.case["candidates"][0]
        candidate["evidence_mode"] = "browser_visual"
        candidate["browser_visual_review"] = {
            "candidate_url": candidate["canonical_url"], "uploader_id": candidate["uploader_id"],
            "page_evidence_id": self.add_browser_screenshot("page.png", "candidate_page", candidate_id),
            "comparison_evidence_id": self.add_browser_screenshot("comparison.png", "comparison", candidate_id),
            "download_unavailable_reason": "browser_blob_not_downloadable",
            "time_basis": "Observed official player media time in seconds; seeks verified on player",
            "observation_method": "ordered_timestamped_screenshots",
            "same_source_expression_confirmed": True,
            "coverage_limitations": "Only listed screenshot points were observed; no claim about intervening frames",
            "continuous_coverage_claimed": False, "continuous_playback_reviewed": False,
        }
        candidate["manual_review"]["reviewer"] = "agent"
        candidate["overlap_segments"] = []
        for index in range(2):
            start = 20 + index * 20
            candidate["overlap_segments"].append({
                "original_start": 10 + index * 20, "original_end": 14 + index * 20,
                "candidate_start": start, "candidate_end": start + 4,
                "description": "Synthetic prop movement and camera cut correspondence",
                "human_verified": True, "shot_id": f"shot_{index}",
                "screenshot_observation": "Specific object position and composition match at the two listed screenshot points",
                "candidate_frames": [
                    {"time": start, "original_time": 10 + index * 20, "evidence_id": self.add_browser_screenshot(f"segment-{index}-start.png", "candidate_frame", candidate_id)},
                    {"time": start + 4, "original_time": 14 + index * 20, "evidence_id": self.add_browser_screenshot(f"segment-{index}-end.png", "candidate_frame", candidate_id)},
                ],
            })
        self.save_case()
        return candidate_id

    def test_browser_visual_passes_without_candidate_video_and_reports_actual_mode(self):
        candidate_id = self.prepare_browser_ready()
        self.assertFalse(any(e["kind"] == "candidate_video" for e in cm.manifest(self.root)["evidence"]))
        result = cm.gate(self.root, self.case, candidate_id)
        self.assertEqual(result["status"], "ready_for_submission_review")
        self.assertEqual(result["evidence_mode"], "browser_visual")
        self.assertFalse(result["network_submission_performed"])
        self.assertEqual(result["reasons"], [])

    def test_missing_or_unknown_mode_does_not_silently_choose_browser_visual(self):
        candidate_id = self.prepare_browser_ready()
        candidate = self.case["candidates"][0]
        candidate.pop("evidence_mode")
        self.assertIn("candidate_video_evidence_missing", cm.gate(self.root, self.case, candidate_id)["reasons"])
        candidate["evidence_mode"] = "title_similarity"
        self.assertIn("unsupported_evidence_mode", cm.gate(self.root, self.case, candidate_id)["reasons"])

    def test_browser_screenshot_registration_requires_candidate_work_official_url_and_image(self):
        candidate_id = self.prepare_ready()
        with self.assertRaisesRegex(cm.CaseError, "official_url"):
            self.add_browser_screenshot("wrong-source.png", "candidate_frame", candidate_id, "https://example.invalid/video/456")
        args = argparse.Namespace(file="04_candidates/wrong-source.png", kind="candidate_frame",
                                  source_url=self.case["candidates"][0]["canonical_url"], captured_at=None,
                                  work_id="work_1", candidate_id=None)
        with self.assertRaisesRegex(cm.CaseError, "requires_candidate_id"):
            cm.evidence_add(self.root, self.case, args)
        args.candidate_id = candidate_id
        self.case["works"].append({"work_id": "work_2"})
        args.work_id = "work_2"
        with self.assertRaisesRegex(cm.CaseError, "match_candidate_work"):
            cm.evidence_add(self.root, self.case, args)
        args.work_id = "work_1"
        (self.root / args.file).write_text("not an image", encoding="utf-8")
        with self.assertRaisesRegex(cm.CaseError, "requires_png_jpeg_or_webp"):
            cm.evidence_add(self.root, self.case, args)

    def test_browser_context_requires_verified_source_identity_timing_and_same_source_review(self):
        candidate_id = self.prepare_browser_ready()
        candidate = self.case["candidates"][0]
        original = copy.deepcopy(candidate["browser_visual_review"])
        variants = [("candidate_url", "https://www.douyin.com/video/other", "browser_visual_source_not_verified"),
                    ("uploader_id", "other_uploader", "browser_visual_source_not_verified"),
                    ("page_evidence_id", "ev_missing", "browser_visual_page_evidence_missing_or_invalid"),
                    ("comparison_evidence_id", "ev_missing", "browser_visual_comparison_evidence_missing_or_invalid"),
                    ("time_basis", "", "browser_visual_context_incomplete"),
                    ("download_unavailable_reason", "", "browser_visual_context_incomplete"),
                    ("coverage_limitations", "", "browser_visual_context_incomplete"),
                    ("observation_method", "similar_title", "browser_visual_context_incomplete"),
                    ("same_source_expression_confirmed", False, "browser_visual_context_incomplete")]
        for key, value, expected in variants:
            with self.subTest(key=key):
                candidate["browser_visual_review"] = {**original, key: value}
                self.assertIn(expected, cm.gate(self.root, self.case, candidate_id)["reasons"])
        candidate["browser_visual_review"] = original
        candidate["uploader_id_verified"] = False
        self.assertIn("browser_visual_source_not_verified", cm.gate(self.root, self.case, candidate_id)["reasons"])

    def test_browser_frames_cannot_reference_other_claim_kind_or_source(self):
        candidate_id = self.prepare_browser_ready()
        data = cm.manifest(self.root)
        target = next(e for e in data["evidence"] if e["kind"] == "candidate_frame")
        original = copy.deepcopy(target)
        for key, value in (("candidate_id", "cand_other"), ("work_id", "work_other"), ("kind", "candidate_page"),
                           ("source_url", "https://www.douyin.com/video/other"), ("evidence_id", "ev_missing")):
            with self.subTest(key=key):
                target.clear(); target.update(original); target[key] = value
                cm.atomic_json(self.root / cm.MANIFEST, data)
                self.assertIn("browser_visual_frame_evidence_invalid", cm.gate(self.root, self.case, candidate_id)["reasons"])

    def test_browser_screenshot_changed_or_missing_cannot_pass(self):
        candidate_id = self.prepare_browser_ready()
        path = self.root / "04_candidates/segment-0-start.png"
        path.write_bytes(b"changed")
        result = cm.gate(self.root, self.case, candidate_id)
        self.assertIn("evidence_integrity_failed", result["reasons"])
        self.assertIn("browser_visual_frame_evidence_invalid", result["reasons"])
        path.unlink()
        self.assertEqual(cm.gate(self.root, self.case, candidate_id)["status"], "blocked")

    def test_browser_requires_two_actual_segments_with_observation_not_title_only(self):
        candidate_id = self.prepare_browser_ready()
        candidate = self.case["candidates"][0]
        original = copy.deepcopy(candidate["overlap_segments"])
        for segments in ([], None, original[:1]):
            with self.subTest(segments=segments):
                candidate["overlap_segments"] = segments
                self.assertIn("browser_visual_two_segments_required", cm.gate(self.root, self.case, candidate_id)["reasons"])
        for key, value in (("description", ""), ("description", None), ("screenshot_observation", ""), ("shot_id", ""), ("human_verified", False), ("candidate_end", 20)):
            with self.subTest(key=key, value=value):
                candidate["overlap_segments"] = copy.deepcopy(original)
                candidate["overlap_segments"][0][key] = value
                self.assertIn("browser_visual_segment_not_reviewed", cm.gate(self.root, self.case, candidate_id)["reasons"])

    def test_browser_frames_require_two_distinct_valid_media_times_in_each_interval(self):
        candidate_id = self.prepare_browser_ready()
        candidate = self.case["candidates"][0]
        original = copy.deepcopy(candidate["overlap_segments"])
        for value in (-1, 25, None, True, float("nan"), float("inf")):
            with self.subTest(value=value):
                candidate["overlap_segments"] = copy.deepcopy(original)
                candidate["overlap_segments"][0]["candidate_frames"][0]["time"] = value
                self.assertIn("browser_visual_frame_evidence_invalid", cm.gate(self.root, self.case, candidate_id)["reasons"])
        candidate["overlap_segments"] = copy.deepcopy(original)
        candidate["overlap_segments"][0]["candidate_frames"].pop()
        self.assertIn("browser_visual_two_frames_per_segment_required", cm.gate(self.root, self.case, candidate_id)["reasons"])
        candidate["overlap_segments"] = copy.deepcopy(original)
        candidate["overlap_segments"][0]["candidate_frames"][1]["time"] = 20
        self.assertIn("browser_visual_reused_frame", cm.gate(self.root, self.case, candidate_id)["reasons"])

    def test_browser_duplicate_ids_hashes_and_intervals_cannot_inflate_evidence(self):
        candidate_id = self.prepare_browser_ready()
        candidate = self.case["candidates"][0]
        original = copy.deepcopy(candidate["overlap_segments"])
        first = candidate["overlap_segments"][0]["candidate_frames"][0]
        candidate["overlap_segments"][1]["candidate_frames"][0]["evidence_id"] = first["evidence_id"]
        self.assertIn("browser_visual_reused_frame", cm.gate(self.root, self.case, candidate_id)["reasons"])
        candidate["overlap_segments"] = [copy.deepcopy(original[0]), copy.deepcopy(original[0])]
        self.assertIn("browser_visual_duplicate_segment", cm.gate(self.root, self.case, candidate_id)["reasons"])
        candidate["overlap_segments"] = original
        data = cm.manifest(self.root)
        images = [e for e in data["evidence"] if e["kind"] == "candidate_frame"]
        (self.root / images[1]["path"]).write_bytes((self.root / images[0]["path"]).read_bytes())
        images[1]["sha256"] = images[0]["sha256"]
        cm.atomic_json(self.root / cm.MANIFEST, data)
        self.assertIn("browser_visual_reused_frame", cm.gate(self.root, self.case, candidate_id)["reasons"])

    def test_browser_mode_still_requires_original_channel_authorization_and_manual_review(self):
        candidate_id = self.prepare_browser_ready()
        original = copy.deepcopy(self.case)
        variants = [(lambda: self.case["submission_authorization"].update(candidate_ids=[]), "target_not_explicitly_authorized_for_submission"),
                    (lambda: self.case["candidates"][0]["manual_review"].update(reviewer="algorithm"), "human_review_pending"),
                    (lambda: self.case["candidates"][0]["complaint_channel_review"].update(infringement_entry_verified=False), "complaint_channel_not_verified"),
                    (lambda: self.case["whitelist"].update(user_confirmed=False), "whitelist_not_explicitly_confirmed"),
                    (lambda: self.case["candidates"][0].update(submission_status="unknown"), "submission_already_sent_unknown_or_not_cleared")]
        for change, reason in variants:
            with self.subTest(reason=reason):
                self.case = copy.deepcopy(original)
                change()
                self.assertIn(reason, cm.gate(self.root, self.case, candidate_id)["reasons"])
        self.case = original
        data = cm.manifest(self.root)
        data["evidence"] = [e for e in data["evidence"] if e["kind"] != "original_video"]
        cm.atomic_json(self.root / cm.MANIFEST, data)
        self.assertIn("original_video_evidence_missing", cm.gate(self.root, self.case, candidate_id)["reasons"])

    def test_browser_gate_rejects_manual_nonimage_placeholder_even_with_matching_hash(self):
        candidate_id = self.prepare_browser_ready()
        data = cm.manifest(self.root)
        entry = next(e for e in data["evidence"] if e["kind"] == "candidate_frame")
        path = self.root / entry["path"]
        path.write_text("synthetic placeholder", encoding="utf-8")
        entry["sha256"] = cm.sha256(path)
        cm.atomic_json(self.root / cm.MANIFEST, data)
        self.assertIn("browser_visual_frame_evidence_invalid", cm.gate(self.root, self.case, candidate_id)["reasons"])

    def test_browser_reordered_cut_segments_are_valid_when_each_interval_is_audited(self):
        candidate_id = self.prepare_browser_ready()
        segments = self.case["candidates"][0]["overlap_segments"]
        segments[0]["original_start"], segments[0]["original_end"] = 30, 34
        segments[1]["original_start"], segments[1]["original_end"] = 10, 14
        for index, segment in enumerate(segments):
            for frame_index, frame in enumerate(segment["candidate_frames"]):
                frame["original_time"] = (30 if index == 0 else 10) + frame_index * 4
        self.assertEqual(cm.gate(self.root, self.case, candidate_id)["status"], "ready_for_submission_review")

    def test_browser_screenshots_never_imply_continuous_coverage(self):
        candidate_id = self.prepare_browser_ready()
        context = self.case["candidates"][0]["browser_visual_review"]
        self.assertFalse(context["continuous_playback_reviewed"])
        self.assertEqual(cm.gate(self.root, self.case, candidate_id)["status"], "ready_for_submission_review")
        for reviewed in (False, True):
            context.update(continuous_coverage_claimed=True, continuous_playback_reviewed=reviewed)
            self.assertIn("browser_visual_cannot_claim_continuous_coverage", cm.gate(self.root, self.case, candidate_id)["reasons"])

    def test_browser_requires_distinct_shots_and_original_frame_time_mapping(self):
        candidate_id = self.prepare_browser_ready()
        segments = self.case["candidates"][0]["overlap_segments"]
        segments[1]["shot_id"] = segments[0]["shot_id"]
        self.assertIn("browser_visual_two_distinct_shots_required", cm.gate(self.root, self.case, candidate_id)["reasons"])
        segments[1]["shot_id"] = "shot_1"
        for value in (None, True, -1, 15, float("nan"), float("inf")):
            with self.subTest(value=value):
                segments[0]["candidate_frames"][0]["original_time"] = value
                self.assertIn("browser_visual_frame_evidence_invalid", cm.gate(self.root, self.case, candidate_id)["reasons"])

    def test_browser_same_candidate_interval_cannot_count_as_two_original_mappings(self):
        candidate_id = self.prepare_browser_ready()
        segment = self.case["candidates"][0]["overlap_segments"][1]
        segment.update(candidate_start=20, candidate_end=24)
        segment["candidate_frames"][0]["time"] = 21
        segment["candidate_frames"][1]["time"] = 23
        self.assertIn("browser_visual_candidate_intervals_overlap", cm.gate(self.root, self.case, candidate_id)["reasons"])

    def test_browser_frame_can_also_supply_verified_page_context(self):
        candidate_id = self.prepare_browser_ready()
        candidate = self.case["candidates"][0]
        frame_id = candidate["overlap_segments"][1]["candidate_frames"][1]["evidence_id"]
        candidate["browser_visual_review"].update(page_evidence_id=frame_id, page_context_verified=True)
        self.assertEqual(cm.gate(self.root, self.case, candidate_id)["status"], "ready_for_submission_review")

    def test_browser_shared_frame_requires_explicit_page_context_review(self):
        candidate_id = self.prepare_browser_ready()
        candidate = self.case["candidates"][0]
        context = candidate["browser_visual_review"]
        context["page_evidence_id"] = candidate["overlap_segments"][0]["candidate_frames"][0]["evidence_id"]
        for value in (None, False, "true"):
            with self.subTest(value=value):
                context["page_context_verified"] = value
                self.assertIn("browser_visual_shared_page_context_not_verified", cm.gate(self.root, self.case, candidate_id)["reasons"])

    def test_browser_shared_page_does_not_replace_a_fourth_distinct_frame(self):
        candidate_id = self.prepare_browser_ready()
        candidate = self.case["candidates"][0]
        frames = candidate["overlap_segments"][1]["candidate_frames"]
        candidate["browser_visual_review"].update(page_evidence_id=frames[1]["evidence_id"], page_context_verified=True)
        removed = frames.pop()
        self.assertIn("browser_visual_two_frames_per_segment_required", cm.gate(self.root, self.case, candidate_id)["reasons"])
        frames.append({**removed, "evidence_id": frames[0]["evidence_id"]})
        self.assertIn("browser_visual_reused_frame", cm.gate(self.root, self.case, candidate_id)["reasons"])

    def test_browser_page_copy_cannot_replace_the_comparison_image(self):
        candidate_id = self.prepare_browser_ready()
        data = cm.manifest(self.root)
        page = next(e for e in data["evidence"] if e["kind"] == "candidate_page")
        comparison = next(e for e in data["evidence"] if e["kind"] == "comparison")
        (self.root / comparison["path"]).write_bytes((self.root / page["path"]).read_bytes())
        comparison["sha256"] = page["sha256"]
        cm.atomic_json(self.root / cm.MANIFEST, data)
        self.assertIn("browser_visual_comparison_cannot_reuse_page", cm.gate(self.root, self.case, candidate_id)["reasons"])

    def test_init_creates_private_template_without_public_pii(self):
        for directory in cm.DIRECTORIES:
            self.assertTrue((self.root / directory).is_dir())
        public = (self.root / "case.json").read_text(encoding="utf-8")
        for field in cm.PRIVATE_FIELDS:
            self.assertNotIn('"' + field + '"', public)
        self.assertEqual(set(cm.read_json(self.root / "00_private/claimant.json")), set(cm.PRIVATE_FIELDS))
        self.assertFalse(self.case["whitelist"]["user_confirmed"])

    def test_init_rejects_existing_nonempty_and_skill_and_repo(self):
        with self.assertRaises(cm.CaseError):
            cm.init_case(str(self.root))
        with self.assertRaises(cm.CaseError):
            cm.init_case(str(Path(cm.__file__).resolve().parents[1] / "must-not-create"))
        repository = Path(self.temporary.name) / "repo"
        (repository / ".git").mkdir(parents=True)
        with self.assertRaises(cm.CaseError):
            cm.init_case(str(repository / "case"))

    def test_empty_intake_and_status_report_only_missing(self):
        result = cm.intake_status(self.root)
        self.assertFalse(result["complete"])
        self.assertEqual(set(result["fields"].values()), {"missing"})
        state = cm.status(self.root, self.case)
        self.assertEqual(set(state["screenshots"].values()), {"missing"})
        candidate_id = self.add_candidate()["candidate_id"]
        gate = cm.gate(self.root, self.case, candidate_id)
        self.assertEqual(gate["status"], "blocked")
        self.assertIn("private_intake_incomplete", gate["reasons"])
        self.assertIn("identity_pending_or_blocked", gate["reasons"])

    def test_complete_private_intake_never_echoes_private_values(self):
        self.fill_private()
        self.assertTrue(cm.intake_status(self.root)["complete"])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = cm.main(["status", "--root", str(self.root)])
        self.assertEqual(exit_code, 0)
        for secret in ("TEST_PRIVATE_NAME", "private@example.invalid", "TEST_PRIVATE_PHONE", "intake/id_front"):
            self.assertNotIn(secret, output.getvalue())
        (self.root / "00_private/intake/id_back.png").unlink()
        self.assertEqual(cm.intake_status(self.root)["fields"]["id_back"], "missing")

    def test_private_file_outside_case_or_private_dir_is_missing(self):
        self.fill_private()
        claimant = cm.read_json(self.root / "00_private/claimant.json")
        claimant["id_front"] = "../outside.png"
        claimant["id_back"] = "02_originals/not-private.png"
        (self.root / claimant["id_back"]).write_bytes(b"synthetic")
        cm.atomic_json(self.root / "00_private/claimant.json", claimant)
        self.assertFalse(cm.intake_status(self.root)["complete"])

    def test_evidence_hash_change_detected_and_not_overwritten(self):
        registered = self.add_evidence("profile.png", "douyin_profile")
        self.assertTrue(cm.verify_evidence(self.root)["ok"])
        (self.root / "02_originals/profile.png").write_bytes(b"changed")
        result = cm.verify_evidence(self.root)
        self.assertFalse(result["ok"])
        self.assertEqual(result["evidence"][0]["status"], "changed")
        self.assertEqual(cm.status(self.root, self.case)["screenshots"]["douyin_profile"], "missing")
        args = argparse.Namespace(file="02_originals/profile.png", kind="douyin_profile", source_url="https://www.douyin.com/video/123", captured_at=None, work_id=None, candidate_id=None)
        with self.assertRaises(cm.CaseError):
            cm.evidence_add(self.root, self.case, args)
        self.assertEqual(cm.manifest(self.root)["evidence"][0]["evidence_id"], registered["evidence_id"])

    def test_source_query_secrets_not_written_to_public_manifest(self):
        self.add_evidence("profile.png", "douyin_profile")
        saved = (self.root / cm.MANIFEST).read_text(encoding="utf-8")
        self.assertNotIn("do-not-save", saved)
        self.assertNotIn("token=", saved)

    def test_user_provided_material_keeps_unknown_capture_time_null(self):
        self.case["works"] = [{"work_id": "work_1"}]
        path = self.root / "02_originals/user-content-manager.png"
        path.write_bytes(b"synthetic user-provided screenshot")
        args = argparse.Namespace(file=path.relative_to(self.root).as_posix(), kind="douyin_creator_center", source_url="https://creator.douyin.com/", captured_at=None, work_id="work_1", candidate_id=None, user_provided=True)
        cm.evidence_add(self.root, self.case, args)
        entry = cm.manifest(self.root)["evidence"][0]
        self.assertIsNone(entry["captured_at"])
        self.assertEqual(entry["provenance"], "user_provided")
        self.assertIsNotNone(cm.datetime.fromisoformat(entry["received_at"]).tzinfo)

    def test_user_provided_known_capture_time_is_preserved(self):
        path = self.root / "02_originals/user-profile.png"
        path.write_bytes(b"synthetic user-provided screenshot")
        known_time = "2026-01-02T12:34:56+09:00"
        args = argparse.Namespace(file=path.relative_to(self.root).as_posix(), kind="douyin_profile", source_url="https://www.douyin.com/", captured_at=known_time, work_id=None, candidate_id=None, user_provided=True)
        cm.evidence_add(self.root, self.case, args)
        entry = cm.manifest(self.root)["evidence"][0]
        self.assertEqual(entry["captured_at"], known_time)
        self.assertEqual(entry["provenance"], "user_provided")
        self.assertIsNotNone(entry["received_at"])

    def test_agent_captured_material_defaults_to_current_capture_time(self):
        self.add_evidence("profile.png", "douyin_profile")
        entry = cm.manifest(self.root)["evidence"][0]
        self.assertEqual(entry["provenance"], "agent_captured")
        self.assertIsNotNone(cm.datetime.fromisoformat(entry["captured_at"]).tzinfo)
        self.assertIsNone(entry["received_at"])
        parsed = cm.parser().parse_args(["evidence-add", "--root", str(self.root), "--file", "example.png", "--kind", "douyin_profile", "--source-url", "https://www.douyin.com/", "--user-provided"])
        self.assertTrue(parsed.user_provided)

    def test_rejects_path_escape_absolute_and_private_evidence(self):
        for value in ("../outside.png", "..\\outside.png", "C:\\private.png", "/tmp/private.png"):
            with self.subTest(value=value), self.assertRaises(cm.CaseError):
                cm.member(self.root, value)
        self.fill_private()
        args = argparse.Namespace(file="00_private/intake/id_front.png", kind="other", source_url="https://www.douyin.com/", captured_at=None, work_id=None, candidate_id=None)
        with self.assertRaises(cm.CaseError):
            cm.evidence_add(self.root, self.case, args)

    def test_duplicate_candidate_uses_platform_video_id(self):
        first = self.add_candidate()
        second = self.add_candidate()
        self.assertEqual(first["candidate_id"], second["candidate_id"])
        self.assertEqual(second["status"], "already_registered")
        self.assertEqual(len(self.case["candidates"]), 1)
        self.assertEqual(self.case["candidates"][0]["canonical_url"], "https://www.douyin.com/video/456")
        self.assertEqual(self.case["candidates"][0]["complaint_channel_review"], {"status": "pending", "observed_path": [], "infringement_entry_verified": False})

    def test_xiaohongshu_auth_and_note_id_cli_alias(self):
        self.assertEqual(self.case["authorizations"]["xiaohongshu"], {"status": "pending", "account_id": "", "authorized_at": None})
        self.assertEqual(cm.status(self.root, self.case)["authorizations"]["xiaohongshu"], "pending")
        del self.case["authorizations"]["xiaohongshu"]
        self.assertEqual(cm.status(self.root, self.case)["authorizations"]["xiaohongshu"], "pending")
        args = cm.parser().parse_args(["candidate-add", "--root", str(self.root), "--platform", "xiaohongshu", "--note-id", "abcdef0123456789abcdef01", "--title", "Synthetic note", "--original-id", "work_1"])
        self.assertEqual(args.video_id, "abcdef0123456789abcdef01")

    def test_xiaohongshu_canonical_note_id_and_duplicate_detection(self):
        self.add_candidate()
        note_id = "abcdef0123456789abcdef01"
        args = argparse.Namespace(platform="xiaohongshu", video_id=note_id.upper(), uploader_id="0123456789abcdef01234567", title="Synthetic note", original_id="work_1")
        first = cm.candidate_add(self.root, self.case, args)
        args.video_id = note_id
        second = cm.candidate_add(self.root, self.case, args)
        self.assertEqual(first["candidate_id"], second["candidate_id"])
        self.assertEqual(second["status"], "already_registered")
        note = self.case["candidates"][-1]
        self.assertEqual(note["video_id"], note_id)
        self.assertEqual(note["canonical_url"], "https://www.xiaohongshu.com/explore/" + note_id)
        args.platform = "kuaishou"
        separate = cm.candidate_add(self.root, self.case, args)
        self.assertNotEqual(first["candidate_id"], separate["candidate_id"])

    def test_xiaohongshu_access_url_rejected_as_id_without_echoing_token(self):
        secret = "SYNTHETIC_XSEC_NOT_FOR_LOGS"
        url = "https://www.xiaohongshu.com/explore/abcdef0123456789abcdef01?xsec_token=" + secret
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = cm.main(["candidate-add", "--root", str(self.root), "--platform", "xiaohongshu", "--note-id", url, "--title", "Synthetic note", "--original-id", "work_1"])
        self.assertEqual(code, 1)
        self.assertIn("invalid_xiaohongshu_note_id", errors.getvalue())
        self.assertNotIn(secret, output.getvalue() + errors.getvalue())
        self.assertNotIn(secret, (self.root / "case.json").read_text(encoding="utf-8"))
        self.assertEqual(self.case["candidates"], [])

    def test_xiaohongshu_source_token_and_source_query_never_persist(self):
        note_id = "abcdef0123456789abcdef01"
        source = "https://www.xiaohongshu.com/explore/" + note_id + "?xsec_token=SYNTHETIC_SECRET&xsec_source=pc_search#SYNTHETIC_FRAGMENT"
        path = self.root / "03_search/synthetic-xhs.png"
        path.write_bytes(b"synthetic screenshot")
        self.assertEqual(cm.public_url(source), "https://www.xiaohongshu.com/explore/" + note_id)
        args = argparse.Namespace(file=path.relative_to(self.root).as_posix(), kind="search", source_url=source, captured_at=None, work_id=None, candidate_id=None)
        result = cm.evidence_add(self.root, self.case, args)
        manifest_text = (self.root / cm.MANIFEST).read_text(encoding="utf-8")
        for forbidden in ("xsec_token", "xsec_source", "SYNTHETIC_SECRET", "SYNTHETIC_FRAGMENT"):
            self.assertNotIn(forbidden, manifest_text + json.dumps(result))

    def test_xiaohongshu_gate_requires_own_auth_and_honors_whitelist(self):
        candidate_id = self.prepare_ready()
        # Historical compatibility only: this platform is not active by default.
        self.case["platform_scope"]["active_platforms"].append("xiaohongshu")
        note_id = "abcdef0123456789abcdef01"
        candidate = self.case["candidates"][0]
        candidate.update(platform="xiaohongshu", video_id=note_id, canonical_url=cm.canonical_url("xiaohongshu", note_id))
        result = cm.gate(self.root, self.case, candidate_id)
        self.assertIn("platform_authorization_pending_or_blocked", result["reasons"])
        self.case["authorizations"]["xiaohongshu"].update(status="authorized", account_id="own_xhs_account", authorized_at=cm.now())
        self.assertEqual(cm.gate(self.root, self.case, candidate_id)["status"], "ready_for_submission_review")
        self.case["whitelist"]["entries"] = [{"platform": "xiaohongshu", "uploader_id": candidate["uploader_id"]}]
        self.assertIn("uploader_is_whitelisted", cm.gate(self.root, self.case, candidate_id)["reasons"])
        self.case["whitelist"]["entries"] = []
        self.case["authorizations"]["xiaohongshu"]["account_id"] = candidate["uploader_id"]
        self.assertIn("uploader_is_authorized_own_account", cm.gate(self.root, self.case, candidate_id)["reasons"])

    def test_xiaohongshu_malformed_note_ids_are_rejected(self):
        for note_id in ("", "123", "a" * 23, "a" * 25, "z" * 24, "../abcdef0123456789abcdef01", "abcdef0123456789abcdef01?xsec_token=SECRET"):
            with self.subTest(note_id=note_id), self.assertRaises(cm.CaseError):
                cm.canonical_url("xiaohongshu", note_id)

    def test_report_separates_submission_states_from_verified_outcomes(self):
        self.case["candidates"] = [
            {"platform": "douyin", "submission_status": "submitted"},
            {"platform": "douyin", "submission_status": "unknown"},
            {"platform": "kuaishou", "submission_status": "failed"},
            {"platform": "bilibili", "submission_status": "needs_supplement"},
            {"platform": "xiaohongshu", "submission_status": "not_submitted"},
            {"platform": "xiaohongshu", "submission_status": "submitting"},
            {"platform": "douyin", "submission_status": "submitted", "review_result": {"status": "approved", "verified": True, "evidence_ref": "07_receipts/synthetic-approval.png"}},
            {"platform": "bilibili", "submission_status": "submitted", "review_result": {"status": "approved", "verified": False, "evidence_ref": "07_receipts/unverified.png"}},
            {"platform": "bilibili", "submission_status": "submitted", "removal_result": {"status": "removed", "verified": True, "evidence_ref": "07_receipts/synthetic-removal.png"}},
            {"platform": "kuaishou", "submission_status": "submitted", "review_result": {"status": "approved", "verified": True, "evidence_ref": None}},
            {"platform": "legacy_platform", "submission_status": "unexpected"},
        ]
        before = json.dumps(self.case, sort_keys=True)
        result = cm.report(self.case)
        self.assertTrue(result["read_only"])
        self.assertEqual(result["totals"], {"candidates_total": 11, "submitted": 5, "needs_supplement": 1, "failed": 1, "unknown": 1, "not_submitted": 1, "submitting": 1, "other_status": 1, "review_approved": 1, "taken_down": 1, "active_todo": 3, "excluded_from_active_todo": 3, "historical_submitted": 5})
        self.assertEqual(result["by_platform"]["xiaohongshu"]["candidates_total"], 2)
        self.assertEqual(result["by_platform"]["douyin"]["review_approved"], 1)
        self.assertEqual(result["by_platform"]["bilibili"]["taken_down"], 1)
        self.assertEqual(before, json.dumps(self.case, sort_keys=True))
        self.assertNotIn("synthetic-approval.png", json.dumps(result))

    def test_report_alias_is_read_only_and_does_not_read_private_material(self):
        self.case["candidates"] = [{"platform": "xiaohongshu", "submission_status": "submitted"}]
        self.save_case()
        before = (self.root / "case.json").read_bytes()
        outputs = []
        for command in ("report", "summary"):
            output = io.StringIO()
            with contextlib.redirect_stdout(output), mock.patch.object(cm, "intake_status", side_effect=AssertionError("must not read identity")), mock.patch.object(cm, "verify_evidence", side_effect=AssertionError("metadata-only report")):
                self.assertEqual(cm.main([command, "--root", str(self.root)]), 0)
            outputs.append(json.loads(output.getvalue()))
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[0]["totals"]["submitted"], 1)
        self.assertEqual(outputs[0]["totals"]["review_approved"], 0)
        self.assertEqual(outputs[0]["totals"]["taken_down"], 0)
        self.assertEqual(before, (self.root / "case.json").read_bytes())

    def test_report_keeps_documented_submission_after_supplement_state_change(self):
        sent = dict(platform="bilibili", submission_status="submitted", submitted_at="2026-01-01T10:00:00+08:00", receipt_path="07_receipts/synthetic.png")
        self.case["candidates"] = [sent]
        self.assertEqual(cm.report(self.case)["totals"]["historical_submitted"], 1)
        sent["submission_status"] = "needs_supplement"
        self.case["candidates"].extend([
            dict(platform="bilibili", submission_status="needs_supplement", submitted_at="2026-01-01T10:00:00+08:00"),
            dict(platform="bilibili", submission_status="unknown", receipt_id="synthetic-id"),
            dict(platform="bilibili", submission_status="failed", submitted_at="invalid", receipt_id="synthetic-id"),
            dict(platform="bilibili", submission_status="needs_supplement", submitted_at="2026-01-01T10:00:00", receipt_path="07_receipts/naive-time.png"),
            dict(platform="bilibili", submission_status="unknown", submitted_at="2026-01-01T10:00:00Z", receipt_id=" "),
        ])
        counts = cm.report(self.case)["totals"]
        self.assertEqual(counts["historical_submitted"], 1)
        self.assertEqual(counts["submitted"], 0)
        self.assertEqual(counts["active_todo"], 6)
        sent.pop("receipt_path")
        sent["receipt_id"] = "synthetic-real-reference"
        cm.platform_withdraw(self.root, self.case, "bilibili")
        counts = cm.report(self.case)["totals"]
        self.assertEqual(counts["historical_submitted"], 1)
        self.assertEqual(counts["active_todo"], 0)
        self.assertEqual(counts["excluded_from_active_todo"], 6)

    def test_new_and_legacy_cases_default_to_three_active_platforms(self):
        self.assertEqual(self.case["platform_scope"]["active_platforms"], list(cm.DEFAULT_ACTIVE_PLATFORMS))
        self.assertEqual(cm.platform_scope(self.case)["inactive_platforms"], ["xiaohongshu"])
        del self.case["platform_scope"]
        original = copy.deepcopy(self.case)
        self.assertEqual(cm.platform_scope(self.case)["active_platforms"], ["douyin", "kuaishou", "bilibili"])
        self.assertEqual(self.case, original)

    def test_retained_xiaohongshu_authorization_does_not_activate_platform(self):
        candidate_id = self.prepare_ready()
        candidate = self.case["candidates"][0]
        note_id = "abcdef0123456789abcdef01"
        candidate.update(platform="xiaohongshu", video_id=note_id, canonical_url=cm.canonical_url("xiaohongshu", note_id))
        self.case["authorizations"]["xiaohongshu"]["status"] = "authorized"
        self.assertEqual(cm.gate(self.root, self.case, candidate_id)["reasons"], ["platform_not_active"])

    def test_withdrawal_blocks_ready_candidate_despite_old_submission_authority(self):
        candidate_id = self.prepare_ready()
        auth_before = copy.deepcopy(self.case["authorizations"])
        submission_before = copy.deepcopy(self.case["submission_authorization"])
        cm.platform_withdraw(self.root, self.case, "douyin")
        self.assertEqual(cm.gate(self.root, self.case, candidate_id)["reasons"], ["platform_withdrawn_by_user"])
        self.assertEqual(self.case["authorizations"], auth_before)
        self.assertEqual(self.case["submission_authorization"], submission_before)
        self.assertEqual(self.case["candidates"][0]["submission_status"], "not_submitted")
        self.assertEqual(self.case["candidates"][0]["activity_status"], "excluded_platform_withdrawn")
        self.case["platform_scope"]["active_platforms"].append("douyin")
        self.assertIn("platform_withdrawn_by_user", cm.gate(self.root, self.case, candidate_id)["reasons"])

    def test_withdrawal_preserves_outcomes_and_counts_only_active_todo(self):
        states = ("submitted", "not_submitted", "unknown", "submitting", "failed", "needs_supplement")
        self.case["candidates"] = [dict(candidate_id=f"synthetic_{index}", platform="bilibili", submission_status=state,
            receipt_id="synthetic-retained-receipt" if state == "submitted" else None,
            submitted_at="2026-01-01T00:00:00+00:00" if state == "submitted" else None)
            for index, state in enumerate(states)]
        self.case["candidates"].append(dict(candidate_id="synthetic_active", platform="douyin", submission_status="not_submitted"))
        outcomes = [(c["submission_status"], c.get("receipt_id"), c.get("submitted_at")) for c in self.case["candidates"]]
        cm.platform_withdraw(self.root, self.case, "bilibili")
        self.assertEqual([(c["submission_status"], c.get("receipt_id"), c.get("submitted_at")) for c in self.case["candidates"]], outcomes)
        result = cm.report(self.case)
        self.assertEqual(result["totals"]["candidates_total"], 7)
        self.assertEqual(result["totals"]["active_todo"], 1)
        self.assertEqual(result["totals"]["excluded_from_active_todo"], 5)
        self.assertEqual(result["totals"]["submitted"], 1)
        self.assertEqual(result["totals"]["historical_submitted"], 1)
        self.assertEqual(result["by_platform"]["bilibili"]["unknown"], 1)
        self.assertEqual(result["totals"]["review_approved"], 0)
        self.assertEqual(result["totals"]["taken_down"], 0)

    def test_withdrawn_gate_stops_before_identity_and_evidence_inspection(self):
        candidate_id = self.prepare_ready()
        cm.platform_withdraw(self.root, self.case, "douyin")
        with mock.patch.object(cm, "intake_status", side_effect=AssertionError("inactive claim must not read identity")), \
                mock.patch.object(cm, "verify_evidence", side_effect=AssertionError("inactive claim must not read media")):
            result = cm.gate(self.root, self.case, candidate_id)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reasons"], ["platform_withdrawn_by_user"])

    def test_withdraw_cli_is_idempotent_and_does_not_touch_evidence_or_identity(self):
        self.add_candidate()
        self.add_evidence("preserved.png", "search")
        self.save_case()
        original_manifest = (self.root / cm.MANIFEST).read_bytes()
        private_template = (self.root / "00_private/claimant.json").read_bytes()
        args = ["platform-withdraw", "--root", str(self.root), "--platform", "douyin"]
        results = []
        for _ in range(2):
            output = io.StringIO()
            with contextlib.redirect_stdout(output), mock.patch.object(cm, "intake_status", side_effect=AssertionError("no identity read")):
                self.assertEqual(cm.main(args), 0)
            results.append(json.loads(output.getvalue()))
            _, saved = cm.load_case(str(self.root))
            if len(results) == 1:
                first_saved = copy.deepcopy(saved)
        self.assertEqual(results[0]["status"], "withdrawn")
        self.assertEqual(results[1]["status"], "already_withdrawn")
        self.assertEqual(saved, first_saved)
        self.assertEqual(original_manifest, (self.root / cm.MANIFEST).read_bytes())
        self.assertEqual(private_template, (self.root / "00_private/claimant.json").read_bytes())
        self.assertTrue((self.root / "02_originals/preserved.png").is_file())

    def test_malformed_scope_fails_closed_instead_of_defaulting_to_active(self):
        candidate_id = self.prepare_ready()
        variants = [None, [], {}, {"active_platforms": "douyin"},
                    {"active_platforms": ["douyin", "douyin"]}, {"active_platforms": ["unknown"]},
                    {"active_platforms": ["douyin"], "withdrawals": {"douyin": {}}}]
        for scope in variants:
            with self.subTest(scope=scope):
                self.case["platform_scope"] = scope
                with self.assertRaisesRegex(cm.CaseError, "invalid_platform_scope"):
                    cm.gate(self.root, self.case, candidate_id)
                with self.assertRaisesRegex(cm.CaseError, "invalid_platform_scope"):
                    cm.report(self.case)

    def test_gate_complete_case_only_ready_for_review_no_network(self):
        result = cm.gate(self.root, self.case, self.prepare_ready())
        self.assertEqual(result["status"], "ready_for_submission_review")
        self.assertFalse(result["network_submission_performed"])
        self.assertEqual(result["reasons"], [])

    def test_missing_channel_review_blocks_existing_candidate(self):
        candidate_id = self.prepare_ready()
        del self.case["candidates"][0]["complaint_channel_review"]
        result = cm.gate(self.root, self.case, candidate_id)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("complaint_channel_not_verified", result["reasons"])

    def test_pending_false_or_empty_observed_channel_cannot_pass(self):
        candidate_id = self.prepare_ready()
        valid = {"status": "verified", "observed_path": ["Observed entry", "Observed branch"], "infringement_entry_verified": True}
        variants = [
            {**valid, "status": "pending"},
            {**valid, "infringement_entry_verified": False},
            {**valid, "observed_path": []},
            {**valid, "observed_path": [" "]},
            {**valid, "observed_path": [None]},
            {**valid, "observed_path": "unstructured assertion"},
            None,
        ]
        for channel in variants:
            with self.subTest(channel=channel):
                self.case["candidates"][0]["complaint_channel_review"] = channel
                result = cm.gate(self.root, self.case, candidate_id)
                self.assertEqual(result["status"], "blocked")
                self.assertIn("complaint_channel_not_verified", result["reasons"])

    def test_verified_channel_does_not_require_hardcoded_button_labels(self):
        candidate_id = self.prepare_ready()
        self.case["candidates"][0]["complaint_channel_review"]["observed_path"] = ["Observed localized rights menu", "Observed copying submenu"]
        self.assertEqual(cm.gate(self.root, self.case, candidate_id)["status"], "ready_for_submission_review")

    def test_delegated_visual_review_uses_batch_authorization_without_false_user_review(self):
        candidate_id = self.prepare_ready()
        review = self.case["candidates"][0]["manual_review"]
        review["reviewer"] = "agent"
        self.assertEqual(cm.gate(self.root, self.case, candidate_id)["status"], "ready_for_submission_review")
        self.case["submission_authorization"]["candidate_ids"] = []
        self.assertIn("target_not_explicitly_authorized_for_submission", cm.gate(self.root, self.case, candidate_id)["reasons"])
        self.case["submission_authorization"]["candidate_ids"] = [candidate_id]
        review["reviewer"] = "algorithm"
        self.assertIn("human_review_pending", cm.gate(self.root, self.case, candidate_id)["reasons"])

    def test_other_episode_screenshots_cannot_prove_current_episode(self):
        candidate_id = self.prepare_ready()
        self.case["works"].append({"work_id": "work_2", "title": "Synthetic episode 2", "url": "https://www.douyin.com/video/789", "user_confirmed": True, "ownership_confirmed": True})
        self.case["candidates"][0]["original_work_id"] = "work_2"
        self.add_evidence("original-ep2.mp4", "original_video", work_id="work_2")
        result = cm.gate(self.root, self.case, candidate_id)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(set(cm.status(self.root, self.case)["work_screenshots"]["work_2"].values()), {"missing"})
        self.assertIn("missing_douyin_work", result["reasons"])
        self.assertIn("missing_douyin_creator_center", result["reasons"])
        self.assertNotIn("missing_douyin_profile", result["reasons"])
        self.add_evidence("ep2-work.png", "douyin_work", work_id="work_2")
        self.add_evidence("ep2-creator.png", "douyin_creator_center", work_id="work_2")
        self.assertEqual(cm.gate(self.root, self.case, candidate_id)["status"], "ready_for_submission_review")

    def test_bound_material_registration_requires_association(self):
        for kind in cm.WORK_BOUND_KINDS:
            with self.subTest(kind=kind), self.assertRaisesRegex(cm.CaseError, "requires_work_id"):
                self.add_evidence(kind + ".png", kind)
        with self.assertRaisesRegex(cm.CaseError, "requires_candidate_id"):
            self.add_evidence("candidate.mp4", "candidate_video")

    def test_same_path_cannot_be_silently_rebound_to_another_work(self):
        self.prepare_ready()
        self.case["works"].append({"work_id": "work_2"})
        args = argparse.Namespace(file="02_originals/douyin_creator_center.png", kind="douyin_creator_center", source_url="https://creator.douyin.com/", captured_at=None, work_id="work_2", candidate_id=None)
        with self.assertRaisesRegex(cm.CaseError, "binding_differs"):
            cm.evidence_add(self.root, self.case, args)
        original = self.root / args.file
        copied = self.root / "02_originals/creator-ep2.png"
        copied.write_bytes(original.read_bytes())
        args.file = copied.relative_to(self.root).as_posix()
        self.assertEqual(cm.evidence_add(self.root, self.case, args)["status"], "registered")
        rows = [e for e in cm.manifest(self.root)["evidence"] if e["kind"] == "douyin_creator_center"]
        self.assertEqual({e["work_id"] for e in rows}, {"work_1", "work_2"})
        self.assertEqual(len({e["sha256"] for e in rows}), 1)

    def test_unrelated_damaged_search_and_candidate_only_warn(self):
        candidate_id = self.prepare_ready()
        unrelated = cm.candidate_add(self.root, self.case, argparse.Namespace(platform="douyin", video_id="999", uploader_id="other_uploader", title="Other candidate", original_id="work_1"))["candidate_id"]
        self.add_evidence("unrelated-search.json", "search")
        self.add_evidence("unrelated-candidate.mp4", "candidate_video", work_id="work_1", candidate_id=unrelated)
        (self.root / "02_originals/unrelated-search.json").unlink()
        (self.root / "02_originals/unrelated-candidate.mp4").write_bytes(b"changed")
        result = cm.gate(self.root, self.case, candidate_id)
        self.assertEqual(result["status"], "ready_for_submission_review")
        integrity_warnings = [w for w in result["warnings"] if w["code"] == "unrelated_evidence_integrity_failed"]
        self.assertEqual(len(integrity_warnings), 2)
        self.assertEqual({w["status"] for w in integrity_warnings}, {"missing", "changed"})
        self.assertFalse(cm.verify_evidence(self.root)["ok"])

    def test_relevant_candidate_work_and_profile_corruption_blocks(self):
        candidate_id = self.prepare_ready()
        for filename in ("candidate.mp4", "douyin_work.png", "douyin_profile.png"):
            path = self.root / "02_originals" / filename
            original = path.read_bytes()
            path.write_bytes(b"changed")
            result = cm.gate(self.root, self.case, candidate_id)
            self.assertEqual(result["status"], "blocked")
            self.assertIn("evidence_integrity_failed", result["reasons"])
            self.assertFalse(any(w["code"] == "unrelated_evidence_integrity_failed" for w in result["warnings"]))
            path.write_bytes(original)

    def test_empty_whitelist_requires_explicit_confirmation(self):
        candidate_id = self.prepare_ready()
        self.case["whitelist"]["user_confirmed"] = False
        self.assertIn("whitelist_not_explicitly_confirmed", cm.gate(self.root, self.case, candidate_id)["reasons"])
        self.case["whitelist"]["user_confirmed"] = True
        self.case["whitelist"]["confirmed_at"] = None
        self.assertIn("whitelist_not_explicitly_confirmed", cm.gate(self.root, self.case, candidate_id)["reasons"])

    def test_exact_whitelist_account_blocks_but_nickname_is_insufficient(self):
        candidate_id = self.prepare_ready()
        self.case["whitelist"]["entries"] = [{"platform": "douyin", "uploader_id": "uploader_456"}]
        self.assertIn("uploader_is_whitelisted", cm.gate(self.root, self.case, candidate_id)["reasons"])
        self.case["whitelist"]["entries"] = [{"platform": "douyin", "nickname": "Synthetic candidate"}]
        self.assertIn("whitelist_requires_platform_and_account_id", cm.gate(self.root, self.case, candidate_id)["reasons"])
        self.case["whitelist"]["entries"] = [{"platform": "bilibili", "uploader_id": "uploader_456"}]
        self.assertNotIn("uploader_is_whitelisted", cm.gate(self.root, self.case, candidate_id)["reasons"])

    def test_unknown_or_submitted_prevents_repeat_submission(self):
        candidate_id = self.prepare_ready()
        for state in ("unknown", "submitted", "pending", "blocked"):
            self.case["candidates"][0]["submission_status"] = state
            self.assertIn("submission_already_sent_unknown_or_not_cleared", cm.gate(self.root, self.case, candidate_id)["reasons"])

    def test_pending_or_blocked_identity_and_absent_target_authorization(self):
        candidate_id = self.prepare_ready()
        for state in ("pending", "blocked"):
            self.case["rights_review"]["identity_status"] = state
            self.assertIn("identity_pending_or_blocked", cm.gate(self.root, self.case, candidate_id)["reasons"])
        self.case["submission_authorization"]["candidate_ids"] = []
        self.assertIn("target_not_explicitly_authorized_for_submission", cm.gate(self.root, self.case, candidate_id)["reasons"])

    def test_overlap_must_be_continuous_specific_and_human_verified(self):
        candidate_id = self.prepare_ready()
        segment = self.case["candidates"][0]["overlap_segments"][0]
        segment["human_verified"] = False
        self.assertIn("specific_continuous_overlap_not_human_verified", cm.gate(self.root, self.case, candidate_id)["reasons"])
        segment["human_verified"] = True
        segment["candidate_end"] = segment["candidate_start"]
        self.assertFalse(cm.valid_overlap(segment))
        segment["candidate_end"] = float("inf")
        self.assertFalse(cm.valid_overlap(segment))


if __name__ == "__main__":
    unittest.main()
