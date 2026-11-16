@echo off
title SalesMesh online
cd /d "%~dp0"
where cloudflared >nul 2>nul
if errorlevel 1 (
  echo Cloudflare Tunnel is not installed. Install it once with:
  echo     winget install --id Cloudflare.cloudflared
  echo then close this window and open it again.
  pause
  exit /b
)
echo Starting SalesMesh...
start "SalesMesh app - keep open" python -m streamlit run streamlit_app.py --server.headless true --server.port 8501
timeout /t 10 /nobreak >nul
echo.
echo Your public address is the line below that ends in  .trycloudflare.com
echo Open it on any phone or computer. It changes every time you start this file.
echo Keep BOTH windows open. Close them to take SalesMesh offline.
echo.
cloudflared tunnel --url http://localhost:8501
pause
