@echo off
REM Daily scrape for Windows. Register once (runs 02:15 every night):
REM   schtasks /Create /SC DAILY /ST 02:15 /TN "HotelPriceScrape" /TR "\"C:\path\to\hotel_price_scraper\scripts\run_daily.bat\""
cd /d "%~dp0.."
if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat
if not exist data\reports mkdir data\reports
python -m hps run --export >> data\reports\scheduler.log 2>&1
