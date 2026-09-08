@echo off
chcp 65001 > nul
cd /d "%~dp0"
rem 윈도우 작업 스케줄러에 **매일** 걸어두면 된다.
rem --weekly 가 마지막 실행을 보고 7일이 안 지났으면 그냥 끝낸다.
if not exist logs mkdir logs
python -X utf8 -u tools\fix10.py --apply --weekly >> logs\fix10_weekly.log 2>&1
