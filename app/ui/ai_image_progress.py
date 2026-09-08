"""
AI 이미지 일괄수정 **진행 창**.

작업이 몇 시간씩 걸리므로 지금 무엇이 돌고 있는지 볼 데가 있어야 한다
(2026-09-07 사용자: "작업중 로그창을 띄워야하는거 아닌가").

창을 닫아도 작업은 계속 돈다. 버튼을 다시 누르면 이 창이 다시 열린다.
"""
import re
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QProgressBar,
                               QPushButton, QTextEdit, QVBoxLayout)

NL = chr(10)
# 상태 글자에서 진행 수치를 뽑는다 — "처리중(381 / 995)"
_PROG = re.compile(r"\((\d+)\s*/\s*(\d+)\)")


class AiImageProgress(QDialog):
    """워커가 보내는 로그·상태를 그대로 보여준다. 사이트를 따로 긁지 않는다."""

    def __init__(self, parent, on_stop=None):
        super().__init__(parent)
        self.on_stop = on_stop
        self.setWindowTitle("AI 이미지 일괄수정 — 진행 상황")
        self.setMinimumSize(660, 460)
        # 창을 띄워둔 채 다른 화면을 쓸 수 있게 한다
        self.setWindowFlag(Qt.Window, True)
        self.setModal(False)

        self._t0 = time.time()
        self._pages_done = 0
        self._pages_total = 0

        v = QVBoxLayout(self)

        self.lbl_head = QLabel("작업을 준비하는 중...")
        self.lbl_head.setStyleSheet("font-size:15px; font-weight:bold;")
        v.addWidget(self.lbl_head)

        self.lbl_sub = QLabel("")
        self.lbl_sub.setWordWrap(True)
        self.lbl_sub.setStyleSheet(
            "background:#e8f5e9; border-radius:6px; padding:8px;")
        v.addWidget(self.lbl_sub)

        self.bar_page = QProgressBar()
        self.bar_page.setFormat("이 페이지 %v / %m")
        self.bar_page.setRange(0, 1)
        v.addWidget(self.bar_page)

        self.bar_all = QProgressBar()
        self.bar_all.setFormat("전체 페이지 %v / %m")
        self.bar_all.setRange(0, 1)
        v.addWidget(self.bar_all)

        self.txt = QTextEdit()
        self.txt.setReadOnly(True)
        self.txt.setStyleSheet(
            "font-family:Consolas,'D2Coding',monospace; font-size:12px;")
        v.addWidget(self.txt, 1)

        row = QHBoxLayout()
        self.btn_stop = QPushButton("중지 (이미 걸린 작업은 로하스에서 계속됩니다)")
        self.btn_stop.clicked.connect(self._stop)
        row.addWidget(self.btn_stop)
        row.addStretch(1)
        btn_close = QPushButton("창 닫기 (작업은 계속)")
        btn_close.clicked.connect(self.hide)
        row.addWidget(btn_close)
        v.addLayout(row)

        # 경과 시간을 1초마다 갱신한다 (사이트 호출 없음)
        self._tick = QTimer(self)
        self._tick.timeout.connect(self._retime)
        self._tick.start(1000)

    # ------------------------------------------------------------------
    def append(self, line: str):
        self.txt.append(f"{time.strftime('%H:%M:%S')}  {line}")
        self.txt.verticalScrollBar().setValue(
            self.txt.verticalScrollBar().maximum())

    def set_plan(self, folder: str, a: int, b: int, query: str):
        self._pages_total = b - a + 1
        self.bar_all.setRange(0, self._pages_total)
        self.bar_all.setValue(0)
        self.lbl_head.setText(f"{folder}  {a}~{b}페이지")
        self.lbl_sub.setText(
            f"질의어 : {query}" + NL
            + f"한 페이지 1,000개씩 · 총 {self._pages_total}회")

    def on_stat(self, st: dict):
        """워커가 보내는 상태. `state` 는 사이트 글자 그대로다."""
        state = st.get("state") or ""
        page = st.get("page")
        no = st.get("no") or ""
        self._pages_done = max(self._pages_done, int(st.get("done_pages") or 0))
        self.bar_all.setValue(self._pages_done)

        m = _PROG.search(state)
        if m:
            cur, tot = int(m.group(1)), int(m.group(2))
            self.bar_page.setRange(0, tot)
            self.bar_page.setValue(cur)
        elif "완료" in state:
            self.bar_page.setValue(self.bar_page.maximum())

        head = f"{page}페이지 · No.{no} · {state}" if page else state
        self.lbl_head.setText(head)
        self._retime()

    def _retime(self):
        el = time.time() - self._t0
        left = ""
        cur, tot = self.bar_page.value(), self.bar_page.maximum()
        if cur > 0 and tot > cur and el > 60:
            per = el / max(cur, 1)
            rest = (tot - cur) * per
            rest += (self._pages_total - self._pages_done - 1) * tot * per
            if rest > 0:
                left = f" · 남은 예상 {int(rest // 3600)}시간 {int(rest % 3600 // 60)}분"
        base = self.lbl_sub.text().split(NL + "경과")[0]
        self.lbl_sub.setText(
            base + NL + f"경과 {int(el // 3600)}시간 {int(el % 3600 // 60)}분"
            f" {int(el % 60)}초{left}")

    def _stop(self):
        if self.on_stop:
            self.on_stop()
        self.append("중지를 요청했습니다 — 이미 걸린 페이지는 로하스에서 끝까지 돕니다.")
        self.btn_stop.setEnabled(False)

    def done_all(self, res: dict):
        self._tick.stop()
        self.bar_all.setValue(self.bar_all.maximum())
        self.lbl_head.setText("전부 끝났습니다")
        self.append(
            f"완료 — {res.get('pages', 0)}페이지 / "
            f"{res.get('count', 0):,}건 / "
            f"{res.get('seconds', 0) / 60:.1f}분")
        self.btn_stop.setEnabled(False)
