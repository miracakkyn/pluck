"""models.py — pydantic şemaları ve Job veri yapısı testleri."""
import pytest
from pydantic import ValidationError

from app.models import FormatsRequest, JobRequest, Job, ProbeUrlsRequest


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

    def test_hostless_url_rejected(self):
        # Şema var ama host yok ("http://") → urlparse hostname None → boş host.
        # _validate_url bu dalı ("URL geçerli bir adres içermeli") ile reddetmeli.
        with pytest.raises(ValidationError) as exc:
            FormatsRequest(url="http://")
        assert "geçerli bir adres" in str(exc.value)

    def test_hostless_https_url_rejected(self):
        # https:// varyantı da aynı host'suz dalı tetikler.
        with pytest.raises(ValidationError):
            FormatsRequest(url="https://")

    def test_crlf_in_url_rejected(self):
        # HTTP header injection savunması — ortadaki CR/LF reddedilmeli.
        with pytest.raises(ValidationError):
            FormatsRequest(url="https://example.com/\r\nX-Injected: evil")

    def test_newline_in_url_rejected(self):
        with pytest.raises(ValidationError):
            FormatsRequest(url="https://example.com/\npath")

    def test_tab_control_char_rejected(self):
        with pytest.raises(ValidationError):
            FormatsRequest(url="https://example.com/\tpath")

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
        with pytest.raises(ValidationError) as exc:
            JobRequest(
                url="https://example.com/v", selection="bad; rm -rf",
                download_dir=str(tmp_path),
            )
        # Geçersiz karakterli seçim özel mesajla reddedilmeli (boş-seçim mesajı değil).
        assert "Geçersiz format seçimi" in str(exc.value)

    def test_whitespace_only_selection_rejected(self, tmp_path):
        # Sadece boşluktan oluşan selection strip sonrası boşalır → "Bir kalite/
        # format seçilmeli" dalı (models.py ~210). Boş-string ile ayrı mesaj vermeli.
        with pytest.raises(ValidationError) as exc:
            JobRequest(
                url="https://example.com/v", selection="   ",
                download_dir=str(tmp_path),
            )
        assert "Bir kalite/format seçilmeli" in str(exc.value)

    def test_empty_selection_rejected(self, tmp_path):
        with pytest.raises(ValidationError) as exc:
            JobRequest(
                url="https://example.com/v", selection="",
                download_dir=str(tmp_path),
            )
        assert "Bir kalite/format seçilmeli" in str(exc.value)

    def test_selection_whitespace_is_stripped(self, tmp_path):
        # Kenardaki boşluk temizlenmeli; iç değer korunmalı.
        req = JobRequest(
            url="https://example.com/v", selection="  720p  ",
            download_dir=str(tmp_path),
        )
        assert req.selection == "720p"

    def test_missing_dir_message_hidden(self, tmp_path):
        # Var olmayan klasör reddedilir; modelin ürettiği HATA MESAJI ham yolu
        # yansıtmamalı (bilgi sızması). Not: Pydantic'in ValidationError.__str__
        # girdi değerini ("input") ayrıca yankılar; bu modelin mesajı değildir,
        # bu yüzden yalnız errors()[..]["msg"] alanını denetliyoruz.
        missing = tmp_path / "yok" / "gizli_klasor_adi"
        with pytest.raises(ValidationError) as exc:
            JobRequest(url="https://example.com/v", selection="best",
                       download_dir=str(missing))
        msg = exc.value.errors()[0]["msg"]
        assert "bulunamadı veya erişilemez" in msg
        assert "gizli_klasor_adi" not in msg  # ham yol modelin mesajında olmamalı

    def test_referer_none_explicit_becomes_none(self, tmp_path):
        # Açıkça None verildiğinde de None dalı çalışmalı (validator None'ı yutar).
        req = JobRequest(url="https://example.com/v", selection="best",
                         download_dir=str(tmp_path), referer=None)
        assert req.referer is None

    def test_referer_optional_default_none(self, tmp_path):
        req = JobRequest(url="https://example.com/v", selection="best",
                         download_dir=str(tmp_path))
        assert req.referer is None

    def test_referer_accepted(self, tmp_path):
        req = JobRequest(url="https://cdn.example/v.m3u8", selection="best",
                         download_dir=str(tmp_path),
                         referer="https://uzemykoabt.com/sayfa/")
        assert req.referer == "https://uzemykoabt.com/sayfa/"

    def test_blank_referer_becomes_none(self, tmp_path):
        req = JobRequest(url="https://example.com/v", selection="best",
                         download_dir=str(tmp_path), referer="  ")
        assert req.referer is None

    def test_invalid_referer_rejected(self, tmp_path):
        # Referer de URL doğrulamasından geçer (loopback/şema reddi).
        with pytest.raises(ValidationError):
            JobRequest(url="https://example.com/v", selection="best",
                       download_dir=str(tmp_path), referer="javascript:alert(1)")

    def test_referer_crlf_header_injection_rejected(self, tmp_path):
        # Referer http_headers'a girdiği için CRLF injection özellikle tehlikeli.
        with pytest.raises(ValidationError):
            JobRequest(url="https://example.com/v", selection="best",
                       download_dir=str(tmp_path),
                       referer="https://ok.com/\r\nX-Evil: 1")


class TestProbeUrlsRequest:
    def test_valid_single_url(self):
        req = ProbeUrlsRequest(
            urls=["https://cdn.example/a.m3u8"],
            referer="https://uzem.example/ders/",
        )
        assert req.urls == ["https://cdn.example/a.m3u8"]
        assert req.referer == "https://uzem.example/ders/"
        assert req.browser is None

    def test_urls_are_stripped_and_validated(self):
        # Her URL _validate_url'den geçer → kenar boşlukları temizlenir.
        req = ProbeUrlsRequest(
            urls=["  https://cdn.example/a.m3u8  "],
            referer="https://uzem.example/ders/",
        )
        assert req.urls == ["https://cdn.example/a.m3u8"]

    def test_max_20_urls_accepted(self):
        urls = [f"https://cdn.example/v{i}.m3u8" for i in range(20)]
        req = ProbeUrlsRequest(urls=urls, referer="https://uzem.example/x")
        assert len(req.urls) == 20

    def test_more_than_20_urls_rejected(self):
        # max_length=20 üst sınırı; 21 URL Field kısıtından reddedilmeli.
        urls = [f"https://cdn.example/v{i}.m3u8" for i in range(21)]
        with pytest.raises(ValidationError):
            ProbeUrlsRequest(urls=urls, referer="https://uzem.example/x")

    def test_empty_urls_rejected(self):
        # min_length=1 alt sınırı; boş liste reddedilmeli.
        with pytest.raises(ValidationError):
            ProbeUrlsRequest(urls=[], referer="https://uzem.example/x")

    def test_invalid_url_in_list_rejected(self):
        # Listedeki geçersiz (şemasız) bir URL tüm isteği reddetmeli.
        with pytest.raises(ValidationError):
            ProbeUrlsRequest(
                urls=["https://ok.example/a.m3u8", "ftp://bad/x"],
                referer="https://uzem.example/x",
            )

    def test_loopback_url_in_list_rejected(self):
        with pytest.raises(ValidationError):
            ProbeUrlsRequest(
                urls=["http://127.0.0.1/a.m3u8"],
                referer="https://uzem.example/x",
            )

    def test_invalid_referer_rejected(self):
        # referer de _validate_url'den geçer (loopback/şema reddi).
        with pytest.raises(ValidationError):
            ProbeUrlsRequest(
                urls=["https://cdn.example/a.m3u8"],
                referer="http://localhost/x",
            )

    def test_missing_referer_rejected(self):
        # referer zorunlu alan (varsayılanı yok).
        with pytest.raises(ValidationError):
            ProbeUrlsRequest(urls=["https://cdn.example/a.m3u8"])

    def test_empty_browser_becomes_none(self):
        # Boş tarayıcı dizesi _validate_browser tarafından None'a indirgenir (models.py 166).
        req = ProbeUrlsRequest(
            urls=["https://cdn.example/a.m3u8"],
            referer="https://uzem.example/x",
            browser="   ",
        )
        assert req.browser is None

    def test_chrome_browser_accepted(self):
        req = ProbeUrlsRequest(
            urls=["https://cdn.example/a.m3u8"],
            referer="https://uzem.example/x",
            browser="Chrome",  # büyük/küçük harf normalize edilmeli
        )
        assert req.browser == "chrome"

    def test_unsupported_browser_rejected(self):
        # Desteklenmeyen tarayıcı _validate_browser dalıyla reddedilmeli (models.py 166).
        with pytest.raises(ValidationError) as exc:
            ProbeUrlsRequest(
                urls=["https://cdn.example/a.m3u8"],
                referer="https://uzem.example/x",
                browser="netscape",
            )
        assert "Desteklenmeyen tarayıcı" in str(exc.value)


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
