# Video İndirici

yt-dlp + ffmpeg motoru üzerine kurulu, basit yerel web arayüzü olan genel
amaçlı bir video indirme aracı. Akış: **bağlantı yapıştır → kalite seç → indir.**

Site-özel kod içermez; yt-dlp'nin desteklediği her sitede çalışır.

## Özellikler

- Bağlantı yapıştırma ve mevcut format/çözünürlükleri listeleme
- Kalite seçimi (varsayılan: en yüksek) — `en yüksek · 1080p · 720p · 480p · ses (MP3)`
- İndirme klasörü seçimi (varsayılan: `~/Downloads`)
- Canlı indirme ilerlemesi (yüzde, hız, kalan süre)
- İndirme kuyruğu (birden fazla bağlantı sırayla)
- Login gerektiren siteler için tarayıcı çerezi desteği (`--cookies-from-browser`)

---

## Kurulum

Uygulama **Windows ve macOS'ta birebir aynı** çalışır. Yalnızca aşağıdaki
kurulum komutları platforma göre değişir. `yt-dlp` ve diğer Python paketleri
başlatma scripti tarafından otomatik kurulur — elle kurmanıza gerek yoktur.

### Windows (winget)

```powershell
winget install Python.Python.3.13
winget install Gyan.FFmpeg
winget install DenoLand.Deno      # isteğe bağlı — bkz. "deno notu"
```

### macOS (Homebrew)

```bash
brew install python@3.13
brew install ffmpeg
brew install deno                  # isteğe bağlı — bkz. "deno notu"
```

> **deno notu:** yt-dlp, YouTube'da bazı formatları çıkarmak için bir
> JavaScript çalışma zamanı (`deno`) ister. Kurulu değilse uygulama yine
> çalışır ancak YouTube'da bazı formatlar eksik kalabilir. YouTube dışı
> sitelerde gerekmez.

---

## Çalıştırma

### Tek tıkla

- **Windows:** `start.bat` dosyasına çift tıklayın.
- **macOS:** `start.command` dosyasına çift tıklayın.

İlk çalıştırmada sanal ortam oluşturulur ve bağımlılıklar kurulur (bir kez,
biraz sürebilir). Sonraki açılışlar hızlıdır. Uygulama hazır olunca tarayıcı
otomatik açılır.

> **macOS — ilk kez:** Finder'da çift tık çalışmazsa, dosyaya çalıştırma izni
> verin: `chmod +x start.command start.sh`

### Komut satırından

```bash
# Windows
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe run.py

# macOS / Linux
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run.py
```

Uygulama `http://127.0.0.1:8765` adresinde açılır (port doluysa bir sonraki
boş port seçilir).

---

## Kullanım

1. Video bağlantısını kutuya yapıştırın, **Formatları getir**'e basın.
2. Bir kalite seçin (varsayılan en yüksek) veya **Tüm formatlar**'dan belirli
   bir format seçin.
3. İndirme klasörünü ve gerekiyorsa çerez tarayıcısını ayarlayın.
4. **Kuyruğa ekle**'ye basın. İndirmeler sırayla işlenir; ilerleme canlı görünür.

---

## Login gerektiren siteler (çerez desteği)

Üyelik/giriş gerektiren bir videoyu indirmek için arayüzdeki **Çerez** menüsünden
o siteye giriş yaptığınız tarayıcıyı seçin. Uygulama arka planda yt-dlp'ye
`--cookies-from-browser` olarak iletir.

- İndirmeden önce **seçtiğiniz tarayıcıyı tamamen kapatın.** Windows'ta güncel
  Chrome'un çerez şifrelemesi yalnızca tarayıcı kapalıyken çözülebilir.
- `Safari` seçeneği yalnızca macOS'ta sunulur.
- Çerez verisi yalnızca yt-dlp'ye iletilir; kaydedilmez veya kayda yazılmaz.

---

## Güncelleme

Siteler değiştikçe yt-dlp'yi güncel tutmak indirmelerin çalışmaya devam etmesini
sağlar:

```bash
# Windows
.venv\Scripts\python.exe -m pip install -U yt-dlp
# macOS / Linux
.venv/bin/python -m pip install -U yt-dlp
```

---

## Geliştirme

```bash
# Test bağımlılıkları
.venv/bin/python -m pip install -r requirements-dev.txt
# Testler (kapsam raporuyla)
.venv/bin/python -m pytest --cov=app
```

Mimari ve tasarım kararları için `DESIGN.md`, proje durumu için `CLAUDE.md`.

---

## Sorun giderme

| Sorun | Çözüm |
|-------|-------|
| "Python bulunamadı" | Python 3.10+ kurun (yukarıdaki kurulum komutları). |
| ffmpeg ile ilgili hata | ffmpeg kurun ve PATH'te olduğundan emin olun. |
| YouTube'da format eksik | `deno` kurun (isteğe bağlı kurulum). |
| Çerezli indirme başarısız | İlgili tarayıcıyı kapatıp tekrar deneyin. |
| Port kullanımda | Uygulama otomatik olarak sonraki boş portu seçer. |
