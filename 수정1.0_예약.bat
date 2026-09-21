@echo off
chcp 65001 > nul
cd /d "%~dp0"
rem ============================================================
rem  수정사항 1.0 — 마스터 폴더 전부, 매일 저녁 8시
rem  윈도우 작업 스케줄러에 매일 20:00 으로 걸어둔다.
rem  --all-folders 가 마스터 폴더를 하나씩 돌며 '정보수정' 건을 밀어준다.
rem  정방향·역방향을 동시에 돌리므로 시간이 절반으로 준다.
rem ============================================================
if not exist logs mkdir logs
python -X utf8 -u tools\fix10.py --apply --all-folders >> logs\fix10_sched.log 2>&1
