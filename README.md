# Pluck

> **Sayfadan videoyu kopar al** — yt-dlp + ffmpeg motorlu, tarayıcı eklentisi
> ve yerel web arayüzü olan genel amaçlı video indirici.

Pluck site-özel kod içermez; yt-dlp'nin desteklediği her sitede çalışır
(YouTube, üniversite portalları, vimeo, twitch vb.).

İki kullanım yolu vardır — ikisi de aynı yerel motoru kullanır:

- **Chrome eklentisi** (önerilen): Tarayıcıda sağ üstteki ikona tıkla →
  bulunduğun sayfadaki video bulunur → kalite seç → indir.
- **Yerel web arayüzü** (yedek): `http://127.0.0.1:8765` adresinde açılan
  tek sayfa — bağlantıyı elle yapıştır.

> **Neden "motor" gerekiyor?** Bir tarayıcı eklentisi tek başına yt-dlp/ffmpeg
> çalıştıramaz (tarayıcı kısıtı). Bu yüzden ağır işi yerel bir yardımcı
> program (motor) yapar; eklenti yalnızca onun ön yüzüdür. Eklentiyi
> kullanmak için motorun arka planda çalışıyor olması gerekir.

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
winget install aria2.aria2        # isteğe bağlı — bkz. "aria2c notu"
```

### macOS (Homebrew)

```bash
brew install python@3.13
brew install ffmpeg
brew install deno                  # isteğe bağlı — bkz. "deno notu"
brew install aria2                 # isteğe bağlı — bkz. "aria2c notu"
```

> **deno notu:** yt-dlp, YouTube'da bazı formatları çıkarmak için bir
> JavaScript çalışma zamanı (`deno`) ister. Kurulu değilse uygulama yine
> çalışır ancak YouTube'da bazı formatlar eksik kalabilir. YouTube dışı
> sitelerde gerekmez.

> **aria2c notu:** `aria2c` kuruluysa Pluck onu otomatik kullanır ve HLS
> stream'lerini paralel HTTP bağlantılarıyla (her parça için 16'ya kadar)
> **2-3x daha hızlı** indirir — özellikle BunnyCDN/üniversite portallarında
> belirgin fark. Yoksa yt-dlp'nin yerleşik indiricisi kullanılır (çalışır,
> sadece daha yavaş).

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

## Chrome eklentisi (önerilen kullanım)

Eklenti, bulunduğun sayfadaki videoyu yerel motora taratıp indirir.

### Kurulum (Chrome / Edge)

1. Yukarıdaki adımlarla yerel motoru en az bir kez çalıştır (bağımlılıklar
   kurulsun).
2. Chrome / Edge'de `chrome://extensions` adresini aç.
3. Sağ üstten **Geliştirici modu**'nu aç.
4. **Paketlenmemiş öğe yükle**'ye tıkla ve proje içindeki `extension/`
   klasörünü seç.
5. Eklenti araç çubuğuna eklenir (görünmüyorsa puzzle ikonundan sabitle).

### Kurulum (Firefox)

Eklenti Firefox 115+ ile uyumludur (MV3). Yükleme:

1. Firefox adres çubuğuna `about:debugging` yaz.
2. Sol menüden **Bu Firefox**'a geç.
3. **Geçici Eklenti Yükle** → proje içindeki `extension/manifest.json`
   dosyasını seç.
4. Eklenti araç çubuğuna eklenir.

> Geçici eklenti Firefox kapanınca silinir; her açılışta tekrar yüklenir.
> Kalıcı kurulum için eklentinin Mozilla tarafından imzalanması gerekir
> (ücretsiz, AMO üzerinden) — v1 için geçici yükleme yeterli.

### Kullanım

1. Yerel motoru başlat (`start.bat` / `start.command`) — **arka planda açık
   kalmalı.** Motor kapalıysa eklenti "motoru başlat" uyarısı gösterir.
2. İndirmek istediğin videonun olduğu sayfaya git.
3. Sağ üstteki **Pluck** ikonuna tıkla.
4. Eklenti sayfayı tarar. Site login gerektiriyorsa **Çerez** menüsünden o
   siteye girişli tarayıcını seç ve ↻ ile yeniden tara.
5. Kaliteyi seç. İndirme klasörünü yazabilir ya da **Gözat…** ile native
   pencereden seçebilirsin (Gözat'tan sonra eklentiyi tekrar aç). **İndir**'e bas.
6. İlerleme popup'ta canlı görünür; popup'ı kapatsan da indirme motorda devam
   eder.

> macOS'ta da aynı: Chrome'a aynı `extension/` klasörü yüklenir.

---

## Kullanım (web arayüzü)

1. Video bağlantısını yapıştırın. Site login gerektiriyorsa **Çerez**
   menüsünden o siteye girişli tarayıcınızı seçin. **Formatları getir**'e basın.
2. Bir kalite seçin (varsayılan en yüksek) veya **Tüm formatlar**'dan belirli
   bir format seçin.
3. İndirme klasörünü yazın ya da **Gözat…** ile native pencereden seçin.
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
