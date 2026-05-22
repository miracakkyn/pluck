"""ytdlp_engine.py — format seçici ve format listeleme testleri (yt-dlp mock'lanır)."""
from unittest.mock import MagicMock, patch

import pytest
from yt_dlp.utils import DownloadError

from app import ytdlp_engine
from app.ytdlp_engine import EngineError, build_format_selector, list_formats

FAKE_INFO = {
    "title": "Test Video",
    "duration": 120.0,
    "thumbnail": "http://x/t.jpg",
    "uploader": "Test Channel",
    "formats": [
        # storyboard — elenmeli
        {"format_id": "sb0", "ext": "mhtml", "vcodec": "none",
         "acodec": "none", "resolution": "320x180"},
        # ses
        {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2",
         "filesize": 1_000_000, "resolution": "audio only", "format_note": "medium"},
        # video-only
        {"format_id": "137", "ext": "mp4", "vcodec": "avc1.64", "acodec": "none",
         "height": 1080, "fps": 30, "filesize": 5_000_000,
         "resolution": "1920x1080", "format_note": "1080p"},
        # birleşik
        {"format_id": "18", "ext": "mp4", "vcodec": "avc1.42", "acodec": "mp4a.40.2",
         "height": 360, "fps": 30, "resolution": "640x360", "format_note": "360p"},
    ],
}


def _mock_ydl(info=None, error=None):
    """`with YoutubeDL(opts) as ydl:` kullanımını taklit eden mock sınıf."""
    ydl_class = MagicMock()
    ctx = ydl_class.return_value.__enter__.return_value
    if error is not None:
        ctx.extract_info.side_effect = error
    else:
        ctx.extract_info.return_value = info
    return ydl_class


class TestBuildFormatSelector:
    def test_best_prefers_compatible_mp4(self):
        sel = build_format_selector("best")
        # mp4 video + m4a (AAC) ses tercih edilir → her oynatıcıda sesli çalar.
        assert "ext=mp4" in sel
        assert "ext=m4a" in sel
        assert sel.endswith("/bv*+ba/b")  # yedek dal

    @pytest.mark.parametrize("preset,height", [("1080p", 1080), ("720p", 720), ("480p", 480)])
    def test_resolution_presets(self, preset, height):
        sel = build_format_selector(preset)
        assert f"height<={height}" in sel
        assert "ext=mp4" in sel  # uyumlu mp4 tercihi

    def test_audio_preset(self):
        assert build_format_selector("audio") == "ba/b"

    def test_raw_format_id_merges_audio(self):
        sel = build_format_selector("137")
        assert "137" in sel
        assert "ba" in sel


class TestListFormats:
    def test_parses_metadata(self):
        with patch.object(ytdlp_engine, "YoutubeDL", _mock_ydl(FAKE_INFO)):
            resp = list_formats("https://x/v")
        assert resp.title == "Test Video"
        assert resp.duration == 120.0
        assert resp.uploader == "Test Channel"

    def test_skips_storyboard(self):
        with patch.object(ytdlp_engine, "YoutubeDL", _mock_ydl(FAKE_INFO)):
            resp = list_formats("https://x/v")
        ids = {f.format_id for f in resp.formats}
        assert ids == {"140", "137", "18"}

    def test_classifies_kind(self):
        with patch.object(ytdlp_engine, "YoutubeDL", _mock_ydl(FAKE_INFO)):
            resp = list_formats("https://x/v")
        kinds = {f.format_id: f.kind for f in resp.formats}
        assert kinds["140"] == "audio"
        assert kinds["137"] == "video"
        assert kinds["18"] == "combined"

    def test_includes_presets(self):
        with patch.object(ytdlp_engine, "YoutubeDL", _mock_ydl(FAKE_INFO)):
            resp = list_formats("https://x/v")
        assert "best" in resp.presets

    def test_download_error_cleaned(self):
        err = DownloadError("ERROR: \x1b[31mVideo unavailable\x1b[0m")
        with patch.object(ytdlp_engine, "YoutubeDL", _mock_ydl(error=err)):
            with pytest.raises(EngineError) as exc:
                list_formats("https://x/v")
        msg = str(exc.value)
        assert "Video unavailable" in msg
        assert "\x1b" not in msg

    def test_none_info_raises(self):
        with patch.object(ytdlp_engine, "YoutubeDL", _mock_ydl(None)):
            with pytest.raises(EngineError):
                list_formats("https://x/v")

    def test_extractor_error_becomes_engine_error(self):
        from yt_dlp.utils import ExtractorError
        with patch.object(ytdlp_engine, "YoutubeDL",
                          _mock_ydl(error=ExtractorError("video bulunamadi"))):
            with pytest.raises(EngineError):
                list_formats("https://x/v")

    def test_resolution_handles_missing_width(self):
        info = {"title": "T", "formats": [
            {"format_id": "x", "ext": "mp4", "vcodec": "avc1",
             "acodec": "none", "height": 720},  # width yok, resolution yok
        ]}
        with patch.object(ytdlp_engine, "YoutubeDL", _mock_ydl(info)):
            resp = list_formats("https://x/v")
        assert resp.formats[0].resolution == "720p"
        assert "None" not in resp.formats[0].resolution

    def test_unsafe_thumbnail_dropped(self):
        info = {"title": "T", "thumbnail": "javascript:alert(1)", "formats": [
            {"format_id": "18", "ext": "mp4", "vcodec": "avc1",
             "acodec": "mp4a", "height": 360, "resolution": "640x360"},
        ]}
        with patch.object(ytdlp_engine, "YoutubeDL", _mock_ydl(info)):
            resp = list_formats("https://x/v")
        assert resp.thumbnail is None

    def test_passes_cookies_when_browser_given(self):
        ydl = _mock_ydl(FAKE_INFO)
        with patch.object(ytdlp_engine, "YoutubeDL", ydl):
            list_formats("https://x/v", browser="chrome")
        assert ydl.call_args[0][0]["cookiesfrombrowser"] == ("chrome",)

    def test_no_cookies_without_browser(self):
        ydl = _mock_ydl(FAKE_INFO)
        with patch.object(ytdlp_engine, "YoutubeDL", ydl):
            list_formats("https://x/v")
        assert "cookiesfrombrowser" not in ydl.call_args[0][0]


class TestDownload:
    def test_builds_format_and_calls_download(self, tmp_path):
        ydl_class = _mock_ydl()
        with patch.object(ytdlp_engine, "YoutubeDL", ydl_class):
            ytdlp_engine.download(
                url="https://x/v", selection="720p", download_dir=str(tmp_path),
            )
        opts = ydl_class.call_args[0][0]
        assert "height<=720" in opts["format"]
        assert opts["merge_output_format"] == "mp4"
        ctx = ydl_class.return_value.__enter__.return_value
        ctx.download.assert_called_once_with(["https://x/v"])

    def test_browser_adds_cookies_option(self, tmp_path):
        ydl_class = _mock_ydl()
        with patch.object(ytdlp_engine, "YoutubeDL", ydl_class):
            ytdlp_engine.download(
                url="https://x/v", selection="best",
                download_dir=str(tmp_path), browser="firefox",
            )
        assert ydl_class.call_args[0][0]["cookiesfrombrowser"] == ("firefox",)

    def test_no_browser_omits_cookies_option(self, tmp_path):
        ydl_class = _mock_ydl()
        with patch.object(ytdlp_engine, "YoutubeDL", ydl_class):
            ytdlp_engine.download(
                url="https://x/v", selection="best", download_dir=str(tmp_path),
            )
        assert "cookiesfrombrowser" not in ydl_class.call_args[0][0]

    def test_outtmpl_includes_selection(self, tmp_path):
        # Aynı videoyu farklı kalitede indirince dosya adı çakışmamalı.
        ydl_class = _mock_ydl()
        with patch.object(ytdlp_engine, "YoutubeDL", ydl_class):
            ytdlp_engine.download(
                url="https://x/v", selection="1080p", download_dir=str(tmp_path),
            )
        assert "1080p" in ydl_class.call_args[0][0]["outtmpl"]

    def test_audio_adds_extract_postprocessor(self, tmp_path):
        ydl_class = _mock_ydl()
        with patch.object(ytdlp_engine, "YoutubeDL", ydl_class):
            ytdlp_engine.download(
                url="https://x/v", selection="audio", download_dir=str(tmp_path),
            )
        opts = ydl_class.call_args[0][0]
        assert opts["postprocessors"][0]["key"] == "FFmpegExtractAudio"

    def test_download_error_becomes_engine_error(self, tmp_path):
        ydl_class = MagicMock()
        ctx = ydl_class.return_value.__enter__.return_value
        ctx.download.side_effect = DownloadError("ERROR: indirme basarisiz")
        with patch.object(ytdlp_engine, "YoutubeDL", ydl_class):
            with pytest.raises(EngineError):
                ytdlp_engine.download(
                    url="https://x/v", selection="best", download_dir=str(tmp_path),
                )
