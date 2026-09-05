@echo off
title 로하스 오토 - 외부망(집)
cd /d "%~dp0"

REM ============================================================
REM  집 등 외부망에서 쓰는 실행 파일.
REM  사내 MySQL/데이터랩은 사설 IP라 직접 못 간다. SSH 로 한 번
REM  들어가 두 서비스를 내 PC 포트로 끌어온다(터널).
REM
REM     127.0.0.1:13306  ->  192.168.219.200:3306   MySQL
REM     127.0.0.1:18900  ->  192.168.219.100:8900   데이터랩
REM
REM  터널이 안 열려도 프로그램은 돈다. 로하스 사이트와 분석 API 는
REM  공인 IP 라 어디서든 되기 때문이다. 미러와 데이터랩만 꺼진다.
REM ============================================================
set NET_PROFILE=external
set TUNNEL_ENABLED=1

echo.
echo   [ 로하스 오토 - 외부망 ]
echo   ----------------------------------------
echo    SSH 터널   : .env 의 SSH_HOST 로 접속
echo    MySQL 미러 : 127.0.0.1:13306  (터널 경유)
echo    데이터랩   : 127.0.0.1:18900  (터널 경유)
echo.

where python >nul 2>nul
if errorlevel 1 goto NOPYTHON

python -c "import PySide6, paramiko" >nul 2>nul
if errorlevel 1 (
    echo   [준비] 필요한 패키지를 설치합니다. 처음 한 번만 걸립니다...
    python -m pip install -r requirements.txt
)

echo   [확인] 터널을 열고 접속 환경을 점검합니다...
python -X utf8 tools\check_env.py
echo.
echo   * 2번 터널이 실패해도 1번이 모두 정상이면 작업은 됩니다.
echo     (미러 저장과 데이터랩 기능만 꺼집니다)
echo.

echo   프로그램을 시작합니다. 이 창은 닫으셔도 됩니다.
start "" pythonw -X utf8 main.py
goto END

:NOPYTHON
echo   [오류] 파이썬을 찾을 수 없습니다.
echo          python.org 에서 설치할 때 "Add to PATH" 를 켜주세요.
pause

:END
