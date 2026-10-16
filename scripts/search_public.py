#!/usr/bin/env python3
"""Bounded public Bilibili discovery through yt-dlp; no login or complaints."""
from __future__ import annotations

import argparse
import html
import importlib.util
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit, urlunsplit

from media_probe import MediaError, run, stamp, validate_url, ytdlp_error

SCOPE_NOTE = "仅为前 N 条公开索引线索；不等同登录搜索页第一页，不证明完整覆盖或侵权。"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def validate_query(query):
    if not isinstance(query, str) or not query.strip() or len(query) > 200:
        raise MediaError("检索词须为 1–200 个字符。")
    if any(ord(char) < 32 for char in query) or re.search(r"https?://|cookie\s*[:=]|sessdata\s*[:=]", query, re.I):
        raise MediaError("只输入作品名等公开检索词，不输入链接、Cookie 或控制字符。")
    return query.strip()


def search_command(query, limit):
    query = validate_query(query)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise MediaError("检索数量须为 1–100。")
    if importlib.util.find_spec("yt_dlp") is None:
        raise MediaError("缺少 yt-dlp；请安装 requirements-media.txt。")
    # This is a search playlist. Do not reuse media_probe's single-video flags.
    return [sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-plugin-dirs",
            "--no-cookies-from-browser", "--no-cookies", "--no-cache-dir",
            "--flat-playlist", "--skip-download", "--dump-single-json",
            "--no-progress", "--no-warnings", "--socket-timeout", "15",
            "--retries", "0", "--extractor-retries", "0", "--fragment-retries", "0",
            "--", f"bilisearch{limit}:{query}"]


def public_text(value):
    """Optional public display text, never raw HTML, URLs or credential strings."""
    if not isinstance(value, str):
        return None
    text = html.unescape(re.sub(r"<[^>]*>", "", value)).strip()
    if (not text or any(ord(char) < 32 for char in text)
            or re.search(r"https?://|cookie\s*[:=]|sessdata\s*[:=]", text, re.I)):
        return None
    return text[:300]


def candidate(entry):
    if not isinstance(entry, dict):
        return None
    url = entry.get("url") or entry.get("webpage_url")
    if not isinstance(url, str):
        return None
    try:
        parsed = urlsplit(url)
        # Older arcurl responses use http; only normalize an official work page.
        if (parsed.scheme not in ("http", "https") or parsed.hostname not in ("www.bilibili.com", "bilibili.com")
                or parsed.username or parsed.password or parsed.port not in (None, 80, 443)):
            return None
        platform, safe_url = validate_url(urlunsplit(("https", "www.bilibili.com", parsed.path, "", "")))
        if platform != "bilibili":
            return None
        work_id = parsed.path.strip("/").split("/")[-1]
        if not re.fullmatch(r"BV[A-Za-z0-9]{10}|av[0-9]+", work_id):
            return None
    except (MediaError, ValueError):
        return None
    result = {"platform": "bilibili", "url": safe_url.rstrip("/"), "work_id": work_id}
    for source, target in (("title", "title"), ("uploader", "uploader")):
        value = public_text(entry.get(source))
        if value:
            result[target] = value
    account_id = entry.get("uploader_id")
    if isinstance(account_id, (int, str)) and not isinstance(account_id, bool) and re.fullmatch(r"[0-9]+", str(account_id)):
        result["uploader_id"] = str(account_id)
        result["account_url"] = "https://space.bilibili.com/" + str(account_id)
    return result


def search(query, limit=20):
    command = search_command(query, limit)
    report = {"schema_version": 1, "platform": "bilibili", "query": validate_query(query),
              "source_kind": "public_index", "batch_kind": "links", "backend": "yt-dlp-bilisearch",
              "requested_limit": limit, "created_at_utc": stamp(), "cookie_used": False,
              "web_first_page_complete": False, "scope_note": SCOPE_NOTE,
              "status": "failed", "results": []}
    try:
        response = run(command, timeout=90, check=False)
        if response.returncode:
            raise ytdlp_error(response, "search")
        if len(response.stdout) > MAX_RESPONSE_BYTES:
            raise MediaError("检索响应超过大小限制，已停止。")
        try:
            raw = json.loads(response.stdout)
        except (ValueError, UnicodeError):
            raise MediaError("检索工具未返回有效 JSON；未记录原始响应。") from None
        entries = raw.get("entries") if isinstance(raw, dict) else None
        if not isinstance(entries, list):
            raise MediaError("检索工具未返回作品列表；未记录原始响应。")
        seen = set()
        skipped = 0
        for entry in entries[:limit]:
            row = candidate(entry)
            if row is None or row["url"] in seen:
                skipped += 1
                continue
            seen.add(row["url"])
            report["results"].append(row)
        report.update({"status": "indexed", "raw_entry_count": len(entries),
                       "skipped_count": skipped, "candidate_count": len(report["results"])})
        if not report["results"]:
            report["scope_note"] += " 本次没有得到有效候选，不能据此认定不存在搬运。"
    except MediaError as error:
        report["reason"] = str(error)
        report["next_step"] = "停止相同条件重试；保留失败状态，改用用户提供的候选链接或一次正常页面检索。"
    return report


def write_report(path, report):
    output = Path(path).expanduser().resolve()
    skill_root = Path(__file__).resolve().parents[1]
    if output == skill_root or skill_root in output.parents:
        raise MediaError("检索结果应写入私密案件目录，不得写入 skill 源码目录。")
    if output.exists():
        raise MediaError("输出文件已存在；请选择新文件，避免覆盖既有检索证据。")
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive create also prevents overwriting a file created after the check.
    try:
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except OSError:
        raise MediaError("无法保存检索回执。") from None


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", required=True, help="公开作品名或检索词")
    parser.add_argument("--limit", type=int, default=20, help="最多线索数量，1–100；默认20")
    parser.add_argument("--out", required=True, help="新建的私密 JSON 回执路径")
    args = parser.parse_args(argv)
    try:
        # Fail before any network request if the evidence destination is occupied.
        output = Path(args.out).expanduser().resolve()
        skill_root = Path(__file__).resolve().parents[1]
        if output.exists() or output == skill_root or skill_root in output.parents:
            raise MediaError("输出路径已存在或位于 skill 内；请使用私密案件目录中的新文件。")
        report = search(args.query, args.limit)
        write_report(output, report)
        print(json.dumps({"status": report["status"], "candidate_count": len(report["results"]),
                          "scope_note": report["scope_note"], **({"reason": report["reason"]} if "reason" in report else {})}, ensure_ascii=False))
        return 0 if report["status"] == "indexed" else 1
    except (MediaError, OSError) as error:
        print(str(error) if isinstance(error, MediaError) else "本地路径或文件操作失败。", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
