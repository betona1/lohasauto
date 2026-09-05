"""
카테고리 검토 화면.

자동으로 넣을 수 있는 건은 이미 넣었다(형제·압도적). 여기 남는 건 기계가
정하면 안 되는 것들이다.

    접전    후보 1·2위 건수가 비슷해 규칙 정확도가 74% 밖에 안 되는 건
    용량    카테고리에 용량·단위가 딸려 있어 값을 사람이 넣어야 하는 건
    갈림    같은 LCP 의 다른 L코드들이 이미 서로 다른 카테고리를 쓰는 건

왼쪽에서 LCP 를 고르면 오른쪽에 판단 근거를 모아 보여준다. 상품명·희망검색어·
타 마켓이 이 상품을 어디에 넣었는지까지 한 화면에 있어야 사람이 고를 수 있다.
"""
import json

from PySide6.QtCore import Qt, QThread, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

from .. import db
from .workers import (AiCategoryWorker, CategoryPlanWorker,
                      CategorySaveWorker)

TIER_COLOR = {
    "접전": "#ef6c00",
    "용량": "#6a1b9a",
    "갈림": "#c62828",
    "형제": "#2e7d32",
    "압도적": "#1565c0",
}


class CategoryReviewPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._plan = []
        self._thread = None
        self._worker = None
        self._pending_lcp = None
        self._all_jobs = None      # ALL 저장 대기열 (AI 판단이 끝나면 이어서 실행)
        self._build()

    # ------------------------------------------------------------------ UI

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)

        bar = QGroupBox("카테고리 검토")
        top = QHBoxLayout(bar)
        top.addWidget(QLabel("등급"))
        self.cmb_tier = QComboBox()
        self.cmb_tier.addItem("남은 전부", ())
        self.cmb_tier.addItem("접전만", ("접전",))
        self.cmb_tier.addItem("용량 입력", ("용량",))
        self.cmb_tier.addItem("형제 갈림", ("갈림",))
        top.addWidget(self.cmb_tier)

        self.btn_load = QPushButton("후보 불러오기")
        self.btn_load.clicked.connect(self.load)
        top.addWidget(self.btn_load)

        self.chk_hide_done = QCheckBox("처리한 건 숨기기")
        self.chk_hide_done.setChecked(True)
        self.chk_hide_done.stateChanged.connect(self._render_list)
        top.addWidget(self.chk_hide_done)

        self.btn_ai = QPushButton("AI가 고르기 (선택 건)")
        self.btn_ai.setToolTip(
            "선택한 LCP 의 카테고리를 AI 가 고릅니다. 저장은 하지 않고 추천만 바꿉니다.")
        self.btn_ai.clicked.connect(lambda: self._ai(False))
        top.addWidget(self.btn_ai)

        self.btn_ai_all = QPushButton("AI 일괄 판단")
        self.btn_ai_all.setToolTip(
            "목록 전체를 AI 가 판단합니다. 규칙과 다른 건에는 ★ 가 붙습니다."
            " 저장은 사람이 확인한 뒤 따로 누릅니다.")
        self.btn_ai_all.clicked.connect(lambda: self._ai(True))
        top.addWidget(self.btn_ai_all)

        self.btn_all = QPushButton("ALL 카테고리")
        self.btn_all.setToolTip(
            "목록에 남은 미저장 LCP 전부에 카테고리를 저장합니다.\n"
            "접전 건은 AI 판단을 먼저 돌린 뒤 그 결과로 저장합니다.")
        self.btn_all.setStyleSheet(
            "QPushButton { background:#b71c1c; color:white; font-weight:bold;"
            " padding:4px 14px; border-radius:4px; }"
            "QPushButton:disabled { background:#b0bec5; }")
        self.btn_all.clicked.connect(self._save_all)
        top.addWidget(self.btn_all)

        top.addStretch(1)
        self.btn_stop = QPushButton("중지")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop)
        top.addWidget(self.btn_stop)
        root.addWidget(bar)

        self.lbl_sum = QLabel("'후보 불러오기' 를 누르면 남은 건을 모아옵니다.")
        self.lbl_sum.setStyleSheet(
            "QLabel { background:#eceff1; border:1px solid #cfd8dc;"
            " border-radius:6px; padding:6px 12px; }")
        root.addWidget(self.lbl_sum)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(self._left())
        split.addWidget(self._right())
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 6)
        split.setSizes([620, 900])
        root.addWidget(split, 1)

        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.setFormat("대기 중")
        self.progress.setRange(0, 1)
        root.addWidget(self.progress)

    def _left(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)

        self.txt_find = QLineEdit()
        self.txt_find.setPlaceholderText("상품명 / LCP 코드 검색")
        self.txt_find.textChanged.connect(self._render_list)
        lay.addWidget(self.txt_find)

        self.tbl_lcp = QTableWidget(0, 6)
        self.tbl_lcp.setHorizontalHeaderLabels(
            ["LCP", "상품명", "L", "1위:2위", "등급", "AI"])
        self.tbl_lcp.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_lcp.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_lcp.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tbl_lcp.verticalHeader().setVisible(False)
        self.tbl_lcp.itemSelectionChanged.connect(self._render_detail)
        lay.addWidget(self.tbl_lcp, 1)
        return w

    def _right(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)

        self.lbl_prod = QLabel("")
        self.lbl_prod.setWordWrap(True)
        self.lbl_prod.setStyleSheet(
            "QLabel { background:#ffffff; border:1px solid #cfd8dc;"
            " border-radius:6px; padding:8px 12px; }")
        lay.addWidget(self.lbl_prod)

        self.txt_hint = QPlainTextEdit()
        self.txt_hint.setReadOnly(True)
        self.txt_hint.setMaximumHeight(96)
        self.txt_hint.setStyleSheet("QPlainTextEdit { background:#fafafa; }")
        lay.addWidget(self.txt_hint)

        lay.addWidget(QLabel("<b>카테고리 후보</b> — 줄을 고르고 저장하세요."))
        self.tbl_cand = QTableWidget(0, 5)
        self.tbl_cand.setHorizontalHeaderLabels(
            ["카테고리", "건수", "비중", "단위", "코드"])
        self.tbl_cand.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_cand.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_cand.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tbl_cand.verticalHeader().setVisible(False)
        self.tbl_cand.itemSelectionChanged.connect(self._on_cand)
        lay.addWidget(self.tbl_cand, 1)

        row = QHBoxLayout()
        self.lbl_unit = QLabel("총 용량")
        self.txt_total = QLineEdit()
        self.txt_total.setPlaceholderText("예: 500")
        self.txt_total.setMaximumWidth(120)
        self.lbl_unit2 = QLabel("")
        for x in (self.lbl_unit, self.txt_total, self.lbl_unit2):
            row.addWidget(x)
            x.setVisible(False)
        row.addStretch(1)

        self.btn_save = QPushButton("이 카테고리로 저장")
        self.btn_save.setEnabled(False)
        self.btn_save.setStyleSheet(
            "QPushButton { background:#0d47a1; color:white; font-weight:bold;"
            " padding:7px 18px; border-radius:4px; }"
            "QPushButton:disabled { background:#b0bec5; }")
        self.btn_save.clicked.connect(self._save)
        row.addWidget(self.btn_save)
        lay.addLayout(row)

        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMaximumHeight(120)
        lay.addWidget(self.txt_log)
        return w

    # ------------------------------------------------------------------ 목록

    def refresh_summary(self):
        """화면에 들어올 때 DB 만으로 남은 양을 보여준다(네트워크 없음)."""
        if self._plan:
            return
        with db.sqlite_conn() as c:
            r = c.execute(
                "select count(*) n, count(distinct lcp_code) k "
                "from lcode_attr where next_step='카테고리'").fetchone()
        self.lbl_sum.setText(
            f"카테고리 미저장 <b>{r['k']:,}</b>종 / L코드 <b>{r['n']:,}</b>건"
            " &nbsp;|&nbsp; '후보 불러오기' 를 누르면 후보를 모아옵니다.")

    def load(self):
        if self._thread:
            return
        tiers = self.cmb_tier.currentData() or ()
        self._log(f"후보 조회 시작 ({self.cmb_tier.currentText()})")
        self._run(CategoryPlanWorker(db.get_job_folder(), tiers), self._on_plan)

    def _on_plan(self, res):
        self._plan = res.get("rows", [])
        for p in self._plan:
            p["product_name"] = _product_info(p["lcp_code"]).get(
                "product_name") or ""
        self._render_list()

    def _visible(self):
        kw = self.txt_find.text().strip().lower()
        out = []
        for p in self._plan:
            if self.chk_hide_done.isChecked() and p.get("done"):
                continue
            if kw and kw not in p["lcp_code"].lower() \
                    and kw not in (p.get("product_name") or "").lower():
                continue
            out.append(p)
        return out

    def _render_list(self):
        rows = self._visible()
        self.tbl_lcp.setRowCount(len(rows))
        for i, p in enumerate(rows):
            ai = p.get("ai") or {}
            ai_txt = ""
            if ai:
                ai_txt = "같음" if ai.get("code") == p.get("code") else "★ 다름"
            elif p.get("ai_tried"):
                ai_txt = "실패"
            vals = [p["lcp_code"], p.get("product_name") or "",
                    str(len(p["rows"])),
                    f"{p.get('cnt', 0)}:{p.get('cnt2', 0)}",
                    p.get("tier", ""), ai_txt]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                if j == 5 and v.startswith("★"):
                    it.setForeground(QColor("#c62828"))
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                elif j == 5 and v == "같음":
                    it.setForeground(QColor("#2e7d32"))
                if j == 4:
                    it.setForeground(QColor(TIER_COLOR.get(v, "#455a64")))
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                if p.get("done"):
                    it.setForeground(QColor("#9e9e9e"))
                self.tbl_lcp.setItem(i, j, it)
            self.tbl_lcp.item(i, 0).setData(Qt.UserRole, p["lcp_code"])
        self.tbl_lcp.resizeColumnsToContents()
        self.tbl_lcp.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch)

        left = sum(len(p["rows"]) for p in rows if not p.get("done"))
        by = {}
        for p in rows:
            by[p.get("tier", "?")] = by.get(p.get("tier", "?"), 0) + 1
        detail = " · ".join(f"{k} {v}종" for k, v in
                            sorted(by.items(), key=lambda x: -x[1]))
        self.lbl_sum.setText(
            f"검토 대상 <b>{len(rows):,}</b>종 / L코드 <b>{left:,}</b>건"
            + (f" &nbsp;|&nbsp; {detail}" if detail else ""))

    def current(self):
        r = self.tbl_lcp.currentRow()
        if r < 0:
            return None
        it = self.tbl_lcp.item(r, 0)
        code = it.data(Qt.UserRole) if it else None
        return next((p for p in self._plan if p["lcp_code"] == code), None)

    # ------------------------------------------------------------------ 상세

    def _render_detail(self):
        p = self.current()
        self.tbl_cand.setRowCount(0)
        self.btn_save.setEnabled(False)
        for x in (self.lbl_unit, self.txt_total, self.lbl_unit2):
            x.setVisible(False)
        if not p:
            self.lbl_prod.setText("")
            self.txt_hint.setPlainText("")
            return

        info = _product_info(p["lcp_code"])
        lcodes = ", ".join(r["l_code"] for r in p["rows"][:8])
        if len(p["rows"]) > 8:
            lcodes += f" 외 {len(p['rows']) - 8}건"
        color = TIER_COLOR.get(p.get("tier"), "#455a64")
        self.lbl_prod.setText(
            f"<b style='font-size:14px'>{info.get('product_name') or ''}</b>"
            f"<br>{p['lcp_code']} &nbsp;·&nbsp; L코드 {len(p['rows'])}건"
            f" &nbsp;·&nbsp; <span style='color:{color}'>"
            f"<b>{p.get('tier', '')}</b> — {p.get('note', '')}</span>"
            f"<br><span style='color:#607d8b'>{lcodes}</span>")

        hint = []
        if info.get("wish_keywords"):
            hint.append(f"희망검색어 : {info['wish_keywords']}")
        if info.get("markets"):
            hint.append(f"타 마켓 분류 : {info['markets']}")
        if p.get("sibling"):
            sib = ", ".join(f"{k}({v}건)" for k, v in p["sibling"].items())
            hint.append(f"형제 L코드가 쓰는 카테고리 : {sib}")
        self.txt_hint.setPlainText("\n".join(hint) or "참고 정보 없음")

        cands = p.get("candidates") or []
        tot = sum(_num(c.get("cnt")) for c in cands) or 1
        self.tbl_cand.setRowCount(len(cands))
        rec = p.get("code")
        sib_codes = set((p.get("sibling") or {}).keys())
        for i, c in enumerate(cands):
            cnt = _num(c.get("cnt"))
            code = str(c.get("code") or "")
            ai_code = (p.get("ai") or {}).get("code")
            mark = ""
            if code == rec and code == ai_code:
                mark = "  <- 추천 + AI"
            elif code == rec:
                mark = "  <- 추천"
            elif code == ai_code:
                mark = "  <- ★ AI 선택"
            elif code in sib_codes:
                mark = "  <- 형제 사용"
            vals = [(c.get("name") or "") + mark, f"{cnt:,}",
                    f"{cnt / tot * 100:.0f}%", c.get("unit") or "", code]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                if code == ai_code and code != rec:
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                    it.setForeground(QColor("#c62828"))
                elif code == rec:
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                    it.setForeground(QColor("#0d47a1"))
                elif code in sib_codes:
                    it.setForeground(QColor("#2e7d32"))
                self.tbl_cand.setItem(i, j, it)
        self.tbl_cand.resizeColumnsToContents()
        self.tbl_cand.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch)
        # AI 가 골랐으면 그 줄을, 아니면 추천 줄을 미리 선택한다.
        want = (p.get("ai") or {}).get("code") or rec
        for i, c in enumerate(cands):
            if str(c.get("code")) == want:
                self.tbl_cand.selectRow(i)
                break

    def _on_cand(self):
        p = self.current()
        r = self.tbl_cand.currentRow()
        if not p or r < 0:
            self.btn_save.setEnabled(False)
            return
        c = (p.get("candidates") or [])[r]
        unit = c.get("unit") or ""
        for x in (self.lbl_unit, self.txt_total, self.lbl_unit2):
            x.setVisible(bool(unit))
        if unit:
            self.lbl_unit2.setText(
                f"{unit}   (기준 {c.get('capacity') or '-'}{unit})")
        self.btn_save.setEnabled(not p.get("done") and self._thread is None)

    # ------------------------------------------------------------------ 저장

    def _save(self):
        p = self.current()
        r = self.tbl_cand.currentRow()
        if not p or r < 0:
            return
        c = (p.get("candidates") or [])[r]
        code = str(c.get("code") or "")
        unit = c.get("unit") or ""
        total = self.txt_total.text().strip()
        if unit and not total:
            QMessageBox.warning(self, "총 용량", "총 용량을 입력해주세요.")
            return

        n = len(p["rows"])
        if QMessageBox.question(
                self, "카테고리 저장",
                f"{p['lcp_code']} 의 L코드 {n}건에\n\n"
                f"  {c.get('name')}\n  ({code})\n\n을 저장합니다. 진행할까요?"
        ) != QMessageBox.Yes:
            return

        job = {"item": p, "code": code, "capacity": c.get("capacity") or "",
               "unit": unit, "total_capacity": total}
        self._pending_lcp = p["lcp_code"]
        self._run(CategorySaveWorker([job], db.get_job_folder()),
                  self._on_saved)

    # ------------------------------------------------------------ ALL 저장

    def _save_all(self):
        """
        남은 미저장 LCP 전부에 카테고리를 저장한다.

        등급마다 근거의 세기가 다르다는 점은 그대로다. 접전은 규칙만 쓰면
        실측 74.1% 라, 저장 전에 AI 판단을 한 번 돌리고 그 결과를 쓴다.
        """
        if self._thread:
            return
        items = [p for p in self._visible()
                 if p.get("candidates") and not p.get("done")]
        if not items:
            QMessageBox.information(self, "안내", "저장할 대상이 없습니다.")
            return

        from ..lohas import category_plan as cp, gemini
        by_tier, n_rows = {}, 0
        for p in items:
            by_tier[p.get("tier")] = by_tier.get(p.get("tier"), 0) + 1
            n_rows += len(p.get("rows") or [])
        close = [p for p in items
                 if p.get("tier") == cp.TIER_CLOSE and not (p.get("ai") or {})]
        use_ai = bool(close) and gemini.available()

        detail = chr(10).join(
            f"    {t or '?'} {n}종" for t, n in sorted(by_tier.items()))
        msg = (f"{len(items):,}종 / L코드 {n_rows:,}건에 카테고리를 저장합니다."
               + chr(10) + chr(10) + detail + chr(10) + chr(10))
        if cp.TIER_CLOSE in by_tier:
            msg += ("접전 등급은 규칙 정확도가 실측 74.1% 입니다."
                    + chr(10)
                    + ("AI 판단을 먼저 돌린 뒤 저장합니다." if use_ai else
                       "AI 를 쓸 수 없어 규칙값으로 저장합니다.")
                    + chr(10) + chr(10))
        if cp.TIER_UNIT in by_tier:
            msg += ("용량 등급은 총 용량을 비운 채 저장합니다 "
                    "(카테고리는 정상 저장됩니다)." + chr(10) + chr(10))
        msg += "진행할까요?"
        if QMessageBox.question(self, "ALL 카테고리", msg) != QMessageBox.Yes:
            return

        if use_ai:
            self._log(f"ALL — 접전 {len(close):,}종을 AI 로 먼저 판단합니다.")
            self._all_jobs = True          # AI 가 끝나면 _on_ai 가 저장을 잇는다
            self._run(AiCategoryWorker(close), self._on_ai)
        else:
            self._run_all_save()

    def _run_all_save(self):
        """등급별로 코드를 정해 저장 작업을 만든다."""
        from ..lohas import category_plan as cp

        jobs, skipped = [], 0
        for p in self._visible():
            if p.get("done") or not p.get("candidates"):
                continue
            ch = cp.auto_choice(p)
            if not ch.get("code"):
                skipped += 1
                continue
            jobs.append({"item": p, **ch})
        if not jobs:
            QMessageBox.information(self, "안내", "저장할 대상이 없습니다.")
            return

        src = {}
        for j in jobs:
            src[j["source"]] = src.get(j["source"], 0) + 1
        self._log("ALL 저장 시작 — " + " / ".join(
            f"{k} {v}종" for k, v in sorted(src.items()))
            + (f" / 후보없음 {skipped}종 제외" if skipped else ""))
        self._all_codes = {j["item"]["lcp_code"]: j["code"] for j in jobs}
        self._run(CategorySaveWorker(jobs, db.get_job_folder()),
                  self._on_saved_all)

    def _on_saved_all(self, res):
        for p in self._plan:
            if p["lcp_code"] in (getattr(self, "_all_codes", None) or {}):
                p["done"] = True
        self._log(f"ALL 저장 완료 — {res.get('ok', 0):,}건"
                  f" (확인 {res.get('verified', 0):,}건)"
                  + (f" / 실패 {res['fail']:,}건" if res.get("fail") else ""))
        self._render_list()
        self.refresh_summary()

    def _ai(self, all_rows: bool):
        if self._thread:
            return
        if all_rows:
            items = [p for p in self._visible() if p.get("candidates")]
            if not items:
                QMessageBox.information(self, "안내", "판단할 대상이 없습니다.")
                return
            if QMessageBox.question(
                    self, "AI 일괄 판단",
                    f"{len(items):,}종을 AI 가 판단합니다." + chr(10)
                    + "저장은 하지 않고 추천만 바꿉니다. 진행할까요?"
            ) != QMessageBox.Yes:
                return
        else:
            p = self.current()
            if not p or not p.get("candidates"):
                return
            items = [p]
        self._log(f"AI 판단 시작 — {len(items):,}종")
        self._run(AiCategoryWorker(items), self._on_ai)

    def _on_ai(self, res):
        by = {r["lcp_code"]: r for r in res.get("rows", [])}
        for p in self._plan:
            r = by.get(p["lcp_code"])
            if not r:
                continue
            p["ai_tried"] = True
            if r["ai"]:
                p["ai"] = r["ai"]
        self._log(f"AI 판단 {res.get('ok', 0):,}건 / "
                  f"규칙과 다른 것 {res.get('diff', 0):,}건")
        self._render_list()
        self._render_detail()
        if self._all_jobs is not None:
            # ALL 흐름 : AI 판단이 끝났으니 그 결과로 저장까지 이어서 한다.
            self._all_jobs = None
            QTimer.singleShot(0, self._run_all_save)

    def _on_saved(self, res):
        if res.get("ok"):
            for p in self._plan:
                if p["lcp_code"] == self._pending_lcp:
                    p["done"] = True
            self._log(f"저장 {res['ok']}건 (확인 {res.get('verified', 0)}건)")
        if res.get("fail"):
            self._log(f"실패 {res['fail']}건")
        self._render_list()

    # ------------------------------------------------------------------ 실행

    def _run(self, worker, on_done):
        self._thread = QThread(self)
        self._worker = worker
        worker.moveToThread(self._thread)
        self._thread.started.connect(worker.run)
        worker.log.connect(self._log)
        worker.progress.connect(self._on_progress)
        worker.failed.connect(lambda m: self._log(f"!! {m}"))
        worker.finished.connect(on_done)
        worker.finished.connect(lambda *_: self._done())
        worker.failed.connect(lambda *_: self._done())
        self.btn_load.setEnabled(False)
        self.btn_save.setEnabled(False)
        self.btn_ai.setEnabled(False)
        self.btn_ai_all.setEnabled(False)
        self.btn_all.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self._thread.start()

    def _done(self):
        if self._thread:
            self._thread.quit()
            self._thread.wait(3000)
        self._thread = None
        self._worker = None
        self.btn_load.setEnabled(True)
        self.btn_ai.setEnabled(True)
        self.btn_ai_all.setEnabled(True)
        self.btn_all.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.progress.setFormat("완료")
        self._on_cand()

    def _stop(self):
        if self._worker:
            self._worker.stop()
            self._log("중지 요청...")

    def _on_progress(self, done, total):
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(done)
            self.progress.setFormat(f"{done:,}/{total:,}")

    def _log(self, msg):
        self.txt_log.appendPlainText(str(msg))


def _num(v) -> int:
    try:
        return int(str(v or 0) or 0)
    except ValueError:
        return 0


def _product_info(lcp_code: str) -> dict:
    """상품명·희망검색어·타 마켓 분류. 사람이 카테고리를 고를 때 쓰는 근거다."""
    with db.sqlite_conn() as c:
        r = c.execute(
            "select product_name, wish_keywords, markets from lcp_product "
            "where lcp_code=?", (lcp_code,)).fetchone()
    if not r:
        return {}
    out = dict(r)
    try:
        mk = json.loads(out.get("markets") or "{}")
        out["markets"] = "  |  ".join(f"{k}: {v}" for k, v in mk.items() if v)
    except Exception:
        out["markets"] = out.get("markets") or ""
    return out
