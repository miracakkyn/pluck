# CLAUDE.md — Proje Hafızası

> Bu dosya Claude Code'un proje hafızasıdır. Her oturumda otomatik yüklenir.

## Proje

**Genel Amaçlı Video İndirme Aracı** — IDM benzeri, yt-dlp + ffmpeg motoru
üzerine kurulu, basit yerel web arayüzü olan bir video indirme aracı.
Akış: link yapıştır → kalite seç → indir.

**Site-özel kod YAZILMAZ.** Yalnızca yt-dlp'nin genel yetenekleri kullanılır.

## Kritik kısıt: çapraz platform

Önce Windows'ta geliştirilip test ediliyor, sonra macOS'ta kullanılacak.
**Tüm kod iki platformda da birebir aynı çalışmalı.** Tek platform farkı
kurulum/başlatma komutları olmalı; bunlar README'de ayrı belgelenir.

- Her yerde `pathlib.Path` kullan; sabit yol ayıracı (`\` veya `/`) yazma.
- Varsayılan indirme klasörü: `Path.home() / "Downloads"`.
- ffmpeg PATH üzerinden bulunur.

## Teknik yığın

- Backend: **FastAPI + uvicorn**, yalnızca `127.0.0.1`'e bağlanır.
- Motor: **yt-dlp gömülü Python kütüphanesi** (venv içine pip ile kurulur).
- Birleştirme/dönüştürme: **ffmpeg** (sistemde kurulu, PATH'ten).
- Frontend: tek sayfa **vanilla JS** (framework yok) — `web/`.
- Chrome eklentisi (Manifest V3) — `extension/`; popup yerel motora HTTP ile
  bağlanır (hibrit mimari, bkz. DESIGN.md §16).
- Canlı ilerleme: web arayüzünde SSE, eklentide `/api/jobs` yoklaması.

## Komutlar

- Çalıştır: `python run.py` (veya `start.bat` / `start.command`).
- Test: `pytest --cov` (hedef ≥%80 kapsam).
- Sanal ortam (Windows): `.venv\Scripts\python.exe`
- Sanal ortam (macOS): `.venv/bin/python`

## İş akışı kuralları

- DESIGN.md onaylanmadan uygulama kodu yazılmaz.
- Her anlamlı adımdan sonra `git commit` (conventional commits: feat/fix/chore/docs/test/refactor).
- **Push yok** — kullanıcı açıkça istemeden push edilmez.
- Sprint mantığıyla ilerlenir.

## Sprint durumu

- [x] Sprint 0 — Kurulum + motor doğrulama (yt-dlp 2026.03.17, ffmpeg 8.0.1, deno 2.8.0)
- [x] Sprint 0.5 — DESIGN.md (onaylandı)
- [x] Sprint 1 — Backend çekirdek (yt-dlp engine + format listeleme)
- [x] Sprint 2 — İndirme kuyruğu + SSE canlı ilerleme
- [x] Sprint 3 — Frontend (tek sayfa web arayüzü, koyu tema)
- [x] Sprint 4 — Cookie desteği + çapraz platform başlatma scriptleri + README
- [x] Sprint 6 — Chrome eklentisi (hibrit mimari, MV3)
- [x] Sprint 7 — Düzeltmeler (ses/kalite/login tarama) + native klasör seçici
- [x] Sprint 8 — Çoklu video (playlist) + eklenti Firefox uyumu
- [x] Sprint 5 — Login'li video testi (Firefox çerezi ile uzem doğrulandı)

Test: 83 test, %96 kapsam. Çalıştır: `.venv\Scripts\python.exe -m pytest`
Uygulamayı çalıştır: `.venv\Scripts\python.exe run.py` (veya start.bat / start.command)
Eklenti: `chrome://extensions` → Geliştirici modu → Paketlenmemiş yükle → `extension/`

## v1 kapsamı (yalnızca bunlar)

1. URL yapıştırma kutusu
2. "Formatları getir" → mevcut çözünürlük/formatları listele
3. Çözünürlük/format seçimi (en yüksek varsayılan)
4. İndirme klasörü seçimi (varsayılan ~/Downloads)
5. Canlı indirme ilerlemesi (yüzde, hız, kalan süre)
6. İndirme kuyruğu (birden fazla URL sırayla)
7. Cookie desteği: tarayıcı seç → `--cookies-from-browser`
