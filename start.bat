@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem ============================================================
rem  Бот живёт в СВОЁМ окружении .venv внутри этой папки.
rem  Основной Python, Студия, Whisper, rembg, torch, numpy НЕ трогаются.
rem ============================================================
set "VPY=%~dp0.venv\Scripts\python.exe"
set "PIPFLAGS=--disable-pip-version-check --trusted-host pypi.org --trusted-host files.pythonhosted.org"

if not exist "%VPY%" (
    echo Создаю отдельное окружение бота .venv ...
    py -3.11 -m venv .venv 2>nul || python -m venv .venv
)
if not exist "%VPY%" (
    echo.
    echo ОШИБКА: не удалось создать окружение .venv. Ничего не устанавливаю.
    echo Проверь, что установлен Python 3.11: py -3.11 --version
    pause
    exit /b 1
)

echo Окружение бота: %VPY%
echo Проверяю библиотеки бота (только в .venv)...
"%VPY%" -m pip install -q %PIPFLAGS% -r requirements.txt
if errorlevel 1 (
    echo.
    echo ОШИБКА установки библиотек. Пришли текст выше Claude.
    pause
    exit /b 1
)

if not exist .env (
    copy .env.example .env >nul
    echo.
    echo Открываю файл .env: впиши токен бота после BOT_TOKEN= , сохрани и закрой Блокнот.
    notepad .env
)

"%VPY%" bot.py
pause
