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
- Frontend: tek sayfa **vanilla JS** (framework yok).
- Canlı ilerleme: SSE (`text/event-stream`).

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
- [ ] Sprint 5 — Login'li video testi (kullanıcının üniversite URL'si)

Test: 70 test, %96 kapsam. Çalıştır: `.venv\Scripts\python.exe -m pytest`
Uygulamayı çalıştır: `.venv\Scripts\python.exe run.py` (veya start.bat / start.command)

## v1 kapsamı (yalnızca bunlar)

1. URL yapıştırma kutusu
2. "Formatları getir" → mevcut çözünürlük/formatları listele
3. Çözünürlük/format seçimi (en yüksek varsayılan)
4. İndirme klasörü seçimi (varsayılan ~/Downloads)
5. Canlı indirme ilerlemesi (yüzde, hız, kalan süre)
6. İndirme kuyruğu (birden fazla URL sırayla)
7. Cookie desteği: tarayıcı seç → `--cookies-from-browser`
