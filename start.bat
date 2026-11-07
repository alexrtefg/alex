@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist .venv (
    echo Создаю окружение Python...
    py -3.11 -m venv .venv 2>nul || python -m venv .venv
)
call .venv\Scripts\activate.bat
echo Проверяю библиотеки...
python -m pip install -q --disable-pip-version-check -r requirements.txt

if not exist .env (
    copy .env.example .env >nul
    echo.
    echo Открываю файл .env: впиши токен бота после BOT_TOKEN= , сохрани и закрой Блокнот.
    notepad .env
)

python bot.py
pause
