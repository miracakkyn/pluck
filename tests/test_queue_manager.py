"""queue_manager.py — sıralı worker, iş durumları ve iptal testleri.

Gerçek yt-dlp çağrılmaz; `engine_download` sahte fonksiyonlarla enjekte edilir.
"""
import asyncio
import time

import pytest

from app.models import JobRequest
from app.queue_manager import QueueManager
from app.ytdlp_engine import EngineError


def _req(tmp_path, selection="best"):
    return JobRequest(
        url="https://example.com/v", selection=selection,
        download_dir=str(tmp_path),
    )


# --- sahte engine_download fonksiyonları ---------------------------------

def _ok_download(*, url, selection, download_dir, browser, progress_hook, **_kwargs):
    # download_dir altında gerçek bir dosya yarat ki queue_manager'ın yeni
    # "diskte var mı" kontrolü geçsin.
    from pathlib import Path
    target = Path(download_dir) / "video.mp4"
    target.write_bytes(b"x")
    progress_hook({"status": "downloading", "downloaded_bytes": 5,
                   "total_bytes": 10, "info_dict": {"title": "Test Başlık"}})
    progress_hook({"status": "downloading", "downloaded_bytes": 10,
                   "total_bytes": 10, "speed": 1048576, "eta": 0})
    progress_hook({"status": "finished", "filename": str(target)})


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
        """yt-dlp exception fırlatmasa bile diskte dosya yoksa 'error'."""
        def _phantom_download(*, url, selection, download_dir, browser,
                              progress_hook, **_kwargs):
            # Dosya yaratmadan finished yayınla (ffmpeg merge çökmüş senaryo)
            progress_hook({"status": "finished",
                           "filename": str(tmp_path / "yok.mp4")})
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
        qm = QueueManager(engine_download=_ok_download)
        job_id = qm.enqueue(_req(tmp_path))
        qm.cancel(job_id)
        qm.start()
        await asyncio.sleep(0.3)
        assert qm.get(job_id).status == "cancelled"
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
