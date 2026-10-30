#!/usr/bin/env bash
# Daily scrape for macOS / Linux. Schedule with cron, e.g. 02:15 every night:
#   15 2 * * * /path/to/hotel_price_scraper/scripts/run_daily.sh
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f .venv/bin/activate ]; then source .venv/bin/activate; fi
mkdir -p data/reports
python -m hps run --export >> data/reports/scheduler.log 2>&1
