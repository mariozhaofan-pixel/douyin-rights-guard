#!/usr/bin/env python3
"""Local media acquisition and review aids; never an infringement classifier."""
from __future__ import annotations

import argparse
import hashlib
import html
import importlib.util
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

NOTICE = "仅为抽样画面相似候选，不是侵权结论；人物、场景、镜头顺序和授权情况须逐段人工复核。未命中不代表没有搬运。"
COMPARISON_ALGORITHM_VERSION = "shared-interval-v2"
ROOTS = {"douyin": ("douyin.com", "iesdouyin.com"), "bilibili": ("bilibili.com", "b23.tv"), "kuaishou": ("kuaishou.com",)}
KS_CDN = ("kuaishou.com", "gifshow.com", "kwaicdn.com", "yximgs.com", "ksapisrv.com")


class MediaError(Exception):
    """Only fixed, safe messages should reach stdout/stderr."""


def stamp():
    return datetime.now(timezone.utc).isoformat()


def dump(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def on_domain(host, roots):
    return any(host == root or host.endswith("." + root) for root in roots)


def validate_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise MediaError("只接受 HTTPS 官方单条作品页链接，不接受凭据、直链或自定义端口。")
    host = (parsed.hostname or "").lower()
    if on_domain(host, ("xiaohongshu.com", "xhslink.com")):
        raise MediaError("小红书不在当前媒体提取范围内；历史本地文件保留，可继续离线复核。")
    platform = next((key for key, roots in ROOTS.items() if on_domain(host, roots)), None)
    patterns = {
        "douyin": r"/(?:video|share/video)/\d+/?",
        "bilibili": r"/video/(?:BV[A-Za-z0-9]+|av\d+)/?",
        "kuaishou": r"/(?:short-video|f|photo)/[A-Za-z0-9_-]+/?",
    }
    short = host in ("v.douyin.com", "v.kuaishou.com", "b23.tv")
    valid_path = re.fullmatch(r"/[A-Za-z0-9_-]+/?", parsed.path) if short else re.fullmatch(patterns.get(platform, r"(?!)"), parsed.path)
    if not platform or not valid_path:
        raise MediaError("链接必须是抖音、快手或哔哩哔哩的单条作品页；主页、搜索页、合集及未支持短链不支持。")
    # Query strings may include temporary share tokens. Keep them in memory only.
    return platform, urllib.parse.urlunsplit(("https", host, parsed.path, "", ""))


def run(args, timeout=180, check=True):
    try:
        result = subprocess.run(args, capture_output=True, timeout=timeout, shell=False)
    except (OSError, subprocess.TimeoutExpired):
        raise MediaError("本地工具不可用或执行超时；未记录原始命令或服务端响应。") from None
    if check and result.returncode:
        raise MediaError("本地媒体工具执行失败；请检查文件有效性和工具安装。")
    return result


def binary(name):
    executable = shutil.which(name)
    if not executable:
        raise MediaError(f"缺少 {name}，请安装后加入 PATH。")
    return executable


def doctor(_args):
    result = {"python": sys.version.split()[0], "checked_at_utc": stamp(), "download_platforms": list(ROOTS), "notice": NOTICE}
    for name in ("ffmpeg", "ffprobe"):
        executable = shutil.which(name)
        result[name] = {"available": bool(executable)}
        if executable:
            output = run([executable, "-version"], timeout=15, check=False)
            result[name]["version"] = output.stdout.decode("utf-8", "replace").splitlines()[0]
    for name in ("PIL", "yt_dlp"):
        available = importlib.util.find_spec(name) is not None
        result[name] = {"available": available}
        if available:
            if name == "PIL":
                import PIL
                result[name]["version"] = PIL.__version__
            else:
                result[name]["version"] = run([sys.executable, "-m", "yt_dlp", "--ignore-config", "--version"], timeout=15).stdout.decode().strip()
    result["kuaishou_public"] = {"available": True, "status": "保守公开分享页适配；需逐条验证，未承诺线上可用"}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def probe(path):
    result = run([binary("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries", "format=duration:stream=width,height,codec_name,start_time,duration,avg_frame_rate", "-of", "json", str(path)])
    try:
        info = json.loads(result.stdout)
        stream = info["streams"][0]
        duration = float(stream.get("duration") or info["format"]["duration"])
        if not math.isfinite(duration) or not 0 < duration <= 21600:
            raise ValueError()
        return {"duration_seconds": duration, "width": stream["width"], "height": stream["height"], "codec": stream["codec_name"], "average_frame_rate": stream.get("avg_frame_rate")}
    except (ValueError, KeyError, IndexError, TypeError):
        raise MediaError("无法读取有限时长的视频流，或视频超过六小时；请使用有效的本地作品片段。") from None


def safe_cookie_copy(source, platform, target):
    """Never mutate the user's source cookie file; never read browser profiles."""
    if platform not in ROOTS:
        raise MediaError("该平台不在当前媒体提取范围内，未读取 Cookie 文件。")
    try:
        body = Path(source).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        raise MediaError("无法读取本地 Cookie 文件。") from None
    lines = body.splitlines()
    if not lines or lines[0] not in ("# Netscape HTTP Cookie File", "# HTTP Cookie File"):
        raise MediaError("Cookie 文件必须是 Netscape 格式；不要把 Cookie 内容粘贴到聊天。")
    found = False
    for line in lines[1:]:
        if not line or (line.startswith("#") and not line.startswith("#HttpOnly_")):
            continue
        fields = line.removeprefix("#HttpOnly_").split("\t")
        if len(fields) != 7 or not on_domain(fields[0].lstrip(".").lower(), ROOTS[platform]):
            raise MediaError("Cookie 文件含非目标平台域或格式错误；请仅提供本次平台的专用授权文件。")
        found = True
    if not found:
        raise MediaError("Cookie 文件没有目标平台记录。")
    target.write_text(body, encoding="utf-8")
    try:
        target.chmod(0o600)
    except OSError:
        pass


def ytdlp_base():
    if importlib.util.find_spec("yt_dlp") is None:
        raise MediaError("缺少 yt-dlp；请安装 requirements-media.txt，或使用平台允许的手动保存文件。")
    return [sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-plugin-dirs", "--no-cookies-from-browser", "--no-cache-dir", "--no-playlist", "--playlist-items", "1", "--no-write-info-json", "--no-write-thumbnail", "--no-write-subs", "--no-write-auto-subs", "--no-progress", "--no-warnings", "--socket-timeout", "20", "--retries", "0", "--fragment-retries", "0"]


def ytdlp_error(result, stage):
    """Classify stderr in memory; never return its text or embedded URLs."""
    message = result.stderr.decode("utf-8", "replace")
    categories = (
        (r"fresh cookies|cookies.{0,60}(?:needed|required)", "cookie_required", "提取器要求有效的平台 Cookie；可使用已明确授权的专域本地文件，或沿浏览器正常保存路径取证。"),
        (r"captcha|verification|验证码", "interactive_verification", "平台要求验证；停止工具访问，由用户在浏览器完成正常交互。"),
        (r"HTTP Error (?:403|412|429)|Forbidden|Too Many Requests", "access_rejected", "平台拒绝或限制提取器访问；停止，不自动切换代理、身份或解析服务。"),
        (r"log ?in|sign ?in|login required", "login_required", "平台要求登录；请使用已授权的正常浏览器会话。"),
        (r"CERTIFICATE_VERIFY_FAILED|certificate verify failed", "tls_certificate", "TLS 证书校验失败；检查本地网络和证书配置，不关闭证书校验。"),
        (r"ProxyError|Cannot connect to proxy|proxy error|Unable to connect to proxy", "proxy_connection", "本地代理连接失败；需先检查已配置网络。"),
        (r"ConnectionRefusedError|ConnectionResetError|Network is unreachable|WinError 10013|WinError 10061|Connection aborted|timed out", "network_connection", "网络连接失败或超时；未保存原始服务端响应。"),
        (r"Unsupported URL|Unable to extract|Failed to parse|JSONDecodeError", "extractor_unsupported_or_changed", "提取器不支持此链接或页面结构已变化；使用正常浏览器核验保存。"),
        (r"No video formats found|Requested format is not available", "format_unavailable", "提取器未找到可用视频格式；使用正常浏览器核验保存。"),
        (r"No module named|ModuleNotFoundError|no such option|unrecognized arguments", "local_dependency", "本地 yt-dlp 安装或参数版本不兼容，请先运行 doctor。"),
    )
    for pattern, category, explanation in categories:
        if re.search(pattern, message, re.I):
            return MediaError(f"[{stage}/{category}] {explanation}")
    if result.returncode == 101:
        return MediaError(f"[{stage}/download_limit] 提取器提前触发单条下载上限；预检阶段不应携带该上限参数。")
    return MediaError(f"[{stage}/unclassified] 提取器未完成；原始响应未输出，请保留此类别并通过正常浏览器核验。")


def download_ytdlp(url, folder, cookies, platform):
    base = ytdlp_base()
    with tempfile.TemporaryDirectory(prefix="rights-media-auth-") as temporary:
        if cookies:
            cookie_copy = Path(temporary) / "session.txt"
            safe_cookie_copy(cookies, platform, cookie_copy)
            base += ["--cookies", str(cookie_copy)]
        else:
            base += ["--no-cookies"]
        # JSON is captured only in RAM to reject playlist and live-stream results.
        # --max-downloads counts simulated results too; putting it here can exit
        # 101 before --dump-single-json emits anything. It belongs below only.
        preflight = run(base + ["--skip-download", "--dump-single-json", "--", url], timeout=90, check=False)
        if preflight.returncode:
            raise ytdlp_error(preflight, "preflight")
        try:
            info = json.loads(preflight.stdout)
        except (ValueError, UnicodeError):
            raise MediaError("提取器未返回有效的单作品结果。") from None
        if info.get("_type") in ("playlist", "multi_video") or "entries" in info or info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
            raise MediaError("拒绝播放列表、多视频合集和直播；请提供单条已发布视频链接。")
        del info, preflight
        command = base + ["--max-downloads", "1", "--no-overwrites", "--restrict-filenames", "--format", "bv*+ba/b", "--merge-output-format", "mp4", "--output", str(folder / "video.%(ext)s"), "--", url]
        result = run(command, timeout=900, check=False)
        files = [p for p in folder.glob("video.*") if p.suffix.lower() in (".mp4", ".webm", ".mkv", ".mov", ".flv") and not re.search(r"\.f\d+\.", p.name)]
        if result.returncode not in (0, 101):
            raise ytdlp_error(result, "download")
        if len(files) != 1:
            raise MediaError("[download/incomplete_file] 未生成唯一完整媒体文件；勿把分片或中间文件当作证据。")
        return files[0]


class RestrictedRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, roots):
        self.roots = roots

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_public_host(newurl, self.roots)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def check_public_host(url, roots):
    value = urllib.parse.urlsplit(url)
    if value.scheme != "https" or value.username or value.password or value.port not in (None, 443) or not on_domain(value.hostname or "", roots):
        raise MediaError("公开分享页或媒体跳转到未允许的地址，已停止。")


def public_open(url, roots):
    check_public_host(url, roots)
    opener = urllib.request.build_opener(RestrictedRedirect(roots))
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "zh-CN"})
    try:
        return opener.open(request, timeout=20)
    except (urllib.error.URLError, TimeoutError, OSError):
        raise MediaError("公开分享页或媒体访问失败；停止，不自动切换代理、Cookie 或挑战解法。") from None


def extract_public_mp4(page):
    """Conservative independent SSR adapter inspired by MIT video-downloader."""
    if re.search(r"captcha|验证码|安全验证|访问受限|异常访问|请先登录", page, flags=re.I):
        raise MediaError("页面显示验证、登录或访问限制；停止公开页面提取，请用户完成正常平台交互。")
    # Public page metadata is preferred. Do not execute page JavaScript.
    from html.parser import HTMLParser

    class Parser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.urls = []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "meta" and attrs.get("property", "").lower() in ("og:video", "og:video:url", "og:video:secure_url"):
                self.urls.append(attrs.get("content", ""))
            elif tag == "video" and attrs.get("src"):
                self.urls.append(attrs["src"])

    parser = Parser()
    parser.feed(page)
    candidates = parser.urls
    if not candidates:
        normalized = page.replace("\\/", "/").replace("\\u0026", "&")
        candidates = re.findall(r"https://[^\s\"'<>]+?\.mp4(?:\?[^\s\"'<>\\]*)?", normalized)
    candidates = list(dict.fromkeys(html.unescape(value) for value in candidates if ".mp4" in value))
    if len(candidates) != 1:
        raise MediaError("分享页没有唯一明确的 MP4；可能是推荐列表、登录页或结构变化。请手动核验保存。")
    check_public_host(candidates[0], KS_CDN)
    return candidates[0]


def download_kuaishou_public(url, folder):
    with public_open(url, ROOTS["kuaishou"]) as response:
        body = response.read(4 * 1024 * 1024 + 1)
        if len(body) > 4 * 1024 * 1024:
            raise MediaError("分享页超过大小限制，已停止。")
    direct = extract_public_mp4(body.decode("utf-8", "replace"))
    target = folder / "video.mp4"
    temporary = folder / "video.mp4.part"
    with public_open(direct, KS_CDN) as response, temporary.open("xb") as stream:
        total = 0
        while block := response.read(1024 * 1024):
            total += len(block)
            if total > 1024 * 1024 * 1024:
                raise MediaError("媒体超过 1 GiB 限制，已停止。")
            stream.write(block)
    temporary.rename(target)
    return target


def download(args):
    platform, safe_url = validate_url(args.url)
    folder = Path(args.out).expanduser().resolve()
    if folder.exists() and any(folder.iterdir()):
        raise MediaError("下载输出目录必须为空，防止覆盖或混入旧证据。")
    folder.mkdir(parents=True, exist_ok=True)
    receipt = {"schema_version": 1, "created_at_utc": stamp(), "platform": platform, "source_page_without_query": safe_url, "backend": args.backend, "cookie_used": bool(args.cookies), "status": "pending", "notice": NOTICE}
    try:
        if args.backend == "kuaishou-public":
            if platform != "kuaishou" or args.cookies:
                raise MediaError("kuaishou-public 仅用于快手公开页且不接受 Cookie；不得用其自动绕过受限下载。")
            target = download_kuaishou_public(args.url, folder)
        else:
            target = download_ytdlp(args.url, folder, args.cookies, platform)
        receipt.update({"status": "downloaded_needs_source_verification", "file": target.name, "sha256": sha256(target), "media": probe(target)})
    except (MediaError, OSError, ValueError) as error:
        if not isinstance(error, MediaError):
            error = MediaError("下载中断或数据异常，已停止；未保存服务端响应或签名直链。")
        receipt.update({"status": "failed", "reason": str(error)})
        dump(folder / "download-receipt.json", receipt)
        raise
    dump(folder / "download-receipt.json", receipt)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


def pillow():
    try:
        from PIL import Image, ImageChops, ImageDraw, ImageOps, ImageStat
    except ImportError:
        raise MediaError("缺少 Pillow，请安装 requirements-media.txt。") from None
    return Image, ImageChops, ImageDraw, ImageOps, ImageStat


def descriptor(path, mirrored=False):
    Image, _, _, ImageOps, ImageStat = pillow()
    with Image.open(path) as opened:
        image = opened.convert("RGB")
    if mirrored:
        image = ImageOps.mirror(image)
    gray = image.convert("L")
    tiny = gray.resize((16, 16), Image.Resampling.LANCZOS)
    pixels = tuple(tiny.tobytes())
    gradients = list(gray.resize((17, 16), Image.Resampling.LANCZOS).tobytes())
    hashed = 0
    for y in range(16):
        for x in range(16):
            hashed = (hashed << 1) | (gradients[y * 17 + x] > gradients[y * 17 + x + 1])
    stats = ImageStat.Stat(gray)
    mean, stddev = stats.mean[0], stats.stddev[0]
    entropy = gray.entropy()
    low_information = mean < 12 or mean > 245 or stddev < 8 or entropy < 2.5
    return {"hash": hashed, "pixels": pixels, "mean": round(mean, 3), "stddev": round(stddev, 3), "entropy": round(entropy, 3), "low_information": low_information}


def mae(a, b):
    return sum(abs(x - y) for x, y in zip(a, b)) / (len(a) * 255)


def sample(path, folder, interval, max_frames, *, media_info=None, minimum_interval=None):
    info = media_info if media_info is not None else probe(path)
    # The effective sampling spacing is explicit; original frame rate is separate.
    spacing = max(interval, info["duration_seconds"] / (max_frames - 1), minimum_interval or 0)
    folder.mkdir()
    expression = f"setpts=PTS-STARTPTS,select='isnan(prev_selected_t)+gte(t-prev_selected_t,{spacing:.9f})',scale=320:-2,showinfo"
    result = run([binary("ffmpeg"), "-hide_banner", "-nostdin", "-i", str(path), "-map", "0:v:0", "-an", "-vf", expression, "-fps_mode", "vfr", "-frames:v", str(max_frames), str(folder / "frame_%05d.jpg")], timeout=600)
    times = [float(value) for value in re.findall(rb"\bn:\s*\d+\s+pts:\s*[-\d]+\s+pts_time:([-+\d.eE]+)", result.stderr)]
    frames = sorted(folder.glob("frame_*.jpg"))
    if len(times) != len(frames) or not frames:
        raise MediaError("实际帧时间与截图数量不一致，已停止，不能使用推算时间戳充当证据。")
    records = []
    for index, (frame, time) in enumerate(zip(frames, times)):
        records.append({"index": index, "time_seconds": time, "file": frame, **descriptor(frame)})
    for index, record in enumerate(records):
        neighbors = records[max(0, index - 1):index] + records[index + 1:index + 2]
        record["static"] = not neighbors or all(mae(record["pixels"], neighbor["pixels"]) < 0.008 for neighbor in neighbors)
        record["eligible"] = not (record["low_information"] or record["static"])
    return {"file_name": path.name, "sha256": sha256(path), **info, "requested_interval_seconds": interval, "effective_minimum_interval_seconds": spacing, "timestamp_basis": "decoded presentation time relative to first displayed video frame (setpts=PTS-STARTPTS), seconds", "sample_count": len(records), "first_sample_seconds": times[0], "last_sample_seconds": times[-1]}, records


def similarity(a, b, threshold=0.10):
    distance = (a["hash"] ^ b["hash"]).bit_count() / 256
    if distance > threshold:
        return None
    difference = mae(a["pixels"], b["pixels"])
    if difference > 0.12:
        return None
    return {"dhash_distance_fraction": round(distance, 5), "gray_mean_absolute_difference": round(difference, 5)}


def find_sequences(left, right, minimum, mirrored=False):
    if mirrored:
        right_features = [{**frame, **descriptor(frame["file"], True)} for frame in right]
    else:
        right_features = right
    # Diagonal contiguous runs enforce monotonic, adjacent sampling order.
    previous = {}
    finished = []
    for i, a in enumerate(left):
        current = {}
        for j, b in enumerate(right_features):
            if not a["eligible"] or not b["eligible"]:
                continue
            score = similarity(a, b)
            if score is None:
                continue
            chain = previous.get(j - 1, []) + [{"original_index": i, "candidate_index": j, "original_seconds": a["time_seconds"], "candidate_seconds": b["time_seconds"], **score}]
            current[j] = chain
        for j, chain in previous.items():
            if j + 1 not in current and len(chain) >= minimum:
                finished.append(chain)
        previous = current
    finished.extend(chain for chain in previous.values() if len(chain) >= minimum)
    sequences = []
    for chain in finished:
        first, last = chain[0], chain[-1]
        # A time-consistency condition also prevents accidental cross-rate claims.
        original_span = last["original_seconds"] - first["original_seconds"]
        candidate_span = last["candidate_seconds"] - first["candidate_seconds"]
        if not original_span or not candidate_span or abs(original_span - candidate_span) > max(original_span, candidate_span) * 0.15:
            continue
        sequences.append({"classification": "ordered_sample_similarity_candidate", "candidate_mirrored_for_comparison": mirrored, "sample_count": len(chain), "original_start_seconds": first["original_seconds"], "original_end_sample_seconds": last["original_seconds"], "candidate_start_seconds": first["candidate_seconds"], "candidate_end_sample_seconds": last["candidate_seconds"], "sampled_span_seconds": min(original_span, candidate_span), "pairs": chain})
    return sequences


def contact_sheet(left, right, sequences, path):
    Image, _, ImageDraw, ImageOps, _ = pillow()
    rows = []
    for sequence in sequences[:4]:
        pairs = sequence["pairs"]
        for index in sorted(set([0, len(pairs) // 2, len(pairs) - 1])):
            rows.append((pairs[index], sequence["candidate_mirrored_for_comparison"]))
    if not rows:
        for index in range(min(6, len(left), len(right))):
            rows.append(({"original_index": index, "candidate_index": index, "original_seconds": left[index]["time_seconds"], "candidate_seconds": right[index]["time_seconds"]}, False))
    canvas = Image.new("RGB", (680, 52 + 210 * len(rows)), "#20242b")
    draw = ImageDraw.Draw(canvas)
    draw.text((12, 10), "SAMPLED REVIEW AID - NO INFRINGEMENT DETERMINATION", fill="white")
    draw.text((12, 28), "Rows without matches are orientation samples only. Review full clips.", fill="#b8c0cc")
    for row, (pair, mirrored) in enumerate(rows):
        y = 52 + row * 210
        for column, key, records in ((0, "original", left), (1, "candidate", right)):
            with Image.open(records[pair[key + "_index"]]["file"]) as image:
                image = image.convert("RGB")
                if column and mirrored:
                    image = ImageOps.mirror(image)
                image.thumbnail((320, 178))
                canvas.paste(image, (12 + column * 340 + (320 - image.width) // 2, y))
            label = f"{key}: {pair[key + '_seconds']:.3f}s"
            if column and mirrored:
                label += " (MIRRORED VIEW)"
            draw.text((12 + column * 340, y + 182), label, fill="white")
    canvas.save(path)


def public_record(record, root):
    return {key: (str(value.relative_to(root)) if key == "file" else value) for key, value in record.items() if key not in ("hash", "pixels")}


def compare(args):
    left_path, right_path = Path(args.original).resolve(), Path(args.candidate).resolve()
    if not left_path.is_file() or not right_path.is_file():
        raise MediaError("compare 只接受两个已保存的本地视频文件。")
    if not math.isfinite(args.interval) or args.interval < 0.1 or not 8 <= args.max_frames <= 2000 or not 3 <= args.min_run <= 20:
        raise MediaError("要求 interval >= 0.1 秒、max-frames 为 8–2000、min-run 为 3–20。")
    folder = Path(args.out).resolve()
    if folder.exists() and any(folder.iterdir()):
        raise MediaError("比对输出目录必须为空，防止覆盖旧报告。")
    folder.mkdir(parents=True, exist_ok=True)
    left_media, right_media = probe(left_path), probe(right_path)
    # Adjacent sequence matching requires the same sampling cadence on both
    # sides. Independently capped intervals make a copy inside a longer
    # compilation appear to advance at a different rate and lose every run.
    shared_interval = max(args.interval, max(left_media["duration_seconds"], right_media["duration_seconds"]) / (args.max_frames - 1))
    left_info, left = sample(left_path, folder / "original-frames", args.interval, args.max_frames, media_info=left_media, minimum_interval=shared_interval)
    right_info, right = sample(right_path, folder / "candidate-frames", args.interval, args.max_frames, media_info=right_media, minimum_interval=shared_interval)
    candidates = find_sequences(left, right, args.min_run)
    if args.mirror:
        candidates += find_sequences(left, right, args.min_run, mirrored=True)
    candidates.sort(key=lambda item: (-item["sample_count"], sum(pair["dhash_distance_fraction"] for pair in item["pairs"])))
    candidates = candidates[:12]
    report = {"schema_version": 1, "created_at_utc": stamp(), "notice": NOTICE, "decision": "human_review_required", "method": "256-bit dHash plus grayscale MAE; adjacent ordered sample runs; optional horizontal mirror", "limitations": ["不是人物身份识别、语义场景识别或镜头设计著作权判断。", "未分析音轨；抽样间隔内的内容、裁切、变速、复杂重剪可能漏检。", "黑帧、近白帧、低纹理帧及静态采样点不参与候选序列；不输出高置信侵权标签。", "区间终点是最后一个匹配采样帧，不能代表整个区间逐帧相同。", "必须核对下载文件与页面作品一致，人工核验来源、授权、白名单和合理引用。"], "byte_identical": left_info["sha256"] == right_info["sha256"], "original": left_info, "candidate": right_info, "minimum_adjacent_matches": args.min_run, "sequences": candidates, "original_samples": [public_record(item, folder) for item in left], "candidate_samples": [public_record(item, folder) for item in right], "contact_sheet": "contact-sheet.jpg"}
    report.update({"algorithm_version": COMPARISON_ALGORITHM_VERSION, "shared_minimum_interval_seconds": shared_interval})
    dump(folder / "comparison.json", report)
    contact_sheet(left, right, candidates, folder / "contact-sheet.jpg")
    summary = ["# 本地视频抽样对照", "", NOTICE, "", f"- 原视频时长：{left_info['duration_seconds']:.3f} 秒；候选视频时长：{right_info['duration_seconds']:.3f} 秒。", f"- 请求采样间隔：{args.interval} 秒；实际最小间隔：原片 {left_info['effective_minimum_interval_seconds']:.6f} 秒，候选 {right_info['effective_minimum_interval_seconds']:.6f} 秒。", f"- 顺序相似候选段：{len(candidates)}；字节完全一致：{report['byte_identical']}（仅文件属性）。", "- 详细真实时间戳、SHA-256、采样排除原因见 comparison.json。", "", "|原作品采样区间（秒）|候选采样区间（秒）|连续采样数|镜像检查|", "|---|---|---|---|"]
    for item in candidates:
        summary.append(f"|{item['original_start_seconds']:.3f}–{item['original_end_sample_seconds']:.3f}|{item['candidate_start_seconds']:.3f}–{item['candidate_end_sample_seconds']:.3f}|{item['sample_count']}|{item['candidate_mirrored_for_comparison']}|")
    summary += ["", "![对照联系表](contact-sheet.jpg)", "", "逐段人工复核：人物的可见外观、场景独有细节、景别/构图/机位、动作与剪辑顺序、音画关系、字幕水印、取样遗漏、授权或白名单。相同人物或题材不等于搬运。"]
    (folder / "review.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(folder / "comparison.json"), "sequence_count": len(candidates), "notice": NOTICE}, ensure_ascii=False, indent=2))
    return 0


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Check local dependencies").set_defaults(func=doctor)
    acquisition = sub.add_parser("download", help="Download one authorized official work URL")
    acquisition.add_argument("--url", required=True)
    acquisition.add_argument("--out", required=True)
    acquisition.add_argument("--cookies", help="Explicit site-only local Netscape cookie file")
    acquisition.add_argument("--backend", choices=("yt-dlp", "kuaishou-public"), default="yt-dlp")
    acquisition.set_defaults(func=download)
    comparison = sub.add_parser("compare", help="Produce local sampled sequence candidates for human review")
    comparison.add_argument("--original", required=True)
    comparison.add_argument("--candidate", required=True)
    comparison.add_argument("--out", required=True)
    comparison.add_argument("--interval", type=float, default=1.0)
    comparison.add_argument("--max-frames", type=int, default=720)
    comparison.add_argument("--min-run", type=int, default=3)
    comparison.add_argument("--mirror", action="store_true")
    comparison.set_defaults(func=compare)
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except MediaError as error:
        print(json.dumps({"status": "stopped", "reason": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (OSError, ValueError, UnicodeError):
        print(json.dumps({"status": "stopped", "reason": "输入、文件系统或媒体数据异常；未输出原始数据。"}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
