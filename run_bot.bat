@echo off
title JobBot - Automation Runner
cls
echo ===================================================
echo 🤖 JobBot - Email & Form Automation Runner
echo Scanning Gmail for 'god bless you' emails...
echo ===================================================
echo.
python src/main.py --mode email --max-emails 5
echo.
echo ===================================================
echo Run finished! Check logs/applications_log.csv
echo ===================================================
pause
