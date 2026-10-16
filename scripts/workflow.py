#!/usr/bin/env python3
"""Offline batch import, planning and complaint drafting. Never sends complaints."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import sys
from urllib.parse import parse_qs, urlsplit

try:
    import case_manager as cm
except ModuleNotFoundError:  # Support loading this file with importlib in tests.
    import importlib.util
    _spec = importlib.util.spec_from_file_location("case_manager", Path(__file__).with_name("case_manager.py"))
    cm = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(cm)

PLATFORMS = ("douyin", "bilibili", "kuaishou")
BATCH_FOLDER = "03_search/workflow"
RESOLUTIONS = "03_search/workflow-resolutions.json"
MAX_INPUT_BYTES = 10 * 1024 * 1024
MAX_ROWS = 5000
URL_RE = re.compile(r"https?://[^\s<>\"'，。；、）】]+")
NOTICE = "本工具只整理本地记录，不联网检索、认定侵权、授予授权或提交投诉；提交成功、审核成立与下架分别记账。"
CHANNELS = {
    "douyin": "官方作品页 → 侵犯权益 → 批量侵权举报 → 搬运/抄袭/洗稿；当天核对实际入口。",
    "bilibili": "官方作品页 → 侵犯合法权益 → 侵犯个人知识产权（盗搬、抄袭）；按真实权利人与操作账号关系选择身份。",
    "kuaishou": "优先使用快手网页普通举报，选择当前页面真实适用的分类并仅填写已核实事实；不强求手机端。普通举报成功不表示版权投诉受理。",
}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def identifier(value, label="identifier"):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
        raise cm.CaseError("invalid_" + label)
    return value


def safe_text(value, limit=8000):
    """Allow reviewed prose, but never echo obvious contact/identity numbers."""
    if not isinstance(value, str):
        return ""
    if len(value) > limit:
        raise cm.CaseError("text_too_long_review_input_without_truncation")
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", value)
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[联系方式已省略]", value)
    value = re.sub(r"(?<!\d)(?:\d{17}[0-9Xx]|1[3-9]\d{9})(?!\d)", "[身份或联系号码已省略]", value)
    return value.strip()


def platform_host(host):
    for platform, domains in (("douyin", ("douyin.com", "iesdouyin.com")),
                              ("kuaishou", ("kuaishou.com",)),
                              ("bilibili", ("bilibili.com", "b23.tv"))):
        if any(host == domain or host.endswith("." + domain) for domain in domains):
            return platform
    return None


def normalize_url(value, expected_platform=None):
    """No requests or redirect resolution; shortlinks remain visibly unresolved."""
    match = URL_RE.search(str(value))
    if not match:
        return {"resolution_status": "unresolved", "reason": "missing_public_work_url"}
    try:
        parsed = urlsplit(match.group().rstrip(".!?,;:)"))
        host = (parsed.hostname or "").lower()
        platform = platform_host(host)
        if (parsed.username or parsed.password or parsed.port not in (None, 80, 443)
                or parsed.scheme not in ("http", "https") or platform is None
                or expected_platform and platform != expected_platform):
            raise ValueError()
    except ValueError:
        return {"resolution_status": "unresolved", "reason": "unsupported_or_mismatched_url"}
    patterns = {"douyin": r"/(?:video|share/video)/(\d+)/?",
                "kuaishou": r"/(?:short-video|photo)/([A-Za-z0-9_-]+)/?",
                "bilibili": r"/video/((?:BV[A-Za-z0-9]+|av\d+))/?"}
    matched = re.fullmatch(patterns[platform], parsed.path)
    video_id = matched.group(1) if matched else None
    # Douyin desktop shares sometimes encode the actual content ID in modal_id.
    modal = parse_qs(parsed.query).get("modal_id", []) if platform == "douyin" else []
    if not video_id and len(modal) == 1 and re.fullmatch(r"\d+", modal[0]):
        video_id = modal[0]
    if video_id:
        return {"resolution_status": "resolved", "platform": platform, "video_id": video_id,
                "canonical_url": cm.canonical_url(platform, video_id), "key": platform + ":" + video_id}
    if host in ("v.douyin.com", "v.kuaishou.com", "b23.tv") and re.fullmatch(r"/[A-Za-z0-9_-]+/?", parsed.path):
        url = "https://" + host + parsed.path.rstrip("/") + "/"
        return {"resolution_status": "pending_resolution", "platform": platform,
                "source_url": url, "key": "short:" + url,
                "reason": "short_url_requires_official_resolution"}
    return {"resolution_status": "unresolved", "reason": "not_a_recognized_work_url"}


def source_url(value, platform):
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        if (platform_host((parsed.hostname or "").lower()) != platform or parsed.username
                or parsed.password or parsed.port not in (None, 443) or parsed.scheme != "https"):
            raise ValueError()
        return "https://" + parsed.hostname.lower() + parsed.path
    except ValueError:
        raise cm.CaseError("source_url_must_be_official_https_page") from None


def load_rows(path):
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise cm.CaseError("input_exceeds_10_mib")
    content = path.read_text(encoding="utf-8-sig")
    suffix = path.suffix.lower()
    observed_empty = False
    if suffix == ".json":
        try:
            data = json.loads(content)
        except ValueError:
            raise cm.CaseError("invalid_search_json") from None
        rows = data.get("results") if isinstance(data, dict) else data
        observed_empty = isinstance(data, dict) and data.get("observed_empty") is True
    elif suffix == ".csv":
        rows = list(csv.DictReader(io.StringIO(content)))
    elif suffix == ".txt":
        rows = [{"url": match.group()} for match in URL_RE.finditer(content)]
    else:
        raise cm.CaseError("search_input_requires_json_csv_or_txt")
    if not isinstance(rows, list) or (not rows and not observed_empty) or len(rows) > MAX_ROWS:
        raise cm.CaseError("search_rows_must_contain_1_to_5000_entries")
    return rows, suffix[1:]


def load_batches(root):
    folder = cm.member(root, BATCH_FOLDER)
    if not folder.exists():
        return []
    batches = []
    for path in sorted(folder.glob("*.json")):
        data = cm.read_json(cm.member(root, path.relative_to(root).as_posix()))
        if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("entries"), list):
            raise cm.CaseError("invalid_workflow_search_batch")
        if data.get("denominator") != len(data["entries"]):
            raise cm.CaseError("search_batch_denominator_changed")
        batches.append(data)
    return batches


def load_resolutions(root):
    path = cm.member(root, RESOLUTIONS)
    if not path.exists():
        return {"schema_version": 1, "entries": {}}
    data = cm.read_json(path)
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("entries"), dict):
        raise cm.CaseError("invalid_search_resolutions")
    return data


def effective_entry(batch, row, resolutions):
    mapping = resolutions["entries"].get(batch["batch_id"], {})
    if not isinstance(mapping, dict):
        raise cm.CaseError("invalid_search_resolutions")
    resolution = mapping.get(row["key"])
    if resolution is None:
        return row
    if not isinstance(resolution, dict) or row["resolution_status"] == "resolved":
        raise cm.CaseError("invalid_search_resolution_binding")
    normalized = normalize_url(resolution.get("canonical_url", ""), batch["platform"])
    if (normalized.get("resolution_status") != "resolved"
            or resolution.get("canonical_url") != normalized.get("canonical_url")
            or not resolution.get("source_note") or not resolution.get("resolved_at")):
        raise cm.CaseError("invalid_search_resolution_binding")
    return {**row, **normalized, "resolved_from_key": row["key"]}


def resolve_entry(root, case, args):
    batches = [b for b in load_batches(root) if b["batch_id"] == args.batch_id]
    if len(batches) != 1:
        raise cm.CaseError("unknown_or_duplicate_search_batch")
    batch = batches[0]
    if batch["platform"] not in cm.platform_scope(case)["active_platforms"]:
        raise cm.CaseError("platform_not_active")
    rows = [r for r in batch["entries"] if r["key"] == args.item_key]
    if len(rows) != 1 or rows[0]["resolution_status"] == "resolved":
        raise cm.CaseError("resolution_requires_existing_unresolved_entry")
    normalized = normalize_url(args.url, batch["platform"])
    if normalized.get("resolution_status") != "resolved" or args.url != normalized.get("canonical_url"):
        raise cm.CaseError("resolution_requires_same_platform_canonical_work_url")
    note = safe_text(args.source_note, 2000)
    if not note:
        raise cm.CaseError("resolution_source_note_required")
    data = load_resolutions(root)
    mapping = data["entries"].setdefault(args.batch_id, {})
    if not isinstance(mapping, dict):
        raise cm.CaseError("invalid_search_resolutions")
    if args.item_key in mapping:
        previous = effective_entry(batch, rows[0], data)
        if previous.get("canonical_url") != normalized["canonical_url"]:
            raise cm.CaseError("resolution_exists_conflicting_target_review_original_mapping")
        return {"status": "already_resolved", "batch_id": args.batch_id, "canonical_url": normalized["canonical_url"],
                "network_submission_performed": False}
    mapping[args.item_key] = {"canonical_url": normalized["canonical_url"], "source_note": note, "resolved_at": cm.now()}
    cm.atomic_json(cm.member(root, RESOLUTIONS), data)
    return {"status": "resolution_recorded", "batch_id": args.batch_id, "canonical_url": normalized["canonical_url"],
            "denominator": batch["denominator"], "network_submission_performed": False}


def import_search(root, case, args):
    identifier(args.batch_id, "batch_id")
    identifier(args.account_ref, "account_ref")
    if args.platform not in cm.platform_scope(case)["active_platforms"]:
        raise cm.CaseError("platform_not_active")
    route = getattr(args, "complaint_route", None) or ("general_report" if args.platform == "kuaishou" else "copyright")
    if route not in ("copyright", "general_report") or route == "general_report" and args.platform != "kuaishou":
        raise cm.CaseError("general_report_route_only_supported_for_kuaishou")
    path = Path(args.file).resolve()
    # Do not turn the private intake into a search ledger, even accidentally.
    if cm.contained(path, (root / "00_private").resolve()):
        raise cm.CaseError("private_input_forbidden")
    rows, fmt = load_rows(path)
    if fmt == "txt" and args.batch_kind != "links":
        raise cm.CaseError("plain_links_cannot_claim_first_page_or_extension_coverage")
    origin = source_url(args.source_url, args.platform)
    if args.batch_kind == "first" and (not origin or not args.boundary_note):
        raise cm.CaseError("first_batch_requires_observed_source_and_boundary")
    if not rows and args.batch_kind != "first":
        raise cm.CaseError("observed_empty_requires_official_first_batch")
    batches = load_batches(root)
    if args.batch_kind == "extension":
        parents = [b for b in batches if b["batch_id"] == args.parent_batch]
        if (len(parents) != 1 or parents[0]["batch_kind"] != "first"
                or parents[0]["platform"] != args.platform):
            raise cm.CaseError("extension_requires_same_platform_first_batch")
    elif args.parent_batch:
        raise cm.CaseError("parent_batch_only_for_extension")
    if args.original_id and sum(w.get("work_id") == args.original_id for w in case.get("works", [])) != 1:
        raise cm.CaseError("unknown_or_duplicate_original_work")
    entries, positions = [], {}
    for ordinal, raw in enumerate(rows, 1):
        row = {"url": raw} if isinstance(raw, str) else raw if isinstance(raw, dict) else {}
        normalized = normalize_url(row.get("url", ""), args.platform)
        key = normalized.get("key", "unresolved:" + str(ordinal) + ":" + digest(row)[:16])
        if key in positions:
            entries[positions[key]]["source_positions"].append(ordinal)
            continue
        positions[key] = len(entries)
        entries.append({**normalized, "key": key, "source_positions": [ordinal],
                        "title": safe_text(row.get("title", ""), 1000),
                        "uploader_id": row.get("uploader_id", "") if isinstance(row.get("uploader_id"), str)
                        and re.fullmatch(r"[A-Za-z0-9_-]{1,256}", row["uploader_id"]) else "",
                        "review_status": "pending_review", "candidate_id": None})
    identity = {"platform": args.platform, "batch_id": args.batch_id, "batch_kind": args.batch_kind,
                "query": safe_text(args.query, 1000), "sort": safe_text(args.sort, 200),
                "account_ref": args.account_ref, "source_url": origin,
                "boundary_note": safe_text(args.boundary_note or "", 2000),
                "parent_batch": args.parent_batch, "input_format": fmt,
                "source_sha256": cm.sha256(path), "original_work_id": args.original_id,
                "requested_complaint_route": route}
    if not rows:
        identity["observed_empty"] = True
    fingerprint = digest(identity)
    target = cm.member(root, BATCH_FOLDER + "/" + args.batch_id + ".json")
    if target.exists():
        previous = cm.read_json(target)
        if previous.get("import_fingerprint") != fingerprint:
            raise cm.CaseError("batch_id_exists_use_new_id_preserve_original_denominator")
        return {"status": "already_imported", "batch_id": args.batch_id, "denominator": previous["denominator"],
                "network_submission_performed": False}
    if args.original_id:
        existing = candidate_index(case)
        if any(row["key"] in existing and existing[row["key"]].get("original_work_id") != args.original_id
               for row in entries if row["resolution_status"] == "resolved"):
            raise cm.CaseError("existing_candidate_original_differs_review_binding_before_import")
        for row in entries:
            if row["resolution_status"] != "resolved":
                continue
            result = cm.candidate_add(root, case, argparse.Namespace(platform=args.platform,
                video_id=row["video_id"], title=row["title"] or row["key"],
                uploader_id=row["uploader_id"], original_id=args.original_id, complaint_route=route))
            row["candidate_id"] = result["candidate_id"]
            stored = next(c for c in case["candidates"] if c["candidate_id"] == row["candidate_id"])
            row["complaint_route"] = stored.get("complaint_route", "copyright")
    batch = {"schema_version": 1, **identity, "created_at": cm.now(), "import_fingerprint": fingerprint,
             "denominator": len(entries), "input_rows": len(rows), "duplicate_rows": len(rows) - len(entries),
             "coverage_claim": "recorded_observed_boundary_not_independently_verified" if args.batch_kind == "first" else "no_first_page_claim",
             "entries": entries}
    cm.atomic_json(target, batch)
    return {"status": "imported", "batch_id": args.batch_id, "denominator": batch["denominator"],
            "duplicate_rows": batch["duplicate_rows"], "unresolved": sum(r["resolution_status"] != "resolved" for r in entries),
            "network_submission_performed": False}


def candidate_index(case):
    result = {}
    for candidate in case.get("candidates", []):
        key = str(candidate.get("platform")) + ":" + str(candidate.get("video_id"))
        if key in result:
            raise cm.CaseError("duplicate_case_candidate_content_id")
        result[key] = candidate
    return result


def reviewed_exclusion(candidate):
    review = candidate.get("manual_review", {})
    return (review.get("status") in ("not_copy", "unrelated_or_shared_material", "excluded")
            and review.get("reviewer") in ("user", "agent") and bool(review.get("reviewed_at")))


def platform_priority(item):
    platform = item.get("platform")
    return PLATFORMS.index(platform) if platform in PLATFORMS else len(PLATFORMS)


def execution_priority(root, case):
    """Derive scheduling from recorded work, never add authorization or case state."""
    if "douyin" not in cm.platform_scope(case)["active_platforms"]:
        return {"platform_order": list(PLATFORMS), "douyin_stage": "not_in_active_scope",
                "off_platform_submission_deferred": False, "pending_items": [], "preparation_allowed": True}
    index = candidate_index(case)
    resolutions = load_resolutions(root)
    pending, seen = [], set()
    douyin_batches = [b for b in load_batches(root) if b["platform"] == "douyin"]
    douyin_candidates = [c for c in case.get("candidates", []) if c.get("platform") == "douyin"]
    for batch in douyin_batches:
        for original_row in batch["entries"]:
            row = effective_entry(batch, original_row, resolutions)
            if row["key"] not in index and row["key"] not in seen:
                seen.add(row["key"])
                pending.append({"batch_id": batch["batch_id"], "item_key": row["key"],
                                "reason": "unresolved_search_item" if row["resolution_status"] != "resolved" else "unregistered_search_item"})
    for candidate in douyin_candidates:
        state = candidate.get("submission_status")
        if state == "submitted" or state == "not_submitted" and reviewed_exclusion(candidate):
            continue
        review = candidate.get("manual_review", {})
        reviewed = review.get("reviewer") in ("user", "agent") and bool(review.get("reviewed_at"))
        reason = ("submission_result_requires_verification" if state in ("unknown", "submitting") else
                  "reviewed_insufficient_evidence" if reviewed and review.get("status") in ("insufficient", "insufficient_evidence") else
                  "confirmed_copy_not_submitted" if reviewed and review.get("status") == "confirmed_copy" else
                  "pending_review_or_existing_record")
        pending.append({"candidate_id": candidate["candidate_id"], "reason": reason})
    recorded = bool(douyin_batches or douyin_candidates)
    return {"platform_order": list(PLATFORMS),
            "douyin_stage": "recorded_work_pending" if pending else "no_recorded_pending_work" if recorded else "scope_not_recorded",
            "off_platform_submission_deferred": bool(pending) or not recorded,
            "pending_items": pending, "preparation_allowed": True,
            "note": "只描述已有清单；证据不足保留具体待办，站外可继续本地准备。已提交待审核不阻塞站外；没有待办不证明搜索覆盖已完整。"}


def next_step(root, case, candidate):
    if candidate.get("platform") not in cm.platform_scope(case)["active_platforms"]:
        return {"action": "inactive_platform_preserve_history", "ready_to_submit": False}
    state = candidate.get("submission_status")
    if state in ("unknown", "submitting"):
        return {"action": "check_official_receipt_before_any_retry", "ready_to_submit": False}
    if state == "submitted":
        return {"action": "check_official_review_and_removal_result", "ready_to_submit": False}
    if state != "not_submitted":
        return {"action": "review_existing_submission_or_supplement_record", "ready_to_submit": False}
    if reviewed_exclusion(candidate):
        return {"action": "excluded_after_recorded_human_review", "ready_to_submit": False}
    check = cm.gate(root, case, candidate["candidate_id"])
    return {"action": "official_form_review" if check["status"] == "ready_for_submission_review" else "complete_missing_review_or_evidence",
            "ready_to_submit": check["status"] == "ready_for_submission_review",
            "gate_reasons": check["reasons"]}


def report(root, case):
    active = cm.platform_scope(case)["active_platforms"]
    index = candidate_index(case)
    resolutions = load_resolutions(root)
    batches = []
    for batch in load_batches(root):
        states = {"pending_resolution": 0, "unregistered": 0, "pending_review": 0,
                  "reviewed_insufficient": 0, "reviewed_other": 0, "confirmed_copy": 0}
        resolved_keys = []
        for original_row in batch["entries"]:
            row = effective_entry(batch, original_row, resolutions)
            if row["resolution_status"] == "resolved":
                resolved_keys.append(row["key"])
            candidate = index.get(row["key"])
            if row["resolution_status"] != "resolved":
                state = "pending_resolution"
            elif candidate is None:
                state = "unregistered"
            else:
                review = candidate.get("manual_review", {})
                manual = review.get("status", "pending")
                complete = review.get("reviewer") in ("user", "agent") and bool(review.get("reviewed_at"))
                state = {"confirmed_copy": "confirmed_copy", "insufficient_evidence": "reviewed_insufficient",
                         "insufficient": "reviewed_insufficient", "not_copy": "reviewed_other",
                         "unrelated_or_shared_material": "reviewed_other", "excluded": "reviewed_other"}.get(manual, "pending_review") if complete else "pending_review"
            states[state] += 1
        batches.append({"batch_id": batch["batch_id"], "platform": batch["platform"], "batch_kind": batch["batch_kind"],
                        "active": batch["platform"] in active, "denominator": batch["denominator"],
                        "unique_resolved_count": len(set(resolved_keys)), "resolved_duplicates": len(resolved_keys) - len(set(resolved_keys)),
                        "coverage_claim": batch["coverage_claim"], **states})
    return {"notice": NOTICE, "read_only": True, "network_submission_performed": False,
            "search_batches": batches, "candidate_states": cm.report(case),
            "counting_note": "每批分母保持原样；扩展批和分享链接不改变首批分母。跨批可能有重复作品，不能直接相加为全网独立视频数。候选统计只描述本地记录，未重新验证平台回执。"}


def plan(root, case):
    data = report(root, case)
    index = candidate_index(case)
    active = cm.platform_scope(case)["active_platforms"]
    resolutions = load_resolutions(root)
    tasks = []
    queued = set()
    for batch in load_batches(root):
        if batch["platform"] not in active:
            continue
        for original_row in batch["entries"]:
            row = effective_entry(batch, original_row, resolutions)
            if row["key"] not in index and row["key"] not in queued:
                queued.add(row["key"])
                tasks.append({"batch_id": batch["batch_id"], "item_key": row["key"], "platform": batch["platform"],
                              "action": "resolve_official_work_url" if row["resolution_status"] != "resolved" else "register_candidate_with_correct_original",
                              "ready_to_submit": False})
    tasks += [{"candidate_id": c["candidate_id"], "platform": c["platform"], **next_step(root, case, c)}
              for c in case.get("candidates", [])]
    priority = execution_priority(root, case)
    for task in tasks:
        task["submission_deferred_by_platform_order"] = bool(priority["off_platform_submission_deferred"]
            and task["platform"] in ("bilibili", "kuaishou") and task["platform"] in active
            and task["action"] in ("official_form_review", "complete_missing_review_or_evidence",
                                   "resolve_official_work_url", "register_candidate_with_correct_original"))
        if task["submission_deferred_by_platform_order"]:
            task["local_preparation_allowed"] = True
            task["prerequisites_ready_for_official_form"] = task["ready_to_submit"]
            task["ready_to_submit"] = False
            if task["action"] == "official_form_review":
                task["action"] = "await_douyin_stage_before_official_submission"
    tasks.sort(key=lambda task: (task["action"] in ("check_official_review_and_removal_result",
                "inactive_platform_preserve_history", "excluded_after_recorded_human_review"), platform_priority(task)))
    return {**data, "execution_priority": priority, "next_steps": tasks,
            "account_reminder": "只登录用户授权的非作者检索/举报操作账号；作者主页、作品与创作者中心证明全部复用用户提供截图，不要求作者账号登录。操作账号不同不改变权利人身份，也不代替平台要求的代理授权。"}


def attachment_refs(root, candidate):
    results = []
    allowed = {"douyin_profile", "douyin_work", "douyin_creator_center", "original_video",
               "candidate_video", "candidate_page", "candidate_frame", "comparison"}
    if candidate.get("complaint_route") == "general_report":
        allowed.difference_update(cm.SCREENSHOT_KINDS)
    for entry in cm.manifest(root)["evidence"]:
        if not isinstance(entry, dict) or entry.get("kind") not in allowed:
            continue
        if not (entry.get("kind") == "douyin_profile" or entry.get("candidate_id") == candidate["candidate_id"]
                or not entry.get("candidate_id") and entry.get("work_id") == candidate.get("original_work_id")):
            continue
        try:
            path = cm.member(root, entry.get("path", ""))
            if cm.contained(path, (root / "00_private").resolve()):
                continue
            if path.relative_to(root).parts[0] not in ("02_originals", "04_candidates", "05_comparisons"):
                continue
            state = "verified" if path.is_file() and cm.sha256(path) == entry.get("sha256") else "missing_or_changed"
        except (cm.CaseError, OSError):
            continue
        results.append({"evidence_id": safe_text(entry.get("evidence_id", ""), 100), "kind": entry["kind"],
                        "case_relative_path": safe_text(path.relative_to(root).as_posix(), 1000),
                        "integrity_status": state, "content_privacy_review_required": True})
    return results


def reviewed_material(root, candidate):
    """Render existing schema records only; screenshot points never become intervals."""
    if candidate.get("evidence_mode", "local_video") != "browser_visual":
        segments = [{key: segment[key] for key in ("original_start", "original_end", "candidate_start", "candidate_end")}
                    | {"description": safe_text(segment["description"])}
                    for segment in candidate.get("overlap_segments", []) if cm.valid_overlap(segment)]
        return {"mode": "local_video", "segments": segments, "points": [], "limitations": "须以实际人工连续片段复核为准。"}
    context = candidate.get("browser_visual_review") or {}
    checks = {item["evidence_id"]: item["status"] for item in cm.verify_evidence(root)["evidence"]}
    evidence = {item.get("evidence_id"): item for item in cm.manifest(root)["evidence"] if isinstance(item, dict)}
    points = []
    for segment in candidate.get("overlap_segments", []):
        if (not cm.valid_overlap(segment) or not segment.get("shot_id")
                or not isinstance(segment.get("screenshot_observation"), str) or not segment["screenshot_observation"].strip()):
            continue
        for frame in segment.get("candidate_frames", []):
            if not isinstance(frame, dict):
                continue
            original_time, time = frame.get("original_time"), frame.get("time")
            entry = evidence.get(frame.get("evidence_id"), {})
            if (isinstance(time, bool) or not isinstance(time, (int, float))
                    or isinstance(original_time, bool) or not isinstance(original_time, (int, float))
                    or not segment["candidate_start"] <= time <= segment["candidate_end"]
                    or not segment["original_start"] <= original_time <= segment["original_end"]
                    or checks.get(frame.get("evidence_id")) != "verified"
                    or entry.get("kind") != "candidate_frame" or entry.get("candidate_id") != candidate["candidate_id"]
                    or entry.get("work_id") != candidate.get("original_work_id")
                    or entry.get("source_url") != candidate.get("canonical_url")):
                continue
            points.append({"shot_id": safe_text(segment["shot_id"], 100), "original_time": original_time,
                           "candidate_time": time, "evidence_id": safe_text(frame["evidence_id"], 100),
                           "observation": safe_text(segment["screenshot_observation"])})
    return {"mode": "browser_visual", "segments": [], "points": points,
            "time_basis": safe_text(context.get("time_basis", "未记录时间依据")),
            "limitations": safe_text(context.get("coverage_limitations", "未记录取样局限，须补充。")),
            "continuous_coverage_claimed": False}


def grouped_links(prepared):
    groups = {}
    for item in prepared:
        key = (item["platform"], item["original_work_id"], item["complaint_route"])
        group = groups.setdefault(key, {"platform": key[0], "original_work_id": key[1], "complaint_route": key[2],
                                       "original_url": item["original_url"], "ready_targets": [], "blocked_targets": []})
        target = {"candidate_id": item["candidate_id"], "target_url": item["target_url"],
                  "ready_for_official_form_review": item["ready_for_official_form_review"],
                  "prerequisites_ready_for_official_form": item["prerequisites_ready_for_official_form"],
                  "submission_deferred_by_platform_order": item["submission_deferred_by_platform_order"],
                  "missing_requirements": item["missing_requirements"]}
        group["ready_targets" if target["ready_for_official_form_review"] else "blocked_targets"].append(target)
    for group in groups.values():
        group["ready_target_links_text"] = "\n".join(t["target_url"] for t in group["ready_targets"])
    return list(groups.values())


def verify_handoff(folder, fingerprint, expected_files):
    manifest_path = folder / "artifact-manifest.json"
    if not manifest_path.is_file():
        raise cm.CaseError("handoff_manifest_missing_review_before_reuse")
    manifest = cm.read_json(manifest_path)
    files = manifest.get("files")
    if (manifest.get("schema_version") != 1 or manifest.get("content_fingerprint") != fingerprint
            or not isinstance(files, dict) or set(files) != set(expected_files)):
        raise cm.CaseError("handoff_manifest_invalid_review_before_reuse")
    for name, expected_hash in files.items():
        path = folder / name
        if not path.is_file() or cm.sha256(path) != expected_hash:
            raise cm.CaseError("handoff_file_missing_or_changed_review_before_reuse")


def prepare(root, case, candidate_id=None):
    active = cm.platform_scope(case)["active_platforms"]
    priority = execution_priority(root, case)
    candidates = case.get("candidates", [])
    if candidate_id:
        candidates = [c for c in candidates if c.get("candidate_id") == candidate_id]
        if len(candidates) != 1:
            raise cm.CaseError("unknown_or_duplicate_candidate_id")
        if candidates[0].get("platform") not in active:
            raise cm.CaseError("candidate_platform_not_active")
    prepared, skipped = [], []
    for candidate in sorted(candidates, key=platform_priority):
        candidate_id = identifier(candidate.get("candidate_id"), "candidate_id")
        if candidate.get("platform") not in active:
            skipped.append({"candidate_id": candidate_id, "reason": "inactive_platform"})
            continue
        state = candidate.get("submission_status")
        if state != "not_submitted":
            skipped.append({"candidate_id": candidate_id, "reason": "existing_submission_requires_followup"})
            continue
        if reviewed_exclusion(candidate):
            skipped.append({"candidate_id": candidate_id, "reason": "excluded_after_recorded_human_review"})
            continue
        step = next_step(root, case, candidate)
        original = next((w for w in case.get("works", []) if w.get("work_id") == candidate.get("original_work_id")), {})
        original_link = normalize_url(original.get("url", ""))
        original_url = original_link.get("canonical_url")
        target_url = cm.canonical_url(candidate["platform"], candidate["video_id"])
        route = candidate.get("complaint_route", "copyright")
        if route not in ("copyright", "general_report") or route == "general_report" and candidate["platform"] != "kuaishou":
            raise cm.CaseError("invalid_candidate_complaint_route")
        material = reviewed_material(root, candidate)
        review = candidate.get("manual_review", {})
        confirmed = review.get("status") == "confirmed_copy" and review.get("reviewer") in ("user", "agent") and bool(review.get("reviewed_at"))
        missing = list(step.get("gate_reasons", []))
        if not original_url:
            missing.append("canonical_original_url_not_resolved")
        if not material["segments"] and not material["points"]:
            missing.append("draft_specific_evidence_not_available_in_current_schema")
        prerequisites_ready = step["ready_to_submit"] and not missing
        deferred = priority["off_platform_submission_deferred"] and candidate["platform"] != "douyin"
        ready = prerequisites_ready and not deferred
        title = "普通举报准备草稿" if route == "general_report" else "版权投诉准备草稿"
        privacy_note = ("本次普通举报准备包不包含身份材料；只使用当前表单实际要求的最小必要内容。"
                        if route == "general_report" else "本文件无身份填表副本；身份材料只在官方表单内按必要范围填写。")
        content = ["# " + title + "（尚未提交）", "", NOTICE, "",
                   privacy_note, "",
                   "- 平台：" + candidate["platform"],
                   "- 通道：" + ("普通举报；成功提示不等于版权投诉受理。" if route == "general_report" else "版权投诉，须核验当前官方入口。"),
                   "- 目标：" + target_url,
                   "- 原作：" + (original_url or original_link.get("source_url", "待补充可定位原作链接")),
                   "- 原作标题：" + safe_text(original.get("title", "待补充"), 1000),
                   "- 人工复核：" + ("已有人工确认记录，具体已核实点与局限见下方。" if confirmed else "尚无完整人工确认记录，不可据此认定搬运或提交。"),
                   "- 提交前本地检查：" + ("已满足材料检查；等待抖音阶段后再提交，仍可继续本地准备。" if prerequisites_ready and deferred else
                                           "可进入官方表单复核。" if ready else "未通过；见 missing_requirements。"),
                   "- 平台执行顺序：抖音 → B站/快手；" + ("当前站外提交待抖音阶段续办，本地准备不受禁止。" if deferred else "按已记录状态继续。"),
                   "", "## 实际已核实的重合点"]
        for segment in material["segments"]:
            content.append(f"- 连续片段：原作 {segment['original_start']}–{segment['original_end']} 秒；目标 {segment['candidate_start']}–{segment['candidate_end']} 秒。{segment['description']}")
        for point in material["points"]:
            content.append(f"- 对应取样点 {point['shot_id']}：原作 {point['original_time']} 秒 ↔ 目标 {point['candidate_time']} 秒；证据 {point['evidence_id']}。{point['observation']}")
        if material["mode"] == "browser_visual":
            content += ["- 时间依据：" + material["time_basis"], "- 已记录局限：" + material["limitations"],
                        "- 仅主张上述实际观察点，不推断取样点之间的连续画面、转场或完整播放经过。"]
        if not material["segments"] and not material["points"]:
            content.append("- 当前证据结构未读取到可输出的具体区段或对应点，请核查记录格式及证据后重新生成。")
        request = ("请平台依据上述已核实事实与对应材料审核该内容，并按当前适用社区规则处理。本次为普通举报，不表示已提出或受理版权投诉。"
                   if route == "general_report" else "请平台依据提交的原作及逐段对照证据审核所主张的具体内容，并处理核实成立的侵权部分。")
        channel = CHANNELS[candidate["platform"]]
        if candidate["platform"] == "kuaishou" and route == "copyright":
            channel = "本候选保留既有版权投诉通道；先核对当前适用于普通作品的官方侵权入口，不将网页普通举报算作版权投诉。"
        content += ["", "## 请求与交接", request, channel,
                    "使用非作者操作账号时，按真实身份选择本人或代理流程；不得为通过表单假称操作账号就是原作者。",
                    "附件清单仅是本地引用，未复制或上传任何材料；逐件确认内容与当前平台要求后再选择上传。",
                    "成功提示或投诉编号出现后再保存回执。异常或结果未知先查询官方记录，不盲目重投。"]
        prepared.append({"candidate_id": candidate_id, "platform": candidate["platform"], "complaint_route": route,
                         "original_work_id": safe_text(candidate.get("original_work_id", ""), 100), "original_url": original_url,
                         "target_url": target_url, "draft": "\n".join(content) + "\n",
                         "prerequisites_ready_for_official_form": prerequisites_ready,
                         "submission_deferred_by_platform_order": deferred,
                         "ready_for_official_form_review": ready, "reviewed_material": material,
                         "missing_requirements": list(dict.fromkeys(missing)), "attachments": attachment_refs(root, candidate)})
    groups = grouped_links(prepared)
    fingerprint = digest({"prepared": prepared, "skipped": skipped, "link_groups": groups, "execution_priority": priority})[:20]
    relative = "06_drafts/workflow/" + fingerprint
    folder = cm.member(root, relative)
    expected_files = ["summary.json", *[item["candidate_id"] + suffix for item in prepared for suffix in (".md", ".json")]]
    if folder.exists():
        verify_handoff(folder, fingerprint, expected_files)
        return {"status": "already_prepared", "integrity_verified": True, "folder": relative, "prepared": len(prepared), "skipped": len(skipped), "execution_priority": priority, "network_submission_performed": False}
    folder.mkdir(parents=True)
    for item in prepared:
        (folder / (item["candidate_id"] + ".md")).write_text(item.pop("draft"), encoding="utf-8")
        cm.atomic_json(folder / (item["candidate_id"] + ".json"), item)
    summary = {"notice": NOTICE, "created_at": cm.now(), "network_submission_performed": False,
               "attachments_copied": False, "private_intake_copied": False,
               "prepared": prepared, "skipped": skipped, "execution_priority": priority,
               "link_groups": groups,
               "by_platform": {p: [item["candidate_id"] for item in prepared if item["platform"] == p] for p in PLATFORMS}}
    cm.atomic_json(folder / "summary.json", summary)
    cm.atomic_json(folder / "artifact-manifest.json", {"schema_version": 1, "content_fingerprint": fingerprint,
                   "files": {name: cm.sha256(folder / name) for name in expected_files}})
    return {"status": "prepared", "folder": relative, "prepared": len(prepared), "skipped": len(skipped), "execution_priority": priority, "network_submission_performed": False}


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("import-search", "resolve-entry", "plan", "prepare", "report"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--root", required=True)
        if name == "import-search":
            cmd.add_argument("--file", required=True)
            cmd.add_argument("--platform", choices=PLATFORMS, required=True)
            cmd.add_argument("--batch-id", required=True)
            cmd.add_argument("--query", required=True)
            cmd.add_argument("--sort", required=True)
            cmd.add_argument("--account-ref", required=True, help="Local account alias, not a password, token or phone")
            cmd.add_argument("--batch-kind", choices=("first", "extension", "links"), required=True)
            cmd.add_argument("--source-url")
            cmd.add_argument("--boundary-note")
            cmd.add_argument("--parent-batch")
            cmd.add_argument("--original-id")
            cmd.add_argument("--complaint-route", choices=("copyright", "general_report"),
                             help="New Kuaishou candidates default to general_report, other platforms to copyright; existing routes stay unchanged")
        elif name == "resolve-entry":
            cmd.add_argument("--batch-id", required=True)
            cmd.add_argument("--item-key", required=True)
            cmd.add_argument("--url", required=True)
            cmd.add_argument("--source-note", required=True, help="Actual official resolution evidence; never a guessed content ID")
        elif name == "prepare":
            cmd.add_argument("--candidate")
    return p


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    try:
        root, case = cm.load_case(args.root)
        result = import_search(root, case, args) if args.command == "import-search" else (
            resolve_entry(root, case, args) if args.command == "resolve-entry" else
            prepare(root, case, args.candidate) if args.command == "prepare" else
            plan(root, case) if args.command == "plan" else report(root, case))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (cm.CaseError, OSError, ValueError, KeyError, TypeError) as exc:
        error = str(exc) if isinstance(exc, cm.CaseError) else "invalid_input_or_local_file_operation_failed"
        print(json.dumps({"status": "error", "error": error, "network_submission_performed": False}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
