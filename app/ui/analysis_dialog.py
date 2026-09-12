"""
ALL 상품분석 — **폴더 선택 + 진행 창을 하나로** 합친 모달.

예전에는 폴더 고르는 창 / 확인 물음 / 진행 표시가 따로 놀았고, 진행 상황은
메인 창의 작은 막대에만 나왔다. 몇 시간짜리 작업이라 지금 어디까지 갔는지
볼 데가 있어야 한다(2026-09-08 사용자 지시).

규칙
  · **한 번에 한 폴더(계정)만** 돈다. 도는 동안에는 폴더를 고를 수도,
    시작할 수도 없다 — 진행 상태만 본다.
  · **창을 닫아도 작업은 계속 돈다.** 버튼을 다시 누르면 이 창이 다시 열리고
    돌고 있던 진행 상황을 그대로 이어서 보여준다.
"""
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel,
                               QProgressBar, QPushButton, QTextEdit,
                               QVBoxLayout)

NL = chr(10)


class AnalysisDialog(QDialog):
    """
    on_start(folder)  시작 버튼을 누르면 부른다. 실제 실행은 메인 창이 한다.
    on_stop()         중지 요청.
    """

    def __init__(self, parent, folders: list, on_start=None, on_stop=None,
                 running_folder: str = "", state: dict = None):
        super().__init__(parent)
        self.on_start = on_start
        self.on_stop = on_stop
        self._t0 = None
        self._done = 0
        self._total = 0

        self.setWindowTitle("ALL 상품분석")
        self.setMinimumSize(640, 480)
        # 창을 띄워둔 채 다른 화면을 쓸 수 있게 한다
        self.setWindowFlag(Qt.Window, True)
        self.setModal(False)

        v = QVBoxLayout(self)

        # ── 폴더 고르기 ────────────────────────────────────────────
        row = QHBoxLayout()
        row.addWidget(QLabel("작업폴더"))
        self.cmb = QComboBox()
        self.cmb.setMinimumWidth(330)
        # **마스터 폴더만** 보여준다. 43개를 다 늘어놓으면 '10. 광고키워드
        # 생성' 같은 것이 먼저 잡힌다 — `ai_image_dialog` 과 같은 방식이다
        # (2026-09-10 사용자).
        from app import db as _db
        names = [f if isinstance(f, str) else (f.get("name") or "")
                 for f in (folders or [])]
        master = [n for n in _db.list_master_folders() if n]
        cnt = {}
        for f in (folders or []):
            if not isinstance(f, str):
                cnt[f.get("name") or ""] = int(f.get("site_count") or 0)
        for name in (master or [n for n in names if n]):
            n = cnt.get(name, 0)
            self.cmb.addItem(f"{name}  ({n:,})" if n else name, name)
        job = _db.get_job_folder()
        i_ = self.cmb.findData(job) if job else -1
        if i_ >= 0:
            self.cmb.setCurrentIndex(i_)
        row.addWidget(self.cmb, 1)
        self.btn_run = QPushButton("🔬 상품분석 시작")
        self.btn_run.setStyleSheet("font-weight:bold;")
        self.btn_run.clicked.connect(self._start)
        row.addWidget(self.btn_run)
        v.addLayout(row)

        self.lbl_note = QLabel(
            "폴더 **전체**를 훑어 LCP 마다 1건씩 분석을 겁니다."
            " 대표이미지가 아직 안 끝난 것도 포함합니다." + NL
            + "이미 분석된 LCP 는 건너뜁니다. 상품정보 상태(미작업/저장완료)는"
              " 바뀌지 않습니다.")
        self.lbl_note.setWordWrap(True)
        self.lbl_note.setStyleSheet(
            "color:#555; background:#f3f6f9; border-radius:6px; padding:8px;")
        v.addWidget(self.lbl_note)

        # ── 진행 ───────────────────────────────────────────────────
        self.lbl_head = QLabel("대기 중")
        self.lbl_head.setStyleSheet("font-size:15px; font-weight:bold;")
        v.addWidget(self.lbl_head)

        self.bar = QProgressBar()
        self.bar.setRange(0, 1)
        self.bar.setValue(0)
        self.bar.setFormat("%v / %m")
        v.addWidget(self.bar)

        self.lbl_sub = QLabel("")
        self.lbl_sub.setWordWrap(True)
        self.lbl_sub.setStyleSheet(
            "background:#e8f5e9; border-radius:6px; padding:8px;")
        v.addWidget(self.lbl_sub)

        self.txt = QTextEdit()
        self.txt.setReadOnly(True)
        self.txt.setStyleSheet(
            "font-family:Consolas,'D2Coding',monospace; font-size:12px;")
        v.addWidget(self.txt, 1)

        bot = QHBoxLayout()
        self.btn_stop = QPushButton("중지")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop)
        bot.addWidget(self.btn_stop)
        bot.addStretch(1)
        btn_close = QPushButton("창 닫기 (작업은 계속)")
        btn_close.clicked.connect(self.hide)
        bot.addWidget(btn_close)
        v.addLayout(bot)

        self._tick = QTimer(self)
        self._tick.timeout.connect(self._retime)

        if running_folder:
            self.mark_running(running_folder, state or {})

    # ------------------------------------------------------------------
    def _start(self):
        folder = self.cmb.currentData() or self.cmb.currentText()
        if not folder:
            return
        if self.on_start:
            self.on_start(folder)

    def _stop(self):
        if self.on_stop:
            self.on_stop()
        self.append("중지를 요청했습니다 — 이미 건 분석은 로하스에서 계속됩니다.")
        self.btn_stop.setEnabled(False)

    # ------------------------------------------------------------------
    def mark_running(self, folder: str, state: dict = None):
        """돌고 있는 상태로 잠근다. 폴더는 못 바꾸고 진행만 본다."""
        i = self.cmb.findData(folder)
        if i >= 0:
            self.cmb.setCurrentIndex(i)
        self.cmb.setEnabled(False)
        self.btn_run.setEnabled(False)
        self.btn_run.setText("돌고 있습니다")
        self.btn_stop.setEnabled(True)
        self.lbl_head.setText(f"{folder} — 분석 중")
        if self._t0 is None:
            self._t0 = (state or {}).get("t0") or time.time()
        if state:
            self.on_stat(state)
        if not self._tick.isActive():
            self._tick.start(1000)

    def mark_external(self, busy: dict):
        """다른 창이나 CLI 가 돌리고 있을 때. 볼 수만 있고 시작은 못 한다."""
        folder = busy.get("folder") or ""
        i = self.cmb.findData(folder)
        if i >= 0:
            self.cmb.setCurrentIndex(i)
        self.cmb.setEnabled(False)
        self.btn_run.setEnabled(False)
        self.btn_run.setText("다른 곳에서 실행 중")
        self.btn_stop.setEnabled(False)      # 남의 작업은 못 멈춘다
        self.lbl_head.setText(f"{folder} — 다른 창/CLI 에서 분석 중")
        d, t = int(busy.get("done") or 0), int(busy.get("total") or 0)
        if t:
            self.on_progress(d, t)
        self.lbl_sub.setText(
            f"이 프로그램 밖(PID {busy.get('pid')})에서 돌고 있습니다."
            + NL + "끝나야 다른 폴더를 시작할 수 있습니다.")

    def mark_idle(self):
        # 다음 실행이 경과시간을 처음부터 세도록 지운다. 창을 재사용하므로
        # 여기서 안 지우면 지난 실행 시각이 그대로 남는다.
        self._t0 = None
        self.cmb.setEnabled(True)
        self.btn_run.setEnabled(True)
        self.btn_run.setText("🔬 상품분석 시작")
        self.btn_stop.setEnabled(False)
        self._tick.stop()

    def append(self, line: str):
        self.txt.append(f"{time.strftime('%H:%M:%S')}  {line}")
        sb = self.txt.verticalScrollBar()
        sb.setValue(sb.maximum())

    def on_progress(self, done: int, total: int):
        self._done, self._total = done, total
        if total > 0:
            self.bar.setRange(0, total)
            self.bar.setValue(done)
            self.bar.setFormat(f"%v / %m  ({done * 100 // max(total, 1)}%)")
        self._retime()

    def on_stat(self, st: dict):
        d = int(st.get("processed") or 0)
        t = int(st.get("total") or 0)
        if t:
            self.on_progress(d, t)
        self.lbl_sub.setText(
            f"완료 {st.get('done', 0):,} · 이미완료 {st.get('already', 0):,}"
            f" · 오류 {st.get('error', 0):,} · 남음 {st.get('remain', 0):,}")

    def _retime(self):
        if not self._t0:
            return
        el = time.time() - self._t0
        rate = self._done / el * 60 if el > 0 and self._done else 0
        left = ""
        if rate > 0 and self._total > self._done:
            rest = (self._total - self._done) / rate
            end = time.strftime("%H:%M", time.localtime(time.time() + rest * 60))
            left = f" · 남은 예상 {int(rest)}분 (약 {end} 끝남)"
        base = self.lbl_sub.text().split(NL)[0]
        self.lbl_sub.setText(
            base + NL
            + f"경과 {int(el // 3600)}시간 {int(el % 3600 // 60)}분 {int(el % 60)}초"
            + (f" · {rate:.1f}종/분" if rate else "") + left)

    def done_all(self, stats: dict):
        self._tick.stop()
        self.bar.setValue(self.bar.maximum())
        self.lbl_head.setText("전부 끝났습니다")
        self.append(
            f"완료 — 대상 {stats.get('total', 0):,}종 / "
            f"완료 {stats.get('done', 0):,} · "
            f"이미완료 {stats.get('already', 0):,} · "
            f"오류 {stats.get('error', 0):,} · "
            f"시간초과 {stats.get('timeout', 0):,}")
        self.mark_idle()
