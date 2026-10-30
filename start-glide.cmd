@echo off
rem Glide to 60 - start the engine (it also archives game data in the background).
cd /d "%~dp0"
set PY=
where py >nul 2>nul && set PY=py -3
if "%PY%"=="" where python >nul 2>nul && set PY=python
if "%PY%"=="" (
  echo Python 3 is not installed. Install it with:
  echo     winget install -e --id Python.Python.3.12
  echo then open a new terminal and run this again.
  pause
  exit /b 1
)
rem the engine needs Python 3.12 or newer (py -3 picks the newest Python 3 installed)
%PY% -c "import sys; sys.exit(sys.version_info < (3, 12))"
if errorlevel 1 (
  echo Glide needs Python 3.12 or newer. Install it with:
  echo     winget install -e --id Python.Python.3.12
  echo then open a new terminal and run this again.
  pause
  exit /b 1
)
rem The engine starts right away on the databases it has and rebuilds glide.db / kb.db in the background while
rem you play (it switches over when they're ready; the next start installs them). Only a first start without
rem data\kb.db builds here, because the coach needs one. --no-kb: no build here and no background refresh.
set "GLIDE_KB=1"
for %%a in (%*) do if /i "%%~a"=="--no-kb" set "GLIDE_KB="
if not exist "data\kb.db" if defined GLIDE_KB (
  echo Glide: first start - building data\glide.db and data\kb.db once ^(a minute or two^) ...
  %PY% tools\build_db.py
  if errorlevel 1 echo Glide: the quest database step reported a problem, see above.
  %PY% knowledge\build_kb.py --if-stale
  if errorlevel 1 echo Glide: the knowledge base step reported a problem, see above. Starting the companion anyway.
)
rem the engine archives SavedVariables and the quest cache itself (no separate window)
%PY% companion\app.py %*
if errorlevel 1 pause
