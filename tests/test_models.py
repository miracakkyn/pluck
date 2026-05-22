"""models.py — pydantic şemaları ve Job veri yapısı testleri."""
import pytest
from pydantic import ValidationError

from app.models import FormatsRequest, JobRequest, Job


class TestFormatsRequest:
    def test_valid_url(self):
        assert FormatsRequest(url="https://example.com/video").url == "https://example.com/video"

    def test_strips_whitespace(self):
        assert FormatsRequest(url="  https://example.com/v  ").url == "https://example.com/v"

    def test_blank_url_rejected(self):
        with pytest.raises(ValidationError):
            FormatsRequest(url="   ")

    def test_non_http_url_rejected(self):
        with pytest.raises(ValidationError):
            FormatsRequest(url="ftp://example.com/v")

    def test_localhost_url_rejected(self):
        with pytest.raises(ValidationError):
            FormatsRequest(url="http://localhost:8765/x")

    def test_loopback_ip_rejected(self):
        with pytest.raises(ValidationError):
            FormatsRequest(url="http://127.0.0.1/x")

    def test_link_local_metadata_rejected(self):
        with pytest.raises(ValidationError):
            FormatsRequest(url="http://169.254.169.254/latest/meta-data/")

    def test_lan_ip_allowed(self):
        # RFC 1918 LAN medya sunucuları bilinçli olarak engellenmez.
        assert FormatsRequest(url="http://192.168.1.50/video.mp4").url

    def test_browser_optional(self):
        assert FormatsRequest(url="https://example.com/v").browser is None

    def test_browser_accepted(self):
        req = FormatsRequest(url="https://example.com/v", browser="chrome")
        assert req.browser == "chrome"

    def test_invalid_browser_rejected(self):
        with pytest.raises(ValidationError):
            FormatsRequest(url="https://example.com/v", browser="netscape")


class TestJobRequest:
    def test_valid(self, tmp_path):
        req = JobRequest(url="https://example.com/v", selection="best", download_dir=str(tmp_path))
        assert req.browser is None
        assert req.selection == "best"

    def test_missing_dir_rejected(self, tmp_path):
        missing = tmp_path / "yok" / "burada"
        with pytest.raises(ValidationError):
            JobRequest(url="https://example.com/v", selection="best", download_dir=str(missing))

    def test_empty_browser_becomes_none(self, tmp_path):
        req = JobRequest(
            url="https://example.com/v", selection="best",
            download_dir=str(tmp_path), browser="",
        )
        assert req.browser is None

    def test_invalid_browser_rejected(self, tmp_path):
        with pytest.raises(ValidationError):
            JobRequest(
                url="https://example.com/v", selection="best",
                download_dir=str(tmp_path), browser="netscape",
            )

    def test_chrome_browser_accepted(self, tmp_path):
        req = JobRequest(
            url="https://example.com/v", selection="best",
            download_dir=str(tmp_path), browser="chrome",
        )
        assert req.browser == "chrome"

    def test_format_id_selection_accepted(self, tmp_path):
        req = JobRequest(
            url="https://example.com/v", selection="299-drc",
            download_dir=str(tmp_path),
        )
        assert req.selection == "299-drc"

    def test_invalid_selection_chars_rejected(self, tmp_path):
        with pytest.raises(ValidationError):
            JobRequest(
                url="https://example.com/v", selection="bad; rm -rf",
                download_dir=str(tmp_path),
            )


class TestJob:
    def test_defaults(self, tmp_path):
        job = Job(job_id="j1", url="https://x/v", selection="best",
                  download_dir=str(tmp_path))
        assert job.status == "queued"
        assert job.progress == 0.0
        assert job.cancel_requested is False

    def test_snapshot_excludes_internal_flag(self, tmp_path):
        job = Job(job_id="j1", url="https://x/v", selection="best",
                  download_dir=str(tmp_path))
        snap = job.snapshot()
        assert snap["job_id"] == "j1"
        assert snap["status"] == "queued"
        assert "cancel_requested" not in snap
