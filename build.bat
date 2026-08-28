@echo off
REM Build StratoXFilter.exe (standalone, no Python needed to run the result).
REM Requires: python + pyinstaller  ->  pip install pyinstaller
cd /d "%~dp0"
python -m PyInstaller --onefile --windowed --name StratoXFilter ^
  --icon StratoXFilter.ico ^
  --add-data "measurments\X_axis_error_maps.csv;." ^
  --exclude-module numpy --exclude-module matplotlib --exclude-module pandas ^
  --distpath dist --workpath build_tmp --noconfirm gui.py
rmdir /s /q build_tmp 2>nul
del /q StratoXFilter.spec 2>nul
echo.
echo Done. Result is in the dist folder.
pause
