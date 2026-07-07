"""İndirme kuyruğu — sıralı async worker ve iş durumu yönetimi.

Tasarım (bkz. DESIGN.md §4, §9):
- İşler bellekte `JobRegistry` (dict) içinde tutulur.
- Tek bir worker coroutine işleri birer birer (sıralı) işler.
- Bloklayan yt-dlp indirmesi `asyncio.to_thread` ile thread'e alınır.
- İlerleme, yt-dlp `progress_hooks` callback'i ile ilgili `Job`'a yazılır;
  worker tek yazıcıdır, SSE yalnızca okur — kilit gerekmez.
- İptal: indirme sırasında `cancel_requested` bayrağı set edilir; progress
  hook bunu görüp `_Cancelled` fırlatarak indirmeyi durdurur.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from uuid import uuid4

from app import ytdlp_engine
from app.models import Job, JobRequest
from app.ytdlp_engine import EngineError

logger = logging.getLogger("videoaraci")

# Bellek sınırı: JobRegistry sınırsız büyümesin (uzun oturumda birçok indirme).
# Aşılınca en eski BİTMİŞ (completed/error/cancelled) işler düşürülür; aktif
# işler her zaman korunur.
_MAX_JOBS = 200


class _Cancelled(Exception):
    """Dahili — indirmeyi iptal etmek için progress hook'tan fırlatılır."""


def _format_speed(speed: float | None) -> str | None:
    """Bayt/sn hızını insan-okur biçime çevirir (ör. '1.2 MiB/s')."""
    if not speed or speed <= 0:
        return None
    value = float(speed)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024:
            return f"{value:.1f} {unit}/s"
        value /= 1024
    return f"{value:.1f} TiB/s"


def _format_eta(eta: float | None) -> str | None:
    """Saniye cinsinden ETA'yı sa:dd:ss / dd:ss biçimine çevirir."""
    if eta is None:
        return None
    total = int(eta)
    if total < 0:
        return None
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


class QueueManager:
    """İndirme işlerinin kuyruğunu ve sıralı işlenmesini yönetir."""

    def __init__(
        self,
        engine_download: Callable[..., None] = ytdlp_engine.download,
    ) -> None:
        # `engine_download` test edilebilirlik için enjekte edilebilir.
        self._engine_download = engine_download
        self._jobs: dict[str, Job] = {}
        self._pending: asyncio.Queue[str] = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None
        self._stopping = False  # kapanışta worker yeni iş başlatmasın

    # --- genel API -------------------------------------------------------

    def enqueue(self, request: JobRequest) -> str:
        """Yeni bir indirme işini kuyruğa ekler; iş kimliğini döndürür."""
        job_id = uuid4().hex
        self._jobs[job_id] = Job(
            job_id=job_id,
            url=request.url,
            selection=request.selection,
            download_dir=request.download_dir,
            browser=request.browser,
            title=request.title or "",
            title_locked=bool(request.title),
            referer=request.referer,
        )
        self._pending.put_nowait(job_id)
        self._evict_old_finished()
        return job_id

    def _evict_old_finished(self) -> None:
        """Kayıt _MAX_JOBS'u aşınca en eski BİTMİŞ işleri düşürür (aktifler kalır).

        dict ekleme sırasını koruduğundan iterasyon en eskiden yeniye gider.
        """
        if len(self._jobs) <= _MAX_JOBS:
            return
        finished = {"completed", "error", "cancelled"}
        for jid, job in list(self._jobs.items()):
            if len(self._jobs) <= _MAX_JOBS:
                break
            if job.status in finished:
                del self._jobs[jid]

    def get(self, job_id: str) -> Job | None:
        """Tek bir işi döndürür; yoksa None."""
        return self._jobs.get(job_id)

    def jobs(self) -> list[dict]:
        """Tüm işlerin serileştirilebilir anlık görüntüsü (ekleme sırasıyla)."""
        return [job.snapshot() for job in self._jobs.values()]

    def cancel(self, job_id: str) -> Job:
        """İşi iptal eder. Bilinmiyorsa KeyError fırlatır."""
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job.status == "queued":
            job.status = "cancelled"
        elif job.status == "downloading":
            job.cancel_requested = True
        # completed / error / cancelled durumlarında işlem yok.
        return job

    def clear_finished(self) -> int:
        """Tamamlanmış/hatalı/iptal edilmiş işleri kuyruktan kaldırır.

        Aktif (queued/downloading) işler korunur. Kaldırılan iş sayısını döndürür.
        """
        finished = {"completed", "error", "cancelled"}
        to_remove = [jid for jid, job in self._jobs.items() if job.status in finished]
        for jid in to_remove:
            del self._jobs[jid]
        return len(to_remove)

    def start(self) -> None:
        """Worker coroutine'ini başlatır (çalışan event loop gerektirir).

        `stop()` sonrası yeniden çağrılabilir; worker yeniden başlatılır.

        `asyncio.Queue` ilk kullanıldığı event loop'a bağlanır; `start()` farklı
        bir loop'ta çağrılırsa (ör. testlerde ardışık TestClient blokları, ya da
        lifespan yeniden başlarsa) eski/kapalı loop'a bağlı kuyruk "bound to a
        different event loop" hatası verir. Bu yüzden burada taze bir kuyruk
        oluşturup bekleyen job_id'leri taşıyarak kuyruğu güncel loop'a bağlarız.
        """
        if self._worker_task is not None:
            return
        pending_ids: list[str] = []
        try:
            while True:
                pending_ids.append(self._pending.get_nowait())
        except asyncio.QueueEmpty:
            pass
        self._pending = asyncio.Queue()
        for jid in pending_ids:
            self._pending.put_nowait(jid)
        self._worker_task = asyncio.create_task(self._worker())

    async def stop(self) -> None:
        """Worker'ı durdurur; devam eden indirmeyi nazikçe sonlandırmayı dener.

        Python thread'leri zorla durdurulamaz; bu yüzden devam eden indirmenin
        progress hook'u `_Cancelled` fırlatıp thread'i kendisi sonlandırsın diye
        cancel bayrağı set edilir ve iş sınırlı bir pencerede nazikçe bitene
        kadar beklenir. İlerleme gelmiyorsa (ör. uzun ffmpeg merge) sonsuza kadar
        beklemeyip worker görevini iptal eder (thread arka planda kendiliğinden
        biter). `_stopping` bayrağı bu sırada yeni iş başlatılmasını engeller.
        """
        self._stopping = True
        for job in self._jobs.values():
            if job.status == "downloading":
                job.cancel_requested = True
        if self._worker_task is None:
            self._stopping = False
            return
        # Devam eden iş cancel'ı görüp thread'ini sonlandırana kadar sınırlı süre
        # bekle (progress hook periyodu genelde < 0.5sn).
        for _ in range(30):  # ~3 sn üst sınır
            if not any(j.status == "downloading" for j in self._jobs.values()):
                break
            await asyncio.sleep(0.1)
        self._worker_task.cancel()
        try:
            await self._worker_task
        except asyncio.CancelledError:
            pass
        self._worker_task = None
        self._stopping = False  # start() ile yeniden başlatılabilsin

    # --- iç işleyiş ------------------------------------------------------

    async def _worker(self) -> None:
        """Kuyruktaki işleri sırayla işler; tek bir iş hatası worker'ı çökertmez."""
        while True:
            job_id = await self._pending.get()
            try:
                if self._stopping:
                    continue  # kapanış: bekleyen yeni işleri başlatma
                job = self._jobs.get(job_id)
                if job is not None and job.status == "queued":
                    await self._run_job(job)
            finally:
                self._pending.task_done()

    async def _run_job(self, job: Job) -> None:
        """Tek bir işi indirir ve sonucuna göre durumunu günceller.

        İptal tespiti istisna türüne bağlı DEĞİLDİR: yt-dlp, progress hook'tan
        fırlayan istisnayı kendi DownloadError'ına sarmalayabilir; bu da
        ytdlp_engine'de EngineError'a dönüşür. Bu yüzden indirme nasıl
        sonlanırsa sonlansın `cancel_requested` bayrağı esas alınır.
        """
        job.status = "downloading"
        try:
            final_path = await asyncio.to_thread(
                self._engine_download,
                url=job.url,
                selection=job.selection,
                download_dir=job.download_dir,
                browser=job.browser,
                title=job.title if job.title_locked else None,
                referer=job.referer,
                progress_hook=self._make_progress_hook(job),
            )
        except _Cancelled:
            job.status = "cancelled"
            await self._cleanup_partial(job)
        except EngineError as exc:
            if job.cancel_requested:
                job.status = "cancelled"
            else:
                job.status = "error"
                job.error = str(exc)
            await self._cleanup_partial(job)
        except Exception:  # beklenmeyen — ham ayrıntı kullanıcıya sızmaz
            if job.cancel_requested:
                job.status = "cancelled"
            else:
                logger.exception("İş işlenirken beklenmeyen hata: %s", job.job_id)
                job.status = "error"
                job.error = "Beklenmeyen bir hata oluştu"
            await self._cleanup_partial(job)
        else:
            if job.cancel_requested:
                job.status = "cancelled"
            elif not final_path:
                # download() nihai (merge sonrası) dosya yolunu yakalayamadı —
                # dosyanın oluştuğunu doğrulayamıyoruz. "completed" yalanı verme.
                # (Merge çöküp dosya eksik kalırsa download() zaten EngineError
                #  fırlatır; bu dal yalnızca hiç yol yakalanmayan nadir durum.)
                job.status = "error"
                job.error = "İndirme tamamlanamadı (dosya bulunamadı)"
            else:
                # Nihai yol download() içinde diskte var olduğu doğrulanarak
                # döndü — progress hook'un bildirdiği (silinmiş olabilen) ara
                # parça dosyasını değil, bunu kullan.
                job.filename = final_path
                job.status = "completed"
                job.progress = 100.0

    async def _cleanup_partial(self, job: Job) -> None:
        """İptal/hata sonrası yarım parça dosyalarını ikinci geçişte temizler.

        `download()` döndükten SONRA çağrılır: o noktada yt-dlp indirici nesnesi
        referanssızdır, gc.collect() tutacı kapatır ve ana `.mp4.part` da silinir
        (bkz. ytdlp_engine.cleanup_partial_files). Engine'in title-locked dosya
        adı önekiyle aynı `title` kullanılır.
        """
        title = job.title if job.title_locked else None
        try:
            await asyncio.to_thread(
                ytdlp_engine.cleanup_partial_files,
                job.download_dir, title=title, selection=job.selection,
            )
        except Exception:
            logger.debug("Parça temizliği başarısız (önemsiz): %s", job.job_id)

    def _make_progress_hook(self, job: Job) -> Callable[[dict], None]:
        """Bir iş için yt-dlp progress_hook callback'i üretir.

        Callback worker thread'inde çalışır ve `job`'u günceller. İptal
        istenmişse `_Cancelled` fırlatarak indirmeyi durdurur.
        """

        def hook(data: dict) -> None:
            if job.cancel_requested:
                raise _Cancelled()

            status = data.get("status")
            if status == "downloading":
                total = data.get("total_bytes") or data.get("total_bytes_estimate")
                downloaded = data.get("downloaded_bytes") or 0
                job.downloaded_bytes = downloaded
                job.total_bytes = total
                if total:
                    job.progress = min(downloaded / total * 100.0, 100.0)
                job.speed = _format_speed(data.get("speed"))
                job.eta = _format_eta(data.get("eta"))
                info = data.get("info_dict") or {}
                if info.get("title") and not job.title_locked:
                    job.title = info["title"]
            elif status == "finished":
                job.progress = 100.0
                if data.get("filename"):
                    job.filename = data["filename"]

        return hook
