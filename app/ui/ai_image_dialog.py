"""
AI 이미지 일괄수정 창.

화면에서 하던 순서를 그대로 담았다.
    폴더 고르기 -> 시작 페이지 -> (기본) 끝 페이지까지 자동으로 이어서

**한 페이지 = 1,000건**이고, 로하스는 작업을 하나씩만 돌린다. 그래서
'끝까지 자동' 은 한 페이지가 완료되면 다음 페이지를 거는 식으로 이어간다.

질의어는 로하스 기본값('잘어울리는 배경')을 채워두고 사용자가 고쳐 쓴다.
"""
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QPushButton,
                               QSpinBox, QTextEdit, QVBoxLayout)

from .. import db
from ..lohas import ai_image

PER_PAGE = 1000
MIN_PER_PAGE = 32          # 실측 1,000건 = 31분31초 / 31분46초
NL = chr(10)


class AiImageDialog(QDialog):
    """`on_run(폴더, 시작페이지, 끝페이지, 작업명, 질의어)` 를 부른다."""

    def __init__(self, parent, on_run, jobs=None):
        super().__init__(parent)
        self.on_run = on_run
        self._jobs = jobs
        self._pages = 1
        self._done = set()
        self.setWindowTitle("AI 이미지 일괄수정")
        self.setMinimumWidth(560)

        v = QVBoxLayout(self)

        warn = QLabel(
            "일괄작업은 1회만 실행해 주세요." + NL
            + "한 페이지에 " + f"{PER_PAGE:,}" + "개씩 작업합니다." + NL
            + "로하스는 작업을 하나씩만 돌립니다 — "
            "앞 작업이 완료되어야 다음 페이지가 시작됩니다.")
        warn.setStyleSheet(
            "background:#fff3e0; border:1px solid #ffb74d; border-radius:6px;"
            "padding:10px; color:#e65100; font-weight:bold;")
        warn.setWordWrap(True)
        v.addWidget(warn)

        f = QFormLayout()
        self.cmb_folder = QComboBox()
        for name in db.list_master_folders():
            self.cmb_folder.addItem(name)
        job = db.get_job_folder()
        if job and self.cmb_folder.findText(job) >= 0:
            self.cmb_folder.setCurrentText(job)
        self.cmb_folder.currentTextChanged.connect(self._folder_changed)
        f.addRow("폴더", self.cmb_folder)

        row = QHBoxLayout()
        self.spn_from = QSpinBox()
        self.spn_from.setRange(1, 999)
        self.spn_from.setPrefix("시작 ")
        self.spn_from.setSuffix(" 페이지")
        self.spn_from.valueChanged.connect(self._recalc)
        row.addWidget(self.spn_from)

        self.spn_to = QSpinBox()
        self.spn_to.setRange(1, 999)
        self.spn_to.setPrefix("끝 ")
        self.spn_to.setSuffix(" 페이지")
        self.spn_to.valueChanged.connect(self._recalc)
        row.addWidget(self.spn_to)
        f.addRow("페이지", row)

        self.chk_all = QCheckBox("시작 페이지부터 끝까지 자동으로 이어서")
        self.chk_all.setChecked(True)
        self.chk_all.toggled.connect(self._toggle_all)
        f.addRow("", self.chk_all)

        self.txt_title = QLineEdit()
        self.txt_title.setMaxLength(100)
        self.txt_title.setPlaceholderText("작업명을 입력하세요.")
        self.txt_title.textChanged.connect(self._recalc)
        f.addRow("작업명", self.txt_title)

        self.txt_query = QTextEdit()
        self.txt_query.setPlainText(ai_image.DEFAULT_QUERY)
        self.txt_query.setFixedHeight(66)
        f.addRow("질의어", self.txt_query)
        v.addLayout(f)

        self.lbl_done = QLabel()
        self.lbl_done.setWordWrap(True)
        self.lbl_done.setStyleSheet(
            "background:#e3f2fd; border-radius:6px; padding:8px;"
            "color:#0d47a1;")
        v.addWidget(self.lbl_done)

        self.lbl_plan = QLabel()
        self.lbl_plan.setWordWrap(True)
        self.lbl_plan.setStyleSheet(
            "background:#e8f5e9; border-radius:6px; padding:8px;")
        v.addWidget(self.lbl_plan)

        later = QLabel("※ L코드로 일괄변경하기 — 추후 구현 예정입니다.")
        later.setStyleSheet("color:#8d6e63;")
        v.addWidget(later)

        self.lbl_state = QLabel("작업 상태를 읽는 중...")
        self.lbl_state.setWordWrap(True)
        self.lbl_state.setStyleSheet(
            "background:#eceff1; border-radius:6px; padding:8px;")
        v.addWidget(self.lbl_state)

        r2 = QHBoxLayout()
        btn = QPushButton("작업결과 새로고침")
        btn.clicked.connect(self._load_state)
        r2.addWidget(btn)
        r2.addStretch(1)
        v.addLayout(r2)

        bb = QDialogButtonBox()
        self.btn_ok = bb.addButton("적용하기", QDialogButtonBox.AcceptRole)
        bb.addButton("닫기", QDialogButtonBox.RejectRole)
        bb.accepted.connect(self._go)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

        self._folder_changed(self.cmb_folder.currentText())
        self._load_state()

    # ------------------------------------------------------------------
    def _folder_changed(self, folder: str):
        """
        그 폴더가 몇 페이지인지 **점검 기록에서** 읽어 범위를 맞춘다.

        이미 끝낸 페이지는 기록(`ai_image_job`)에서 읽어 **다음 페이지부터**
        시작하게 한다 — 같은 페이지를 두 번 걸지 않기 위해서다.
        """
        n = ai_image.total_pages(folder, PER_PAGE) or 1
        self._pages = n
        self._done = db.ai_done_pages(folder)
        for sp in (self.spn_from, self.spn_to):
            sp.blockSignals(True)
            sp.setRange(1, n)
            sp.blockSignals(False)
        nxt = 1
        while nxt in self._done and nxt < n:
            nxt += 1
        self.spn_from.setValue(nxt)
        self.spn_to.setValue(n)
        self.lbl_done.setText(
            ("이미 완료: " + ", ".join(f"{p}p" for p in sorted(self._done)))
            if self._done else "이 폴더에서 완료한 페이지가 없습니다.")
        self._toggle_all(self.chk_all.isChecked())
        self._recalc()

    def _toggle_all(self, on: bool):
        self.spn_to.setEnabled(not on)
        if on:
            self.spn_to.setValue(self._pages)
        self._recalc()

    def _range(self) -> tuple:
        a = self.spn_from.value()
        b = self._pages if self.chk_all.isChecked() else self.spn_to.value()
        return a, max(a, b)

    def _recalc(self):
        a, b = self._range()
        cnt = b - a + 1
        mins = cnt * MIN_PER_PAGE
        names = [ai_image.title_for(self.txt_title.text(), p)
                 for p in range(a, min(b, a + 2) + 1)]
        dup = sorted(p for p in range(a, b + 1) if p in self._done)
        warn = (NL + "※ " + ", ".join(f"{p}p" for p in dup)
                + " 는 이미 완료한 페이지입니다 — 다시 걸면 중복입니다."
                ) if dup else ""
        self.lbl_plan.setText(
            f"{a}페이지 ~ {b}페이지 = {cnt}회 (최대 {cnt * PER_PAGE:,}건)" + warn + NL
            + f"예상 소요 {mins // 60}시간 {mins % 60}분 "
              f"(1,000건에 약 {MIN_PER_PAGE}분 · 실측)" + NL
            + "작업명: " + ", ".join(names) + (" ..." if cnt > 3 else ""))

    def _load_state(self):
        try:
            rows = self._jobs() if self._jobs else []
        except Exception as e:
            self.lbl_state.setText(f"작업결과를 읽지 못했습니다 — {str(e)[:60]}")
            return
        run = [r for r in rows
               if any(k in (r.get("상태") or "") for k in ("대기중", "처리중"))]
        if run:
            r = run[0]
            self.lbl_state.setText(
                f"진행 중 — No.{r.get('No')} {r.get('작업명/분류')} / "
                f"{r.get('상태')}" + NL
                + "    이 작업이 끝난 뒤에 다시 걸어주세요.")
            self.btn_ok.setEnabled(False)
            return
        self.btn_ok.setEnabled(True)
        if rows:
            r = rows[0]
            self.lbl_state.setText(
                f"진행 중인 작업 없음. 마지막 — No.{r.get('No')} "
                f"{r.get('작업명/분류')} / {r.get('상태')} "
                f"({r.get('완료일시') or '-'})")
            if not self.txt_title.text().strip():
                self.txt_title.setText(r.get("작업명/분류") or "")
                self._recalc()
        else:
            self.lbl_state.setText("작업 기록이 없습니다.")

    def _go(self):
        title = self.txt_title.text().strip()
        query = self.txt_query.toPlainText().strip()
        if not title:
            QMessageBox.information(self, "안내", "작업명을 입력해주세요.")
            self.txt_title.setFocus()
            return
        if not query:
            QMessageBox.information(self, "안내", "질의어를 입력해주세요.")
            self.txt_query.setFocus()
            return
        folder = self.cmb_folder.currentText()
        a, b = self._range()
        cnt = b - a + 1
        mins = cnt * MIN_PER_PAGE
        ret = QMessageBox.question(
            self, "AI 이미지 일괄수정", NL.join([
                f"폴더 : {folder}",
                f"페이지 : {a} ~ {b} ({cnt}회, 한 페이지 {PER_PAGE:,}개씩)",
                f"작업명 : {ai_image.title_for(title, a)} ~ "
                f"{ai_image.title_for(title, b)}",
                f"질의어 : {query}",
                f"예상 소요 : {mins // 60}시간 {mins % 60}분",
                "",
                "일괄작업은 1회만 실행해 주세요.",
                "실제로 로하스에 이미지 변형 작업이 생성됩니다. 진행할까요?",
            ]), QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        self.accept()
        self.on_run(folder, a, b, title, query)
