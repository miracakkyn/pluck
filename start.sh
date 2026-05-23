#!/usr/bin/env bash
# Pluck - macOS / Linux baslatici
# Sanal ortami olusturur, bagimliliklari kurar ve uygulamayi calistirir.
set -e
cd "$(dirname "$0")"

# Mevcut Python'lardan en yenisini sec (3.10+ gerekli).
PY=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PY="$candidate"
        break
    fi
done
if [ -z "$PY" ]; then
    echo "HATA: Python 3.10+ bulunamadi. Once 'brew install python@3.13' calistirin." >&2
    exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
    echo "Sanal ortam olusturuluyor ($PY)..."
    "$PY" -m venv .venv
fi

echo "Bagimliliklar denetleniyor..."
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r requirements.txt

.venv/bin/python run.py
