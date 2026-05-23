#!/usr/bin/env bash
# Pluck — macOS tek-tıkla kurulum
# https://github.com/miracakkyn/pluck
#
# Çalıştır (Terminal'e yapıştır + Enter):
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/miracakkyn/pluck/main/install-mac.sh)"
#
# NOT: `curl | bash` ŞEKLİ KULLANMA — sudo parola promptunu engeller
# (terminal stdin'i pipe'a takılır). Yukarıdaki `bash -c "$(curl ...)"`
# Homebrew'un kendi resmi installer'ının kullandığı yöntemdir; çalışır.
#
# Yaptıkları:
#   1. Homebrew yoksa kurar (Mac parolası sorar — admin kullanıcı gerek)
#   2. python@3.13, ffmpeg, aria2, deno, git'i brew ile kurar
#   3. Pluck'ı ~/pluck dizinine klonlar (varsa günceller)
#   4. start.sh ile motoru başlatır; tarayıcı 127.0.0.1:8765'e açılır
#
# Eklentiyi yüklemek manuel — script motoru başlattıktan sonra
# talimatlar terminale yazdırılır.
set -e

INSTALL_DIR="${HOME}/pluck"
REPO="https://github.com/miracakkyn/pluck.git"

step() { printf '\n\033[1;36m▸\033[0m %s\n' "$1"; }
ok()   { printf '\033[1;32m✓\033[0m %s\n' "$1"; }

step "Pluck kurulumu başlıyor (Mac)..."

# 1. Homebrew
if ! command -v brew >/dev/null 2>&1; then
    step "Homebrew bulunamadı, kuruluyor"
    printf '  \033[33m!\033[0m Mac parolan istenecek — bu normal (Homebrew /opt/homebrew yazma izni için).\n'
    printf '    Admin kullanıcı değilsen kurulum başarısız olur; o Mac'\''te admin olan biriyle yap.\n\n'
    # NONINTERACTIVE BIRAKMA — parola prompt'unun çalışmasına izin ver.
    /bin/bash -c \
        "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Yeni kurulan brew'u bu oturumun PATH'ine ekle (Apple Silicon + Intel).
    if [[ -x /opt/homebrew/bin/brew ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    elif [[ -x /usr/local/bin/brew ]]; then
        eval "$(/usr/local/bin/brew shellenv)"
    fi
fi
ok "Homebrew hazır ($(brew --version | head -1))"

# 2. Bağımlılıklar
step "Bağımlılıklar kuruluyor: python@3.13, ffmpeg, aria2, deno, git"
brew install python@3.13 ffmpeg aria2 deno git
ok "Tüm bağımlılıklar kurulu"

# 3. Repo (klon ya da güncelle)
if [[ -d "${INSTALL_DIR}/.git" ]]; then
    step "Mevcut Pluck güncelleniyor: ${INSTALL_DIR}"
    git -C "${INSTALL_DIR}" pull --rebase --quiet
else
    step "Pluck indiriliyor → ${INSTALL_DIR}"
    git clone --quiet "${REPO}" "${INSTALL_DIR}"
fi
chmod +x "${INSTALL_DIR}/start.sh" "${INSTALL_DIR}/start.command" 2>/dev/null || true
ok "Pluck ${INSTALL_DIR} altında"

# 4. Eklenti & sonraki adımlar talimatı
cat <<EOF

╔══════════════════════════════════════════════════════════════╗
║                  ✅  PLUCK KURULDU                           ║
╚══════════════════════════════════════════════════════════════╝

Motor birazdan başlayacak; varsayılan tarayıcı 127.0.0.1:8765'e açılacak.

▸ Eklentiyi yüklemek için (motor çalışırken, ayrı pencerede yap):

  CHROME / EDGE:
    chrome://extensions  →  Geliştirici modu (sağ üst)  →
    "Paketlenmemiş öğe yükle"  →  şu klasörü seç:
       ${INSTALL_DIR}/extension

  FIREFOX:
    about:debugging  →  "Bu Firefox"  →
    "Geçici Eklenti Yükle"  →  şu dosyayı seç:
       ${INSTALL_DIR}/extension/manifest.json

▸ Motoru sonraki seferlerde başlatmak için:
    bash ${INSTALL_DIR}/start.sh
  (veya Finder'da ${INSTALL_DIR}/start.command dosyasına çift tıkla)

▸ Motoru durdurmak için: bu terminalde Ctrl+C
▸ Pluck'ı güncellemek için: bu komutu tekrar çalıştır (kurulum atlanır).

────────────────────────────────────────────────────────────────
EOF

# 5. Motor
step "Motor başlatılıyor (Ctrl+C ile durdur)"
exec bash "${INSTALL_DIR}/start.sh"
