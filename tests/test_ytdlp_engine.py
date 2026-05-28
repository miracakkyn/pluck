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


class TestVideoUrlPatterns:
    """Sprint 9: çoklu medya URL regex'lerinin saf davranışı."""

    def test_bcdn_classic_playlist_m3u8(self):
        html = 'src: "https://video.b-cdn.net/abc123def-456/playlist.m3u8"'
        urls = ytdlp_engine._extract_urls_from_html(html)
        assert urls == ["https://video.b-cdn.net/abc123def-456/playlist.m3u8"]

    def test_bcdn_master_m3u8_with_query(self):
        # Broad pattern: playlist.m3u8 olmayan + query'li yakalanmalı.
        html = '"https://vz-1abc.b-cdn.net/aaaaaaaa-1111/master.m3u8?token=xyz"'
        urls = ytdlp_engine._extract_urls_from_html(html)
        assert any("master.m3u8" in u for u in urls)
        assert any("token=xyz" in u for u in urls)

    def test_mediadelivery_iframe_url(self):
        # Bunny Stream embed URL'leri yt-dlp tarafından çözülebilir.
        html = '<iframe src="https://iframe.mediadelivery.net/embed/12345/abc-def-1234"></iframe>'
        urls = ytdlp_engine._extract_urls_from_html(html)
        assert "https://iframe.mediadelivery.net/embed/12345/abc-def-1234" in urls

    def test_generic_m3u8_fallback(self):
        # BCDN/MediaDelivery dışı: generic m3u8 yedek pattern.
        html = '<source src="https://cdn.other-host.com/path/stream.m3u8">'
        urls = ytdlp_engine._extract_urls_from_html(html)
        assert "https://cdn.other-host.com/path/stream.m3u8" in urls

    def test_escaped_json_urls_normalized(self):
        # JSON içindeki \/ kaçışlı URL'ler normalize edilmeli.
        html = r'{"file":"https:\/\/cdn.b-cdn.net\/uuid-here\/playlist.m3u8"}'
        urls = ytdlp_engine._extract_urls_from_html(html)
        assert any("playlist.m3u8" in u for u in urls)

    def test_dedupe_preserves_order(self):
        html = (
            "https://a.b-cdn.net/aaaa/playlist.m3u8 "
            "https://a.b-cdn.net/aaaa/playlist.m3u8 "
            "https://b.b-cdn.net/bbbb/playlist.m3u8"
        )
        urls = ytdlp_engine._extract_urls_from_html(html)
        assert urls == [
            "https://a.b-cdn.net/aaaa/playlist.m3u8",
            "https://b.b-cdn.net/bbbb/playlist.m3u8",
        ]

    def test_multiple_distinct_videos(self):
        # uzemykoabt benzeri senaryo: 3 ayrı m3u8, hepsi bulunmalı.
        html = (
            'd1:"https://vz1.b-cdn.net/aaaa-1111/playlist.m3u8" '
            'd2:"https://vz1.b-cdn.net/bbbb-2222/playlist.m3u8" '
            'd3:"https://vz1.b-cdn.net/cccc-3333/playlist.m3u8"'
        )
        urls = ytdlp_engine._extract_urls_from_html(html)
        assert len(urls) == 3


class TestExtractEach:
    """Sprint 9: _extract_each warning toplama."""

    def test_returns_tuple_of_entries_and_warnings(self):
        ydl_class = _mock_ydl({**FAKE_INFO, "title": "OK"})
        with patch.object(ytdlp_engine, "YoutubeDL", ydl_class):
            entries, warnings = ytdlp_engine._extract_each(
                ["https://x.b-cdn.net/u/playlist.m3u8"],
                browser=None, referer="https://page",
            )
        assert len(entries) == 1
        assert warnings == []

    def test_failed_url_added_to_warnings(self):
        # Hata fırlatan URL sessizce yutulmaz; warning'e dahil edilir.
        ydl_class = MagicMock()
        ctx = ydl_class.return_value.__enter__.return_value
        ctx.extract_info.side_effect = DownloadError("ERROR: 403 forbidden")
        with patch.object(ytdlp_engine, "YoutubeDL", ydl_class):
            entries, warnings = ytdlp_engine._extract_each(
                ["https://bad.b-cdn.net/u/playlist.m3u8"],
                browser=None, referer="https://page",
            )
        assert entries == []
        assert len(warnings) == 1
        assert "Video 1 alınamadı" in warnings[0]
        assert "403 forbidden" in warnings[0]

    def test_mixed_success_and_failure(self):
        # 3 URL: ilki ve sonuncusu başarılı, ortadaki hata.
        ydl_class = MagicMock()
        ctx = ydl_class.return_value.__enter__.return_value
        ctx.extract_info.side_effect = [
            {**FAKE_INFO, "title": "Video A"},
            DownloadError("ERROR: missing"),
            {**FAKE_INFO, "title": "Video C"},
        ]
        with patch.object(ytdlp_engine, "YoutubeDL", ydl_class):
            entries, warnings = ytdlp_engine._extract_each(
                ["https://a", "https://b", "https://c"],
                browser=None, referer="https://page",
            )
        assert [e.title for e in entries] == ["Video A", "Video C"]
        assert len(warnings) == 1
        assert "Video 2 alınamadı" in warnings[0]


class TestListFormatsScanIntegration:
    """Sprint 9: list_formats generic + extra URL birleşimi."""

    def test_merges_generic_with_extra_urls(self):
        """Generic extractor'ın bulduğu URL feda edilmemeli — combined listeye dahil."""
        # Senaryo: yt-dlp generic ilk videoyu (Ders-1) buluyor, sayfa
        # regex'i Ders-2 + Ders-3 m3u8'lerini buluyor. Sonuçta entries=3 olmalı.
        ders1_info = {
            **FAKE_INFO,
            "title": "Ders 1",
            "webpage_url": "https://page.com/ders-1",
        }
        ders2_info = {**FAKE_INFO, "title": "Ders 2"}
        ders3_info = {**FAKE_INFO, "title": "Ders 3"}

        ydl_class = MagicMock()
        ctx = ydl_class.return_value.__enter__.return_value
        # 1. çağrı (list_formats ana extract) → Ders-1.
        # _scan_page_for_video_urls: urlopen mock'lanmamış → exception → []
        # _extract_each: combined=[ders-1-url] → tek URL → playlist'e geçmez
        # Bu yüzden _scan_page_for_video_urls'i mock'lamamız gerek.
        ctx.extract_info.side_effect = [
            ders1_info,
            ders2_info,
            ders3_info,
        ]
        with patch.object(ytdlp_engine, "YoutubeDL", ydl_class), \
             patch.object(
                ytdlp_engine, "_scan_page_for_video_urls",
                return_value=[
                    "https://cdn.b-cdn.net/uuid2/playlist.m3u8",
                    "https://cdn.b-cdn.net/uuid3/playlist.m3u8",
                ],
             ):
            scan = list_formats("https://page.com/ders-1")
        assert scan.type == "playlist"
        assert scan.entries is not None
        assert len(scan.entries) == 3
        assert scan.entries[0].title == "Ders 1"
        assert scan.entries[1].title == "Ders 2"
        assert scan.entries[2].title == "Ders 3"
        assert scan.warnings == []

    def test_warnings_propagated_to_scan_response(self):
        """_extract_each warning'leri ScanResponse.warnings'e taşınmalı."""
        ydl_class = MagicMock()
        ctx = ydl_class.return_value.__enter__.return_value
        # 1. çağrı: list_formats ana extract (generic_entry için kullanılır,
        #   tekrar fetch yok). 2. ve 3. çağrı: _extract_each iki extra URL —
        #   biri hata, biri başarılı.
        ctx.extract_info.side_effect = [
            {**FAKE_INFO, "title": "Ders 1", "webpage_url": "https://p/d1"},
            DownloadError("ERROR: token expired"),
            {**FAKE_INFO, "title": "Ders 3"},
        ]
        with patch.object(ytdlp_engine, "YoutubeDL", ydl_class), \
             patch.object(
                ytdlp_engine, "_scan_page_for_video_urls",
                return_value=[
                    "https://cdn.b-cdn.net/uuid2/playlist.m3u8",
                    "https://cdn.b-cdn.net/uuid3/playlist.m3u8",
                ],
             ):
            scan = list_formats("https://p/d1")
        assert scan.type == "playlist"
        assert len(scan.entries) == 2  # generic + 1 başarılı extra
        assert scan.entries[0].title == "Ders 1"
        assert scan.entries[1].title == "Ders 3"
        assert len(scan.warnings) == 1
        assert "token expired" in scan.warnings[0]

    def test_generic_url_dedup_when_in_extras(self):
        """extra_urls içinde generic URL varsa tekrar fetch edilmemeli."""
        # extra_urls listesi generic URL'yi içeriyor + 1 ek. _extract_each
        # yalnızca 1 kez çağrılmalı (generic atlanır, sadece ek için).
        ydl_class = MagicMock()
        ctx = ydl_class.return_value.__enter__.return_value
        ctx.extract_info.side_effect = [
            {**FAKE_INFO, "title": "Generic", "webpage_url": "https://p/g"},
            {**FAKE_INFO, "title": "Extra"},
        ]
        with patch.object(ytdlp_engine, "YoutubeDL", ydl_class), \
             patch.object(
                ytdlp_engine, "_scan_page_for_video_urls",
                return_value=[
                    "https://p/g",  # generic ile aynı — dedupe edilmeli
                    "https://cdn.b-cdn.net/x/playlist.m3u8",
                ],
             ):
            scan = list_formats("https://p/g")
        assert scan.type == "playlist"
        assert len(scan.entries) == 2
        assert [e.title for e in scan.entries] == ["Generic", "Extra"]


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
