@echo off
setlocal
title TITAN MT5 bridge
cd /d "%~dp0\.."
where py >nul 2>nul
if errorlevel 1 (
  echo Python nije instaliran. Instalirajte Python 3.12 s python.org i oznacite "Add python.exe to PATH".
  pause
  exit /b 1
)
echo Instaliram/azuriram pakete (MetaTrader5, fastapi, uvicorn, qrcode)...
py -3 -m pip install --upgrade --quiet MetaTrader5 fastapi uvicorn qrcode
if not exist bridge_secret.txt py -3 -c "import secrets;open('bridge_secret.txt','w').write(secrets.token_urlsafe(18))"
echo.
echo ================= TITAN MT5 BRIDGE =================
echo  SECRET za TITAN aplikaciju (polje "secret"):
type bridge_secret.txt
echo.
echo.
echo  URL za TITAN aplikaciju (polje "url"):
for /f %%i in ('tailscale ip -4 2^>nul') do echo    http://%%i:8765
echo    (ako iznad nema adrese: instalirajte Tailscale i prijavite se)
echo.
echo  MetaTrader 5 mora biti otvoren i prijavljen. Ne zatvarajte ovaj prozor.
echo ====================================================
echo  Otvaram stranicu s QR kodom u pregledniku...
start "" cmd /c "timeout /t 5 >nul & start http://127.0.0.1:8765/pair"
py -3 mt5_bridge.py --host 0.0.0.0 --port 8765
pause
