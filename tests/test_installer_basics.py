import io
import sys

import httpx
import pytest

from tzara.installer.commands import CommandResult, SubprocessRunner
from tzara.installer.download import Downloader, DownloadError
from tzara.installer.errors import SetupError
from tzara.installer.reporter import Reporter


class TestReporter:
    def make(self, interactive=False):
        out = io.StringIO()
        return Reporter(out, interactive=interactive), out

    def test_each_kind_of_message_has_its_own_marker(self):
        reporter, out = self.make()
        reporter.step("Checking")
        reporter.ok("fine")
        reporter.note("fyi")
        reporter.warn("careful")
        reporter.fail("broken")
        text = out.getvalue()
        assert "==> Checking" in text
        assert "✓ fine" in text
        assert "    fyi" in text
        assert "! careful" in text
        assert "✗ broken" in text

    def test_when_not_a_terminal_progress_prints_a_line_every_ten_percent(self):
        reporter, out = self.make()
        for percent in range(0, 101):
            reporter.progress("downloading x", percent / 100)
        lines = [line for line in out.getvalue().splitlines() if "downloading x" in line]
        assert len(lines) == 11
        assert lines[0].endswith("0%") and lines[-1].endswith("100%")

    def test_progress_in_a_terminal_updates_one_line_in_place(self):
        reporter, out = self.make(interactive=True)
        reporter.progress("downloading x", 0.25)
        reporter.progress("downloading x", 0.5)
        text = out.getvalue()
        assert text.count("\r") == 2
        assert "\n" not in text

    def test_the_next_message_ends_an_in_place_progress_line(self):
        reporter, out = self.make(interactive=True)
        reporter.progress("downloading x", 1.0)
        reporter.ok("done")
        assert out.getvalue().endswith("100%\n  ✓ done\n")

    def test_progress_outside_zero_to_one_is_clamped(self):
        reporter, out = self.make(interactive=True)
        reporter.progress("x", -3)
        reporter.progress("x", 7)
        assert "  0%" in out.getvalue() and "100%" in out.getvalue()

    def test_a_new_download_after_another_prints_from_the_start_again(self):
        reporter, out = self.make()
        reporter.progress("first", 1.0)
        reporter.ok("done")
        reporter.progress("second", 0.0)
        assert "second  0%" in out.getvalue()

    def test_detects_whether_the_output_is_a_terminal(self):
        assert Reporter(io.StringIO())._interactive is False

    def test_defaults_to_standard_output(self, capsys):
        Reporter(interactive=False).ok("hello")
        assert "hello" in capsys.readouterr().out


class TestSubprocessRunner:
    def test_captures_output_and_status(self):
        result = SubprocessRunner().run([sys.executable, "-c", "print('hi')"])
        assert result.succeeded
        assert result.stdout.strip() == "hi"

    def test_a_failing_command_reports_its_status_and_stderr(self):
        code = "import sys; sys.stderr.write('nope'); sys.exit(3)"
        result = SubprocessRunner().run([sys.executable, "-c", code])
        assert result.returncode == 3
        assert not result.succeeded
        assert result.stderr == "nope"

    def test_a_program_that_does_not_exist_is_status_127_not_an_exception(self):
        result = SubprocessRunner().run(["definitely-not-a-real-program-xyz"])
        assert result.returncode == SubprocessRunner.NOT_FOUND
        assert "definitely-not-a-real-program-xyz" in result.stderr

    def test_command_result_defaults(self):
        assert CommandResult(0).stdout == ""


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


class TestDownloader:
    def make(self, handler):
        out = io.StringIO()
        return Downloader(client_for(handler), Reporter(out, interactive=False)), out

    def test_saves_the_file_and_shows_progress(self, tmp_path):
        body = b"x" * 5000
        downloader, out = self.make(lambda request: httpx.Response(200, content=body))
        target = tmp_path / "file.bin"
        downloader.save("https://example.test/file", target, "the file")
        assert target.read_bytes() == body
        assert "downloading the file  100%" in out.getvalue()

    def test_a_download_of_unknown_length_still_works_just_without_a_progress_line(self, tmp_path):
        chunks = httpx.Response(200, content=iter([b"abc", b"def"]))
        downloader, out = self.make(lambda request: chunks)
        target = tmp_path / "file.bin"
        downloader.save("https://example.test/file", target, "the file")
        assert target.read_bytes() == b"abcdef"
        assert "downloading" not in out.getvalue()

    def test_follows_redirects_as_release_downloads_do(self, tmp_path):
        def handler(request):
            if request.url.path == "/start":
                return httpx.Response(302, headers={"location": "https://cdn.example.test/real"})
            return httpx.Response(200, content=b"payload")

        downloader, _ = self.make(handler)
        target = tmp_path / "file.bin"
        downloader.save("https://example.test/start", target, "file")
        assert target.read_bytes() == b"payload"

    def test_a_refusal_carries_the_status_and_the_address(self, tmp_path):
        downloader, _ = self.make(lambda request: httpx.Response(404))
        with pytest.raises(DownloadError, match="HTTP 404") as error:
            downloader.save("https://example.test/missing", tmp_path / "f", "f")
        assert error.value.status == 404
        assert "https://example.test/missing" in str(error.value)

    def test_a_network_failure_has_no_status(self, tmp_path):
        def handler(request):
            raise httpx.ConnectError("refused")

        downloader, _ = self.make(handler)
        with pytest.raises(DownloadError, match="refused") as error:
            downloader.save("https://example.test/f", tmp_path / "f", "f")
        assert error.value.status is None

    def test_text_returns_the_body(self):
        downloader, _ = self.make(lambda request: httpx.Response(200, text="abc  file.zip\n"))
        assert downloader.text("https://example.test/sum") == "abc  file.zip\n"

    def test_text_refusal_and_network_failure_are_download_errors(self):
        refused, _ = self.make(lambda request: httpx.Response(500))
        with pytest.raises(DownloadError, match="HTTP 500"):
            refused.text("https://example.test/x")

        def broken(request):
            raise httpx.ConnectError("down")

        unreachable, _ = self.make(broken)
        with pytest.raises(DownloadError):
            unreachable.text("https://example.test/x")

    def test_download_errors_are_setup_errors(self):
        assert issubclass(DownloadError, SetupError)
