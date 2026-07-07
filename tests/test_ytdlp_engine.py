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


@pytest.fixture(autouse=True)
def _offline_dns(monkeypatch):
    """Testlerde gerçek DNS yapma. _safe_discovered_url'in rebinding kontrolü
    varsayılan fail-open olur (çözülemedi → engelleme); rebinding testi override eder."""
    def _no_dns(host, *args, **kwargs):
        raise OSError("offline test")
    monkeypatch.setattr(ytdlp_engine, "_resolve_host", _no_dns)

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


def _mock_ydl_download(on_download):
    """`with YoutubeDL(opts) as ydl: ydl.download([url])` akışını taklit eder.

    `ydl.download()` çağrısında `on_download(opts)` çalıştırılır — böylece test,
    download()'ın verdiği progress/postprocessor hook'larını (opts içindeki)
    tetikleyip gerçek yt-dlp'nin "finished" davranışını modelleyebilir.
    """
    class _FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def download(self, urls):
            on_download(self.opts)

    return _FakeYDL


class TestAria2cOptions:
    """aria2c argümanlarının geçerliliği — canlı çöküşleri önler."""

    def test_min_split_size_within_aria2c_range(self):
        # aria2c -k (min-split-size) 1MiB–1GiB aralığında OLMALI; altında
        # (ör. 256K) "code 28: min-split-size must be between 1048576 and
        # 1073741824" ile çöker. Bu test o hatayı bir daha getirmez.
        args = ytdlp_engine._aria2c_download_options()[
            "external_downloader_args"]["aria2c"]
        k_val = None
        for i, arg in enumerate(args):
            if arg == "-k" and i + 1 < len(args):
                k_val = args[i + 1]
            elif arg.startswith("-k") and len(arg) > 2:
                k_val = arg[2:]
        assert k_val is not None, "aria2c -k (min-split-size) tanımlı olmalı"
        unit = k_val[-1].upper()
        mult = {"K": 1024, "M": 1024 ** 2, "G": 1024 ** 3}.get(unit, 1)
        num = int(k_val[:-1]) if unit in ("K", "M", "G") else int(k_val)
        size = num * mult
        assert 1048576 <= size <= 1073741824, \
            f"aria2c -k {k_val} ({size} B) geçerli aralık dışında → code 28"


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

    @pytest.mark.parametrize("preset", ["1080p", "720p", "480p"])
    def test_resolution_presets_have_unconstrained_fallback(self, preset):
        # İstenen yükseklik mevcut değilse (ör. yalnız 720p embed'de 480p)
        # sınırsız `/b` ile en iyi mevcuda düşülmeli — "format yok" hatası olmasın.
        sel = build_format_selector(preset)
        assert sel.endswith("/b")
        # Son dal koşulsuz olmalı (height kısıtı taşımamalı).
        assert sel.rsplit("/", 1)[-1] == "b"

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

    def test_download_returns_final_merged_path(self, tmp_path):
        # Çok-akışlı indirmede güvenilir yol yalnızca merge sonrası bilinir;
        # download() bu nihai yolu döndürmeli (queue_manager job.filename için).
        target = tmp_path / "Test [id] best.mp4"

        def _fire(opts):
            target.write_bytes(b"x")
            opts["progress_hooks"][0](
                {"status": "finished", "filename": str(target)}
            )

        with patch.object(ytdlp_engine, "YoutubeDL", _mock_ydl_download(_fire)):
            result = ytdlp_engine.download(
                url="https://x/v", selection="best", download_dir=str(tmp_path),
            )
        assert result == str(target)

    def test_download_raises_when_final_file_missing(self, tmp_path):
        # ffmpeg merge sessizce çöktü: "finished" bildirilir ama dosya diskte yok.
        def _fire(opts):
            opts["progress_hooks"][0](
                {"status": "finished", "filename": str(tmp_path / "yok.mp4")}
            )

        with patch.object(ytdlp_engine, "YoutubeDL", _mock_ydl_download(_fire)):
            with pytest.raises(EngineError):
                ytdlp_engine.download(
                    url="https://x/v", selection="best", download_dir=str(tmp_path),
                )

    def test_explicit_referer_sets_header(self, tmp_path):
        # Eklenti rozeti temiz embed URL + sayfa referer'ı gönderir.
        ydl_class = _mock_ydl()
        with patch.object(ytdlp_engine, "YoutubeDL", ydl_class):
            ytdlp_engine.download(
                url="https://iframe.mediadelivery.net/embed/1/abc",
                selection="best", download_dir=str(tmp_path),
                referer="https://uzemykoabt.com/sayfa/",
            )
        headers = ydl_class.call_args[0][0].get("http_headers") or {}
        assert headers.get("Referer") == "https://uzemykoabt.com/sayfa/"

    def test_referer_overrides_bcdn_default(self, tmp_path):
        # Açık referer verildiğinde b-cdn varsayılanı ezilir.
        ydl_class = _mock_ydl()
        with patch.object(ytdlp_engine, "YoutubeDL", ydl_class):
            ytdlp_engine.download(
                url="https://vz.b-cdn.net/u/playlist.m3u8",
                selection="best", download_dir=str(tmp_path),
                referer="https://page.example/",
            )
        headers = ydl_class.call_args[0][0].get("http_headers") or {}
        assert headers.get("Referer") == "https://page.example/"

    def test_bcdn_default_referer_when_none_given(self, tmp_path):
        # referer yoksa doğrudan b-cdn m3u8 için mediadelivery referer'ı.
        ydl_class = _mock_ydl()
        with patch.object(ytdlp_engine, "YoutubeDL", ydl_class):
            ytdlp_engine.download(
                url="https://vz.b-cdn.net/u/playlist.m3u8",
                selection="best", download_dir=str(tmp_path),
            )
        headers = ydl_class.call_args[0][0].get("http_headers") or {}
        assert headers.get("Referer") == "https://iframe.mediadelivery.net/"


class TestFfmpegPrecheck:
    def test_missing_ffmpeg_raises_for_video(self, tmp_path, monkeypatch):
        # _ffmpeg_available() re-probe yaptığından (kurulum-sonrası tespit için),
        # "ffmpeg yok" senaryosunu helper'ı doğrudan mock'layarak kur.
        monkeypatch.setattr(ytdlp_engine, "_ffmpeg_available", lambda: False)
        with pytest.raises(EngineError) as exc:
            ytdlp_engine.download(
                url="https://x/v", selection="best", download_dir=str(tmp_path),
            )
        assert "ffmpeg" in str(exc.value).lower()

    def test_missing_ffmpeg_raises_for_audio(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ytdlp_engine, "_ffmpeg_available", lambda: False)
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

    def test_cancel_cleans_part_files(self, tmp_path):
        """İptal (progress hook'tan non-YoutubeDLError) sonrası .part temizlenmeli.

        id 'downloading' event'inde yakalanmalı (finished hiç gelmez) ve cleanup
        YoutubeDLError olmayan istisnada da çalışmalı."""
        class _Cancelled(Exception):
            pass
        # İndirme başladı, parça dosyaları oluştu, sonra kullanıcı iptal etti.
        part = tmp_path / "video [vid42] 480p.mp4.part-Frag1"
        part.write_bytes(b"x")
        ydl_class = MagicMock()
        ctx = ydl_class.return_value.__enter__.return_value

        def fake_download(_urls):
            opts = ydl_class.call_args[0][0]
            # downloading event: id burada gelir (finished asla gelmez)
            opts["progress_hooks"][0]({
                "status": "downloading", "downloaded_bytes": 1, "total_bytes": 100,
                "info_dict": {"id": "vid42"},
            })
            raise _Cancelled()  # kullanıcı iptali

        ctx.download.side_effect = fake_download
        with patch.object(ytdlp_engine, "YoutubeDL", ydl_class):
            with pytest.raises(_Cancelled):  # istisna aynen yükseltilmeli
                ytdlp_engine.download(
                    url="https://x/v", selection="480p",
                    download_dir=str(tmp_path),
                )
        assert not part.exists()  # iptal sonrası çöp parça temizlendi


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

    def test_removes_aria2c_fragment_files(self, tmp_path):
        # aria2c HLS parçaları leftover işaretini adın ORTASINDA taşır:
        # 'video [abc123] 480p.mp4.part-Frag44' — endswith(".part") tutmaz,
        # substring eşleşmesi gerekir.
        frag = tmp_path / "video [abc123] 480p.mp4.part-Frag44"
        frag_aria = tmp_path / "video [abc123] 480p.mp4.part-Frag44.aria2"
        done = tmp_path / "video [abc123] 480p.mp4"
        for f in (frag, frag_aria, done):
            f.write_bytes(b"x")
        ytdlp_engine._cleanup_artifacts(tmp_path, {"id": "abc123"})
        assert not frag.exists()
        assert not frag_aria.exists()
        assert done.exists()  # tamamlanmış dosya korunur

    def test_removes_by_name_prefix_when_id_missing(self, tmp_path):
        # İptal senaryosu: id yakalanamadı ama title-locked outtmpl öneki biliniyor.
        frag = tmp_path / "Video 1 [playlist] 480p.mp4.part-Frag5"
        bare = tmp_path / "Video 1 [playlist] 480p.mp4.part"
        other = tmp_path / "Baska Video [xyz] best.mp4.part"  # farklı önek → korunur
        for f in (frag, bare, other):
            f.write_bytes(b"x")
        ytdlp_engine._cleanup_artifacts(tmp_path, {}, name_prefix="Video 1 [")
        assert not frag.exists()
        assert not bare.exists()
        assert other.exists()  # başka indirmenin parçası korunur

    def test_no_id_and_no_prefix_is_noop(self, tmp_path):
        part = tmp_path / "video [abc123] best.part"
        part.write_bytes(b"x")
        ytdlp_engine._cleanup_artifacts(tmp_path, {})  # ne id ne önek
        assert part.exists()  # bilinmiyor → dokunma

    def test_missing_dir_silently_ignored(self, tmp_path):
        from pathlib import Path
        ytdlp_engine._cleanup_artifacts(Path(tmp_path / "yok"), {"id": "x"})


class TestUnknownCodecFormats:
    """HLS m3u8 varyantları codec bildirmez; yine de listelenmeli."""

    def test_hls_variant_without_codecs_is_combined(self):
        # vcodec/acodec None ama çözünürlük var → muxed video (combined).
        info = {"title": "HLS", "formats": [
            {"format_id": "2800", "ext": "mp4", "vcodec": None,
             "acodec": None, "resolution": "1280x720", "height": 720},
        ]}
        with patch.object(ytdlp_engine, "YoutubeDL", _mock_ydl(info)):
            resp = list_formats("https://x/v").video
        assert len(resp.formats) == 1
        assert resp.formats[0].kind == "combined"
        assert resp.formats[0].format_id == "2800"

    def test_multiple_hls_variants_all_listed(self):
        # BunnyCDN tarzı 5 çözünürlük — hepsi listelenmeli (eskiden hepsi elenirdi).
        info = {"title": "Ders 1", "formats": [
            {"format_id": str(br), "ext": "mp4", "vcodec": None, "acodec": None,
             "resolution": res, "height": h}
            for br, res, h in [
                ("600", "352x240", 240), ("800", "640x360", 360),
                ("1400", "842x480", 480), ("2800", "1280x720", 720),
                ("5000", "1920x1080", 1080),
            ]
        ]}
        with patch.object(ytdlp_engine, "YoutubeDL", _mock_ydl(info)):
            resp = list_formats("https://x/v").video
        assert len(resp.formats) == 5
        assert all(f.kind == "combined" for f in resp.formats)
        # En yüksek çözünürlük başta (sort).
        assert resp.formats[0].height == 1080

    def test_audio_only_without_codecs_inferred_from_bitrate(self):
        info = {"title": "A", "formats": [
            {"format_id": "audio-0", "ext": "m4a", "vcodec": None,
             "acodec": None, "abr": 128},  # boyut yok ama bitrate var → audio
        ]}
        with patch.object(ytdlp_engine, "YoutubeDL", _mock_ydl(info)):
            resp = list_formats("https://x/v").video
        assert len(resp.formats) == 1
        assert resp.formats[0].kind == "audio"

    def test_truly_unclassifiable_format_dropped(self):
        # Ne codec, ne boyut, ne bitrate → elenmeli (eski davranış korunur).
        info = {"title": "X", "formats": [
            {"format_id": "junk", "ext": "mp4", "vcodec": None, "acodec": None},
            {"format_id": "ok", "ext": "mp4", "vcodec": "avc1",
             "acodec": "mp4a", "height": 360, "resolution": "640x360"},
        ]}
        with patch.object(ytdlp_engine, "YoutubeDL", _mock_ydl(info)):
            resp = list_formats("https://x/v").video
        ids = {f.format_id for f in resp.formats}
        assert ids == {"ok"}  # junk elenir

    def test_explicit_codecs_still_classified_normally(self):
        # codec açıkça bildirilmişse inference devreye girmez.
        from app.ytdlp_engine import _classify
        assert _classify("avc1", "mp4a") == "combined"
        assert _classify("avc1", "none") == "video"
        assert _classify("none", "mp4a") == "audio"


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

    def test_loopback_and_linklocal_urls_filtered_ssrf(self):
        # SSRF savunması: sayfadan çıkarılan loopback/link-local URL'ler elenir;
        # yalnızca dış (public) medya URL'leri kalır.
        html = (
            'a:"http://127.0.0.1:5000/secret.mp4" '
            'b:"https://cdn.other-host.com/ok.m3u8" '
            'c:"http://169.254.169.254/latest/meta.m3u8"'
        )
        urls = ytdlp_engine._extract_urls_from_html(html)
        assert urls == ["https://cdn.other-host.com/ok.m3u8"]

    def test_iframe_host_substring_bypass_rejected(self):
        # _IFRAME_RE substring eşleşse de gerçek host parse edilip doğrulanır:
        # loopback bir host, path'inde 'mediadelivery.net' geçse bile reddedilir.
        assert ytdlp_engine._is_known_iframe_host(
            "https://iframe.mediadelivery.net/embed/1/abc") is True
        assert ytdlp_engine._is_known_iframe_host(
            "http://127.0.0.1:9000/x?d=mediadelivery.net") is False

    def test_dns_rebinding_domain_rejected(self, monkeypatch):
        # Alan adı loopback'e çözülürse keşfedilen URL reddedilir (DNS rebinding);
        # public IP'ye çözülürse geçer.
        monkeypatch.setattr(
            ytdlp_engine, "_resolve_host",
            lambda host, *a, **k: [(2, 1, 6, "", ("127.0.0.1", 0))],
        )
        assert ytdlp_engine._safe_discovered_url(
            "https://evil.example/x.m3u8") is None
        monkeypatch.setattr(
            ytdlp_engine, "_resolve_host",
            lambda host, *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))],
        )
        assert ytdlp_engine._safe_discovered_url(
            "https://ok.example/x.m3u8") == "https://ok.example/x.m3u8"


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


# ---------------------------------------------------------------------------
# Sprint 18: kapsam boşlukları — sayfa tarama, temizlik, title outtmpl,
# playlist merge, _extract_each davranışı, saf yardımcılar.
# ---------------------------------------------------------------------------


def _mock_ydl_urlopen(html_by_url, fetched=None):
    """`with YoutubeDL(opts) as ydl: ydl.urlopen(url).read()` akışını taklit eder.

    `html_by_url[url]` string'i UTF-8 baytlara çevrilip döndürülür; URL sözlükte
    yoksa OSError fırlatılır (fetch başarısızlığı — best-effort tarama testi).
    `fetched` verilirse fetch edilen her URL sırayla ona eklenir (SSRF savunması
    için hangi kaynakların gerçekten çekildiğini doğrulamak amacıyla).
    """
    class _Resp:
        def __init__(self, data: bytes):
            self._data = data

        def read(self) -> bytes:
            return self._data

    class _FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def urlopen(self, url):
            if fetched is not None:
                fetched.append(url)
            if url in html_by_url:
                return _Resp(html_by_url[url].encode("utf-8"))
            raise OSError(f"mock yok: {url}")

    return _FakeYDL


class TestScanPageForVideoUrls:
    """#10 _scan_page_for_video_urls: ana sayfa + iframe ikinci geçiş + SSRF."""

    def test_main_page_and_iframe_second_pass_collect_all(self):
        # Ana sayfa: 1 doğrudan m3u8 + 2 iframe (mediadelivery + jwplayer).
        # Her iframe'in İÇİNDE ayrı bir m3u8 var → ikinci geçişte toplanmalı.
        page_url = "https://course.example/ders-1"
        md_embed = "https://iframe.mediadelivery.net/embed/12345/abc-def-1234"
        jw_iframe = "https://cdn.jwplayer.com/players/xyz789"
        main_m3u8 = "https://cdn.other-host.com/main/stream.m3u8"
        md_inner = "https://vz.b-cdn.net/abcd1234-5678/playlist.m3u8"
        jw_inner = "https://media.jwpsrv.com/inner/clip.m3u8"
        main_html = (
            f'<iframe src="{md_embed}"></iframe>'
            f'<iframe src="{jw_iframe}"></iframe>'
            f'<source src="{main_m3u8}">'
        )
        html_by_url = {
            page_url: main_html,
            md_embed: f'<source src="{md_inner}">',
            jw_iframe: f'<source src="{jw_inner}">',
        }
        fetched: list[str] = []
        with patch.object(
            ytdlp_engine, "YoutubeDL",
            _mock_ydl_urlopen(html_by_url, fetched),
        ):
            result = ytdlp_engine._scan_page_for_video_urls(page_url, None)
        # 5 URL: ana m3u8 + md embed adayı + md iframe m3u8 + jw iframe adayı +
        # jw iframe m3u8. Hepsi tam olarak bir kez, fazlası yok.
        assert set(result) == {md_embed, main_m3u8, md_inner, jw_iframe, jw_inner}
        assert len(result) == 5
        # İkinci-geçiş kanıtı: iç m3u8'ler yalnızca iframe fetch'inden gelebilir.
        assert md_inner in result
        assert jw_inner in result
        # Fetch sırası: ana sayfa, sonra iki bilinen iframe (jwplayer adayı 507
        # satırında listeye eklenir çünkü ana taramada yakalanmaz).
        assert fetched == [page_url, md_embed, jw_iframe]

    def test_main_page_fetch_failure_returns_empty(self):
        # urlopen ana sayfada exception → best-effort → [] (generic sonuca düşülür).
        with patch.object(
            ytdlp_engine, "YoutubeDL", _mock_ydl_urlopen({}),
        ):
            result = ytdlp_engine._scan_page_for_video_urls(
                "https://course.example/x", None
            )
        assert result == []

    def test_iframe_fetch_failure_keeps_candidate_and_continues(self):
        # iframe adayı (508-518) fetch'i patlarsa: aday yine de listede kalır
        # (506-507 fetch'ten ÖNCE eklenir), iç URL eklenmez, fonksiyon çökmez.
        page_url = "https://course.example/ders-2"
        jw_iframe = "https://cdn.jwplayer.com/players/broken"
        main_m3u8 = "https://cdn.host.com/only/main.m3u8"
        main_html = (
            f'<iframe src="{jw_iframe}"></iframe>'
            f'<source src="{main_m3u8}">'
        )
        # jw_iframe html_by_url'de YOK → urlopen OSError → except: continue.
        fetched: list[str] = []
        with patch.object(
            ytdlp_engine, "YoutubeDL",
            _mock_ydl_urlopen({page_url: main_html}, fetched),
        ):
            result = ytdlp_engine._scan_page_for_video_urls(page_url, None)
        assert set(result) == {main_m3u8, jw_iframe}  # aday korunur, iç URL yok
        assert fetched == [page_url, jw_iframe]  # iframe fetch denendi ve patladı

    def test_ssrf_iframes_rejected_before_fetch(self):
        # SSRF savunması gerçek akışta:
        #  1) evil.example: _IFRAME_RE substring ('mediadelivery.net' query'de)
        #     eşleşir ama parse edilen host bilinen host DEĞİL →
        #     _is_known_iframe_host False → fetch edilmez.
        #  2) 127.0.0.1: _IFRAME_RE ('b-cdn.net' query'de) eşleşir ama loopback →
        #     _safe_discovered_url None → fetch edilmez.
        page_url = "https://course.example/ders-3"
        safe_m3u8 = "https://cdn.safe-host.com/ok.m3u8"
        main_html = (
            '<iframe src="https://evil.example/embed?ref=mediadelivery.net"></iframe>'
            '<iframe src="http://127.0.0.1:9000/p?d=b-cdn.net"></iframe>'
            f'<source src="{safe_m3u8}">'
        )
        fetched: list[str] = []
        with patch.object(
            ytdlp_engine, "YoutubeDL",
            _mock_ydl_urlopen({page_url: main_html}, fetched),
        ):
            result = ytdlp_engine._scan_page_for_video_urls(page_url, None)
        assert result == [safe_m3u8]          # yalnız güvenli dış m3u8 kalır
        assert fetched == [page_url]          # kötü iframe'ler ASLA fetch edilmedi


class TestCleanupPartialFiles:
    """#11 cleanup_partial_files (public, title-önek tabanlı ikinci geçiş)."""

    def test_removes_prefix_matched_leftovers(self, tmp_path):
        keep_done = tmp_path / "My Video [abc123] 720p.mp4"        # tamam → korunur
        keep_other = tmp_path / "Other Video [xyz] 720p.mp4.part"  # farklı önek
        part = tmp_path / "My Video [abc123] 720p.mp4.part"
        ytdl = tmp_path / "My Video [abc123] 720p.ytdl"
        frag_aria = tmp_path / "My Video [abc123] 720p.mp4.part-Frag3.aria2"
        for f in (keep_done, keep_other, part, ytdl, frag_aria):
            f.write_bytes(b"x")
        ytdlp_engine.cleanup_partial_files(
            str(tmp_path), title="My Video", selection="720p"
        )
        assert keep_done.exists()   # leftover işareti yok → önek eşleşse de korunur
        assert keep_other.exists()  # farklı önek → başka indirmenin parçası
        assert not part.exists()
        assert not ytdl.exists()
        assert not frag_aria.exists()

    def test_no_title_is_noop(self, tmp_path):
        part = tmp_path / "Video [abc] 720p.mp4.part"
        part.write_bytes(b"x")
        ytdlp_engine.cleanup_partial_files(
            str(tmp_path), title=None, selection="720p"
        )
        assert part.exists()  # önek bilinmiyor → dokunma

    def test_empty_title_is_noop(self, tmp_path):
        part = tmp_path / "Video [abc] 720p.mp4.part"
        part.write_bytes(b"x")
        ytdlp_engine.cleanup_partial_files(
            str(tmp_path), title="", selection="720p"
        )
        assert part.exists()  # boş title falsy → erken çıkış


class TestCleanupArtifactsRetry:
    """#12 _cleanup_artifacts kilitli-dosya retry döngüsü (gc + backoff + max_wait)."""

    def test_locked_file_retries_gc_and_terminates(self, tmp_path, monkeypatch):
        import itertools
        from pathlib import Path

        part = tmp_path / "video [abc123] best.mp4.part"
        part.write_bytes(b"x")

        # unlink kalıcı olarak başarısız (kilitli dosya simülasyonu).
        attempts = {"n": 0}

        def _fail_unlink(self, *a, **k):
            attempts["n"] += 1
            raise OSError("kilitli")

        monkeypatch.setattr(Path, "unlink", _fail_unlink)

        gc_mock = MagicMock()
        monkeypatch.setattr(ytdlp_engine.gc, "collect", gc_mock)
        sleep_mock = MagicMock()
        monkeypatch.setattr(ytdlp_engine.time, "sleep", sleep_mock)
        # monotonic'i deterministik yap: deadline'ı birkaç turdan sonra aşsın
        # (sonsuz sayaç → StopIteration riski yok, sürekli artar → mutlaka biter).
        counter = itertools.count(100.0, 0.1)
        monkeypatch.setattr(ytdlp_engine.time, "monotonic", lambda: next(counter))

        # Kalıcı kilit olsa bile fonksiyon max_wait ile sınırlı — takılmamalı.
        ytdlp_engine._cleanup_artifacts(
            tmp_path, {"id": "abc123"}, max_wait=0.25
        )

        assert gc_mock.call_count >= 1   # tutamak serbest bırakma için gc denendi
        assert attempts["n"] >= 2        # ilk başarısızlıktan sonra yeniden denendi
        assert sleep_mock.called         # denemeler arası backoff uygulandı
        assert part.exists()             # silinemedi ama fonksiyon geri döndü


class TestDownloadTitle:
    """#13 download(title=...): outtmpl öneki + cleanup_prefix kullanımı."""

    def test_title_sets_outtmpl_and_returns_final(self, tmp_path):
        # title dosya-adı-güvenli hale getirilir; '/' → '_'. outtmpl bu öneki taşır.
        target = tmp_path / "Ders 1_Bölüm 2 [vid] 720p.mp4"
        captured: dict = {}

        def _fire(opts):
            captured["opts"] = opts
            target.write_bytes(b"x")  # nihai dosya diskte var
            opts["postprocessor_hooks"][0]({
                "status": "finished",
                "info_dict": {"filepath": str(target)},
            })

        with patch.object(ytdlp_engine, "YoutubeDL", _mock_ydl_download(_fire)):
            result = ytdlp_engine.download(
                url="https://x/v", selection="720p",
                download_dir=str(tmp_path), title="Ders 1/Bölüm 2",
            )
        # outtmpl: 'safe_title [%(id)s] selection.%(ext)s' — id ve ext yt-dlp'ye kalır.
        assert captured["opts"]["outtmpl"] == "Ders 1_Bölüm 2 [%(id)s] 720p.%(ext)s"
        # postprocessor hook'undan yakalanan nihai yol döndürülür (_pp_hook: 740-744).
        assert result == str(target)

    def test_title_cleanup_prefix_removes_leftover_on_failed_merge(self, tmp_path):
        # id yakalanmasa bile (yalnız pp hook ateşlendi, progress yok) title öneki
        # ile çöp parça temizlenir — merge sessizce başarısız olursa (dosya yok).
        leftover = tmp_path / "Ders 1 [vid99] 480p.mp4.part"
        unrelated = tmp_path / "Baska [x] 480p.mp4.part"
        leftover.write_bytes(b"x")
        unrelated.write_bytes(b"x")
        missing_final = tmp_path / "Ders 1 [vid99] 480p.mp4"  # diskte YOK
        captured: dict = {}

        def _fire(opts):
            captured["opts"] = opts
            opts["postprocessor_hooks"][0]({
                "status": "finished",
                "info_dict": {"filepath": str(missing_final)},
            })

        with patch.object(ytdlp_engine, "YoutubeDL", _mock_ydl_download(_fire)):
            with pytest.raises(EngineError):
                ytdlp_engine.download(
                    url="https://x/v", selection="480p",
                    download_dir=str(tmp_path), title="Ders 1",
                )
        assert captured["opts"]["outtmpl"] == "Ders 1 [%(id)s] 480p.%(ext)s"
        assert not leftover.exists()   # cleanup_prefix 'Ders 1 [' ile silindi
        assert unrelated.exists()      # farklı önek → korunur


class TestPlaylistScanMerge:
    """#16 list_formats playlist dalı + ek-keşfedilen-URL birleşimi."""

    def test_playlist_merges_extra_discovered_urls(self):
        playlist_info = {
            "_type": "playlist",
            "title": "Kurs",
            "entries": [
                {**FAKE_INFO, "webpage_url": "https://x/v1", "title": "Ders 1"},
                {**FAKE_INFO, "webpage_url": "https://x/v2", "title": "Ders 2"},
            ],
        }
        extra_info = {**FAKE_INFO, "title": "Ders 3 (ek)"}
        ydl_class = MagicMock()
        ctx = ydl_class.return_value.__enter__.return_value
        # 1. çağrı: playlist extract. 2. çağrı: _extract_each ek URL.
        ctx.extract_info.side_effect = [playlist_info, extra_info]
        extra_url = "https://cdn.b-cdn.net/uuid9/playlist.m3u8"
        with patch.object(ytdlp_engine, "YoutubeDL", ydl_class), \
             patch.object(
                ytdlp_engine, "_scan_page_for_video_urls",
                return_value=[extra_url],
             ):
            scan = list_formats("https://x/playlist")
        assert scan.type == "playlist"
        assert scan.playlist_title == "Kurs"
        assert [e.title for e in scan.entries] == ["Ders 1", "Ders 2", "Ders 3 (ek)"]
        assert scan.entries[2].url == extra_url  # ek URL girdiye taşındı
        assert scan.warnings == []
        # playlist + tek ek URL = 2 extract (entry başına yeniden fetch YOK).
        assert ctx.extract_info.call_count == 2

    def test_playlist_skips_extra_url_already_in_entries(self):
        # Ek URL zaten playlist girdisiyle aynıysa dedupe edilir (tekrar fetch yok).
        playlist_info = {
            "_type": "playlist",
            "title": "Kurs",
            "entries": [
                {**FAKE_INFO, "webpage_url": "https://x/v1", "title": "Ders 1"},
                {**FAKE_INFO, "webpage_url": "https://x/v2", "title": "Ders 2"},
            ],
        }
        extra_info = {**FAKE_INFO, "title": "Ders 3"}
        ydl_class = MagicMock()
        ctx = ydl_class.return_value.__enter__.return_value
        ctx.extract_info.side_effect = [playlist_info, extra_info]
        with patch.object(ytdlp_engine, "YoutubeDL", ydl_class), \
             patch.object(
                ytdlp_engine, "_scan_page_for_video_urls",
                return_value=["https://x/v1", "https://x/v3"],  # v1 → dupe
             ):
            scan = list_formats("https://x/playlist")
        assert len(scan.entries) == 3       # 2 playlist + 1 gerçek ek (v3)
        assert scan.entries[2].url == "https://x/v3"
        # v1 zaten girdide → tekrar extract edilmez: playlist + v3 = 2 çağrı.
        assert ctx.extract_info.call_count == 2


class TestExtractEachMore:
    """#36 generic-title değişimi + #39 cookies/referer opsiyonları."""

    def test_generic_titles_renamed_to_video_n(self):
        # 'playlist'/'index'/boş/None → 'Video N'; gerçek başlık korunur.
        ydl_class = MagicMock()
        ctx = ydl_class.return_value.__enter__.return_value
        ctx.extract_info.side_effect = [
            {**FAKE_INFO, "title": "playlist"},   # → Video 1
            {**FAKE_INFO, "title": "INDEX"},      # case-insensitive → Video 2
            {**FAKE_INFO, "title": None},         # None → "" → Video 3
            {**FAKE_INFO, "title": "Gerçek Ders"},  # korunur
        ]
        with patch.object(ytdlp_engine, "YoutubeDL", ydl_class):
            entries, warnings = ytdlp_engine._extract_each(
                ["https://a", "https://b", "https://c", "https://d"],
                browser=None, referer="https://page",
            )
        assert [e.title for e in entries] == [
            "Video 1", "Video 2", "Video 3", "Gerçek Ders",
        ]
        assert [e.url for e in entries] == [
            "https://a", "https://b", "https://c", "https://d",
        ]
        assert warnings == []

    def test_browser_adds_cookies_and_referer_header(self):
        ydl = _mock_ydl({**FAKE_INFO, "title": "V"})
        with patch.object(ytdlp_engine, "YoutubeDL", ydl):
            entries, warnings = ytdlp_engine._extract_each(
                ["https://x/v"], browser="firefox", referer="https://page/",
            )
        opts = ydl.call_args[0][0]
        assert opts["cookiesfrombrowser"] == ("firefox",)
        assert opts["http_headers"]["Referer"] == "https://page/"
        assert opts["skip_download"] is True
        assert len(entries) == 1

    def test_no_browser_omits_cookies_but_keeps_referer(self):
        ydl = _mock_ydl({**FAKE_INFO, "title": "V"})
        with patch.object(ytdlp_engine, "YoutubeDL", ydl):
            ytdlp_engine._extract_each(
                ["https://x/v"], browser=None, referer="https://p/",
            )
        opts = ydl.call_args[0][0]
        assert "cookiesfrombrowser" not in opts
        assert opts["http_headers"]["Referer"] == "https://p/"


class TestUrlDedupKey:
    """_url_dedup_key: host+path'e indirger; parse hatasında orijinali döndürür."""

    def test_strips_query_fragment_and_lowercases_host(self):
        key = ytdlp_engine._url_dedup_key(
            "HTTPS://CDN.Example.COM/Path/x?a=1&b=2#frag"
        )
        # scheme+host küçük harf; path olduğu gibi; query/fragment atılır.
        assert key == "https://cdn.example.com/Path/x"

    def test_invalid_ipv6_url_returns_original(self):
        # Kapanmamış IPv6 parantezi → urlsplit ValueError → orijinal aynen döner.
        bad = "http://[::1"
        assert ytdlp_engine._url_dedup_key(bad) == bad


class TestResolvesToInternal:
    """_resolves_to_internal: geçersiz addrinfo atlanır; iç adres tespit edilir."""

    def test_skips_unparseable_addr_then_detects_loopback(self, monkeypatch):
        # İlk addrinfo geçersiz IP (ValueError) → atlanır; ikinci loopback → True.
        monkeypatch.setattr(
            ytdlp_engine, "_resolve_host",
            lambda host, *a, **k: [
                (2, 1, 6, "", ("not-an-ip", 0)),
                (2, 1, 6, "", ("127.0.0.1", 0)),
            ],
        )
        assert ytdlp_engine._resolves_to_internal("evil.example") is True

    def test_skips_malformed_tuple_then_detects_linklocal(self, monkeypatch):
        # info[4] boş tuple → IndexError → atlanır; ikinci link-local → True.
        monkeypatch.setattr(
            ytdlp_engine, "_resolve_host",
            lambda host, *a, **k: [
                (2, 1, 6, "", ()),
                (2, 1, 6, "", ("169.254.10.20", 0)),
            ],
        )
        assert ytdlp_engine._resolves_to_internal("evil.example") is True

    def test_public_addr_is_not_internal(self, monkeypatch):
        monkeypatch.setattr(
            ytdlp_engine, "_resolve_host",
            lambda host, *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))],
        )
        assert ytdlp_engine._resolves_to_internal("ok.example") is False

    def test_resolution_failure_fails_open(self, monkeypatch):
        # DNS hatası → False (fail-open): geçici hata geçerli URL'yi reddetmez.
        def _boom(host, *a, **k):
            raise OSError("dns down")
        monkeypatch.setattr(ytdlp_engine, "_resolve_host", _boom)
        assert ytdlp_engine._resolves_to_internal("whatever.example") is False
