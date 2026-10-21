@echo off
title SalesMesh
cd /d "%~dp0"
echo Starting SalesMesh... keep this window open while you use the app.
python -m streamlit run streamlit_app.py
pause
