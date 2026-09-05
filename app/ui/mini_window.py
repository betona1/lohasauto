"""
미니 모드 — 평소에 띄워두고 보는 작은 창.

하루 종일 필요한 건 사실 세 가지뿐이다.

    오늘 작업량   ·   저장완료 수량   ·   ALL 상품분석

나머지는 필요할 때만 큰 창을 열면 된다. 그래서 이 창은 그 셋만 담고
화면 구석에 항상 떠 있어도 방해되지 않을 만큼 작다(기본 340x210).

큰 창과 같은 프로세스를 쓴다. 미니로 갈 때 큰 창을 숨기고, 「크게」를
누르면 다시 보여준다 — 자동점검이나 진행 중인 작업이 끊기지 않는다.
"""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from .. import db


class _Num(QFrame):
    """숫자 하나를 큼직하게 보여주는 칸."""

    def __init__(self, title: str, color: str, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            "QFrame { background:#ffffff; border:1px solid #cfd8dc;"
            " border-radius:8px; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 8)
        lay.setSpacing(0)

        t = QLabel(title)
        t.setStyleSheet("color:#607d8b; border:none;")
        f = t.font()
        f.setPointSize(9)
        t.setFont(f)
        lay.addWidget(t)

        self.val = QLabel("-")
        f = QFont()
        f.setPointSize(24)
        f.setBold(True)
        self.val.setFont(f)
        self.val.setStyleSheet(f"color:{color}; border:none;")
        lay.addWidget(self.val)

    def set(self, v):
        self.val.setText(f"{v:,}" if isinstance(v, int) else str(v))


class MiniWindow(QWidget):
    """
    큰 창(MainWindow)을 물고 있는 작은 창.

    버튼은 큰 창의 것을 그대로 누른다(`main.on_run_analysis`). 미니에서
    시작해도 진행 상황은 큰 창의 워커가 관리하므로 둘이 어긋나지 않는다.
    """

    def __init__(self, main):
        super().__init__()
        self.main = main
        self.setWindowTitle("로하스 오토")
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.resize(340, 210)
        self._build()

        # 큰 창이 점검할 때마다 같이 갱신되게 하고, 놓쳐도 주기적으로 읽는다
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(20_000)

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        self.lbl_folder = QLabel("")
        self.lbl_folder.setStyleSheet("color:#37474f; font-weight:bold;")
        self.lbl_folder.setWordWrap(True)
        root.addWidget(self.lbl_folder)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.card_today = _Num("오늘 작업량", "#c62828")
        self.card_done = _Num("저장완료", "#00695c")
        row.addWidget(self.card_today)
        row.addWidget(self.card_done)
        root.addLayout(row)

        row = QHBoxLayout()
        self.btn_analysis = QPushButton("🔬 ALL 상품분석")
        self.btn_analysis.setMinimumHeight(34)
        self.btn_analysis.setStyleSheet(
            "QPushButton { font-weight:bold; color:#b71c1c; }")
        self.btn_analysis.clicked.connect(self._run_analysis)
        row.addWidget(self.btn_analysis, 1)

        self.btn_big = QPushButton("크게")
        self.btn_big.setMinimumHeight(34)
        self.btn_big.setToolTip("전체 화면으로 돌아갑니다 (Ctrl+M)")
        self.btn_big.setShortcut("Ctrl+M")
        self.btn_big.setStyleSheet(
            "QPushButton { background:#0d47a1; color:white; font-weight:bold;"
            " padding:0 16px; border-radius:4px; }")
        self.btn_big.clicked.connect(self.to_big)
        row.addWidget(self.btn_big)
        root.addLayout(row)

        self.lbl_state = QLabel("")
        self.lbl_state.setStyleSheet("color:#78909c;")
        root.addWidget(self.lbl_state)

    # ------------------------------------------------------------------

    def refresh(self):
        """숫자만 다시 읽는다. 사이트를 부르지 않고 로컬 DB 만 본다."""
        folder = db.get_job_folder()
        self.lbl_folder.setText(folder or "작업폴더 미지정")
        if not folder:
            self.card_today.set("-")
            self.card_done.set("-")
            return
        try:
            today = db.today_totals(folder)
            self.card_today.set(int(today.get("info") or 0))
        except Exception:
            self.card_today.set("-")
        try:
            last = db.latest_scan(folder)
            self.card_done.set(int((last or {}).get("info_save_rows") or 0))
        except Exception:
            self.card_done.set("-")

    def set_state(self, text: str):
        self.lbl_state.setText(text or "")

    def _run_analysis(self):
        self.main.on_run_analysis()

    def to_big(self):
        self.main.to_big()

    def closeEvent(self, e):
        # 미니를 닫으면 프로그램이 사라진 것처럼 보인다. 큰 창을 되살린다.
        self.main.to_big()
        e.ignore()
