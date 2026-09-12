"""
ALL 태그 — 폴더 하나의 태그를 끝까지 채우는 창.

`AnalysisDialog` 과 같은 규칙이다.
  · **한 번에 한 폴더만** 돈다. 도는 동안에는 폴더를 고를 수도, 시작할
    수도 없다 — 진행만 본다.
  · **창을 닫아도 작업은 계속 돈다.** 버튼을 다시 누르면 돌던 상황을
    이어서 보여준다.
  · 폴더를 고르고 [시작] 을 누르면 **무엇을 하는지 안내**하고, 확인을
    눌러야 시작한다(2026-09-09 사용자 지시).
"""
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel,
                               QMessageBox, QProgressBar, QPushButton,
                               QTextEdit, QVBoxLayout)

NL = chr(10)


class TagDialog(QDialog):
    """on_start(folder) / on_stop() 는 메인 창이 준다."""

    def __init__(self, parent, folders: list, on_start=None, on_stop=None,
                 counter=None):
        super().__init__(parent)
        self.on_start = on_start
        self.on_stop = on_stop
        self.counter = counter          # counter(folder) -> (종, 건) 미리보기
        self._t0 = None
        self._done = 0
        self._total = 0

        self.setWindowTitle("ALL 태그")
        self.setMinimumSize(640, 480)
        self.setWindowFlag(Qt.Window, True)
        self.setModal(False)

        v = QVBoxLayout(self)

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
        self.cmb.currentIndexChanged.connect(self._preview)
        row.addWidget(self.cmb, 1)
        self.btn_run = QPushButton("🏷 태그 채우기")
        self.btn_run.setStyleSheet("font-weight:bold;")
        self.btn_run.clicked.connect(self._start)
        row.addWidget(self.btn_run)
        v.addLayout(row)

        self.lbl_note = QLabel()
        self.lbl_note.setWordWrap(True)
        self.lbl_note.setStyleSheet(
            "color:#555; background:#f3f6f9; border-radius:6px; padding:8px;")
        v.addWidget(self.lbl_note)

        self.lbl_head = QLabel("대기 중")
        self.lbl_head.setStyleSheet("font-size:15px; font-weight:bold;")
        v.addWidget(self.lbl_head)

        self.bar = QProgressBar()
        self.bar.setRange(0, 1)
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
        self._preview()

    # ------------------------------------------------------------------
    def _preview(self):
        """고른 폴더에 무엇이 몇 건 남았는지 미리 보여준다."""
        folder = self.cmb.currentData() or ""
        n = ""
        if self.counter and folder:
            try:
                lcps, rows = self.counter(folder)
                n = (f"지금 이 폴더의 태그 작업 대상이 "
                     f"**{lcps:,}묶음 / {rows:,}건** 입니다." + NL)
            except Exception:
                n = ""
        self.lbl_note.setText(
            n
            + "· 대상은 **카테고리가 저장된 · 상품정보 「미작업」**인 것입니다."
              " 대표이미지 승인 여부는 가리지 않습니다." + NL
            + "· 태그는 **로하스 태그 후보 표에서만** 고르고, 규격·재질·색상·"
              "수량이 그 상품 원상품명과 맞는 것만 씁니다." + NL
            + "· 형제끼리 겹치지 않게 돌아가며 최대 10개씩 나눠 줍니다."
              " 태그 후보가 하나도 없을 때만 상품명 후보에서 2개까지." + NL
            + "· 태그 저장은 **상품정보 상태를 바꾸지 않습니다.**"
              " 「저장완료」로 넘어가지 않습니다.")

    def _start(self):
        folder = self.cmb.currentData() or self.cmb.currentText()
        if not folder:
            return
        lcps = rows = 0
        if self.counter:
            try:
                lcps, rows = self.counter(folder)
            except Exception:
                pass
        msg = [
            f"작업폴더 : {folder}",
            "",
            f"대상 : 카테고리 저장 · 상품정보 미작업  {lcps:,}종 / {rows:,}건",
            "        (대표이미지 상태는 가리지 않습니다)",
            "",
            "하는 일",
            "  1) LCP·카테고리 묶음마다 태그 후보 표 조회",
            "  2) 상품 특성(규격·재질·색상·수량)이 맞는 것만 남김",
            "  3) 형제끼리 겹치지 않게 돌아가며 최대 10개씩 저장",
            "",
            "태그를 저장해도 상품정보 상태는 바뀌지 않습니다.",
            "",
            "시작할까요?",
        ]
        if QMessageBox.question(
                self, "ALL 태그", NL.join(msg),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No) != QMessageBox.Yes:
            return
        if self.on_start:
            self.on_start(folder)

    def _stop(self):
        if self.on_stop:
            self.on_stop()
        self.append("중지를 요청했습니다 — 이미 저장한 것은 그대로입니다.")
        self.btn_stop.setEnabled(False)

    # ------------------------------------------------------------------
    def mark_running(self, folder: str, state: dict = None):
        i = self.cmb.findData(folder)
        if i >= 0:
            self.cmb.setCurrentIndex(i)
        self.cmb.setEnabled(False)
        self.btn_run.setEnabled(False)
        self.btn_run.setText("돌고 있습니다")
        self.btn_stop.setEnabled(True)
        self.lbl_head.setText(f"{folder} — 태그 채우는 중")
        if self._t0 is None:
            self._t0 = time.time()
        if state:
            self.on_stat(state)
        if not self._tick.isActive():
            self._tick.start(1000)

    def mark_external(self, busy: dict):
        folder = busy.get("folder") or ""
        i = self.cmb.findData(folder)
        if i >= 0:
            self.cmb.setCurrentIndex(i)
        self.cmb.setEnabled(False)
        self.btn_run.setEnabled(False)
        self.btn_run.setText("다른 곳에서 실행 중")
        self.btn_stop.setEnabled(False)
        self.lbl_head.setText(f"{folder} — 다른 창/CLI 에서 실행 중")

    def mark_idle(self):
        self._t0 = None
        self.cmb.setEnabled(True)
        self.btn_run.setEnabled(True)
        self.btn_run.setText("🏷 태그 채우기")
        self.btn_stop.setEnabled(False)
        self._tick.stop()
        self._preview()

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
        d, t = int(st.get("processed") or 0), int(st.get("total") or 0)
        if t:
            self.on_progress(d, t)
        phase = st.get("phase") or ""
        extra = ""
        if st.get("ok") is not None:
            extra = f" · 저장 {st.get('ok', 0):,} · 실패 {st.get('fail', 0):,}"
        self.lbl_sub.setText(f"{phase}{extra}")
        self._retime()

    def _retime(self):
        if not self._t0:
            return
        el = time.time() - self._t0
        rate = self._done / el * 60 if el > 0 and self._done else 0
        left = ""
        if rate > 0 and self._total > self._done:
            rest = (self._total - self._done) / rate
            end = time.strftime("%H:%M",
                                time.localtime(time.time() + rest * 60))
            left = f" · 남은 예상 {int(rest)}분 (약 {end})"
        base = self.lbl_sub.text().split(NL)[0]
        self.lbl_sub.setText(
            base + NL
            + f"경과 {int(el // 3600)}시간 {int(el % 3600 // 60)}분 "
              f"{int(el % 60)}초" + (f" · {rate:.1f}/분" if rate else "") + left)

    def done_all(self, res: dict):
        self._tick.stop()
        self.bar.setValue(self.bar.maximum())
        self.lbl_head.setText("전부 끝났습니다")
        if res.get("stopped"):
            self.append("중지되었습니다.")
        else:
            self.append(
                f"완료 — 묶음 {res.get('groups', 0):,}개 / "
                f"저장 {res.get('ok', 0):,}건 · 실패 {res.get('fail', 0):,}건")
            if res.get("empty"):
                self.append(f"후보가 아예 없던 LCP {len(res['empty'])}종")
        self.mark_idle()
