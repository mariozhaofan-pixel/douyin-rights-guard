"""Offline integration tests: python -m unittest discover -s tests -p test_workflow.py."""
import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location("workflow", Path(__file__).resolve().parents[1] / "scripts" / "workflow.py")
wf = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wf)
cm = wf.cm


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rights-workflow-test-")
        self.base = Path(self.temp.name)
        self.root = self.base / "case"
        cm.init_case(str(self.root))
        self.root, self.case = cm.load_case(str(self.root))
        self.case["works"] = [{"work_id": "work_1", "title": "Synthetic episode 1",
                               "url": "https://www.douyin.com/video/123?token=PRIVATE_URL_TOKEN",
                               "user_confirmed": False, "ownership_confirmed": False}]
        cm.atomic_json(self.root / "case.json", self.case)

    def tearDown(self):
        self.temp.cleanup()

    def args(self, rows=None, **changes):
        file = self.base / (changes.pop("filename", "search.json"))
        if rows is None:
            rows = [{"url": "https://www.douyin.com/video/456?token=PRIVATE_TOKEN", "title": "Synthetic candidate"}]
        file.write_text(json.dumps({"results": rows}), encoding="utf-8")
        values = dict(file=str(file), platform="douyin", batch_id="dy-first", query="Synthetic title", sort="综合",
                      account_ref="dy-reporter", batch_kind="first", source_url="https://www.douyin.com/search/test?token=PRIVATE_TOKEN",
                      boundary_note="Observed initial stable result cards", parent_batch=None, original_id=None)
        values.update(changes)
        return argparse.Namespace(**values)

    def add_candidate(self, video_id="456", platform="douyin"):
        return cm.candidate_add(self.root, self.case, argparse.Namespace(platform=platform, video_id=video_id,
            uploader_id="uploader_1", title="Synthetic candidate", original_id="work_1"))["candidate_id"]

    def pack_text(self, result):
        folder = self.root / result["folder"]
        return "\n".join(p.read_text(encoding="utf-8") for p in folder.iterdir() if p.suffix in (".json", ".md"))

    def test_normalization_platform_and_content_id_dedup(self):
        args = self.args([
            {"url": "https://www.douyin.com/video/456/?enter_from=search", "title": "First title"},
            {"url": "https://www.douyin.com/?modal_id=456&token=secret"},
            {"url": "https://www.iesdouyin.com/share/video/456/"},
            {"url": "https://www.douyin.com/video/789"},
        ])
        result = wf.import_search(self.root, self.case, args)
        self.assertEqual((result["denominator"], result["duplicate_rows"]), (2, 2))
        batch = wf.load_batches(self.root)[0]
        self.assertEqual(batch["entries"][0]["source_positions"], [1, 2, 3])
        self.assertEqual(batch["entries"][0]["canonical_url"], "https://www.douyin.com/video/456")
        self.assertEqual(batch["account_ref"], "dy-reporter")
        self.assertNotIn("PRIVATE_TOKEN", json.dumps(batch))

    def test_unknown_items_are_preserved_and_shortlink_not_resolved_by_guess(self):
        args = self.args([{"url": "https://v.douyin.com/abcdef/"}, {"url": "https://v.douyin.com/abcdef/?share_token=abc"},
                          {"url": "https://unsupported.example/video/2"}, {"title": "missing link"}])
        result = wf.import_search(self.root, self.case, args)
        self.assertEqual((result["denominator"], result["unresolved"]), (3, 3))
        self.assertEqual(wf.load_batches(self.root)[0]["entries"][0]["resolution_status"], "pending_resolution")
        self.assertEqual(wf.report(self.root, self.case)["search_batches"][0]["pending_resolution"], 3)

    def test_plain_text_requires_links_and_never_claims_search_page_coverage(self):
        file = self.base / "links.txt"
        file.write_text("分享 https://www.bilibili.com/video/BV1abc/ 复制。\n另条 https://b23.tv/abcdef/", encoding="utf-8")
        args = self.args(platform="bilibili", source_url="https://search.bilibili.com/all")
        args.file = str(file)
        with self.assertRaisesRegex(cm.CaseError, "plain_links"):
            wf.import_search(self.root, self.case, args)
        args.batch_kind = "links"
        result = wf.import_search(self.root, self.case, args)
        self.assertEqual(result["denominator"], 2)
        self.assertEqual(wf.load_batches(self.root)[0]["coverage_claim"], "no_first_page_claim")

    def test_csv_import_and_optional_registration_preserve_authorization(self):
        file = self.base / "search.csv"
        file.write_text('url,title,uploader_id,submission_status\nhttps://www.douyin.com/video/456,"Synthetic, candidate",up_1,submitted\n', encoding="utf-8")
        args = self.args(original_id="work_1")
        args.file = str(file)
        before_auth = json.dumps(self.case["authorizations"], sort_keys=True)
        wf.import_search(self.root, self.case, args)
        candidate = self.case["candidates"][0]
        self.assertEqual(candidate["submission_status"], "not_submitted")
        self.assertEqual(candidate["manual_review"]["status"], "pending")
        self.assertFalse(candidate["uploader_id_verified"])
        self.assertFalse(self.case["works"][0]["ownership_confirmed"])
        self.assertFalse(self.case["whitelist"]["user_confirmed"])
        self.assertFalse(self.case["submission_authorization"]["user_confirmed"])
        self.assertEqual(before_auth, json.dumps(self.case["authorizations"], sort_keys=True))
        self.assertEqual(wf.load_batches(self.root)[0]["entries"][0]["candidate_id"], candidate["candidate_id"])

    def test_reimport_idempotent_and_cannot_replace_first_denominator(self):
        args = self.args(original_id="work_1")
        wf.import_search(self.root, self.case, args)
        before = (self.root / wf.BATCH_FOLDER / "dy-first.json").read_bytes()
        self.assertEqual(wf.import_search(self.root, self.case, args)["status"], "already_imported")
        self.assertEqual(len(self.case["candidates"]), 1)
        args = self.args([{"url": "https://www.douyin.com/video/456"}, {"url": "https://www.douyin.com/video/789"}], original_id="work_1")
        with self.assertRaisesRegex(cm.CaseError, "preserve_original_denominator"):
            wf.import_search(self.root, self.case, args)
        self.assertEqual((self.root / wf.BATCH_FOLDER / "dy-first.json").read_bytes(), before)

    def test_extension_preserves_first_denominator_and_dynamic_case_status(self):
        wf.import_search(self.root, self.case, self.args(original_id="work_1"))
        args = self.args([{"url": "https://www.douyin.com/video/789"}, {"url": "https://www.douyin.com/video/456"}],
                         filename="extension.json", batch_id="dy-more", batch_kind="extension", parent_batch="dy-first")
        wf.import_search(self.root, self.case, args)
        self.case["candidates"][0]["manual_review"].update(status="confirmed_copy", reviewer="agent", reviewed_at=cm.now())
        report = wf.report(self.root, self.case)
        first = next(b for b in report["search_batches"] if b["batch_kind"] == "first")
        self.assertEqual(first["denominator"], 1)
        self.assertEqual(first["confirmed_copy"], 1)
        self.assertEqual(report["candidate_states"]["totals"]["submitted"], 0)

    def test_requires_observed_boundary_and_valid_parent(self):
        for changes in ({"source_url": None}, {"boundary_note": ""},
                        {"batch_kind": "extension", "parent_batch": "unknown"}):
            with self.assertRaises(cm.CaseError):
                wf.import_search(self.root, self.case, self.args(**changes))

    def test_existing_original_binding_is_not_overwritten(self):
        self.add_candidate()
        self.case["works"].append({"work_id": "work_2", "title": "Synthetic episode 2", "url": "https://www.douyin.com/video/124"})
        with self.assertRaisesRegex(cm.CaseError, "existing_candidate_original_differs"):
            wf.import_search(self.root, self.case, self.args(original_id="work_2"))
        self.assertEqual(self.case["candidates"][0]["original_work_id"], "work_1")
        self.assertEqual(wf.load_batches(self.root), [])

    def test_algorithm_or_unknown_review_state_is_not_counted_review_complete(self):
        wf.import_search(self.root, self.case, self.args(original_id="work_1"))
        self.case["candidates"][0]["manual_review"].update(status="confirmed_copy", reviewer="algorithm", reviewed_at=cm.now())
        batch = wf.report(self.root, self.case)["search_batches"][0]
        self.assertEqual(batch["confirmed_copy"], 0)
        self.assertEqual(batch["pending_review"], 1)
        self.case["candidates"][0]["manual_review"].update(status="arbitrary", reviewer="agent")
        self.assertEqual(wf.report(self.root, self.case)["search_batches"][0]["pending_review"], 1)

    def test_prepare_copies_no_identity_and_never_submits(self):
        candidate_id = self.add_candidate()
        private = {"real_name": "PRIVATE_NAME_DO_NOT_COPY", "email": "private@example.invalid", "phone": "13800138000",
                   "id_front": "00_private/front.jpg", "id_back": "00_private/back.jpg", "profile_mobile": "00_private/mobile.png"}
        cm.atomic_json(self.root / "00_private/claimant.json", private)
        for name in ("front.jpg", "back.jpg", "mobile.png"):
            (self.root / "00_private" / name).write_bytes(b"PRIVATE_IMAGE_DO_NOT_COPY")
        candidate = self.case["candidates"][0]
        candidate["overlap_segments"] = [{"original_start": 1, "original_end": 3, "candidate_start": 2, "candidate_end": 4,
                                          "description": "Synthetic details; accidental private@example.invalid 13800138000",
                                          "human_verified": True}]
        path = self.root / "05_comparisons" / "comparison.txt"
        path.write_text("Synthetic observations", encoding="utf-8")
        cm.evidence_add(self.root, self.case, argparse.Namespace(file="05_comparisons/comparison.txt", kind="comparison",
            source_url="https://www.douyin.com/video/456", captured_at=None, work_id="work_1", candidate_id=candidate_id))
        before = json.dumps(self.case, sort_keys=True)
        result = wf.prepare(self.root, self.case)
        text = self.pack_text(result)
        self.assertEqual(result["prepared"], 1)
        self.assertFalse(result["network_submission_performed"])
        for secret in ("PRIVATE_NAME", "private@example.invalid", "13800138000", "PRIVATE_IMAGE", "PRIVATE_URL_TOKEN", "00_private/front.jpg"):
            self.assertNotIn(secret, text)
        self.assertIn("05_comparisons/comparison.txt", text)
        self.assertIn("尚未提交", text)
        self.assertEqual(before, json.dumps(self.case, sort_keys=True))
        self.assertEqual(wf.prepare(self.root, self.case)["status"], "already_prepared")

    def test_unknown_submitting_and_submitted_are_followup_not_ready(self):
        for index, state in enumerate(("unknown", "submitting", "submitted")):
            self.add_candidate(str(500 + index))
            self.case["candidates"][-1]["submission_status"] = state
        with mock.patch.object(cm, "gate", side_effect=AssertionError("must not re-enter submission gate")):
            planned = wf.plan(self.root, self.case)
            packed = wf.prepare(self.root, self.case)
        self.assertFalse(any(s["ready_to_submit"] for s in planned["next_steps"]))
        self.assertEqual((packed["prepared"], packed["skipped"]), (0, 3))
        self.assertEqual(wf.report(self.root, self.case)["candidate_states"]["totals"]["submitted"], 1)

    def test_withdrawn_platform_never_reads_private_or_prepares_actionable_draft(self):
        candidate_id = self.add_candidate(platform="kuaishou")
        cm.platform_withdraw(self.root, self.case, "kuaishou")
        with mock.patch.object(cm, "gate", side_effect=AssertionError("gate must not read private")), \
                mock.patch.object(cm, "intake_status", side_effect=AssertionError("private read forbidden")):
            self.assertEqual(wf.prepare(self.root, self.case)["prepared"], 0)
            with self.assertRaisesRegex(cm.CaseError, "platform_not_active"):
                wf.prepare(self.root, self.case, candidate_id)
            self.assertEqual(wf.plan(self.root, self.case)["next_steps"][0]["action"], "inactive_platform_preserve_history")

    def test_private_manifest_entry_is_not_exported(self):
        candidate_id = self.add_candidate()
        secret = self.root / "00_private/secret.jpg"
        secret.write_bytes(b"SECRET_IMAGE")
        cm.atomic_json(self.root / cm.MANIFEST, {"schema_version": 1, "evidence": [{"evidence_id": "ev_private",
            "kind": "comparison", "candidate_id": candidate_id, "work_id": "work_1", "path": "00_private/secret.jpg", "sha256": cm.sha256(secret)}]})
        result = wf.prepare(self.root, self.case)
        self.assertNotIn("00_private/secret.jpg", self.pack_text(result))
        self.assertNotIn("SECRET_IMAGE", self.pack_text(result))

    def test_invalid_urls_and_credentials_never_echo_secret(self):
        for url in ("https://www.douyin.com.evil.example/video/123", "https://username:SECRET@www.douyin.com/video/123",
                    "https://www.douyin.com:8443/video/123", "file:///private.json"):
            normalized = wf.normalize_url(url)
            self.assertEqual(normalized["resolution_status"], "unresolved")
            self.assertNotIn("SECRET", json.dumps(normalized))

    def test_readonly_commands_do_not_modify_case_or_grant_success(self):
        self.add_candidate()
        before = (self.root / "case.json").read_bytes()
        plan = wf.plan(self.root, self.case)
        self.assertIn("不要求作者账号登录", plan["account_reminder"])
        summary = wf.report(self.root, self.case)
        self.assertEqual((self.root / "case.json").read_bytes(), before)
        self.assertEqual(summary["candidate_states"]["totals"]["submitted"], 0)
        self.assertEqual(summary["candidate_states"]["totals"]["review_approved"], 0)
        self.assertEqual(summary["candidate_states"]["totals"]["taken_down"], 0)

    def test_oversized_text_is_rejected_not_silently_truncated(self):
        with self.assertRaisesRegex(cm.CaseError, "without_truncation"):
            wf.import_search(self.root, self.case, self.args([{"url": "https://www.douyin.com/video/123", "title": "x" * 1001}]))

    def test_cli_output_is_valid_json_and_claims_no_submission(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(wf.main(["report", "--root", str(self.root)]), 0)
        self.assertFalse(json.loads(output.getvalue())["network_submission_performed"])

    def test_prepared_file_edits_and_missing_files_are_not_reused(self):
        candidate_id = self.add_candidate()
        result = wf.prepare(self.root, self.case)
        folder = self.root / result["folder"]
        target = folder / (candidate_id + ".md")
        original = target.read_bytes()
        target.write_text("User edit must be preserved", encoding="utf-8")
        with self.assertRaisesRegex(cm.CaseError, "missing_or_changed"):
            wf.prepare(self.root, self.case)
        self.assertEqual(target.read_text(encoding="utf-8"), "User edit must be preserved")
        target.write_bytes(original)
        self.assertTrue(wf.prepare(self.root, self.case)["integrity_verified"])
        target.unlink()
        with self.assertRaisesRegex(cm.CaseError, "missing_or_changed"):
            wf.prepare(self.root, self.case)

    def test_legacy_pack_without_manifest_requires_review(self):
        self.add_candidate()
        result = wf.prepare(self.root, self.case)
        (self.root / result["folder"] / "artifact-manifest.json").unlink()
        with self.assertRaisesRegex(cm.CaseError, "manifest_missing"):
            wf.prepare(self.root, self.case)

    def test_browser_visual_draft_renders_actual_points_and_limits(self):
        import test_case_manager as fixtures
        fixture = fixtures.CaseManagerTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        candidate_id = fixture.prepare_browser_ready()
        self.assertEqual(wf.cm.gate(fixture.root, fixture.case, candidate_id)["status"], "ready_for_submission_review")
        result = wf.prepare(fixture.root, fixture.case)
        folder = fixture.root / result["folder"]
        data = cm.read_json(folder / (candidate_id + ".json"))
        text = (folder / (candidate_id + ".md")).read_text(encoding="utf-8")
        expected = [(f["original_time"], f["time"], f["evidence_id"])
                    for s in fixture.case["candidates"][0]["overlap_segments"] for f in s["candidate_frames"]]
        actual = [(p["original_time"], p["candidate_time"], p["evidence_id"]) for p in data["reviewed_material"]["points"]]
        self.assertEqual(actual, expected)
        self.assertTrue(data["ready_for_official_form_review"])
        self.assertEqual(data["reviewed_material"]["segments"], [])
        self.assertIn("仅主张上述实际观察点", text)
        self.assertIn(fixture.case["candidates"][0]["browser_visual_review"]["coverage_limitations"], text)
        self.assertNotIn("连续片段：", text)
        self.assertNotIn("尚无完整人工确认记录", text)

    def test_ready_link_group_excludes_blocked_and_keeps_canonical_original(self):
        import test_case_manager as fixtures
        fixture = fixtures.CaseManagerTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        ready_id = fixture.prepare_ready()
        blocked_id = cm.candidate_add(fixture.root, fixture.case, argparse.Namespace(platform="douyin", video_id="789",
            title="Synthetic blocked", uploader_id="another_uploader", original_id="work_1"))["candidate_id"]
        result = wf.prepare(fixture.root, fixture.case)
        summary = cm.read_json(fixture.root / result["folder"] / "summary.json")
        group = summary["link_groups"][0]
        self.assertEqual(group["original_url"], "https://www.douyin.com/video/123")
        self.assertEqual([t["candidate_id"] for t in group["ready_targets"]], [ready_id])
        self.assertEqual([t["candidate_id"] for t in group["blocked_targets"]], [blocked_id])
        self.assertEqual(group["ready_target_links_text"], "https://www.douyin.com/video/456")
        self.assertNotIn("789", group["ready_target_links_text"])

    def test_general_route_is_explicit_kuaishou_only_and_does_not_change_existing_route(self):
        args = self.args(platform="kuaishou", source_url="https://www.kuaishou.com/search", original_id="work_1",
                         complaint_route="general_report", rows=[{"url": "https://www.kuaishou.com/short-video/ks456"}])
        wf.import_search(self.root, self.case, args)
        self.assertEqual(self.case["candidates"][0]["complaint_route"], "general_report")
        result = wf.prepare(self.root, self.case)
        text = self.pack_text(result)
        self.assertIn("普通举报准备草稿", text)
        self.assertIn("普通举报成功不表示版权投诉受理", text)
        summary = cm.read_json(self.root / result["folder"] / "summary.json")
        self.assertEqual(summary["link_groups"][0]["complaint_route"], "general_report")
        self.assertFalse(summary["link_groups"][0]["ready_targets"])
        args.batch_id = "ks-second"
        args.complaint_route = "copyright"
        wf.import_search(self.root, self.case, args)
        self.assertEqual(self.case["candidates"][0]["complaint_route"], "general_report")
        with self.assertRaisesRegex(cm.CaseError, "only_supported_for_kuaishou"):
            wf.import_search(self.root, self.case, self.args(batch_id="bad-route", complaint_route="general_report"))

    def test_new_kuaishou_default_is_general_report_without_identity_intake(self):
        args = self.args(platform="kuaishou", source_url="https://www.kuaishou.com/search", original_id="work_1",
                         rows=[{"url": "https://www.kuaishou.com/short-video/ks456"}])
        self.assertIsNone(wf.parser().parse_args(["import-search", "--root", str(self.root), "--file", args.file,
            "--platform", "kuaishou", "--batch-id", "ks-cli", "--query", "Synthetic", "--sort", "综合",
            "--account-ref", "ks-reporter", "--batch-kind", "links"]).complaint_route)
        wf.import_search(self.root, self.case, args)
        self.assertEqual(self.case["candidates"][0]["complaint_route"], "general_report")
        self.assertEqual(wf.load_batches(self.root)[0]["requested_complaint_route"], "general_report")
        with mock.patch.object(cm, "intake_status", side_effect=AssertionError("ordinary report must not read identity intake")):
            plan = wf.plan(self.root, self.case)
            result = wf.prepare(self.root, self.case)
        self.assertNotIn("private_intake_incomplete", plan["next_steps"][0].get("gate_reasons", []))
        self.assertIn("普通举报准备草稿", self.pack_text(result))

    def test_new_kuaishou_explicit_copyright_is_preserved(self):
        args = self.args(platform="kuaishou", source_url="https://www.kuaishou.com/search", original_id="work_1",
                         complaint_route="copyright", rows=[{"url": "https://www.kuaishou.com/short-video/ks456"}])
        wf.import_search(self.root, self.case, args)
        self.assertEqual(self.case["candidates"][0]["complaint_route"], "copyright")
        args.batch_id = "ks-default-import-existing"
        args.complaint_route = None
        wf.import_search(self.root, self.case, args)
        self.assertEqual(self.case["candidates"][0]["complaint_route"], "copyright")

    def test_resolved_shortlinks_converge_without_mutating_first_batch(self):
        wf.import_search(self.root, self.case, self.args(rows=[{"url": "https://v.douyin.com/first/"},
                                                            {"url": "https://v.douyin.com/second/"}]))
        batch_path = self.root / wf.BATCH_FOLDER / "dy-first.json"
        original_bytes = batch_path.read_bytes()
        for suffix in ("first", "second"):
            wf.resolve_entry(self.root, self.case, argparse.Namespace(batch_id="dy-first",
                item_key="short:https://v.douyin.com/" + suffix + "/", url="https://www.douyin.com/video/456",
                source_note="Synthetic official redirect observed during manual test"))
        unresolved_plan = wf.plan(self.root, self.case)
        self.assertEqual(len(unresolved_plan["next_steps"]), 1)
        self.assertEqual(unresolved_plan["next_steps"][0]["action"], "register_candidate_with_correct_original")
        self.add_candidate()
        summary = wf.report(self.root, self.case)["search_batches"][0]
        self.assertEqual((summary["denominator"], summary["pending_resolution"], summary["unique_resolved_count"], summary["resolved_duplicates"]),
                         (2, 0, 1, 1))
        self.assertEqual(batch_path.read_bytes(), original_bytes)
        actions = [task["action"] for task in wf.plan(self.root, self.case)["next_steps"]]
        self.assertNotIn("resolve_official_work_url", actions)
        self.assertNotIn("register_candidate_with_correct_original", actions)

    def test_resolution_rejects_cross_platform_and_conflicting_remap(self):
        wf.import_search(self.root, self.case, self.args(rows=[{"url": "https://v.douyin.com/first/"}]))
        args = argparse.Namespace(batch_id="dy-first", item_key="short:https://v.douyin.com/first/",
            url="https://www.bilibili.com/video/BV1abc", source_note="Synthetic observed redirect")
        with self.assertRaisesRegex(cm.CaseError, "same_platform_canonical"):
            wf.resolve_entry(self.root, self.case, args)
        args.url = "https://www.douyin.com/video/456"
        wf.resolve_entry(self.root, self.case, args)
        stored = (self.root / wf.RESOLUTIONS).read_bytes()
        self.assertEqual(wf.resolve_entry(self.root, self.case, args)["status"], "already_resolved")
        self.assertEqual((self.root / wf.RESOLUTIONS).read_bytes(), stored)
        args.url = "https://www.douyin.com/video/789"
        with self.assertRaisesRegex(cm.CaseError, "conflicting_target"):
            wf.resolve_entry(self.root, self.case, args)
        self.assertEqual((self.root / wf.RESOLUTIONS).read_bytes(), stored)

    def test_resolution_requires_existing_unresolved_row(self):
        wf.import_search(self.root, self.case, self.args())
        args = argparse.Namespace(batch_id="dy-first", item_key="douyin:456", url="https://www.douyin.com/video/456",
                                  source_note="Synthetic verified link")
        with self.assertRaisesRegex(cm.CaseError, "existing_unresolved"):
            wf.resolve_entry(self.root, self.case, args)

    def test_real_human_exclusions_skip_preparation_but_incomplete_claims_do_not(self):
        for index, status in enumerate(("not_copy", "unrelated_or_shared_material", "excluded")):
            self.add_candidate(str(600 + index))
            self.case["candidates"][-1]["manual_review"].update(status=status, reviewer="agent", reviewed_at=cm.now())
        with mock.patch.object(cm, "gate", side_effect=AssertionError("excluded candidate should not seek evidence")):
            plan = wf.plan(self.root, self.case)
            result = wf.prepare(self.root, self.case)
        self.assertEqual(result["prepared"], 0)
        self.assertTrue(all(item["action"] == "excluded_after_recorded_human_review" for item in plan["next_steps"]))
        self.case["candidates"][0]["manual_review"]["reviewer"] = "algorithm"
        self.case["candidates"][1]["manual_review"]["reviewed_at"] = None
        plan = wf.plan(self.root, self.case)
        self.assertNotEqual(plan["next_steps"][0]["action"], "excluded_after_recorded_human_review")
        self.assertNotEqual(plan["next_steps"][1]["action"], "excluded_after_recorded_human_review")
        self.assertEqual(wf.prepare(self.root, self.case)["prepared"], 2)

    def ready_segments(self):
        for candidate in self.case["candidates"]:
            candidate["overlap_segments"] = [{"original_start": 1, "original_end": 3,
                "candidate_start": 1, "candidate_end": 3, "description": "Synthetic copied motion", "human_verified": True}]

    def test_priority_orders_mixed_queue_and_defers_submission_without_blocking_preparation(self):
        ks_id = self.add_candidate("ks789", platform="kuaishou")
        bili_id = self.add_candidate("BV1abc456", platform="bilibili")
        dy_id = self.add_candidate()
        self.ready_segments()
        before = json.dumps(self.case, sort_keys=True)
        with mock.patch.object(cm, "gate", return_value={"status": "ready_for_submission_review", "reasons": []}):
            plan = wf.plan(self.root, self.case)
            result = wf.prepare(self.root, self.case)
            singled = wf.prepare(self.root, self.case, bili_id)
        self.assertEqual([s["candidate_id"] for s in plan["next_steps"]], [dy_id, bili_id, ks_id])
        self.assertTrue(plan["next_steps"][0]["ready_to_submit"])
        for step in plan["next_steps"][1:]:
            self.assertFalse(step["ready_to_submit"])
            self.assertTrue(step["local_preparation_allowed"])
            self.assertTrue(step["prerequisites_ready_for_official_form"])
        summary = cm.read_json(self.root / result["folder"] / "summary.json")
        self.assertEqual([s["candidate_id"] for s in summary["prepared"]], [dy_id, bili_id, ks_id])
        self.assertEqual(len(summary["prepared"]), 3)
        for group in summary["link_groups"][1:]:
            self.assertEqual(group["ready_target_links_text"], "")
            self.assertTrue(group["blocked_targets"][0]["submission_deferred_by_platform_order"])
        single_data = cm.read_json(self.root / singled["folder"] / (bili_id + ".json"))
        self.assertTrue(single_data["submission_deferred_by_platform_order"])
        self.assertTrue(single_data["prerequisites_ready_for_official_form"])
        self.assertEqual(single_data["missing_requirements"], [])
        self.assertEqual(before, json.dumps(self.case, sort_keys=True))

    def test_submitted_douyin_does_not_delay_off_platform_work_or_trigger_resubmission(self):
        bili_id = self.add_candidate("BV1abc456", platform="bilibili")
        dy_id = self.add_candidate()
        self.ready_segments()
        dy = self.case["candidates"][-1]
        dy["submission_status"] = "submitted"
        dy["review_status"] = "pending"
        with mock.patch.object(cm, "gate", return_value={"status": "ready_for_submission_review", "reasons": []}) as gate:
            plan = wf.plan(self.root, self.case)
            result = wf.prepare(self.root, self.case)
        self.assertFalse(plan["execution_priority"]["off_platform_submission_deferred"])
        self.assertEqual(plan["next_steps"][0]["candidate_id"], bili_id)
        self.assertTrue(plan["next_steps"][0]["ready_to_submit"])
        self.assertEqual(plan["next_steps"][-1]["action"], "check_official_review_and_removal_result")
        self.assertTrue(all(call.args[2] != dy_id for call in gate.call_args_list))
        summary = cm.read_json(self.root / result["folder"] / "summary.json")
        self.assertEqual(summary["link_groups"][0]["ready_target_links_text"], "https://www.bilibili.com/video/BV1abc456")
        self.assertEqual(result["prepared"], 1)

    def test_unresolved_and_unknown_douyin_work_remain_pending_with_specific_reasons(self):
        bili_id = self.add_candidate("BV1abc456", platform="bilibili")
        self.add_candidate()
        dy = self.case["candidates"][-1]
        wf.import_search(self.root, self.case, self.args(rows=[{"url": "https://v.douyin.com/unknown/"}]))
        dy["manual_review"].update(status="insufficient_evidence", reviewer="agent", reviewed_at=cm.now())
        priority = wf.execution_priority(self.root, self.case)
        self.assertEqual({s["reason"] for s in priority["pending_items"]},
                         {"unresolved_search_item", "reviewed_insufficient_evidence"})
        self.assertTrue(priority["preparation_allowed"])
        for state in ("unknown", "submitting"):
            dy["submission_status"] = state
            self.assertIn("submission_result_requires_verification",
                          [s["reason"] for s in wf.execution_priority(self.root, self.case)["pending_items"]])
            with mock.patch.object(cm, "gate", return_value={"status": "ready_for_submission_review", "reasons": []}):
                result = wf.prepare(self.root, self.case, bili_id)
            self.assertTrue(result["execution_priority"]["off_platform_submission_deferred"])

    def test_douyin_excluded_or_inactive_does_not_block_off_platform(self):
        self.add_candidate("BV1abc456", platform="bilibili")
        self.assertEqual(wf.execution_priority(self.root, self.case)["douyin_stage"], "scope_not_recorded")
        self.add_candidate()
        dy = self.case["candidates"][-1]
        dy["manual_review"].update(status="not_copy", reviewer="agent", reviewed_at=cm.now())
        self.assertFalse(wf.execution_priority(self.root, self.case)["off_platform_submission_deferred"])
        dy["manual_review"]["reviewed_at"] = None
        self.assertTrue(wf.execution_priority(self.root, self.case)["off_platform_submission_deferred"])
        cm.platform_withdraw(self.root, self.case, "douyin")
        self.assertFalse(wf.execution_priority(self.root, self.case)["off_platform_submission_deferred"])

    def test_explicit_observed_empty_first_batch_can_record_zero_results(self):
        args = self.args(rows=[])
        with self.assertRaises(cm.CaseError):
            wf.import_search(self.root, self.case, args)
        Path(args.file).write_text(json.dumps({"results": [], "observed_empty": True}), encoding="utf-8")
        args.batch_kind = "links"
        with self.assertRaisesRegex(cm.CaseError, "official_first_batch"):
            wf.import_search(self.root, self.case, args)
        args.batch_kind = "first"
        args.source_url = None
        with self.assertRaisesRegex(cm.CaseError, "source_and_boundary"):
            wf.import_search(self.root, self.case, args)
        args.source_url = "https://www.douyin.com/search/test"
        result = wf.import_search(self.root, self.case, args)
        self.assertEqual(result["denominator"], 0)
        self.assertEqual(wf.execution_priority(self.root, self.case)["douyin_stage"], "no_recorded_pending_work")
        self.assertFalse(wf.execution_priority(self.root, self.case)["off_platform_submission_deferred"])
if __name__ == "__main__":
    unittest.main()
