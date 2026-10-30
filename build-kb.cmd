@echo off
rem Glide - rebuild the knowledge base the coach uses (data\kb.db); a full build takes a minute or two.
rem   build-kb.cmd              full rebuild
rem   build-kb.cmd --if-stale   only when glide.db, recordings, logs, rules or code changed since the last build
rem   build-kb.cmd --check      only say whether a rebuild is needed, and why
rem While the engine runs it keeps kb.db open: the new KB is then saved as data\kb.new.db, and the engine
rem installs it the next time it starts (from the app or from start-glide.cmd). The app also rebuilds the KB by
rem itself in the background when it is stale, so this is for development.
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
%PY% knowledge\build_kb.py %*
set RC=%ERRORLEVEL%
if not "%RC%"=="0" pause
exit /b %RC%
