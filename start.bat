@echo off
REM Video Indirici - Windows tek-tik baslatici
REM Sanal ortami olusturur, bagimliliklari kurar ve uygulamayi calistirir.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Sanal ortam olusturuluyor...
    py -m venv .venv
    if errorlevel 1 (
        echo HATA: Python bulunamadi. Lutfen Python 3.10+ kurun.
        pause
        exit /b 1
    )
)

echo Bagimliliklar denetleniyor...
".venv\Scripts\python.exe" -m pip install -q --upgrade pip
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if errorlevel 1 (
    echo HATA: Bagimliliklar kurulamadi.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" run.py
pause
