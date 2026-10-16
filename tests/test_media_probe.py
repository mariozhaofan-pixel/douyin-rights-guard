"""No accounts or personal data: synthetic clips and in-memory service fixtures."""
import argparse
import contextlib
import importlib.util
import io
import json
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

MODULE = Path(__file__).resolve().parents[1] / "scripts" / "media_probe.py"
spec = importlib.util.spec_from_file_location("media_probe", MODULE)
media = importlib.util.module_from_spec(spec)
spec.loader.exec_module(media)


class InputTests(unittest.TestCase):
    def test_xiaohongshu_urls_are_rejected_without_echoing_tokens(self):
        note_id = "abcdef0123456789abcdef01"
        for url in ("https://www.xiaohongshu.com/explore/" + note_id + "?xsec_token=SYNTHETIC_SECRET",
                    "https://www.xiaohongshu.com/discovery/item/" + note_id + "?xsec_token=SYNTHETIC_SECRET",
                    "https://xhslink.com/synthetic", "https://www.xiaohongshu.com/user/profile/" + note_id,
                    "https://www.xiaohongshu.com/search_result?keyword=test", "https://www.xiaohongshu.com.attacker.test/explore/" + note_id,
                    "https://other.xiaohongshu.com/explore/" + note_id, "https://www.xiaohongshu.com/explore/not-a-note-id"):
            with self.subTest(url=url):
                with self.assertRaises(media.MediaError) as raised:
                    media.validate_url(url)
                self.assertNotIn("SYNTHETIC_SECRET", str(raised.exception))
                self.assertNotIn(note_id, str(raised.exception))

    def test_disabled_platform_does_not_read_cookie_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "synthetic-cookies.txt"
            with mock.patch.object(Path, "read_text") as reader:
                with self.assertRaises(media.MediaError):
                    media.safe_cookie_copy(source, "xiaohongshu", root / "copy.txt")
                reader.assert_not_called()
            self.assertFalse((root / "copy.txt").exists())

    def test_disabled_platform_download_has_no_network_or_archive_side_effects(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archived = root / "archived"
            archived.mkdir()
            receipt = archived / "download-receipt.json"
            historical = b'{"platform":"xiaohongshu","status":"downloaded_needs_source_verification"}\n'
            receipt.write_bytes(historical)
            note_id = "abcdef0123456789abcdef01"
            for backend in ("yt-dlp", "kuaishou-public"):
                for output in (root / "new-output", archived):
                    args = argparse.Namespace(url="https://www.xiaohongshu.com/explore/" + note_id + "?xsec_token=SYNTHETIC_SECRET", out=str(output), cookies=None, backend=backend)
                    with self.subTest(backend=backend, output=output), mock.patch.object(media, "download_ytdlp") as ytdlp, mock.patch.object(media, "download_kuaishou_public") as kuaishou:
                        with self.assertRaises(media.MediaError):
                            media.download(args)
                        ytdlp.assert_not_called()
                        kuaishou.assert_not_called()
            self.assertFalse((root / "new-output").exists())
            self.assertEqual(receipt.read_bytes(), historical)

    def test_only_single_official_https_pages(self):
        for platform, url in (("douyin", "https://www.douyin.com/video/123456"),
                              ("douyin", "https://v.douyin.com/AbC123/"),
                              ("bilibili", "https://www.bilibili.com/video/BVsynthetic"),
                              ("bilibili", "https://b23.tv/AbC123/"),
                              ("kuaishou", "https://www.kuaishou.com/short-video/synthetic"),
                              ("kuaishou", "https://v.kuaishou.com/AbC123/")):
            with self.subTest(platform=platform, url=url):
                self.assertEqual(media.validate_url(url + "?token=private#private"), (platform, url))
        for url in ("https://www.douyin.com/user/123", "https://www.bilibili.com/", "https://www.douyin.com.attacker.test/video/123", "http://www.douyin.com/video/123", "https://user:secret@www.douyin.com/video/123", "https://127.0.0.1/video/123"):
            with self.subTest(url=url), self.assertRaises(media.MediaError):
                media.validate_url(url)

    def test_cookie_source_is_unchanged_and_other_domains_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "original.txt"
            content = "# Netscape HTTP Cookie File\n.douyin.com\tTRUE\t/\tTRUE\t2000000000\tsession\tsynthetic-secret\n"
            original.write_text(content, encoding="utf-8")
            media.safe_cookie_copy(original, "douyin", root / "copy.txt")
            self.assertEqual(original.read_text(), content)
            original.write_text(content + ".unrelated.test\tTRUE\t/\tTRUE\t2000000000\tx\ty\n")
            with self.assertRaises(media.MediaError):
                media.safe_cookie_copy(original, "douyin", root / "rejected.txt")

    def test_public_ssr_requires_unambiguous_media_and_domain(self):
        url = "https://v1.kwaicdn.com/path.mp4?token=synthetic"
        self.assertEqual(media.extract_public_mp4('<meta property="og:video" content="' + url + '">'), url)
        for page in ("login challenge", 'captcha https://v1.kwaicdn.com/a.mp4', '<video src="https://evil.test/x.mp4">', 'https://v1.kwaicdn.com/a.mp4 https://v1.kwaicdn.com/b.mp4'):
            with self.subTest(page=page), self.assertRaises(media.MediaError):
                media.extract_public_mp4(page)

    def test_downloader_rejects_playlist_before_download(self):
        result = subprocess.CompletedProcess([], 0, stdout=json.dumps({"_type": "playlist", "entries": []}).encode(), stderr=b"")
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(media, "ytdlp_base", return_value=["synthetic-yt-dlp"]), mock.patch.object(media, "run", return_value=result) as runner:
            with self.assertRaises(media.MediaError):
                media.download_ytdlp("https://v.douyin.com/synthetic", Path(temporary), None, "douyin")
            self.assertEqual(runner.call_count, 1)

    def test_max_download_limit_is_only_applied_to_download_not_metadata(self):
        with mock.patch.object(media.importlib.util, "find_spec", return_value=object()):
            self.assertNotIn("--max-downloads", media.ytdlp_base())
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)

            def response(command, **kwargs):
                if "--dump-single-json" in command:
                    self.assertNotIn("--max-downloads", command)
                    return subprocess.CompletedProcess(command, 0, stdout=b'{"id":"synthetic","_type":"video"}', stderr=b"")
                self.assertEqual(command[command.index("--max-downloads") + 1], "1")
                (folder / "video.mp4").write_bytes(b"synthetic-file-not-media")
                return subprocess.CompletedProcess(command, 101, stdout=b"", stderr=b"")

            with mock.patch.object(media, "ytdlp_base", return_value=["synthetic-yt-dlp"]), mock.patch.object(media, "run", side_effect=response) as runner:
                self.assertEqual(media.download_ytdlp("https://www.bilibili.com/video/BVsynthetic", folder, None, "bilibili"), folder / "video.mp4")
                self.assertEqual(runner.call_count, 2)

    def test_failure_classification_never_includes_raw_signed_url_or_cookie(self):
        result = subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"ERROR: Fresh cookies are needed. Cookie=session-secret https://cdn.test/x.mp4?token=secret")
        error = str(media.ytdlp_error(result, "preflight"))
        self.assertIn("cookie_required", error)
        self.assertNotIn("session-secret", error)
        self.assertNotIn("https://", error)

    def test_download_failure_receipt_omits_cookie_and_raw_response(self):
        with tempfile.TemporaryDirectory() as temporary:
            args = argparse.Namespace(url="https://www.douyin.com/video/123?token=do-not-log", out=str(Path(temporary) / "output"), cookies=None, backend="yt-dlp")
            with mock.patch.object(media, "download_ytdlp", side_effect=media.MediaError("公开作品提取失败。")), self.assertRaises(media.MediaError):
                media.download(args)
            receipt = (Path(args.out) / "download-receipt.json").read_text(encoding="utf-8")
            self.assertNotIn("do-not-log", receipt)
            self.assertEqual(json.loads(receipt)["status"], "failed")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg and ffprobe required")
class SyntheticVideoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PIL import Image, ImageDraw
        cls.directory = tempfile.TemporaryDirectory(prefix="rights-synthetic-tests-")
        cls.root = Path(cls.directory.name)
        frames = cls.root / "frames"
        frames.mkdir()
        # Every half-second has a different seeded geometric scene; never real faces.
        for index in range(60):
            rng = random.Random(index // 5)
            image = Image.new("RGB", (192, 128), (42, 45, 50))
            draw = ImageDraw.Draw(image)
            for _ in range(30):
                x, y = rng.randrange(175), rng.randrange(110)
                draw.rectangle((x, y, x + 17, y + 17), fill=tuple(rng.randrange(30, 240) for _ in range(3)))
            image.save(frames / f"{index:03d}.png")
        cls.original = cls.root / "original.mp4"
        cls.encode(["-framerate", "10", "-i", str(frames / "%03d.png"), "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", str(cls.original)])
        cls.trimmed = cls.root / "trimmed.mp4"
        cls.encode(["-ss", "1", "-i", str(cls.original), "-t", "4", "-c:v", "libx264", "-crf", "18", str(cls.trimmed)])
        cls.mirrored = cls.root / "mirrored.mp4"
        cls.encode(["-i", str(cls.trimmed), "-vf", "hflip", "-c:v", "libx264", "-crf", "18", str(cls.mirrored)])
        cls.black = cls.root / "black.mp4"
        cls.encode(["-f", "lavfi", "-i", "color=black:s=192x128:r=10:d=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(cls.black)])
        cls.static = cls.root / "static.mp4"
        cls.encode(["-loop", "1", "-i", str(frames / "000.png"), "-t", "3", "-r", "10", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(cls.static)])
        cls.compilation = cls.root / "compilation.mp4"
        # The same six-second opening followed by a long, ineligible static
        # tail. A small frame budget forces only the long input above 0.5 s
        # under the former independent sampling rule.
        cls.encode(["-i", str(cls.original), "-vf", "tpad=stop_mode=clone:stop_duration=24", "-c:v", "libx264", "-crf", "18", str(cls.compilation)])

    @classmethod
    def encode(cls, options):
        subprocess.run([shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-y"] + options, check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def comparison(self, original, candidate, name, mirror=False, max_frames=100):
        output = self.root / name
        args = argparse.Namespace(original=str(original), candidate=str(candidate), out=str(output), interval=0.5, max_frames=max_frames, min_run=3, mirror=mirror)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(media.compare(args), 0)
        return json.loads((output / "comparison.json").read_text(encoding="utf-8")), output

    def test_trimmed_copy_has_ordered_real_timestamp_candidates(self):
        report, output = self.comparison(self.original, self.trimmed, "trim-report")
        self.assertTrue(report["sequences"])
        best = report["sequences"][0]
        self.assertAlmostEqual(best["original_start_seconds"] - best["candidate_start_seconds"], 1.0, places=1)
        self.assertGreaterEqual(best["sample_count"], 6)
        self.assertEqual(report["decision"], "human_review_required")
        self.assertEqual(len(report["original"]["sha256"]), 64)
        self.assertTrue((output / "contact-sheet.jpg").is_file())
        for pair, following in zip(best["pairs"], best["pairs"][1:]):
            self.assertEqual(following["original_index"], pair["original_index"] + 1)
            self.assertEqual(following["candidate_index"], pair["candidate_index"] + 1)

    def test_mirrored_copy_detected_only_as_explicit_mirror_candidate(self):
        report, _ = self.comparison(self.original, self.mirrored, "mirror-report", mirror=True)
        self.assertTrue(any(item["candidate_mirrored_for_comparison"] and item["sample_count"] >= 6 for item in report["sequences"]))

    def test_black_and_static_do_not_generate_similarity_sequences(self):
        for source, name in ((self.black, "black-report"), (self.static, "static-report")):
            with self.subTest(name=name):
                report, _ = self.comparison(source, source, name)
                self.assertEqual(report["sequences"], [])
                self.assertTrue(report["byte_identical"])
                self.assertFalse(any(item["eligible"] for item in report["original_samples"]))

    def test_actual_pts_is_not_requested_interval_or_frame_number(self):
        info, frames = media.sample(self.original, self.root / "nonintegral-interval", 0.37, 100)
        self.assertAlmostEqual(frames[1]["time_seconds"], 0.4, places=5)
        self.assertNotEqual(frames[1]["time_seconds"], info["requested_interval_seconds"])
        self.assertAlmostEqual(info["duration_seconds"], 6.0, places=1)

    def test_unequal_length_copy_survives_sampling_frame_budget(self):
        for original, candidate, name in ((self.original, self.compilation, "compilation-report"),
                                          (self.compilation, self.original, "reverse-compilation-report")):
            with self.subTest(name=name):
                report, _ = self.comparison(original, candidate, name, max_frames=12)
                self.assertTrue(report["sequences"], "A copied opening must retain an adjacent run when the longer input hits the sample cap")
                self.assertEqual(report["algorithm_version"], media.COMPARISON_ALGORITHM_VERSION)
                self.assertEqual(report["decision"], "human_review_required")
                shared = report["shared_minimum_interval_seconds"]
                self.assertGreater(shared, 0.5)
                self.assertAlmostEqual(shared, 30 / 11, places=4)
                for side in ("original", "candidate"):
                    self.assertEqual(report[side]["requested_interval_seconds"], 0.5)
                    self.assertEqual(report[side]["effective_minimum_interval_seconds"], shared)
                    self.assertLessEqual(report[side]["sample_count"], 12)
                best = report["sequences"][0]
                self.assertGreaterEqual(best["sample_count"], 3)
                for pair in best["pairs"]:
                    self.assertAlmostEqual(pair["original_seconds"], pair["candidate_seconds"], places=4)
                    self.assertLess(pair["original_seconds"], 6)
                # Report decoded timestamps, not the unachievable 2.727... s
                # minimum interval on the synthetic source's 10 fps grid.
                self.assertAlmostEqual(best["pairs"][1]["original_seconds"], 2.8, places=4)


if __name__ == "__main__":
    unittest.main()
