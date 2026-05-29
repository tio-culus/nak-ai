@echo off
echo ===================================================
echo  NakAI PyInstaller Build Script
echo ===================================================
echo Installing dependencies from requirements.txt...
pip install -r requirements.txt

echo.
echo Building NakAI.exe...
python -m PyInstaller --noconsole --onefile --name NakAI src/main.py

echo.
echo Cleaning up build temporary files...
if exist build rmdir /s /q build
if exist NakAI.spec del /f /q NakAI.spec

echo.
echo ===================================================
echo  Build Completed!
echo  Executable is located in: dist/NakAI.exe
echo ===================================================
pause
