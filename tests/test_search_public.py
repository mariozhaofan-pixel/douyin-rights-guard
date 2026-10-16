"""Public-index fixtures only; never log in, search real accounts or submit."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("search_public", SCRIPTS / "search_public.py")
searcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(searcher)


def response(entries=None, *, stderr=b"", code=0, stdout=None):
    if stdout is None:
        stdout = json.dumps({"_type": "playlist", "entries": entries or []}).encode()
    return subprocess.CompletedProcess([], code, stdout=stdout, stderr=stderr)


class PublicSearchTests(unittest.TestCase):
    def execute(self, fixture, limit=20):
        with mock.patch.object(searcher.importlib.util, "find_spec", return_value=object()), \
                mock.patch.object(searcher, "run", return_value=fixture) as runner:
            result = searcher.search("synthetic public title", limit)
        return result, runner

    def test_search_keeps_bounded_playlist_and_disables_accounts_and_retries(self):
        result, runner = self.execute(response(), 36)
        command = runner.call_args.args[0]
        self.assertEqual(command[-2:], ["--", "bilisearch36:synthetic public title"])
        for flag in ("--ignore-config", "--no-plugin-dirs", "--no-cookies", "--no-cookies-from-browser",
                     "--no-cache-dir", "--flat-playlist", "--skip-download", "--dump-single-json"):
            self.assertIn(flag, command)
        for flag in ("--no-playlist", "--playlist-items", "--cookies", "--cookies-from-browser", "--max-downloads"):
            self.assertNotIn(flag, command)
        for flag in ("--retries", "--extractor-retries", "--fragment-retries"):
            self.assertEqual(command[command.index(flag) + 1], "0")
        self.assertEqual(runner.call_args.kwargs["timeout"], 90)
        self.assertEqual(result["batch_kind"], "links")
        self.assertFalse(result["web_first_page_complete"])

    def test_invalid_inputs_are_rejected_before_network(self):
        with mock.patch.object(searcher, "run") as runner:
            for query in ("", "a" * 201, "https://private.test/?token=SECRET", "x\nCookie=SECRET", "SESSDATA=SECRET"):
                with self.subTest(query=query), self.assertRaises(searcher.MediaError) as raised:
                    searcher.search(query)
                self.assertNotIn("SECRET", str(raised.exception))
            for limit in (0, 101, -1, True, "2"):
                with self.subTest(limit=limit), self.assertRaises(searcher.MediaError):
                    searcher.search("synthetic", limit)
            runner.assert_not_called()

    def test_projected_fields_strip_tokens_and_do_not_invent_uploader(self):
        fixture = response([{"url": "http://www.bilibili.com/video/BV1234567890/?token=SIGNED_SECRET#fragment",
                             "id": "123", "title": "<em>Synthetic</em> &amp; public",
                             "http_headers": {"Cookie": "COOKIE_SECRET"}, "formats": [{"url": "https://cdn.test/?key=MEDIA_SECRET"}]}])
        result, runner = self.execute(fixture)
        row = result["results"][0]
        self.assertEqual(row["url"], "https://www.bilibili.com/video/BV1234567890")
        self.assertEqual(row["work_id"], "BV1234567890")
        self.assertEqual(row["title"], "Synthetic & public")
        self.assertNotIn("uploader_id", row)
        self.assertNotIn("123", row.get("uploader_id", ""))
        serialized = json.dumps(result)
        for secret in ("SIGNED_SECRET", "COOKIE_SECRET", "MEDIA_SECRET", "formats", "http_headers"):
            self.assertNotIn(secret, serialized)
        self.assertEqual(runner.call_count, 1)

    def test_keeps_available_stable_uploader_and_omits_unsafe_titles(self):
        result, _ = self.execute(response([{"url": "https://www.bilibili.com/video/av123456",
                                           "uploader_id": 42, "uploader": "Synthetic Author",
                                           "title": "https://cdn.test/?token=SECRET"}]))
        row = result["results"][0]
        self.assertEqual(row["uploader_id"], "42")
        self.assertEqual(row["account_url"], "https://space.bilibili.com/42")
        self.assertNotIn("title", row)
        self.assertNotIn("SECRET", json.dumps(result))

    def test_deduplicates_and_rejects_nonwork_or_host_spoof_results(self):
        entries = [{"url": "https://www.bilibili.com/video/BV1234567890"},
                   {"url": "https://bilibili.com/video/BV1234567890?share=secret"},
                   {"url": "https://bilibili.com.attacker.test/video/BV1234567890"},
                   {"url": "https://user:secret@www.bilibili.com/video/BV1234567890"},
                   {"url": "https://www.bilibili.com/video/BV1234567890:444"},
                   {"url": "https://www.bilibili.com/space/123"}, None]
        result, _ = self.execute(response(entries))
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["skipped_count"], 6)

    def test_caps_even_an_overlong_service_fixture(self):
        result, _ = self.execute(response([{"url": "https://www.bilibili.com/video/av1"},
                                          {"url": "https://www.bilibili.com/video/av2"}]), 1)
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["requested_limit"], 1)
        self.assertEqual(result["raw_entry_count"], 2)

    def test_access_rejection_is_safe_failed_receipt_without_retry(self):
        result, runner = self.execute(response(code=1, stderr=b"HTTP Error 412 https://cdn.test/?token=SECRET Cookie=PRIVATE"))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["results"], [])
        self.assertIn("access_rejected", result["reason"])
        self.assertEqual(runner.call_count, 1)
        for secret in ("SECRET", "PRIVATE", "https://cdn"):
            self.assertNotIn(secret, json.dumps(result))

    def test_invalid_and_oversized_responses_never_escape(self):
        for payload in (b"COOKIE_SECRET", b'{"entries":"SECRET"}', b"s" * (searcher.MAX_RESPONSE_BYTES + 1)):
            with self.subTest(size=len(payload)):
                result, _ = self.execute(response(stdout=payload))
                self.assertEqual(result["status"], "failed")
                self.assertNotIn("SECRET", json.dumps(result))

    def test_empty_results_do_not_claim_no_reposts(self):
        result, _ = self.execute(response())
        self.assertEqual(result["status"], "indexed")
        self.assertFalse(result["web_first_page_complete"])
        self.assertIn("不能据此认定不存在搬运", result["scope_note"])

    def test_cli_persists_safe_failure_and_rejects_existing_output_before_network(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "index.json"
            with mock.patch.object(searcher.importlib.util, "find_spec", return_value=object()), \
                    mock.patch.object(searcher, "run", return_value=response(code=1, stderr=b"captcha COOKIE_SECRET")), \
                    contextlib.redirect_stdout(io.StringIO()) as stdout:
                code = searcher.main(["--query", "synthetic", "--out", str(output)])
            self.assertEqual(code, 1)
            before = output.read_bytes()
            self.assertEqual(json.loads(before)["status"], "failed")
            self.assertNotIn("COOKIE_SECRET", stdout.getvalue())
            self.assertNotIn(b"COOKIE_SECRET", before)
            with mock.patch.object(searcher, "run") as runner, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(searcher.main(["--query", "synthetic", "--out", str(output)]), 2)
                runner.assert_not_called()
            self.assertEqual(output.read_bytes(), before)

    def test_cli_does_not_write_private_search_results_inside_skill(self):
        with mock.patch.object(searcher, "run") as runner, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(searcher.main(["--query", "synthetic", "--out", str(SCRIPTS / "never-created.json")]), 2)
            runner.assert_not_called()
        self.assertFalse((SCRIPTS / "never-created.json").exists())


if __name__ == "__main__":
    unittest.main()
