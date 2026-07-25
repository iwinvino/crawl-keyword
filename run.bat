@echo off
chcp 65001 >nul
title SEO iGaming Toolkit - Crawl Keyword / Check Index / Check URL / Day Index
cd /d "%~dp0"

REM ============================================================
REM  Cong RIENG cho tool nay = 8510
REM  (Podcast Studio dung 8501 -> tach cong de khong mo nham app)
REM ============================================================
set "PORT=8510"

REM ====== Tao venv lan dau (neu chua co) ======
if not exist ".venv\Scripts\python.exe" (
    echo [*] Lan dau chay - dang tao moi truong ao .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo [X] Khong tao duoc venv. Kiem tra Python da cai chua: python --version
        pause
        exit /b 1
    )
    echo [*] Dang cai thu vien ^(requirements.txt^) ...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [X] Cai thu vien that bai.
        pause
        exit /b 1
    )
)

REM ====== Mo app (tro THANG toi app.py trong chinh thu muc nay) ======
echo [*] Dang mo SEO iGaming Toolkit tai http://localhost:%PORT% ...
echo     (Dong cua so nay de tat app)
".venv\Scripts\python.exe" -m streamlit run "%~dp0app.py" --server.port %PORT% --server.headless false --browser.gatherUsageStats false

pause
