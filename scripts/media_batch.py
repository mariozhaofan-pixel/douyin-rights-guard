#!/usr/bin/env python3
"""Resume local candidate media preparation; never review or submit complaints."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import case_manager as cm
import media_probe as mp

STATE = "08_logs/media_batch.json"


def quiet_call(function, args):
    with contextlib.redirect_stdout(io.StringIO()):
        return function(args)


def verified_media(root, evidence, checks, kind, key, value):
    entries = [entry for entry, check in zip(evidence, checks)
               if check["status"] == "verified" and entry.get("kind") == kind
               and entry.get(key) == value]
    # Conflicting versions require an explicit choice in the evidence ledger.
    files = {entry["sha256"]: cm.member(root, entry["path"]) for entry in entries}
    return next(iter(files.values())) if len(files) == 1 else None


def fail_category(error):
    match = re.search(r"\[(?:preflight|download)/([a-z_]+)\]", str(error))
    return match.group(1) if match else "local_or_media_failure"


def load_state(root):
    path = cm.member(root, STATE)
    if not path.exists():
        return {"schema_version": 1, "items": {}, "platform_stops": {}, "cookie_streak": {}}
    data = cm.read_json(path)
    if (not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("items"), dict)
            or not isinstance(data.get("platform_stops"), dict)):
        raise cm.CaseError("invalid_media_batch_state")
    data.setdefault("cookie_streak", {})
    if not isinstance(data["cookie_streak"], dict) or any(type(n) is not int or n < 0 for n in data["cookie_streak"].values()):
        raise cm.CaseError("invalid_media_batch_state")
    return data


def saved_download(root, item, source_url):
    record = item.get("download", {})
    if not isinstance(record, dict) or not record.get("file") or record.get("source_url") != source_url:
        return None
    path = cm.member(root, record["file"])
    if cm.contained(path, (root / "00_private").resolve()):
        raise cm.CaseError("private_media_path_forbidden")
    if path.is_file() and cm.sha256(path) == record.get("sha256"):
        return path
    return None


def comparison_valid(root, record, fingerprint):
    if record.get("fingerprint") != fingerprint:
        return False
    files = record.get("files", {})
    if not files:
        return False
    for relative, digest in files.items():
        path = cm.member(root, relative)
        if not path.is_file() or cm.sha256(path) != digest:
            return False
    return True


def fingerprint_for(original, candidate, args):
    return {"original_sha256": cm.sha256(original), "candidate_sha256": cm.sha256(candidate),
            "interval": args.interval, "mirror": args.mirror, "max_frames": args.max_frames,
            "comparison_algorithm_version": mp.COMPARISON_ALGORITHM_VERSION}


def run_batch(args):
    root, case = cm.load_case(args.root)
    scope = cm.platform_scope(case)
    state = load_state(root)
    for platform in args.resume_platform:
        state["platform_stops"].pop(platform, None)
        state["cookie_streak"].pop(platform, None)
    candidates = case.get("candidates", [])
    selected = set(args.candidate or [])
    if selected - {c.get("candidate_id") for c in candidates}:
        raise cm.CaseError("unknown_candidate_id")
    evidence = cm.manifest(root)["evidence"]
    checks = cm.verify_evidence(root)["evidence"]
    results, attempts = [], 0
    cookie_streak = Counter(state["cookie_streak"])
    priority = {"douyin": 0, "bilibili": 1, "kuaishou": 2}
    for candidate in sorted(candidates, key=lambda c: priority.get(c.get("platform"), 3)):
        cid, platform = candidate.get("candidate_id"), candidate.get("platform")
        if selected and cid not in selected:
            continue
        if not isinstance(cid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", cid):
            raise cm.CaseError("invalid_candidate_id")
        result = {"candidate_id": cid, "platform": platform}
        item = state["items"].setdefault(cid, {})
        if platform not in scope["active_platforms"]:
            result["status"] = "inactive_platform"
        elif candidate.get("submission_status") != "not_submitted":
            result["status"] = "submission_state_requires_followup"
        elif (candidate.get("manual_review", {}).get("status") in ("not_copy", "unrelated_or_shared_material", "excluded")
                and candidate.get("manual_review", {}).get("reviewer") in ("agent", "user")
                and candidate.get("manual_review", {}).get("reviewed_at")):
            result["status"] = "excluded_by_existing_review"
        else:
            uploader = candidate.get("uploader_id")
            own = cm.known_own_account_ids(case, platform)
            reporter_id = cm.effective_account(case, platform, "reporter").get("account_id")
            own.add(reporter_id)
            allowed = case.get("whitelist", {}).get("entries", [])
            if uploader and (uploader in own or any(e.get("platform") == platform and e.get("uploader_id") == uploader for e in allowed)):
                result["status"] = "own_or_whitelisted_account"
            else:
                url = candidate.get("canonical_url", "")
                if url != cm.canonical_url(platform, candidate.get("video_id", "")):
                    raise cm.CaseError("candidate_canonical_url_mismatch")
                original = verified_media(root, evidence, checks, "original_video", "work_id", candidate.get("original_work_id"))
                media = verified_media(root, evidence, checks, "candidate_video", "candidate_id", cid)
                media = media or saved_download(root, item, url)
                if not original:
                    result["status"] = "original_media_missing_or_ambiguous"
                elif media and comparison_valid(root, item.get("comparison", {}), fingerprint_for(original, media, args)):
                    result.update(status="reused_comparison", report=item["comparison"]["report"])
                elif not media and not args.download:
                    result["status"] = "candidate_media_needed"
                elif not media and platform in state["platform_stops"]:
                    result.update(status="platform_download_stopped", reason=state["platform_stops"][platform]["reason"])
                elif not media and item.get("last_download_failed") and not args.retry_failed:
                    result["status"] = "previous_download_failed_review_before_retry"
                elif attempts >= args.limit:
                    result["status"] = "deferred_by_limit"
                else:
                    attempts += 1
                    try:
                        if not media:
                            number = int(item.get("download_attempts", 0)) + 1
                            item["download_attempts"] = number
                            folder = cm.member(root, f"04_candidates/media-batch/{cid}/attempt-{number:04d}")
                            # Persist intent before network work; interrupted attempts are never blindly replayed.
                            item["last_download_failed"] = True
                            cm.atomic_json(cm.member(root, STATE), state)
                            backend = "kuaishou-public" if platform == "kuaishou" and args.kuaishou_public else "yt-dlp"
                            quiet_call(mp.download, SimpleNamespace(url=url, out=str(folder), cookies=None, backend=backend))
                            receipt = cm.read_json(folder / "download-receipt.json")
                            media = cm.member(root, str((folder / receipt["file"]).relative_to(root)))
                            item["download"] = {"file": media.relative_to(root).as_posix(), "sha256": cm.sha256(media), "source_url": url, "source_verification": "pending"}
                            item["last_download_failed"] = False
                            cookie_streak[platform] = 0
                        fingerprint = fingerprint_for(original, media, args)
                        previous = item.get("comparison", {})
                        if comparison_valid(root, previous, fingerprint):
                            result.update(status="reused_comparison", report=previous["report"])
                        else:
                            number = int(item.get("comparison_attempts", 0)) + 1
                            item["comparison_attempts"] = number
                            folder = cm.member(root, f"05_comparisons/media-batch/{cid}/attempt-{number:04d}")
                            cm.atomic_json(cm.member(root, STATE), state)
                            quiet_call(mp.compare, SimpleNamespace(original=str(original), candidate=str(media), out=str(folder),
                                       interval=args.interval, mirror=args.mirror, max_frames=args.max_frames, min_run=3))
                            report = (folder / "comparison.json").relative_to(root).as_posix()
                            item["comparison"] = {"fingerprint": fingerprint, "report": report,
                                "files": {p.relative_to(root).as_posix(): cm.sha256(p) for p in folder.rglob("*") if p.is_file()}}
                            result.update(status="prepared_human_review_required", report=report)
                    except mp.MediaError as error:
                        category = fail_category(error)
                        if platform == "kuaishou" and args.kuaishou_public and item.get("last_download_failed"):
                            category = "public_backend_stopped"
                        result.update(status="preparation_failed", reason=category)
                        cookie_streak[platform] = cookie_streak[platform] + 1 if category == "cookie_required" else 0
                        if category in ("interactive_verification", "access_rejected", "login_required", "public_backend_stopped") or cookie_streak[platform] >= 2:
                            state["platform_stops"][platform] = {"reason": category, "stopped_at": cm.now()}
                    except (OSError, ValueError, KeyError, TypeError):
                        result.update(status="preparation_failed", reason="invalid_input_or_local_failure")
        item["latest"] = result
        item["updated_at"] = cm.now()
        results.append(result)
        state["cookie_streak"] = dict(cookie_streak)
        cm.atomic_json(cm.member(root, STATE), state)
    summary = {"network_submission_performed": False, "case_review_or_authorization_changed": False,
               "counts": dict(Counter(r["status"] for r in results)), "results": results,
               "platform_stops": state["platform_stops"], "notice": mp.NOTICE}
    cm.atomic_json(cm.member(root, "08_logs/media_batch_latest.json"), summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--candidate", action="append", default=[])
    parser.add_argument("--download", action="store_true", help="Try anonymous official-page download when no local candidate file exists")
    parser.add_argument("--kuaishou-public", action="store_true", help="Explicitly select public HTML backend for Kuaishou")
    parser.add_argument("--limit", type=int, default=20, help="Maximum candidate jobs per run (1-100)")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--max-frames", type=int, default=720)
    parser.add_argument("--mirror", action="store_true")
    parser.add_argument("--retry-failed", action="store_true", help="Retry a failed download once after its cause has changed")
    parser.add_argument("--resume-platform", action="append", choices=cm.DEFAULT_ACTIVE_PLATFORMS, default=[])
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 100 or not 0.1 <= args.interval <= 21600 or not 8 <= args.max_frames <= 2000:
        parser.error("limit=1..100, interval=0.1..21600, max-frames=8..2000")
    try:
        print(json.dumps(run_batch(args), ensure_ascii=False, indent=2))
        return 0
    except (cm.CaseError, OSError, ValueError, TypeError, KeyError):
        print(json.dumps({"status": "stopped", "reason": "invalid_case_or_batch_input"}))
        return 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
