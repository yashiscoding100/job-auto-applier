@echo off
echo ===================================================
echo [1/2] Installing Python Dependencies (Zero-Rupee Stack)...
echo ===================================================
pip install -r requirements.txt

echo.
echo ===================================================
echo [2/2] Installing Playwright Chromium Browser...
echo ===================================================
playwright install chromium

echo.
echo ===================================================
echo SETUP COMPLETE!
echo 1. Put your GEMINI_API_KEY in .env
echo 2. Run 'start_mobile_dashboard.bat' to control from phone!
echo ===================================================
pause
