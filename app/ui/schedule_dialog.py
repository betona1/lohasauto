"""
예약 작업 관리 — 윈도우 작업 스케줄러를 화면에서 보고 켜고 끈다.

지금까지 예약은 `.bat` + 스케줄러로만 걸려 있어서 **무엇이 언제 도는지
화면에서 알 수가 없었다.** 여기서 한눈에 보고 시각을 바꾼다
(2026-09-12 사용자).

    lohasauto-수정1.0    매일 20:00  마스터 폴더 전부 · 수정사항 1.0
    lohasauto-광고수집    매일 03:00  광고비·매체·소재·실매출

`schtasks` 를 부르지 않고 PowerShell 의 ScheduledTask 명령을 쓴다 —
한글 작업명이 깨지지 않는다.
"""
import subprocess

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout,
                               QHeaderView, QLabel, QMessageBox, QPushButton,
                               QTableWidget, QTableWidgetItem, QTimeEdit,
                               QVBoxLayout)

NL = chr(10)

# (작업명, 배치파일, 설명, 기본 시각)
TASKS = [
    ("lohasauto-수정1.0", "수정1.0_예약.bat",
     "마스터 폴더 전부 — 광고상품 '정보수정' 을 1.0 수정으로 밀어준다",
     "20:00"),
    ("lohasauto-광고수집", "광고수집_매일.bat",
     "광고비·매체·소재·실매출 + 놓친 검색어 (3:00/3:30/3:50 재시도)",
     "03:00"),
]


def _ps(script: str) -> str:
    """PowerShell 한 줄 실행. 출력은 UTF-8 로 받는다."""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + script],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=40)
        return (r.stdout or "").strip()
    except Exception as e:
        return f"!! {str(e)[:80]}"


def task_info(name: str) -> dict:
    """
    그 작업의 상태·시각·다음 실행.

    PowerShell 의 `-f` 와 `-join` 을 한 줄에 섞으면 우선순위 때문에 빈
    문자열이 돌아온다 — 한 줄씩 따로 물어본다(2026-09-12).
    """
    out = _ps(
        f"$t = Get-ScheduledTask -TaskName '{name}' -ErrorAction SilentlyContinue;"
        " if (-not $t) { 'NONE'; exit }"
        f" $i = Get-ScheduledTaskInfo -TaskName '{name}';"
        " $t.State; '---';"
        " foreach ($g in $t.Triggers) { $g.StartBoundary }; '---';"
        " $i.NextRunTime; '---'; $i.LastRunTime")
    if not out or out.startswith("!!") or out.startswith("NONE"):
        return {"exists": False, "raw": out}
    parts = [p.strip() for p in out.split("---")]
    while len(parts) < 4:
        parts.append("")
    times = []
    for b in parts[1].splitlines():
        b = b.strip()
        if "T" in b:
            times.append(b.split("T")[1][:5])
    return {"exists": True, "state": parts[0].strip(), "times": times,
            "next": parts[2].strip(), "last": parts[3].strip()}


class ScheduleDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("예약 작업")
        self.setWindowFlag(Qt.Window, True)
        self.setMinimumSize(900, 420)
        v = QVBoxLayout(self)

        t = QLabel("예약 작업 — 윈도우 작업 스케줄러")
        t.setStyleSheet("font-size:16px; font-weight:bold;")
        v.addWidget(t)
        note = QLabel(
            "PC 가 켜져 있어야 돕니다. 꺼져 있었으면 켠 뒤에 한 번 돕니다"
            "(StartWhenAvailable)." + NL
            + "시각을 바꾸려면 줄을 고르고 아래에서 시간을 정한 뒤"
              " [시각 변경] 을 누르십시오.")
        note.setWordWrap(True)
        note.setStyleSheet(
            "color:#555; background:#f3f6f9; border-radius:6px; padding:8px;")
        v.addWidget(note)

        self.tbl = QTableWidget(0, 6)
        self.tbl.setHorizontalHeaderLabels(
            ["작업", "상태", "시각", "다음 실행", "마지막 실행", "하는 일"])
        self.tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl.horizontalHeader().setSectionResizeMode(
            5, QHeaderView.Stretch)
        v.addWidget(self.tbl, 1)

        row = QHBoxLayout()
        row.addWidget(QLabel("시각"))
        self.te = QTimeEdit()
        self.te.setDisplayFormat("HH:mm")
        row.addWidget(self.te)
        b1 = QPushButton("시각 변경")
        b1.clicked.connect(self.on_time)
        row.addWidget(b1)
        b2 = QPushButton("지금 실행")
        b2.clicked.connect(self.on_run)
        row.addWidget(b2)
        self.btn_toggle = QPushButton("끄기 / 켜기")
        self.btn_toggle.clicked.connect(self.on_toggle)
        row.addWidget(self.btn_toggle)
        row.addStretch(1)
        b4 = QPushButton("새로고침")
        b4.clicked.connect(self.reload)
        row.addWidget(b4)
        v.addLayout(row)

        self.lbl = QLabel("")
        self.lbl.setStyleSheet("color:#57606a;")
        self.lbl.setWordWrap(True)
        v.addWidget(self.lbl)
        self.reload()

    # ------------------------------------------------------------
    def reload(self):
        self.tbl.setRowCount(len(TASKS))
        for i, (name, bat, desc, _t) in enumerate(TASKS):
            d = task_info(name)
            def it(s, color=""):
                x = QTableWidgetItem(str(s))
                x.setFlags(x.flags() & ~Qt.ItemIsEditable)
                if color:
                    from PySide6.QtGui import QColor
                    x.setForeground(QColor(color))
                return x
            self.tbl.setItem(i, 0, it(name))
            if not d.get("exists"):
                self.tbl.setItem(i, 1, it("없음", "#c62828"))
                for j in (2, 3, 4):
                    self.tbl.setItem(i, j, it("-"))
            else:
                st = d.get("state") or ""
                self.tbl.setItem(i, 1, it(
                    "켜짐" if st != "Disabled" else "꺼짐",
                    "#2e7d32" if st != "Disabled" else "#9e9e9e"))
                self.tbl.setItem(i, 2, it(" / ".join(d.get("times") or [])))
                self.tbl.setItem(i, 3, it(d.get("next") or "-"))
                self.tbl.setItem(i, 4, it(d.get("last") or "-"))
            self.tbl.setItem(i, 5, it(desc))
        self.tbl.resizeColumnsToContents()
        self.tbl.horizontalHeader().setSectionResizeMode(
            5, QHeaderView.Stretch)
        if self.tbl.rowCount():
            self.tbl.selectRow(0)

    def _sel(self):
        r = self.tbl.currentRow()
        return TASKS[r] if 0 <= r < len(TASKS) else None

    def on_time(self):
        s = self._sel()
        if not s:
            return
        name, bat, _d, _t = s
        hhmm = self.te.time().toString("HH:mm")
        out = _ps(
            f"$t = New-ScheduledTaskTrigger -Daily -At '{hhmm}';"
            f" Set-ScheduledTask -TaskName '{name}' -Trigger $t | Out-Null;"
            f" 'ok'")
        self.lbl.setText(f"{name} → 매일 {hhmm}  ({out})")
        self.reload()

    def on_toggle(self):
        s = self._sel()
        if not s:
            return
        name = s[0]
        d = task_info(name)
        if not d.get("exists"):
            QMessageBox.information(self, "예약", "등록되지 않은 작업입니다.")
            return
        off = (d.get("state") == "Disabled")
        cmd = "Enable-ScheduledTask" if off else "Disable-ScheduledTask"
        _ps(f"{cmd} -TaskName '{name}' | Out-Null")
        self.lbl.setText(f"{name} → {'켰습니다' if off else '껐습니다'}")
        self.reload()

    def on_run(self):
        s = self._sel()
        if not s:
            return
        name, bat, desc, _ = s
        if QMessageBox.question(
                self, "지금 실행",
                f"{name} 을(를) 지금 실행합니다." + NL + desc + NL * 2
                + "백그라운드로 돌며 로그는 logs/ 에 쌓입니다. 계속할까요?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No) != QMessageBox.Yes:
            return
        _ps(f"Start-ScheduledTask -TaskName '{name}'")
        self.lbl.setText(f"{name} 을(를) 시작했습니다. 로그를 보십시오.")
        self.reload()
