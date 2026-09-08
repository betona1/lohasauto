@echo off
chcp 65001 > nul
cd /d "S:\python\lohasauto"
if not exist logs mkdir logs
python -X utf8 -u toolsix10.py --apply >> logsix10_sched.log 2>&1
