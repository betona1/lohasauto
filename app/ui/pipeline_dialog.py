"""
전체 작업 — 마스터 폴더를 순서대로 끝까지 미는 창.

    상태수집 → AI 이미지 일괄 → 상품분석 → 카테고리(+총용량) → 태그 → 상품명

**저장완료는 누르지 않는다.** 값만 채운다 (사용자 지침).
**비트마인드는 기본 제외** — 이미 끝난 폴더다(2026-09-12 사용자).

창을 닫아도 돈다. 하위 도구를 그대로 불러 쓰므로 화면과 CLI 가 같은 것을
실행한다(`tools/pipeline.py`).
"""
import os
import subprocess
import sys
import time

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QMessageBox,
                               QPushButton, QTextEdit, QVBoxLayout)

from .. import db

NL = chr(10)
STEPS = ["상태수집", "이미지", "상품분석", "카테고리", "태그", "상품명"]
SKIP_DEFAULT = "594. 광고진행-비트마인드"


class PipeWorker(QObject):
    line = Signal(str)
    done = Signal(int)

    def __init__(self, args):
        super().__init__()
        self.args = args
        self.proc = None
        self._stop = False

    def run(self):
        root = os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))
        cmd = [sys.executable, "-X", "utf8", "-u",
               os.path.join(root, "tools", "pipeline.py")] + self.args
        try:
            self.proc = subprocess.Popen(
                cmd, cwd=root, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            for ln in self.proc.stdout:
                self.line.emit(ln.rstrip())
                if self._stop:
                    break
            self.done.emit(self.proc.wait())
        except Exception as e:
            self.line.emit(f"!! {e}")
            self.done.emit(-1)

    def stop(self):
        self._stop = True
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:
                pass


class PipelineDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("전체 작업 (파이프라인)")
        self.setWindowFlag(Qt.Window, True)
        self.setModal(False)
        self.resize(1080, 720)
        self._thread = None
        self._t0 = None

        v = QVBoxLayout(self)
        t = QLabel("전체 작업 — 마스터 폴더를 순서대로 끝까지 밉니다")
        t.setStyleSheet("font-size:16px; font-weight:bold;")
        v.addWidget(t)
        note = QLabel(
            "상태수집 → AI 이미지 일괄 → 상품분석 → 카테고리(+총용량)"
            " → 태그 → 상품명" + NL
            + "**저장완료는 누르지 않습니다.** 값만 채웁니다 — 확인은"
              " 사람 몫입니다." + NL
            + "AI 이미지는 1,000건에 30분쯤 걸리고 앞 작업이 끝나야 다음이"
              " 돕니다. 전부 돌리면 여러 시간 걸립니다.")
        note.setWordWrap(True)
        note.setStyleSheet(
            "color:#555; background:#fff8e1; border-radius:6px; padding:8px;")
        v.addWidget(note)

        mid = QHBoxLayout()
        lf = QVBoxLayout()
        lf.addWidget(QLabel("폴더 (체크한 것만)"))
        self.lst = QListWidget()
        for f in db.list_master_folders():
            if not f:
                continue
            it = QListWidgetItem(f)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            # 비트마인드는 끝난 폴더 — 기본 해제
            it.setCheckState(Qt.Unchecked if f == SKIP_DEFAULT
                             else Qt.Checked)
            self.lst.addItem(it)
        lf.addWidget(self.lst, 1)
        mid.addLayout(lf, 3)

        rf = QVBoxLayout()
        rf.addWidget(QLabel("단계"))
        self.chk = {}
        for s in STEPS:
            c = QCheckBox(s)
            c.setChecked(True)
            self.chk[s] = c
            rf.addWidget(c)
        rf.addStretch(1)
        mid.addLayout(rf, 1)
        v.addLayout(mid, 2)

        row = QHBoxLayout()
        self.btn_plan = QPushButton("계획 보기")
        self.btn_plan.clicked.connect(lambda: self._start(plan=True))
        row.addWidget(self.btn_plan)
        self.btn_run = QPushButton("▶ 전체 작업 시작")
        self.btn_run.setStyleSheet("font-weight:bold; color:#b71c1c;")
        self.btn_run.clicked.connect(lambda: self._start(plan=False))
        row.addWidget(self.btn_run)
        self.btn_stop = QPushButton("중지")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop)
        row.addWidget(self.btn_stop)
        row.addStretch(1)
        self.lbl = QLabel("대기 중")
        self.lbl.setStyleSheet("font-weight:bold; color:#1565c0;")
        row.addWidget(self.lbl)
        btn_c = QPushButton("창 닫기 (작업은 계속)")
        btn_c.clicked.connect(self.hide)
        row.addWidget(btn_c)
        v.addLayout(row)

        self.txt = QTextEdit()
        self.txt.setReadOnly(True)
        self.txt.setStyleSheet(
            "font-family:Consolas,'D2Coding',monospace; font-size:12px;")
        v.addWidget(self.txt, 3)

    # ------------------------------------------------------------
    def _folders(self):
        out = []
        for i in range(self.lst.count()):
            it = self.lst.item(i)
            if it.checkState() == Qt.Checked:
                out.append(it.text())
        return out

    def _start(self, plan: bool):
        if self._thread is not None:
            QMessageBox.information(self, "전체 작업", "이미 돌고 있습니다.")
            return
        fs = self._folders()
        steps = [s for s in STEPS if self.chk[s].isChecked()]
        if not fs or not steps:
            QMessageBox.warning(self, "전체 작업",
                                "폴더와 단계를 하나 이상 고르십시오.")
            return
        skip = [self.lst.item(i).text() for i in range(self.lst.count())
                if self.lst.item(i).checkState() != Qt.Checked]
        if not plan:
            msg = ["폴더 " + str(len(fs)) + "개를 순서대로 밉니다.", ""]
            msg += ["  " + f for f in fs]
            msg += ["", "단계 : " + ", ".join(steps), ""]
            if skip:
                msg += ["제외 : " + ", ".join(skip), ""]
            msg += ["저장완료는 누르지 않습니다.",
                    "여러 시간 걸립니다. 시작할까요?"]
            if QMessageBox.question(
                    self, "전체 작업", NL.join(msg),
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No) != QMessageBox.Yes:
                return
        args = ["--step", ",".join(steps)]
        args += ["--plan"] if plan else ["--run"]
        for s in skip:
            args += ["--skip", s]
        if len(fs) == 1:
            args += ["--folder", fs[0]]
        self.txt.clear()
        self._t0 = time.time()
        w = PipeWorker(args)
        th = QThread(self)
        w.moveToThread(th)
        th.started.connect(w.run)
        w.line.connect(self._on_line)
        w.done.connect(self._on_done)
        w.done.connect(lambda *_: th.quit())
        th.finished.connect(self._on_thread_done)
        self._thread, self._worker = th, w
        self.btn_run.setEnabled(False)
        self.btn_plan.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.lbl.setText("계획 보는 중…" if plan else "돌고 있습니다")
        th.start()

    def _on_line(self, s: str):
        self.txt.append(s)
        sb = self.txt.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_done(self, rc: int):
        el = (time.time() - (self._t0 or time.time())) / 60
        self.lbl.setText(f"끝 ({el:.0f}분, rc={rc})")

    def _on_thread_done(self):
        if self._thread is not None:
            self._thread.deleteLater()
        self._thread = None
        self.btn_run.setEnabled(True)
        self.btn_plan.setEnabled(True)
        self.btn_stop.setEnabled(False)

    def _stop(self):
        if getattr(self, "_worker", None) is not None:
            self._worker.stop()
        self.lbl.setText("중지를 요청했습니다")
