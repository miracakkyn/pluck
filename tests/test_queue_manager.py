"""queue_manager.py — sıralı worker, iş durumları ve iptal testleri.

Gerçek yt-dlp çağrılmaz; `engine_download` sahte fonksiyonlarla enjekte edilir.
"""
import asyncio
import threading
import time
from pathlib import Path

import pytest

from app import ytdlp_engine
from app.models import JobRequest
from app.queue_manager import (
    QueueManager,
    _MAX_JOBS,
    _format_eta,
    _format_speed,
)
from app.ytdlp_engine import EngineError


def _req(tmp_path, selection="best"):
    return JobRequest(
        url="https://example.com/v", selection=selection,
        download_dir=str(tmp_path),
    )


# --- sahte engine_download fonksiyonları ---------------------------------

def _ok_download(*, url, selection, download_dir, browser, progress_hook, **_kwargs):
    # download_dir altında gerçek bir dosya yarat ve nihai yolu DÖNDÜR — gerçek
    # download() gibi (queue_manager dönüş değerini job.filename olarak kullanır).
    from pathlib import Path
    target = Path(download_dir) / "video.mp4"
    target.write_bytes(b"x")
    progress_hook({"status": "downloading", "downloaded_bytes": 5,
                   "total_bytes": 10, "info_dict": {"title": "Test Başlık"}})
    progress_hook({"status": "downloading", "downloaded_bytes": 10,
                   "total_bytes": 10, "speed": 1048576, "eta": 0})
    progress_hook({"status": "finished", "filename": str(target)})
    return str(target)


def _fail_download(*, url, selection, download_dir, browser, progress_hook, **_kwargs):
    raise EngineError("indirme basarisiz")


def _crash_download(*, url, selection, download_dir, browser, progress_hook, **_kwargs):
    raise RuntimeError("beklenmeyen ic hata")


def _slow_download(*, url, selection, download_dir, browser, progress_hook, **_kwargs):
    from pathlib import Path
    target = Path(download_dir) / "video.mp4"
    for i in range(1, 300):
        progress_hook({"status": "downloading", "downloaded_bytes": i,
                       "total_bytes": 300})
        time.sleep(0.02)
    target.write_bytes(b"x")
    progress_hook({"status": "finished", "filename": str(target)})
    return str(target)


def _ytdlp_like_download(*, url, selection, download_dir, browser, progress_hook, **_kwargs):
    """Gerçek yt-dlp davranışını taklit eder: progress hook'tan fırlayan istisna
    yakalanıp EngineError'a sarmalanır (yt-dlp onu DownloadError'a sarmalar)."""
    from pathlib import Path
    target = Path(download_dir) / "video.mp4"
    try:
        for i in range(1, 300):
            progress_hook({"status": "downloading", "downloaded_bytes": i,
                           "total_bytes": 300})
            time.sleep(0.02)
    except Exception as exc:
        raise EngineError("indirme kesildi") from exc
    target.write_bytes(b"x")
    progress_hook({"status": "finished", "filename": str(target)})
    return str(target)


async def _wait_for(qm, job_id, status, timeout=4.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        job = qm.get(job_id)
        if job is not None and job.status == status:
            return job
        await asyncio.sleep(0.02)
    current = qm.get(job_id)
    raise AssertionError(
        f"'{status}' beklendi, durum='{current.status if current else None}'"
    )


# --- testler -------------------------------------------------------------

class TestEnqueue:
    def test_creates_queued_job(self, tmp_path):
        qm = QueueManager(engine_download=_ok_download)
        job_id = qm.enqueue(_req(tmp_path))
        job = qm.get(job_id)
        assert job is not None
        assert job.status == "queued"

    async def test_referer_passed_to_engine(self, tmp_path):
        """JobRequest.referer → Job → download(referer=...) zinciri."""
        captured = {}

        def _capture(*, url, selection, download_dir, browser,
                     progress_hook, **kwargs):
            captured["referer"] = kwargs.get("referer")
            from pathlib import Path
            target = Path(download_dir) / "video.mp4"
            target.write_bytes(b"x")
            progress_hook({"status": "finished", "filename": str(target)})
            return str(target)

        qm = QueueManager(engine_download=_capture)
        qm.start()
        req = JobRequest(
            url="https://iframe.mediadelivery.net/embed/1/abc",
            selection="best", download_dir=str(tmp_path),
            referer="https://uzemykoabt.com/sayfa/",
        )
        job_id = qm.enqueue(req)
        await _wait_for(qm, job_id, "completed")
        assert captured["referer"] == "https://uzemykoabt.com/sayfa/"
        await qm.stop()

    def test_job_appears_in_listing(self, tmp_path):
        qm = QueueManager(engine_download=_ok_download)
        job_id = qm.enqueue(_req(tmp_path))
        assert job_id in {j["job_id"] for j in qm.jobs()}

    def test_unknown_job_is_none(self):
        qm = QueueManager(engine_download=_ok_download)
        assert qm.get("yok") is None


class TestWorker:
    async def test_completes_job(self, tmp_path):
        qm = QueueManager(engine_download=_ok_download)
        qm.start()
        job_id = qm.enqueue(_req(tmp_path))
        job = await _wait_for(qm, job_id, "completed")
        assert job.progress == 100.0
        assert job.filename.endswith("video.mp4")
        await qm.stop()

    async def test_completed_only_if_file_exists(self, tmp_path):
        """download() nihai yolu döndüremezse (None) 'error' işaretlenmeli.

        Progress hook bir "finished" filename bildirse bile queue_manager artık
        ARA parça adına güvenmez; yalnızca download()'ın DÖNDÜRDÜĞÜ (merge sonrası,
        var olduğu doğrulanmış) yola bakar. Dönüş None → dosya doğrulanamadı → error.
        """
        def _phantom_download(*, url, selection, download_dir, browser,
                              progress_hook, **_kwargs):
            # Ara parça adı yayınla ama nihai yol döndürme (None) — merge sonrası
            # dosya doğrulanamadı senaryosu.
            progress_hook({"status": "finished",
                           "filename": str(tmp_path / "yok.mp4")})
            return None
        qm = QueueManager(engine_download=_phantom_download)
        qm.start()
        job_id = qm.enqueue(_req(tmp_path))
        job = await _wait_for(qm, job_id, "error")
        assert "bulunamadı" in (job.error or "")
        await qm.stop()

    async def test_completed_error_when_no_filename(self, tmp_path):
        """progress hook hiç filename vermediyse 'error' işaretlenmeli."""
        def _no_filename_download(*, url, selection, download_dir, browser,
                                  progress_hook, **_kwargs):
            # Hiç finished event'i yok → job.filename boş kalır
            progress_hook({"status": "downloading", "downloaded_bytes": 5,
                           "total_bytes": 10})
        qm = QueueManager(engine_download=_no_filename_download)
        qm.start()
        job_id = qm.enqueue(_req(tmp_path))
        job = await _wait_for(qm, job_id, "error")
        assert job.error is not None
        await qm.stop()

    async def test_progress_and_metadata_updated(self, tmp_path):
        qm = QueueManager(engine_download=_ok_download)
        qm.start()
        job_id = qm.enqueue(_req(tmp_path))
        job = await _wait_for(qm, job_id, "completed")
        assert job.title == "Test Başlık"
        assert job.speed == "1.0 MiB/s"
        assert job.eta == "00:00"
        await qm.stop()

    async def test_engine_error_sets_error_status(self, tmp_path):
        qm = QueueManager(engine_download=_fail_download)
        qm.start()
        job_id = qm.enqueue(_req(tmp_path))
        job = await _wait_for(qm, job_id, "error")
        assert "basarisiz" in job.error
        await qm.stop()

    async def test_unexpected_error_does_not_leak(self, tmp_path):
        qm = QueueManager(engine_download=_crash_download)
        qm.start()
        job_id = qm.enqueue(_req(tmp_path))
        job = await _wait_for(qm, job_id, "error")
        assert "beklenmeyen ic hata" not in (job.error or "")
        await qm.stop()

    async def test_processes_jobs_sequentially(self, tmp_path):
        qm = QueueManager(engine_download=_ok_download)
        qm.start()
        first = qm.enqueue(_req(tmp_path))
        second = qm.enqueue(_req(tmp_path))
        await _wait_for(qm, first, "completed")
        await _wait_for(qm, second, "completed")
        await qm.stop()


class TestCancel:
    def test_cancel_unknown_raises(self):
        qm = QueueManager(engine_download=_ok_download)
        with pytest.raises(KeyError):
            qm.cancel("yok")

    def test_cancel_queued_job(self, tmp_path):
        qm = QueueManager(engine_download=_ok_download)
        job_id = qm.enqueue(_req(tmp_path))  # worker başlatılmadı
        qm.cancel(job_id)
        assert qm.get(job_id).status == "cancelled"

    async def test_worker_skips_cancelled_job(self, tmp_path):
        # Sabit sleep yerine deterministik yaklaşım: iptal edilen işten SONRA bir
        # "sentinel" iş kuyruğa alınır. Worker FIFO işlediğinden sentinel
        # tamamlandığında iptal iş de mutlaka sıradan geçmiş (atlanmış) olur.
        qm = QueueManager(engine_download=_ok_download)
        skipped_id = qm.enqueue(_req(tmp_path))
        qm.cancel(skipped_id)  # start öncesi: queued → cancelled
        sentinel_id = qm.enqueue(_req(tmp_path))  # bu tamamlanacak
        qm.start()
        await _wait_for(qm, sentinel_id, "completed")
        # Sentinel bitti → worker iptal işi zaten dequeue edip ATLADI (çalıştırmadı)
        assert qm.get(skipped_id).status == "cancelled"
        assert qm.get(skipped_id).filename is None  # indirme hiç başlamadı
        await qm.stop()

    async def test_cancel_downloading_job(self, tmp_path):
        qm = QueueManager(engine_download=_slow_download)
        qm.start()
        job_id = qm.enqueue(_req(tmp_path))
        await _wait_for(qm, job_id, "downloading")
        qm.cancel(job_id)
        job = await _wait_for(qm, job_id, "cancelled")
        assert job.status == "cancelled"
        await qm.stop()

    async def test_cancel_when_engine_wraps_hook_exception(self, tmp_path):
        # yt-dlp progress hook istisnasını kendi hatasına sarmalar; iptal yine
        # de cancel_requested bayrağından tespit edilmeli (status 'error' olmamalı).
        qm = QueueManager(engine_download=_ytdlp_like_download)
        qm.start()
        job_id = qm.enqueue(_req(tmp_path))
        await _wait_for(qm, job_id, "downloading")
        qm.cancel(job_id)
        job = await _wait_for(qm, job_id, "cancelled")
        assert job.status == "cancelled"
        assert job.error is None
        await qm.stop()

    async def test_stop_signals_inflight_download(self, tmp_path):
        qm = QueueManager(engine_download=_slow_download)
        qm.start()
        job_id = qm.enqueue(_req(tmp_path))
        await _wait_for(qm, job_id, "downloading")
        await qm.stop()
        assert qm.get(job_id).cancel_requested is True


class TestClearFinished:
    async def test_removes_completed_keeps_active(self, tmp_path):
        qm = QueueManager(engine_download=_ok_download)
        qm.start()
        # Bir iş tamamlansın
        done_id = qm.enqueue(_req(tmp_path))
        await _wait_for(qm, done_id, "completed")
        await qm.stop()
        # Bir iş queued, bir iş cancelled olsun
        queued_id = qm.enqueue(_req(tmp_path))
        cancelled_id = qm.enqueue(_req(tmp_path))
        qm.cancel(cancelled_id)
        # Şimdi temizle
        removed = qm.clear_finished()
        assert removed == 2  # done + cancelled
        assert qm.get(done_id) is None
        assert qm.get(cancelled_id) is None
        assert qm.get(queued_id) is not None  # queued korunmalı

    def test_clear_with_no_finished_returns_zero(self, tmp_path):
        qm = QueueManager(engine_download=_ok_download)
        qm.enqueue(_req(tmp_path))  # queued kalır
        assert qm.clear_finished() == 0


# --- _format_speed / _format_eta kenar dalları (satır 44/53/57) -----------

class TestFormatSpeed:
    def test_none_zero_negative_return_none(self):
        # None / 0 / negatif hız → None (guard dalı, satır 37-38)
        assert _format_speed(None) is None
        assert _format_speed(0) is None
        assert _format_speed(-5) is None

    def test_units_scale_through_binary_prefixes(self):
        # Her birim eşiği ayrı bir döngü turunu tetikler.
        assert _format_speed(512) == "512.0 B/s"
        assert _format_speed(1536) == "1.5 KiB/s"        # 1.5 * 1024
        assert _format_speed(1024 ** 2) == "1.0 MiB/s"
        assert _format_speed(1024 ** 3) == "1.0 GiB/s"

    def test_huge_speed_falls_back_to_tibs(self):
        # 1024^4 ve üzeri → döngü ("B".."GiB") tükenir, TiB/s dalına düşer (satır 44).
        assert _format_speed(2 * 1024 ** 4) == "2.0 TiB/s"


class TestFormatEta:
    def test_none_returns_none(self):
        assert _format_eta(None) is None

    def test_negative_returns_none(self):
        # total < 0 → None (satır 52-53). yt-dlp bazen -1 ETA bildirir.
        assert _format_eta(-1) is None
        assert _format_eta(-3600) is None

    def test_zero_and_sub_hour(self):
        assert _format_eta(0) == "00:00"
        assert _format_eta(65) == "01:05"        # 1dk 5sn → dd:ss
        assert _format_eta(599) == "09:59"

    def test_hours_branch(self):
        # saat > 0 → sa:dd:ss biçimi (satır 56-57)
        assert _format_eta(3600) == "1:00:00"
        assert _format_eta(3661) == "1:01:01"    # 1sa 1dk 1sn
        assert _format_eta(7325) == "2:02:05"


# --- _evict_old_finished (satır 99-106) -----------------------------------

class TestEviction:
    def test_evicts_oldest_finished_keeps_active(self, tmp_path):
        """_MAX_JOBS aşılınca en eski BİTMİŞ işler düşer; aktifler korunur.

        Kurgu: en eski 2 iş 'queued' bırakılır (aktif), 2..11 arası 10 iş
        'completed' işaretlenir. Sonra 5 yeni iş eklenir; her ekleme sınırı
        aşıp döngüyü çalıştırır. Döngü baştaki aktif işleri ATLAR (satır 105
        False dalı) ve en eski bitmiş işleri siler (satır 106).
        """
        qm = QueueManager(engine_download=_ok_download)
        ids = [qm.enqueue(_req(tmp_path)) for _ in range(_MAX_JOBS)]
        # Tam sınırdayken henüz tahliye olmamalı.
        assert len(qm.jobs()) == _MAX_JOBS
        for jid in ids:
            assert qm.get(jid) is not None

        # İlk 2 iş aktif kalsın; 2..11 arası bitmiş olsun.
        for jid in ids[2:12]:
            qm.get(jid).status = "completed"

        # 5 yeni iş → her biri sınırı 1 aşar, en eski bitmiş işi düşürür.
        new_ids = [qm.enqueue(_req(tmp_path)) for _ in range(5)]

        # Kayıt sınırda tutulur.
        assert len(qm.jobs()) == _MAX_JOBS
        # En eski 5 BİTMİŞ iş (indeks 2..6) düşmüş olmalı.
        for jid in ids[2:7]:
            assert qm.get(jid) is None
        # Kalan bitmiş işler (7..11) hâlâ mevcut.
        for jid in ids[7:12]:
            assert qm.get(jid) is not None
        # Baştaki aktif (queued) işler ASLA düşmez (satır 105 skip dalı).
        for jid in ids[:2]:
            job = qm.get(jid)
            assert job is not None and job.status == "queued"
        # Sondaki 188 aktif iş de korunur.
        for jid in ids[12:]:
            job = qm.get(jid)
            assert job is not None and job.status == "queued"
        # Yeni eklenen işler mevcut.
        for jid in new_ids:
            assert qm.get(jid) is not None

    def test_no_eviction_when_all_active_over_limit(self, tmp_path):
        """Sınır aşılsa bile BİTMİŞ iş yoksa hiçbir aktif iş düşmez."""
        qm = QueueManager(engine_download=_ok_download)
        ids = [qm.enqueue(_req(tmp_path)) for _ in range(_MAX_JOBS + 3)]
        # Hepsi queued (aktif) → tahliye edilebilecek bitmiş iş yok.
        assert len(qm.jobs()) == _MAX_JOBS + 3
        for jid in ids:
            assert qm.get(jid) is not None


# --- İptal-yarışı dalları (#17) + cancel no-op ----------------------------

class TestCancelRaces:
    def test_cancel_downloading_sets_flag_without_status_change(self, tmp_path):
        """'downloading' işte cancel() yalnız bayrağı set eder; status HEMEN değişmez."""
        qm = QueueManager(engine_download=_ok_download)
        job_id = qm.enqueue(_req(tmp_path))
        job = qm.get(job_id)
        job.status = "downloading"  # aktif indirmeyi simüle et (worker olmadan)
        returned = qm.cancel(job_id)
        assert returned is job
        assert returned.cancel_requested is True     # bayrak set edildi
        assert returned.status == "downloading"       # ama status değişmedi

    def test_cancel_completed_is_noop(self, tmp_path):
        """Tamamlanmış işte cancel() hiçbir şey yapmaz (no-op dalı)."""
        qm = QueueManager(engine_download=_ok_download)
        job_id = qm.enqueue(_req(tmp_path))
        job = qm.get(job_id)
        job.status = "completed"
        returned = qm.cancel(job_id)
        assert returned.status == "completed"          # değişmedi
        assert returned.cancel_requested is False      # bayrak set EDİLMEDİ

    def test_cancel_error_is_noop(self, tmp_path):
        """Hatalı işte cancel() no-op olmalı."""
        qm = QueueManager(engine_download=_ok_download)
        job_id = qm.enqueue(_req(tmp_path))
        job = qm.get(job_id)
        job.status = "error"
        job.error = "bir hata"
        qm.cancel(job_id)
        assert qm.get(job_id).status == "error"        # değişmedi
        assert qm.get(job_id).cancel_requested is False

    async def test_cancel_race_when_engine_wraps_hook_in_generic_error(self, tmp_path):
        """Hook istisnası EngineError DIŞI genel bir hataya sarılırsa: cancel_requested
        True olduğundan 'except Exception' dalı 'cancelled' der (satır 240-241),
        'error' DEMEZ ve ham hata mesajı sızmaz."""
        def _wrap_hook_as_runtimeerror(*, url, selection, download_dir, browser,
                                       progress_hook, **_kwargs):
            target = Path(download_dir) / "video.mp4"
            try:
                for i in range(1, 300):
                    progress_hook({"status": "downloading",
                                   "downloaded_bytes": i, "total_bytes": 300})
                    time.sleep(0.02)
            except Exception as exc:
                # EngineError DEĞİL — beklenmeyen genel hata dalını tetikler.
                raise RuntimeError("hook sarmalandı: generic") from exc
            target.write_bytes(b"x")
            progress_hook({"status": "finished", "filename": str(target)})
            return str(target)

        qm = QueueManager(engine_download=_wrap_hook_as_runtimeerror)
        qm.start()
        job_id = qm.enqueue(_req(tmp_path))
        await _wait_for(qm, job_id, "downloading")
        qm.cancel(job_id)
        job = await _wait_for(qm, job_id, "cancelled")
        assert job.status == "cancelled"               # 'error' DEĞİL
        assert job.error is None                        # iptalde hata mesajı yok
        await qm.stop()

    async def test_cancel_race_after_successful_download(self, tmp_path):
        """İndirme BAŞARIYLA nihai yolu döndürdü ama cancel_requested set edildi:
        else dalı 'completed' yerine 'cancelled' işaretler (satır 248-249).

        Kurgu: engine son hook'u çağırdıktan SONRA (artık _Cancelled fırlamaz)
        bir olayla durur; test bu noktada cancel() çağırır, sonra engine hook
        ÇAĞIRMADAN nihai yolu döndürür."""
        ready = threading.Event()    # engine → test: son hook bitti, dönmek üzere
        proceed = threading.Event()  # test → engine: cancel set edildi, dönebilirsin

        def _finish_then_wait(*, url, selection, download_dir, browser,
                              progress_hook, **_kwargs):
            target = Path(download_dir) / "video.mp4"
            target.write_bytes(b"x")
            # Cancel henüz istenmedi; bu hook normal ilerler.
            progress_hook({"status": "downloading",
                           "downloaded_bytes": 10, "total_bytes": 10})
            ready.set()
            proceed.wait(timeout=5)
            # Buradan sonra hook ÇAĞIRILMAZ → cancel bayrağına rağmen _Cancelled
            # fırlamaz; download başarıyla nihai yolu döndürür.
            return str(target)

        qm = QueueManager(engine_download=_finish_then_wait)
        qm.start()
        job_id = qm.enqueue(_req(tmp_path))
        await _wait_for(qm, job_id, "downloading")
        # Engine son hook'u çağırıp ready.set() edene kadar bekle (thread bloklamadan).
        for _ in range(250):  # ~5 sn üst sınır
            if ready.is_set():
                break
            await asyncio.sleep(0.02)
        assert ready.is_set(), "engine son hook'a ulaşmadı"

        qm.cancel(job_id)   # status 'downloading' → cancel_requested=True
        proceed.set()       # engine başarıyla dönebilir
        job = await _wait_for(qm, job_id, "cancelled")
        # else dalında cancel_requested True → 'cancelled' (satır 249); 'completed' DEĞİL.
        assert job.status == "cancelled"
        assert job.error is None
        await qm.stop()


# --- _cleanup_partial yutulan-istisna dalı (satır 279-280) ----------------

class TestCleanupPartial:
    async def test_cleanup_exception_is_swallowed_on_error(self, tmp_path, monkeypatch):
        """cleanup_partial_files patlarsa iş yine de düzgün sonlanmalı (çökme yok)."""
        def _boom(*_a, **_k):
            raise OSError("temizlik patladı")
        monkeypatch.setattr(ytdlp_engine, "cleanup_partial_files", _boom)

        qm = QueueManager(engine_download=_fail_download)
        qm.start()
        job_id = qm.enqueue(_req(tmp_path))
        # Cleanup istisnası yutulur → iş yine 'error'a ulaşır, worker çökmez.
        job = await _wait_for(qm, job_id, "error")
        assert "basarisiz" in (job.error or "")
        await qm.stop()

    async def test_cleanup_exception_is_swallowed_on_cancel(self, tmp_path, monkeypatch):
        """İptal yolunda da cleanup istisnası yutulmalı; status 'cancelled' kalmalı."""
        def _boom(*_a, **_k):
            raise RuntimeError("temizlik patladı")
        monkeypatch.setattr(ytdlp_engine, "cleanup_partial_files", _boom)

        qm = QueueManager(engine_download=_slow_download)
        qm.start()
        job_id = qm.enqueue(_req(tmp_path))
        await _wait_for(qm, job_id, "downloading")
        qm.cancel(job_id)
        job = await _wait_for(qm, job_id, "cancelled")
        assert job.status == "cancelled"
        await qm.stop()


# --- stop() erken-dönüş dalı (satır 173-179) ------------------------------

class TestStopWithoutWorker:
    async def test_stop_without_worker_signals_downloading_and_resets(self, tmp_path):
        """Worker hiç başlatılmadıysa stop(): önce 'downloading' işlere cancel
        bayrağı koyar (satır 174-176), sonra worker None olduğu için erken döner
        ve _stopping'i sıfırlar (satır 177-179)."""
        qm = QueueManager(engine_download=_ok_download)
        job_id = qm.enqueue(_req(tmp_path))
        qm.get(job_id).status = "downloading"  # worker olmadan aktif iş simüle et

        await qm.stop()  # worker_task None → erken dönüş

        assert qm.get(job_id).cancel_requested is True  # 174-176 çalıştı
        assert qm._stopping is False                     # 178: bayrak sıfırlandı
        # Erken dönüş sonrası start() hâlâ mümkün olmalı (worker temiz).
        assert qm._worker_task is None
