"""자동점검 체크박스 + 로그 + 그래프 실동작 검증 (3주기)."""
import io, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from app import db
from app.ui.main_window import MainWindow

app = QApplication([])
w = MainWindow(); w.show()
w.spn_interval.setCurrentText("10")

def start():
    w.chk_monitor.setChecked(True)
    if w._monitor_worker is not None:
        w._monitor_worker.rate_log_every = 12   # 검증용: 12초마다 속도로그
    print("체크박스 ON →", w.lbl_monitor_state.text(), flush=True)
    # 35초 뒤 메인스레드에서 체크 해제 (사용자가 끄는 것과 동일)
    QTimer.singleShot(50000, lambda: (
        print("체크박스 OFF (사용자 조작과 동일)", flush=True),
        w.chk_monitor.setChecked(False)))

def finish():
    if w._monitor_thread is not None:
        return
    print()
    print("상태:", w.lbl_monitor_state.text(), "| 체크:", w.chk_monitor.isChecked())
    print("=" * 62)
    print(w.lbl_board.text())
    print("=" * 62)
    logs = db.recent_work_log()
    print(f"work_log     : {len(logs)}건")
    print(f"로컬 파일    : {db.work_log_path().name} "
          f"{db.work_log_path().exists()}")
    print(f"시간별 통계  : {db.hourly_stats(db.get_job_folder(), 24)}")
    print(f"막대 시리즈  : {len(w.chart_hourly.series())} "
          f"| 추이 시리즈 : {len(w.chart_trend.series())}")
    print(f"시간표       : {w.tbl_hourly.rowCount()}행")
    print(f"처리율       : {w.lbl_rate.text()}")
    rl = db.recent_rate_log(db.get_job_folder())
    print(f"rate_log     : {len(rl)}건 | 로컬 {db.rate_log_path().exists()}")
    for r in rl[:3]:
        print(f"   {r['ts']}  30분 {r['m30_info']} / 1시간 {r['h1_info']} "
              f"/ 10개당 {r['per10_info']}분 / 잔여예상 {r['eta_min']}분")
    print(f"속도표       : {w.tbl_rate.rowCount()}행")
    app.quit()

QTimer.singleShot(300, start)
t = QTimer(); t.timeout.connect(finish); t.start(700)
QTimer.singleShot(180000, app.quit)
app.exec()
