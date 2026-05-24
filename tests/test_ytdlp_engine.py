"""ytdlp_engine.py — format seçici ve format listeleme testleri (yt-dlp mock'lanır)."""
from unittest.mock import MagicMock, patch

import pytest
from yt_dlp.utils import DownloadError

from app import ytdlp_engine
from app.ytdlp_engine import EngineError, build_format_selector, list_formats


@pytest.fixture(autouse=True)
def _ffmpeg_present(monkeypatch):
    """Test ortamında ffmpeg PATH'te olmayabilir; mock indirme yolu için sahte var."""
    monkeypatch.setattr(ytdlp_engine, "_FFMPEG_AVAILABLE", True)

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
            resp = list_formats("https://x/v").video
        assert resp.title == "Test Video"
        assert resp.duration == 120.0
        assert resp.uploader == "Test Channel"

    def test_skips_storyboard(self):
        with patch.object(ytdlp_engine, "YoutubeDL", _mock_ydl(FAKE_INFO)):
            resp = list_formats("https://x/v").video
        ids = {f.format_id for f in resp.formats}
        assert ids == {"140", "137", "18"}

    def test_classifies_kind(self):
        with patch.object(ytdlp_engine, "YoutubeDL", _mock_ydl(FAKE_INFO)):
            resp = list_formats("https://x/v").video
        kinds = {f.format_id: f.kind for f in resp.formats}
        assert kinds["140"] == "audio"
        assert kinds["137"] == "video"
        assert kinds["18"] == "combined"

    def test_includes_presets(self):
        with patch.object(ytdlp_engine, "YoutubeDL", _mock_ydl(FAKE_INFO)):
            resp = list_formats("https://x/v").video
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
            resp = list_formats("https://x/v").video
        assert resp.formats[0].resolution == "720p"
        assert "None" not in resp.formats[0].resolution

    def test_unsafe_thumbnail_dropped(self):
        info = {"title": "T", "thumbnail": "javascript:alert(1)", "formats": [
            {"format_id": "18", "ext": "mp4", "vcodec": "avc1",
             "acodec": "mp4a", "height": 360, "resolution": "640x360"},
        ]}
        with patch.object(ytdlp_engine, "YoutubeDL", _mock_ydl(info)):
            resp = list_formats("https://x/v").video
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

    def test_playlist_returns_all_entries(self):
        # Çoklu video sayfası: yt-dlp playlist döndürür; tüm girdiler dönmeli.
        playlist_info = {
            "_type": "playlist",
            "title": "Test Playlist",
            "entries": [
                {**FAKE_INFO, "webpage_url": "https://x/v1", "title": "Entry 1"},
                {**FAKE_INFO, "webpage_url": "https://x/v2", "title": "Entry 2"},
            ],
        }
        with patch.object(ytdlp_engine, "YoutubeDL", _mock_ydl(playlist_info)):
            scan = list_formats("https://x/playlist")
        assert scan.type == "playlist"
        assert scan.playlist_title == "Test Playlist"
        assert len(scan.entries) == 2
        assert scan.entries[0].title == "Entry 1"
        assert scan.entries[0].url == "https://x/v1"
        assert scan.entries[1].url == "https://x/v2"

    def test_empty_playlist_raises(self):
        empty = {"_type": "playlist", "title": "T", "entries": [None, None]}
        with patch.object(ytdlp_engine, "YoutubeDL", _mock_ydl(empty)):
            with pytest.raises(EngineError):
                list_formats("https://x/empty")


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


class TestFfmpegPrecheck:
    def test_missing_ffmpeg_raises_for_video(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ytdlp_engine, "_FFMPEG_AVAILABLE", False)
        with pytest.raises(EngineError) as exc:
            ytdlp_engine.download(
                url="https://x/v", selection="best", download_dir=str(tmp_path),
            )
        assert "ffmpeg" in str(exc.value).lower()

    def test_missing_ffmpeg_raises_for_audio(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ytdlp_engine, "_FFMPEG_AVAILABLE", False)
        with pytest.raises(EngineError):
            ytdlp_engine.download(
                url="https://x/v", selection="audio", download_dir=str(tmp_path),
            )


class TestPostDownloadVerification:
    def test_missing_final_file_raises(self, tmp_path):
        """yt-dlp exception fırlatmasa bile diskte dosya yoksa hata olmalı."""
        ydl_class = MagicMock()
        ctx = ydl_class.return_value.__enter__.return_value
        # progress hook'tan filename yayınla ama disk'te dosya yarat ma
        def fake_download(_urls):
            opts = ydl_class.call_args[0][0]
            for hook in opts["progress_hooks"]:
                hook({"status": "finished",
                      "filename": str(tmp_path / "yok.mp4"),
                      "info_dict": {"id": "abc123"}})
        ctx.download.side_effect = fake_download
        with patch.object(ytdlp_engine, "YoutubeDL", ydl_class):
            with pytest.raises(EngineError) as exc:
                ytdlp_engine.download(
                    url="https://x/v", selection="best",
                    download_dir=str(tmp_path),
                )
        assert "tamamlanamadı" in str(exc.value).lower() or \
               "tamamlanamadi" in str(exc.value).lower()

    def test_existing_final_file_completes(self, tmp_path):
        """Disk'te dosya varsa exception fırlatılmaz."""
        target = tmp_path / "var.mp4"
        target.write_bytes(b"x")
        ydl_class = MagicMock()
        ctx = ydl_class.return_value.__enter__.return_value
        def fake_download(_urls):
            opts = ydl_class.call_args[0][0]
            for hook in opts["progress_hooks"]:
                hook({"status": "finished", "filename": str(target),
                      "info_dict": {"id": "abc123"}})
        ctx.download.side_effect = fake_download
        with patch.object(ytdlp_engine, "YoutubeDL", ydl_class):
            ytdlp_engine.download(
                url="https://x/v", selection="best",
                download_dir=str(tmp_path),
            )  # exception olmamalı


class TestCleanupArtifacts:
    def test_removes_part_files_with_matching_id(self, tmp_path):
        keep_other = tmp_path / "baska_video [zzz999] best.mp4"
        keep_done = tmp_path / "video [abc123] best.mp4"
        part1 = tmp_path / "video [abc123] best.f137.mp4.part"
        part2 = tmp_path / "video [abc123] best.f140.m4a.part"
        ytdl_file = tmp_path / "video [abc123] best.ytdl"
        for f in (keep_other, keep_done, part1, part2, ytdl_file):
            f.write_bytes(b"x")
        ytdlp_engine._cleanup_artifacts(tmp_path, {"id": "abc123"})
        assert keep_other.exists()
        assert keep_done.exists()
        assert not part1.exists()
        assert not part2.exists()
        assert not ytdl_file.exists()

    def test_no_id_is_noop(self, tmp_path):
        part = tmp_path / "video [abc123] best.part"
        part.write_bytes(b"x")
        ytdlp_engine._cleanup_artifacts(tmp_path, {})
        assert part.exists()  # bilinmiyor → dokunma

    def test_missing_dir_silently_ignored(self, tmp_path):
        from pathlib import Path
        ytdlp_engine._cleanup_artifacts(Path(tmp_path / "yok"), {"id": "x"})


class TestCleanError:
    def test_preserves_multiline(self):
        from app.ytdlp_engine import _clean_error
        exc = Exception("ERROR: Sign in to confirm your age\n"
                        "Use --cookies-from-browser or pass --cookies")
        msg = _clean_error(exc)
        assert "Sign in" in msg
        assert "cookies" in msg  # 2. satır da korunmalı

    def test_truncates_too_long(self):
        from app.ytdlp_engine import _clean_error
        long_msg = "x" * 1000
        msg = _clean_error(Exception(long_msg))
        assert len(msg) <= 501  # 500 + ellipsis
        assert msg.endswith("…")

    def test_empty_returns_default(self):
        from app.ytdlp_engine import _clean_error
        assert _clean_error(Exception("")) == "Bilinmeyen indirme hatası"
