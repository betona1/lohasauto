@echo off
title 로하스 오토 - 사내망
cd /d "%~dp0"

REM ============================================================
REM  회사(사내망)에서 쓰는 실행 파일.
REM  MySQL 미러(192.168.219.200)와 데이터랩(192.168.219.100)에
REM  바로 붙으므로 SSH 터널이 필요 없다.
REM ============================================================
set NET_PROFILE=internal
set TUNNEL_ENABLED=0

echo.
echo   [ 로하스 오토 - 사내망 ]
echo   ----------------------------------------
echo    MySQL 미러 : 192.168.219.200  (직접)
echo    데이터랩   : 192.168.219.100  (직접)
echo.

where python >nul 2>nul
if errorlevel 1 goto NOPYTHON

python -c "import PySide6" >nul 2>nul
if errorlevel 1 (
    echo   [준비] 필요한 패키지를 설치합니다. 처음 한 번만 걸립니다...
    python -m pip install -r requirements.txt
)

echo   [확인] 접속 환경을 점검합니다...
python -X utf8 tools\check_env.py
echo.

echo   프로그램을 시작합니다. 이 창은 닫으셔도 됩니다.
start "" pythonw -X utf8 main.py
goto END

:NOPYTHON
echo   [오류] 파이썬을 찾을 수 없습니다.
echo          python.org 에서 설치할 때 "Add to PATH" 를 켜주세요.
pause

:END
