#!/usr/bin/env python3
"""Local, privacy-conscious evidence ledger. Never submits complaints or logs PII."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from datetime import datetime, timezone
from urllib.parse import urlsplit
from uuid import uuid4

DIRECTORIES = (
    "00_private", "01_auth", "02_originals", "03_search", "04_candidates",
    "05_comparisons", "06_drafts", "07_receipts", "08_logs",
)
PLATFORMS = ("douyin", "kuaishou", "bilibili", "xiaohongshu")
DEFAULT_ACTIVE_PLATFORMS = ("douyin", "kuaishou", "bilibili")
EVIDENCE_KINDS = (
    "douyin_profile", "douyin_work", "douyin_creator_center",
    "original_video", "candidate_video", "candidate_page", "candidate_frame",
    "comparison", "search", "receipt", "other",
)
SCREENSHOT_KINDS = ("douyin_profile", "douyin_work", "douyin_creator_center")
WORK_BOUND_KINDS = ("douyin_work", "douyin_creator_center", "original_video", "candidate_page", "candidate_frame")
BROWSER_EVIDENCE_KINDS = ("candidate_page", "candidate_frame")
PRIVATE_FIELDS = ("real_name", "email", "phone", "id_front", "id_back", "profile_mobile")
FILE_FIELDS = ("id_front", "id_back", "profile_mobile")
MANIFEST = "08_logs/evidence_manifest.json"


class CaseError(ValueError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def contained(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def member(root: Path, value: str, *, private: bool = False) -> Path:
    """Require relative project paths; resolve symlinks before checking the boundary."""
    if not isinstance(value, str) or not value.strip():
        raise CaseError("missing_file_path")
    # Reject Windows traversal syntax even when tests run on another OS.
    normalized = value.replace("\\", "/")
    parts = normalized.split("/")
    if normalized.startswith("/") or ":" in normalized or ".." in parts:
        raise CaseError("path_outside_case")
    path = (root / normalized).resolve()
    if not contained(path, root) or path == root:
        raise CaseError("path_outside_case")
    private_root = (root / "00_private").resolve()
    if private and not contained(path, private_root):
        raise CaseError("identity_material_must_be_in_00_private")
    return path


def read_json(path: Path):
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        raise CaseError("unreadable_or_invalid_json") from None


def atomic_json(path: Path, data) -> None:
    """Replace complete JSON atomically; never append partial records."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".case-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_case(root_value: str):
    root = Path(root_value).expanduser().resolve()
    case = read_json(member(root, "case.json"))
    if not isinstance(case, dict) or case.get("schema_version") != 1:
        raise CaseError("unsupported_case_schema")
    return root, case


def init_case(root_value: str):
    root = Path(root_value).expanduser().resolve()
    skill_root = Path(__file__).resolve().parents[1]
    if contained(root, skill_root):
        raise CaseError("case_must_be_outside_skill_and_repository")
    if skill_root.parent.name.casefold() == "skills" and contained(root, skill_root.parent):
        raise CaseError("case_must_be_outside_skill_and_repository")
    for ancestor in (root, *root.parents):
        if (ancestor / ".git").exists() or (ancestor / "SKILL.md").exists():
            raise CaseError("case_must_be_outside_skill_and_repository")
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise CaseError("case_directory_must_be_new_or_empty")
    root.mkdir(parents=True, exist_ok=True)
    for name in DIRECTORIES:
        (root / name).mkdir()
    case = {
        "schema_version": 1,
        "case_id": str(uuid4()),
        "created_at": now(),
        "full_access_acknowledged": False,
        "platform_scope": {"active_platforms": list(DEFAULT_ACTIVE_PLATFORMS), "withdrawals": {}},
        "authorizations": {p: {"status": "pending", "account_id": "", "authorized_at": None} for p in PLATFORMS},
        "rights_review": {"identity_status": "pending", "ownership_status": "pending", "reviewed_at": None},
        "works": [],
        "whitelist": {"user_confirmed": False, "confirmed_at": None, "entries": []},
        "submission_authorization": {"user_confirmed": False, "confirmed_at": None, "candidate_ids": []},
        "candidates": [],
    }
    atomic_json(root / "case.json", case)
    atomic_json(root / "00_private" / "claimant.json", {key: "" for key in PRIVATE_FIELDS})
    atomic_json(root / MANIFEST, {"schema_version": 1, "evidence": []})
    return {"status": "initialized", "case_id": case["case_id"], "private_materials": "missing"}


def intake_status(root: Path):
    claimant = read_json(member(root, "00_private/claimant.json", private=True))
    if not isinstance(claimant, dict):
        raise CaseError("invalid_private_intake")
    fields = {}
    for key in PRIVATE_FIELDS:
        value = claimant.get(key)
        provided = isinstance(value, str) and bool(value.strip())
        if provided and key in FILE_FIELDS:
            try:
                provided = member(root, value, private=True).is_file()
            except CaseError:
                provided = False
        fields[key] = "provided" if provided else "missing"
    return {"complete": all(v == "provided" for v in fields.values()), "fields": fields}


def manifest(root: Path):
    data = read_json(member(root, MANIFEST))
    if not isinstance(data, dict) or not isinstance(data.get("evidence"), list):
        raise CaseError("invalid_evidence_manifest")
    return data


def verify_evidence(root: Path):
    results = []
    for entry in manifest(root)["evidence"]:
        state = "invalid_record"
        if isinstance(entry, dict):
            try:
                path = member(root, entry.get("path", ""))
                if contained(path, (root / "00_private").resolve()):
                    state = "private_path_forbidden"
                elif not path.is_file():
                    state = "missing"
                else:
                    state = "verified" if sha256(path) == entry.get("sha256") else "changed"
            except (CaseError, OSError):
                state = "invalid_or_unreadable_path"
        # IDs only: paths and source URLs are not needed in status output.
        results.append({"evidence_id": entry.get("evidence_id", "invalid") if isinstance(entry, dict) else "invalid", "status": state})
    return {"ok": all(row["status"] == "verified" for row in results), "evidence": results}


def status(root: Path, case):
    verification = verify_evidence(root)
    verified = {row["evidence_id"] for row in verification["evidence"] if row["status"] == "verified"}
    evidence = [entry for entry in manifest(root)["evidence"] if isinstance(entry, dict) and entry.get("evidence_id") in verified]
    kinds = {entry.get("kind") for entry in evidence}
    return {
        "case_id": case["case_id"],
        "intake": intake_status(root),
        "screenshots": {kind: "provided" if kind in kinds else "missing" for kind in SCREENSHOT_KINDS},
        "work_screenshots": {
            work["work_id"]: {kind: "provided" if any(e.get("kind") == kind and e.get("work_id") == work["work_id"] for e in evidence) else "missing"
                              for kind in ("douyin_work", "douyin_creator_center")}
            for work in case.get("works", []) if isinstance(work, dict) and work.get("work_id")
        },
        "authorizations": {p: case.get("authorizations", {}).get(p, {}).get("status", "pending") for p in PLATFORMS},
        "platform_scope": platform_scope(case),
        "work_count": len(case.get("works", [])),
        "candidate_count": len(case.get("candidates", [])),
        "whitelist_confirmed": case.get("whitelist", {}).get("user_confirmed") is True,
        "evidence_integrity": verification["ok"],
    }


def platform_scope(case):
    """Scope is independent of retained login authorizations and submission history."""
    scope = case.get("platform_scope", {"active_platforms": list(DEFAULT_ACTIVE_PLATFORMS), "withdrawals": {}})
    if not isinstance(scope, dict):
        raise CaseError("invalid_platform_scope")
    active = scope.get("active_platforms")
    withdrawals = scope.get("withdrawals", {})
    if (not isinstance(active, list) or any(p not in PLATFORMS for p in active)
            or len(active) != len(set(active)) or not isinstance(withdrawals, dict)
            or any(p not in PLATFORMS or not isinstance(record, dict)
                   or not record.get("withdrawn_at") or record.get("reason_code") != "user_cancelled_platform"
                   for p, record in withdrawals.items())):
        raise CaseError("invalid_platform_scope")
    # An accidentally re-added list item cannot override the withdrawal record.
    return {"active_platforms": [p for p in active if p not in withdrawals],
            "withdrawn_platforms": [p for p in PLATFORMS if p in withdrawals],
            "inactive_platforms": [p for p in PLATFORMS if p not in active and p not in withdrawals]}


def platform_withdraw(root: Path, case, platform: str):
    """Record an explicit user platform withdrawal, preserving files and all outcomes."""
    if platform not in PLATFORMS:
        raise CaseError("unknown_platform")
    current = platform_scope(case)
    scope = case.setdefault("platform_scope", {"active_platforms": current["active_platforms"], "withdrawals": {}})
    withdrawals = scope.setdefault("withdrawals", {})
    already = platform in withdrawals
    withdrawals.setdefault(platform, {"withdrawn_at": now(), "reason_code": "user_cancelled_platform"})
    scope["active_platforms"] = [p for p in scope["active_platforms"] if p != platform]
    affected = 0
    for candidate in case.get("candidates", []):
        if candidate.get("platform") == platform:
            candidate["activity_status"] = "excluded_platform_withdrawn"
            affected += 1
    atomic_json(member(root, "case.json"), case)
    return {"status": "already_withdrawn" if already else "withdrawn", "platform": platform,
            "affected_candidates": affected, "platform_scope": platform_scope(case),
            "submission_history_preserved": True, "network_submission_performed": False}


def recorded_submission(candidate):
    if candidate.get("submission_status") == "submitted":
        return True
    timestamp = candidate.get("submitted_at")
    if not isinstance(timestamp, str):
        return False
    try:
        if datetime.fromisoformat(timestamp.replace("Z", "+00:00")).tzinfo is None:
            return False
    except ValueError:
        return False
    return any(isinstance(candidate.get(key), str) and candidate[key].strip()
               for key in ("receipt_id", "receipt_path"))


def report(case):
    """Aggregate recorded states only; no mutation, network access or identity reads."""
    states = ("submitted", "needs_supplement", "failed", "unknown", "not_submitted", "submitting")
    keys = ("candidates_total", *states, "other_status", "review_approved", "taken_down",
            "active_todo", "excluded_from_active_todo", "historical_submitted")
    scope = platform_scope(case)
    by_platform = {platform: dict.fromkeys(keys, 0) for platform in PLATFORMS}
    for candidate in case.get("candidates", []):
        platform = candidate.get("platform")
        bucket = by_platform.setdefault(platform if platform in PLATFORMS else "other", dict.fromkeys(keys, 0))
        bucket["candidates_total"] += 1
        state = candidate.get("submission_status")
        bucket[state if state in states else "other_status"] += 1
        if recorded_submission(candidate):
            bucket["historical_submitted"] += 1
        if state != "submitted":
            if platform in scope["active_platforms"]:
                bucket["active_todo"] += 1
            else:
                bucket["excluded_from_active_todo"] += 1
        for field, positive, counter in (("review_result", "approved", "review_approved"), ("removal_result", "removed", "taken_down")):
            outcome = candidate.get(field)
            if (isinstance(outcome, dict) and outcome.get("status") == positive
                    and outcome.get("verified") is True
                    and isinstance(outcome.get("evidence_ref"), str) and outcome["evidence_ref"].strip()):
                bucket[counter] += 1
    return {"case_id": case["case_id"], "read_only": True,
            "platform_scope": scope,
            "totals": {key: sum(counts[key] for counts in by_platform.values()) for key in keys},
            "by_platform": by_platform,
            "notice": "All platforms remain in the recorded counts. active_todo counts non-submitted states on active platforms, including supplement/uncertain follow-up; excluded_from_active_todo counts the other non-submitted states. historical_submitted counts submitted states or a timezone-aware submitted_at plus a nonempty receipt_id/receipt_path, retaining documented submissions after status changes. Todo counts do not authorize retrying supplement, unknown, submitting or failed records. Review approval and removal require separately verified results with references; this metadata report does not revalidate receipts or evidence."}


def public_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password:
        raise CaseError("invalid_public_source_url")
    # Queries/fragments may carry tokens. Keep only the public provenance location.
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def screenshot_file(path: Path) -> bool:
    """Reject obvious non-image placeholders; this does not verify visible content."""
    try:
        with path.open("rb") as handle:
            header = handle.read(12)
        suffix = path.suffix.lower()
        return ((suffix == ".png" and header.startswith(b"\x89PNG\r\n\x1a\n"))
                or (suffix in (".jpg", ".jpeg") and header.startswith(b"\xff\xd8\xff"))
                or (suffix == ".webp" and header[:4] == b"RIFF" and header[8:12] == b"WEBP"))
    except OSError:
        return False


def evidence_add(root: Path, case, args):
    path = member(root, args.file)
    if contained(path, (root / "00_private").resolve()):
        raise CaseError("private_material_cannot_enter_public_manifest")
    if not path.is_file() or path == member(root, "case.json") or path == member(root, MANIFEST):
        raise CaseError("evidence_must_be_an_existing_material_file")
    if args.kind in WORK_BOUND_KINDS and not args.work_id:
        raise CaseError("this_evidence_kind_requires_work_id")
    if args.kind in ("candidate_video", *BROWSER_EVIDENCE_KINDS) and not args.candidate_id:
        raise CaseError("candidate_video_requires_candidate_id" if args.kind == "candidate_video"
                        else "browser_evidence_requires_candidate_id")
    if args.work_id and args.work_id not in {w.get("work_id") for w in case.get("works", [])}:
        raise CaseError("unknown_original_work")
    if args.candidate_id and args.candidate_id not in {c.get("candidate_id") for c in case.get("candidates", [])}:
        raise CaseError("unknown_candidate")
    source_url = public_url(args.source_url)
    if args.kind in BROWSER_EVIDENCE_KINDS:
        candidate = next(c for c in case["candidates"] if c.get("candidate_id") == args.candidate_id)
        if args.work_id != candidate.get("original_work_id") or source_url != canonical_url(candidate["platform"], candidate["video_id"]):
            raise CaseError("browser_evidence_must_match_candidate_work_and_official_url")
        if not screenshot_file(path):
            raise CaseError("browser_evidence_requires_png_jpeg_or_webp_screenshot")
    user_provided = getattr(args, "user_provided", False)
    timestamp = args.captured_at or (None if user_provided else now())
    if timestamp is not None:
        try:
            if datetime.fromisoformat(timestamp.replace("Z", "+00:00")).tzinfo is None:
                raise ValueError()
        except ValueError:
            raise CaseError("captured_at_requires_iso8601_with_timezone") from None
    rel = path.relative_to(root).as_posix()
    digest = sha256(path)
    data = manifest(root)
    for entry in data["evidence"]:
        if entry.get("path") == rel:
            if entry.get("sha256") != digest:
                raise CaseError("registered_evidence_changed_preserve_original_and_use_new_file")
            if (entry.get("kind"), entry.get("work_id"), entry.get("candidate_id")) != (args.kind, args.work_id, args.candidate_id):
                raise CaseError("registered_file_binding_differs_use_separate_copy")
            return {"status": "already_registered", "evidence_id": entry["evidence_id"]}
    entry = {"evidence_id": "ev_" + uuid4().hex[:12], "kind": args.kind, "path": rel, "sha256": digest,
             "source_url": source_url, "captured_at": timestamp,
             "received_at": now() if user_provided else None,
             "provenance": "user_provided" if user_provided else "agent_captured",
             "registered_at": now(), "work_id": args.work_id, "candidate_id": args.candidate_id}
    data["evidence"].append(entry)
    atomic_json(member(root, MANIFEST), data)
    return {"status": "registered", "evidence_id": entry["evidence_id"]}


def normalize_content_id(platform: str, video_id: str) -> str:
    if not isinstance(video_id, str):
        raise CaseError("invalid_video_id")
    if platform == "xiaohongshu":
        # Use the note ID only. Never accept an access-token-bearing URL here.
        if not re.fullmatch(r"[0-9A-Fa-f]{24}", video_id):
            raise CaseError("invalid_xiaohongshu_note_id")
        return video_id.lower()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", video_id):
        raise CaseError("invalid_video_id")
    return video_id


def canonical_url(platform: str, video_id: str) -> str:
    video_id = normalize_content_id(platform, video_id)
    return {"douyin": "https://www.douyin.com/video/", "kuaishou": "https://www.kuaishou.com/short-video/",
            "bilibili": "https://www.bilibili.com/video/", "xiaohongshu": "https://www.xiaohongshu.com/explore/"}[platform] + video_id


def candidate_add(root: Path, case, args):
    if not args.title.strip():
        raise CaseError("candidate_title_required")
    video_id = normalize_content_id(args.platform, args.video_id)
    url = canonical_url(args.platform, video_id)
    if args.original_id not in {w.get("work_id") for w in case.get("works", [])}:
        raise CaseError("unknown_original_work")
    if args.uploader_id and not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", args.uploader_id):
        raise CaseError("invalid_uploader_id")
    for candidate in case["candidates"]:
        if candidate["platform"] == args.platform and normalize_content_id(args.platform, candidate["video_id"]) == video_id:
            return {"status": "already_registered", "candidate_id": candidate["candidate_id"]}
    candidate_id = "cand_" + uuid4().hex[:12]
    case["candidates"].append({
        "candidate_id": candidate_id, "platform": args.platform, "video_id": video_id,
        "canonical_url": url, "uploader_id": args.uploader_id or "",
        "title": args.title, "original_work_id": args.original_id, "discovered_at": now(),
        "uploader_id_verified": False,
        "evidence_mode": "local_video",
        "browser_visual_review": None,
        "overlap_segments": [],
        "manual_review": {"status": "pending", "reviewer": "", "reviewed_at": None},
        "complaint_channel_review": {"status": "pending", "observed_path": [], "infringement_entry_verified": False},
        "submission_status": "not_submitted", "submitted_at": None, "receipt_id": None,
        "review_result": {"status": "not_checked", "verified": False, "evidence_ref": None},
        "removal_result": {"status": "not_checked", "verified": False, "evidence_ref": None},
    })
    atomic_json(member(root, "case.json"), case)
    return {"status": "registered", "candidate_id": candidate_id}


def valid_overlap(segment) -> bool:
    if (not isinstance(segment, dict) or segment.get("human_verified") is not True
            or not isinstance(segment.get("description"), str) or not segment["description"].strip()):
        return False
    for prefix in ("original", "candidate"):
        start, end = segment.get(prefix + "_start"), segment.get(prefix + "_end")
        if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            return False
        if not 0 <= start < end < float("inf"):
            return False
    return True


def browser_visual_reasons(root: Path, candidate, evidence):
    """Audit recorded browser playback evidence without claiming to inspect pixels."""
    reasons = []
    context = candidate.get("browser_visual_review")
    context = context if isinstance(context, dict) else {}
    if (context.get("candidate_url") != candidate.get("canonical_url")
            or not candidate.get("uploader_id") or context.get("uploader_id") != candidate.get("uploader_id")
            or candidate.get("uploader_id_verified") is not True):
        reasons.append("browser_visual_source_not_verified")
    if (context.get("observation_method") != "ordered_timestamped_screenshots"
            or context.get("same_source_expression_confirmed") is not True
            or not all(isinstance(context.get(key), str) and context[key].strip()
                       for key in ("download_unavailable_reason", "time_basis", "coverage_limitations"))):
        reasons.append("browser_visual_context_incomplete")
    # Timestamped stills do not establish what happened between those points.
    # A true playback flag is never sufficient to expand this mode's claim.
    if context.get("continuous_coverage_claimed") is not False:
        reasons.append("browser_visual_cannot_claim_continuous_coverage")

    def get_screenshot(evidence_id, kind):
        if not isinstance(evidence_id, str) or not evidence_id:
            return None
        matches = [entry for entry in evidence if entry.get("evidence_id") == evidence_id]
        if len(matches) != 1:
            return None
        entry = matches[0]
        kinds = (kind,) if isinstance(kind, str) else kind
        if (entry.get("kind") not in kinds or entry.get("candidate_id") != candidate.get("candidate_id")
                or entry.get("work_id") != candidate.get("original_work_id")
                or entry.get("source_url") != candidate.get("canonical_url")):
            return None
        try:
            return entry if screenshot_file(member(root, entry.get("path", ""))) else None
        except CaseError:
            return None

    page = get_screenshot(context.get("page_evidence_id"), ("candidate_page", "candidate_frame"))
    if page is None:
        reasons.append("browser_visual_page_evidence_missing_or_invalid")
    elif page.get("kind") == "candidate_frame" and context.get("page_context_verified") is not True:
        reasons.append("browser_visual_shared_page_context_not_verified")
    comparison = get_screenshot(context.get("comparison_evidence_id"), "comparison")
    if comparison is None:
        reasons.append("browser_visual_comparison_evidence_missing_or_invalid")
    elif page is not None and comparison.get("sha256") == page.get("sha256"):
        reasons.append("browser_visual_comparison_cannot_reuse_page")
    segments = candidate.get("overlap_segments")
    if not isinstance(segments, list) or len(segments) < 2:
        return reasons + ["browser_visual_two_segments_required"]
    seen_intervals, seen_shots, seen_ids = set(), set(), set()
    # A full-page timed screenshot can also establish source context. That role
    # does not add a fifth observation; only the four sampled frames are counted.
    seen_hashes = {comparison["sha256"]} if comparison is not None else set()
    candidate_intervals, candidate_times = [], set()
    for segment in segments:
        if (not valid_overlap(segment) or not isinstance(segment.get("screenshot_observation"), str)
                or not segment["screenshot_observation"].strip()
                or not isinstance(segment.get("shot_id"), str) or not segment["shot_id"].strip()):
            reasons.append("browser_visual_segment_not_reviewed")
            continue
        if segment["shot_id"] in seen_shots:
            reasons.append("browser_visual_two_distinct_shots_required")
        seen_shots.add(segment["shot_id"])
        interval = tuple(segment[key] for key in ("original_start", "original_end", "candidate_start", "candidate_end"))
        if interval in seen_intervals:
            reasons.append("browser_visual_duplicate_segment")
        seen_intervals.add(interval)
        if any(max(segment["candidate_start"], start) < min(segment["candidate_end"], end)
               for start, end in candidate_intervals):
            reasons.append("browser_visual_candidate_intervals_overlap")
        candidate_intervals.append((segment["candidate_start"], segment["candidate_end"]))
        frames = segment.get("candidate_frames")
        if not isinstance(frames, list) or len(frames) < 2:
            reasons.append("browser_visual_two_frames_per_segment_required")
            continue
        times, original_times = set(), set()
        for frame in frames:
            if not isinstance(frame, dict):
                reasons.append("browser_visual_frame_evidence_invalid")
                continue
            timestamp = frame.get("time")
            original_time = frame.get("original_time")
            image = get_screenshot(frame.get("evidence_id"), "candidate_frame")
            if (isinstance(timestamp, bool) or not isinstance(timestamp, (int, float))
                    or not segment["candidate_start"] <= timestamp <= segment["candidate_end"]
                    or isinstance(original_time, bool) or not isinstance(original_time, (int, float))
                    or not segment["original_start"] <= original_time <= segment["original_end"]
                    or image is None):
                reasons.append("browser_visual_frame_evidence_invalid")
                continue
            if (timestamp in times or timestamp in candidate_times or original_time in original_times
                    or image["evidence_id"] in seen_ids or image.get("sha256") in seen_hashes
                    or (comparison is not None and image.get("sha256") == comparison.get("sha256"))):
                reasons.append("browser_visual_reused_frame")
            times.add(timestamp)
            candidate_times.add(timestamp)
            original_times.add(original_time)
            seen_ids.add(image["evidence_id"])
            seen_hashes.add(image.get("sha256"))
    return reasons


def gate(root: Path, case, candidate_id: str):
    candidates = [c for c in case.get("candidates", []) if c.get("candidate_id") == candidate_id]
    if len(candidates) != 1:
        raise CaseError("unknown_or_duplicate_candidate_id")
    candidate = candidates[0]
    reasons = []
    warnings = []
    scope = platform_scope(case)
    if candidate.get("platform") in scope["withdrawn_platforms"]:
        reasons.append("platform_withdrawn_by_user")
    elif candidate.get("platform") not in scope["active_platforms"]:
        reasons.append("platform_not_active")
    if reasons:
        return {"candidate_id": candidate_id, "status": "blocked",
                "evidence_mode": candidate.get("evidence_mode", "local_video"),
                "network_submission_performed": False, "reasons": reasons, "warnings": []}
    try:
        expected_url = canonical_url(candidate.get("platform"), candidate.get("video_id", ""))
        if candidate.get("canonical_url") != expected_url:
            reasons.append("candidate_canonical_url_mismatch")
    except (CaseError, KeyError, TypeError):
        reasons.append("invalid_candidate_platform_or_video_id")
    if candidate.get("submission_status") != "not_submitted":
        reasons.append("submission_already_sent_unknown_or_not_cleared")
    if not intake_status(root)["complete"]:
        reasons.append("private_intake_incomplete")
    verification = verify_evidence(root)
    work_id = candidate.get("original_work_id")
    evidence = []
    # Only material bound to this claim (plus account-wide profile proof) blocks
    # this gate. Other cases' missing files are reported, never silently ignored.
    for entry, check in zip(manifest(root)["evidence"], verification["evidence"]):
        relevant = isinstance(entry, dict) and (
            entry.get("kind") == "douyin_profile"
            or entry.get("candidate_id") == candidate_id
            or (not entry.get("candidate_id") and bool(work_id) and entry.get("work_id") == work_id)
        )
        if check["status"] != "verified":
            if relevant:
                reasons.append("evidence_integrity_failed")
            else:
                warnings.append({"code": "unrelated_evidence_integrity_failed", **check})
        elif relevant:
            evidence.append(entry)
    for kind in SCREENSHOT_KINDS:
        if not any(e.get("kind") == kind and (kind == "douyin_profile" or (bool(work_id) and e.get("work_id") == work_id)) for e in evidence):
            reasons.append("missing_" + kind)
    if case.get("full_access_acknowledged") is not True:
        reasons.append("full_access_not_acknowledged")
    for platform in set(("douyin", candidate.get("platform"))):
        if platform not in PLATFORMS or case.get("authorizations", {}).get(platform, {}).get("status") != "authorized":
            reasons.append("platform_authorization_pending_or_blocked")
    rights = case.get("rights_review", {})
    if rights.get("identity_status") != "user_verified":
        reasons.append("identity_pending_or_blocked")
    if rights.get("ownership_status") != "user_verified":
        reasons.append("ownership_pending_or_blocked")
    originals = [w for w in case.get("works", []) if w.get("work_id") == candidate.get("original_work_id")]
    original = originals[0] if len(originals) == 1 else {}
    if not str(original.get("title", "")).strip() or not original.get("url") or original.get("user_confirmed") is not True:
        reasons.append("original_title_and_link_not_user_confirmed")
    if original.get("url"):
        try:
            public_url(original["url"])
        except (CaseError, TypeError, ValueError):
            reasons.append("original_link_invalid")
    if original.get("ownership_confirmed") is not True:
        reasons.append("original_ownership_not_confirmed")
    if not any(e.get("kind") == "original_video" and e.get("work_id") == original.get("work_id") for e in evidence):
        reasons.append("original_video_evidence_missing")
    evidence_mode = candidate.get("evidence_mode", "local_video")
    if evidence_mode == "local_video":
        if not any(e.get("kind") == "candidate_video" and e.get("candidate_id") == candidate_id for e in evidence):
            reasons.append("candidate_video_evidence_missing")
    elif evidence_mode == "browser_visual":
        reasons.extend(browser_visual_reasons(root, candidate, evidence))
    else:
        reasons.append("unsupported_evidence_mode")
    whitelist = case.get("whitelist", {})
    entries = whitelist.get("entries")
    if whitelist.get("user_confirmed") is not True or not whitelist.get("confirmed_at") or not isinstance(entries, list):
        reasons.append("whitelist_not_explicitly_confirmed")
    if isinstance(entries, list) and any(not isinstance(e, dict) or e.get("platform") not in PLATFORMS or not e.get("uploader_id") for e in entries):
        reasons.append("whitelist_requires_platform_and_account_id")
    uploader_id = candidate.get("uploader_id")
    if not uploader_id or candidate.get("uploader_id_verified") is not True:
        reasons.append("uploader_account_id_not_verified")
    if isinstance(entries, list) and any(isinstance(e, dict) and e.get("platform") == candidate.get("platform") and str(e.get("uploader_id")) == str(uploader_id) for e in entries):
        reasons.append("uploader_is_whitelisted")
    own_account = case.get("authorizations", {}).get(candidate.get("platform"), {}).get("account_id")
    if own_account and own_account == uploader_id:
        reasons.append("uploader_is_authorized_own_account")
    overlaps = candidate.get("overlap_segments")
    if evidence_mode == "local_video" and (not isinstance(overlaps, list) or not any(valid_overlap(segment) for segment in overlaps)):
        reasons.append("specific_continuous_overlap_not_human_verified")
    review = candidate.get("manual_review", {})
    if review.get("status") != "confirmed_copy" or review.get("reviewer") not in ("user", "agent") or not review.get("reviewed_at"):
        reasons.append("human_review_pending")
    channel = candidate.get("complaint_channel_review", {})
    channel = channel if isinstance(channel, dict) else {}
    observed_path = channel.get("observed_path")
    if (channel.get("status") != "verified" or channel.get("infringement_entry_verified") is not True
            or not isinstance(observed_path, list) or not observed_path
            or not all(isinstance(step, str) and step.strip() for step in observed_path)):
        reasons.append("complaint_channel_not_verified")
    authorization = case.get("submission_authorization", {})
    if authorization.get("user_confirmed") is not True or not authorization.get("confirmed_at") or candidate_id not in authorization.get("candidate_ids", []):
        reasons.append("target_not_explicitly_authorized_for_submission")
    return {"candidate_id": candidate_id, "status": "blocked" if reasons else "ready_for_submission_review",
            "evidence_mode": evidence_mode, "network_submission_performed": False,
            "reasons": list(dict.fromkeys(reasons)), "warnings": warnings}


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("init", "intake", "status", "report", "summary", "verify", "evidence-add", "candidate-add", "platform-withdraw", "gate"):
        command = sub.add_parser(name)
        command.add_argument("--root", required=True, help="Separate case directory")
        if name == "evidence-add":
            command.add_argument("--file", required=True, help="Relative path inside the case; never 00_private")
            command.add_argument("--kind", choices=EVIDENCE_KINDS, required=True)
            command.add_argument("--source-url", required=True)
            command.add_argument("--captured-at", help="Known capture time including timezone; defaults to now for agent capture, null for user-provided material")
            command.add_argument("--user-provided", action="store_true", help="Record user-provided provenance and receipt time; never infer an unknown capture time")
            command.add_argument("--work-id")
            command.add_argument("--candidate-id")
        elif name == "candidate-add":
            command.add_argument("--platform", choices=PLATFORMS, required=True)
            command.add_argument("--video-id", "--note-id", dest="video_id", required=True,
                                 help="Bare video ID; for xiaohongshu use a 24-hex note ID, never a URL or xsec_token")
            command.add_argument("--uploader-id")
            command.add_argument("--title", required=True)
            command.add_argument("--original-id", required=True)
        elif name == "gate":
            command.add_argument("--candidate", required=True)
        elif name == "platform-withdraw":
            command.add_argument("--platform", choices=PLATFORMS, required=True,
                                 help="Record the user's explicit cancellation of this platform; preserve history")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "init":
            result = init_case(args.root)
        else:
            root, case = load_case(args.root)
            if args.command == "intake":
                result = intake_status(root)
            elif args.command == "status":
                result = status(root, case)
            elif args.command in ("report", "summary"):
                result = report(case)
            elif args.command == "verify":
                result = verify_evidence(root)
            elif args.command == "evidence-add":
                result = evidence_add(root, case, args)
            elif args.command == "candidate-add":
                result = candidate_add(root, case, args)
            elif args.command == "platform-withdraw":
                result = platform_withdraw(root, case, args.platform)
            else:
                result = gate(root, case, args.candidate)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result.get("status") == "blocked" or result.get("ok") is False or result.get("complete") is False:
            return 2
        return 0
    except (CaseError, OSError) as exc:
        # Never print OSError filenames or private JSON contents.
        error = str(exc) if isinstance(exc, CaseError) else "local_file_operation_failed"
        print(json.dumps({"status": "error", "error": error}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
