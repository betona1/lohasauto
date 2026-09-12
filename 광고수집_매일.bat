@echo off
chcp 65001 > nul
cd /d "%~dp0"
rem ============================================================
rem  네이버 검색광고 자료 매일 받아오기
rem  윈도우 작업 스케줄러에 **매일** 걸어두면 된다.
rem  --once-a-day 가 오늘 이미 받았으면 아무것도 하지 않고 끝낸다.
rem
rem    지출(/stats)   오늘 포함 최근 7일 (실시간)
rem    상세 보고서    어제까지, DB 에 빠진 날짜만 (최대 14일 소급)
rem
rem  당일치 상세 보고서는 네이버가 만들어 주지 않는다(20007 준비중).
rem  그래서 오늘 숫자는 /stats, 어제까지 상세는 보고서로 본다.
rem ============================================================
if not exist logs mkdir logs
python -X utf8 -u tools\ad_daily.py --once-a-day --catchup >> logs\ad_daily.log 2>&1
