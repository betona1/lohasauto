"""
수정사항 1.0 창.

바로 실행하거나, 요일과 시각을 골라 예약해 둔다. 예약은 윈도우 작업
스케줄러에 등록하므로 **프로그램을 꺼두어도 돈다** — 켜져 있어야만 도는
방식이면 밤에 자동으로 돌릴 수가 없다.
"""
import os
import re
import subprocess
import sys

from PySide6.QtCore import Qt, QTime
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QGroupBox,
                               QHBoxLayout, QLabel, QMessageBox,
                               QPlainTextEdit, QPushButton, QTimeEdit,
                               QVBoxLayout)

from .. import db

TASK_NAME = "lohasauto_fix10"
SETTING_SOLDOUT = "fix10_soldout_folder"
DAYS = [("월", "MON"), ("화", "TUE"), ("수", "WED"), ("목", "THU"),
        ("금", "FRI"), ("토", "SAT"), ("일", "SUN")]
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BAT = os.path.join(ROOT, "수정1.0_예약.bat")


def _run(args: list) -> tuple:
    """schtasks 를 부른다. (성공, 출력) 을 돌려준다."""
    try:
        p = subprocess.run(args, capture_output=True, text=True,
                           encoding="cp949", errors="replace",
                           creationflags=getattr(subprocess,
                                                 "CREATE_NO_WINDOW", 0))
        return p.returncode == 0, (p.stdout or "") + (p.stderr or "")
    except Exception as e:
        return False, str(e)


def schedule_status() -> dict:
    """등록된 예약을 읽는다. {'on': bool, 'when': '월,수 20:00' , 'raw': ...}"""
    ok, out = _run(["schtasks", "/query", "/tn", TASK_NAME, "/fo", "LIST",
                    "/v"])
    if not ok:
        return {"on": False, "when": "", "raw": out}
    when = ""
    m = re.search(r"(?:Start Time|시작 시간)\s*:\s*([^\r\n]+)", out)
    if m:
        when = m.group(1).strip()
    m2 = re.search(r"(?:Days|일)\s*:\s*([^\r\n]+)", out)
    if m2 and m2.group(1).strip() not in ("", "N/A"):
        when = f"{m2.group(1).strip()} {when}"
    return {"on": True, "when": when, "raw": out}


class Fix10Dialog(QDialog):
    """바로 실행 / 예약. 실행 자체는 부모 창의 작업 스레드로 넘긴다."""

    def __init__(self, parent=None, on_run=None, on_sweep=None):
        super().__init__(parent)
        self.on_run = on_run
        self.on_sweep = on_sweep
        self.setWindowTitle("수정사항 1.0")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)

        folder = db.get_job_folder() or "(지정 안 됨)"
        last = db.fix10_last_run(folder).get("at") or "없음"
        head = QLabel(
            f"작업폴더 : <b>{folder}</b><br>"
            f"마지막 실행 : {last}<br>"
            "목록에서 <b>정보수정</b> 이 붙은 광고상품을 1.0 수정으로 밀어 줍니다."
            "<br>정방향·역방향을 동시에 돌립니다. 품절 상품은 건너뜁니다.")
        head.setTextFormat(Qt.RichText)
        head.setWordWrap(True)
        lay.addWidget(head)

        # 어느 폴더를 돌릴지. 마스터폴더를 여럿 쓰면 작업폴더 하나만 봐서는
        # 새로 넣은 폴더가 빠진다 (2026-09-06 '595. 광고진행-엑사' 가 그랬다).
        pick = QHBoxLayout()
        pick.addWidget(QLabel("폴더"))
        self.cmb = QComboBox()
        masters = db.list_master_folders()
        job = db.get_job_folder()
        if len(masters) > 1:
            self.cmb.addItem(f"작업대상 폴더 전부 ({len(masters)}개)", "*")
        for m in masters:
            self.cmb.addItem(m + ("  (작업폴더)" if m == job else ""), m)
        pick.addWidget(self.cmb, 1)
        lay.addLayout(pick)

        row = QHBoxLayout()
        self.btn_now = QPushButton("▶ 바로 실행")
        self.btn_now.setMinimumHeight(36)
        self.btn_now.setStyleSheet("font-weight:bold; color:#4527a0;")
        self.btn_now.clicked.connect(self._run_now)
        row.addWidget(self.btn_now)
        lay.addLayout(row)

        # 전상품품절을 옮길 임의분류. 사용자마다 폴더 이름이 다르다.
        sb = QGroupBox("품절 처리")
        sl = QHBoxLayout(sb)
        sl.addWidget(QLabel("품절시 옮길 폴더"))
        self.cmb_out = QComboBox()
        self.cmb_out.addItem("(옮기지 않음)", "")
        cur = db.get_setting(SETTING_SOLDOUT, "")
        with db.sqlite_conn() as c:
            allf = [r["name"] for r in c.execute(
                "SELECT name FROM folder ORDER BY sort_order, name")]
        # '품절' 이 든 폴더를 맨 위로 올려 고르기 쉽게 한다
        hot = [f for f in allf if "품절" in f]
        for f in hot + [f for f in allf if f not in hot]:
            self.cmb_out.addItem(f, f)
        i = self.cmb_out.findData(cur)
        if i >= 0:
            self.cmb_out.setCurrentIndex(i)
        elif hot:
            self.cmb_out.setCurrentIndex(self.cmb_out.findData(hot[0]))
        sl.addWidget(self.cmb_out, 1)
        b = QPushButton("저장")
        b.clicked.connect(self._save_soldout)
        sl.addWidget(b)
        b2 = QPushButton("지금 넘기기")
        b2.setToolTip("수정사항이 있으면서 품절인 상품을 찾아 지정한 폴더로"
                      + chr(10) + "분류변경 합니다 (화면에서 하던 것과 같습니다).")
        b2.clicked.connect(self._sweep)
        sl.addWidget(b2)
        lay.addWidget(sb)

        box = QGroupBox("예약 — 고른 요일마다 그 시각에 자동 실행")
        bl = QVBoxLayout(box)

        days = QHBoxLayout()
        self.chk_days = []
        for ko, _ in DAYS:
            c = QCheckBox(ko)
            self.chk_days.append(c)
            days.addWidget(c)
        self.chk_all = QCheckBox("매일")
        self.chk_all.toggled.connect(self._toggle_all)
        days.addWidget(self.chk_all)
        days.addStretch()
        bl.addLayout(days)

        t = QHBoxLayout()
        t.addWidget(QLabel("시각"))
        self.time = QTimeEdit(QTime(20, 0))       # 점검이 도는 저녁 8시
        self.time.setDisplayFormat("HH:mm")
        t.addWidget(self.time)
        self.btn_set = QPushButton("예약 등록")
        self.btn_set.clicked.connect(self._set_schedule)
        t.addWidget(self.btn_set)
        self.btn_del = QPushButton("예약 해제")
        self.btn_del.clicked.connect(self._del_schedule)
        t.addWidget(self.btn_del)
        t.addStretch()
        bl.addLayout(t)

        self.lbl_state = QLabel("")
        self.lbl_state.setWordWrap(True)
        bl.addWidget(self.lbl_state)
        lay.addWidget(box)

        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        self.out.setMaximumHeight(120)
        self.out.setStyleSheet("font-family:'Consolas','D2Coding',monospace;")
        lay.addWidget(self.out)

        close = QPushButton("닫기")
        close.clicked.connect(self.accept)
        lay.addWidget(close)

        self._refresh()

    # ------------------------------------------------------------ 동작
    def _toggle_all(self, on):
        for c in self.chk_days:
            c.setChecked(on)

    def _picked(self) -> list:
        return [en for c, (_, en) in zip(self.chk_days, DAYS) if c.isChecked()]

    def _refresh(self):
        st = schedule_status()
        if st["on"]:
            self.lbl_state.setText(
                f"<b style='color:#2e7d32'>● 예약됨</b> — {st['when']}")
        else:
            self.lbl_state.setText("<b style='color:#9e9e9e'>● 예약 없음</b>")
        self.lbl_state.setTextFormat(Qt.RichText)

    def _run_now(self):
        v = self.cmb.currentData() if self.cmb.count() else None
        folders = (db.list_master_folders() if v == "*"
                   else [v or db.get_job_folder()])
        folders = [f for f in folders if f]
        if not folders:
            QMessageBox.information(self, "안내", "폴더를 먼저 지정해 주세요.")
            return
        if self.on_run:
            self.on_run(folders)
            self.accept()

    def _save_soldout(self):
        v = self.cmb_out.currentData() or ""
        db.set_setting(SETTING_SOLDOUT, v)
        self.out.appendPlainText(
            f"품절 이동 폴더 = {v or '(옮기지 않음)'}  · 로컬·서버 저장")
        QMessageBox.information(
            self, "저장", f"전상품품절은 '{v}' 로 넘깁니다."
            if v else "품절 상품을 옮기지 않습니다.")

    def _sweep(self):
        v = self.cmb_out.currentData() or ""
        if not v:
            QMessageBox.information(self, "안내", "옮길 폴더를 먼저 고르세요.")
            return
        db.set_setting(SETTING_SOLDOUT, v)
        pv = self.cmb.currentData() if self.cmb.count() else None
        folders = (db.list_master_folders() if pv == "*"
                   else [pv or db.get_job_folder()])
        if self.on_sweep:
            self.on_sweep([f for f in folders if f], v)
            self.accept()

    def _write_bat(self):
        """예약이 부를 배치 파일. 스케줄러가 요일을 정하므로 --weekly 는 안 쓴다."""
        py = sys.executable
        if py.lower().endswith("pythonw.exe"):
            py = py[:-len("pythonw.exe")] + "python.exe"
        txt = (
            "@echo off\r\n"
            "chcp 65001 > nul\r\n"
            f'cd /d "{ROOT}"\r\n'
            "if not exist logs mkdir logs\r\n"
            f'"{py}" -X utf8 -u tools\\fix10.py --apply --all-folders '
            ">> logs\\fix10_sched.log 2>&1\r\n")
        with open(BAT, "w", encoding="utf-8") as f:
            f.write(txt)

    def _set_schedule(self):
        days = self._picked()
        if not days:
            QMessageBox.information(self, "안내", "요일을 하나 이상 골라주세요.")
            return
        self._write_bat()
        hhmm = self.time.time().toString("HH:mm")
        args = ["schtasks", "/create", "/tn", TASK_NAME, "/tr", f'"{BAT}"',
                "/st", hhmm, "/f"]
        if len(days) == 7:
            args += ["/sc", "daily"]
        else:
            args += ["/sc", "weekly", "/d", ",".join(days)]
        ok, out = _run(args)
        self.out.appendPlainText(out.strip() or ("등록됨" if ok else "실패"))
        if ok:
            ko = "매일" if len(days) == 7 else \
                ",".join(k for k, e in DAYS if e in days)
            QMessageBox.information(
                self, "예약 등록",
                f"{ko} {hhmm} 에 수정사항 1.0 이 자동으로 돕니다.\n"
                "프로그램이 꺼져 있어도 실행됩니다.")
        else:
            QMessageBox.warning(self, "예약 실패",
                                "작업 스케줄러 등록에 실패했습니다.\n" + out[:300])
        self._refresh()

    def _del_schedule(self):
        ok, out = _run(["schtasks", "/delete", "/tn", TASK_NAME, "/f"])
        self.out.appendPlainText(out.strip() or ("해제됨" if ok else "실패"))
        self._refresh()
