@echo off
rem Glide to 60 - install local speech-to-text for push-to-talk (one time).
cd /d "%~dp0"
set PY=
where py >nul 2>nul && set PY=py -3
if "%PY%"=="" where python >nul 2>nul && set PY=python
if "%PY%"=="" ( echo Install Python first: winget install -e --id Python.Python.3.12 & pause & exit /b 1 )
%PY% -m pip install faster-whisper==1.2.1
echo Downloading the speech model (one time, about 480 MB)...
%PY% -c "from faster_whisper import WhisperModel; WhisperModel('small.en', device='cpu', compute_type='int8'); print('speech model ready')"
pause
