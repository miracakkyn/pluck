#!/usr/bin/env bash
# Pluck - macOS / Linux baslatici
# Sanal ortami olusturur, bagimliliklari kurar ve uygulamayi calistirir.
set -e
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
    echo "Sanal ortam olusturuluyor..."
    python3 -m venv .venv
fi

echo "Bagimliliklar denetleniyor..."
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r requirements.txt

.venv/bin/python run.py
