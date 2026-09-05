"""
카테고리 수정 — 이미 저장된 카테고리를 LCP 단위로 골라 고친다.

자동 저장(형제·압도적·AI)은 틀릴 때가 있다. 실제로 야전삽 LCP 는 AI 가
'기타캠핑용품'(cnt 22)을 골랐지만 정답은 '기타텐트/타프용품'(cnt 38)이었다.
그런 건을 사람이 찾아 한 번에 바로잡는 화면이다.

  1) LCP 코드나 상품명으로 찾는다
  2) 그 LCP 의 L코드가 지금 어떤 카테고리인지, 상품명·태그가 있는지 본다
  3) 후보 중 맞는 것을 골라 선택한 L코드에 한 번에 저장한다

기본 선택은 '상품정보 미완료' 인 L코드다. 완료된 것은 체크가 풀린 채로
두고, 굳이 포함하면 확인창에서 한 번 더 묻는다.

카테고리 변경과 상품명·태그 — 사이트 경고("카테고리 변경 저장시 속성 및
상품명/태그의 값이 초기화 됩니다")는 사실이다. 2026-09-05 에 L0263302 를
50001304 -> 50009402 로 바꾸고 전/후를 재보니 상품명 1건과 태그 6개가
그대로 지워졌다. 그래서 완료 건은 기본으로 체크를 빼두고, 굳이 포함하면
확인창에서 무엇이 지워지는지 알리고 한 번 더 묻는다.
"""
import webbrowser

from PySide6.QtCore import Qt, QThread, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import db
from .workers import (CategoryFixWorker, TagCopyWorker, TagPlanWorker,
                      TagSaveWorker)

OK_COLOR = "#1565c0"
TODO_COLOR = "#ef6c00"
WARN_COLOR = "#c62828"


class CategoryFixPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._lcps = []          # 검색 결과
        self._rows = []          # 선택한 LCP 의 L코드
        self._cands = []         # 후보 카테고리
        self._plan = []          # 자동 태그 제안
        self._thread = None
        self._worker = None
        self._build()

    # ------------------------------------------------------------------ UI

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)

        bar = QGroupBox("카테고리 수정 · 태그 (LCP 단위 일괄 작업)")
        top = QHBoxLayout(bar)

        self.txt_find = QLineEdit()
        self.txt_find.setPlaceholderText(
            "LCP 코드 / 상품명 / L코드 로 검색 후 Enter")
        self.txt_find.setMinimumWidth(320)
        self.txt_find.returnPressed.connect(self.search)
        top.addWidget(self.txt_find)

        btn = QPushButton("검색")
        btn.clicked.connect(self.search)
        top.addWidget(btn)

        self.chk_only_bad = QCheckBox("카테고리 갈린 LCP 만")
        self.chk_only_bad.setToolTip(
            "같은 LCP 안에서 L코드끼리 카테고리가 다른 것만 봅니다.\n"
            "자동 저장이 틀렸을 때 흔히 이 모습이 됩니다.")
        self.chk_only_bad.stateChanged.connect(self.search)
        top.addWidget(self.chk_only_bad)

        top.addStretch(1)
        self.btn_stop = QPushButton("중지")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop)
        top.addWidget(self.btn_stop)
        root.addWidget(bar)

        self.lbl_sum = QLabel("검색어를 넣고 Enter 를 누르세요.")
        self.lbl_sum.setStyleSheet(
            "QLabel { background:#eceff1; border:1px solid #cfd8dc;"
            " border-radius:6px; padding:6px 12px; }")
        root.addWidget(self.lbl_sum)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(self._left())
        split.addWidget(self._right())
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 6)
        split.setSizes([560, 940])
        root.addWidget(split, 1)

        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMaximumHeight(110)
        root.addWidget(self.txt_log)

        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.setFormat("대기 중")
        self.progress.setRange(0, 1)
        root.addWidget(self.progress)

    def _left(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(QLabel("검색 결과"))
        self.tbl_lcp = QTableWidget(0, 5)
        self.tbl_lcp.setHorizontalHeaderLabels(
            ["LCP", "상품명", "L", "카테고리", "완료"])
        self.tbl_lcp.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_lcp.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_lcp.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tbl_lcp.verticalHeader().setVisible(False)
        self.tbl_lcp.itemSelectionChanged.connect(self._pick_lcp)
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

        # --- 바꿀 L코드 ---
        row = QHBoxLayout()
        row.addWidget(QLabel("바꿀 L코드"))
        b = QPushButton("미완료만 선택")
        b.setToolTip("상품명·태그가 아직 없는 것만 고릅니다. 기본값입니다.")
        b.clicked.connect(lambda: self._check(lambda r: not r["title_saved"]))
        row.addWidget(b)
        b = QPushButton("전체 선택")
        b.clicked.connect(lambda: self._check(lambda r: True))
        row.addWidget(b)
        b = QPushButton("선택 해제")
        b.clicked.connect(lambda: self._check(lambda r: False))
        row.addWidget(b)
        self.lbl_pick = QLabel("")
        row.addWidget(self.lbl_pick, 1)
        lay.addLayout(row)

        self.tbl_row = QTableWidget(0, 5)
        self.tbl_row.setHorizontalHeaderLabels(
            ["", "L코드", "현재 카테고리", "상품명", "태그"])
        self.tbl_row.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_row.verticalHeader().setVisible(False)
        self.tbl_row.setMaximumHeight(200)
        self.tbl_row.itemChanged.connect(lambda *_: self._sync_pick())
        lay.addWidget(self.tbl_row)

        # --- 후보 카테고리 ---
        row = QHBoxLayout()
        row.addWidget(QLabel("바꿀 카테고리"))
        self.txt_cand = QLineEdit()
        self.txt_cand.setPlaceholderText("후보 이름으로 좁히기")
        self.txt_cand.textChanged.connect(self._render_cands)
        self.txt_cand.setMaximumWidth(240)
        row.addWidget(self.txt_cand)
        self.lbl_cand = QLabel("")
        row.addWidget(self.lbl_cand, 1)
        lay.addLayout(row)

        self.tbl_cand = QTableWidget(0, 5)
        self.tbl_cand.setHorizontalHeaderLabels(
            ["카테고리", "건수", "단위", "코드", "현재"])
        self.tbl_cand.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_cand.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_cand.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tbl_cand.verticalHeader().setVisible(False)
        self.tbl_cand.itemSelectionChanged.connect(self._sync_pick)
        lay.addWidget(self.tbl_cand, 1)

        row = QHBoxLayout()
        row.addWidget(QLabel("총 용량"))
        self.txt_total = QLineEdit()
        self.txt_total.setPlaceholderText("단위가 있는 후보만 (예: 500)")
        self.txt_total.setMaximumWidth(150)
        row.addWidget(self.txt_total)
        row.addStretch(1)
        self.btn_apply = QPushButton("선택한 L코드에 저장")
        self.btn_apply.setEnabled(False)
        self.btn_apply.setStyleSheet(
            "QPushButton { background:#0d47a1; color:white; font-weight:bold;"
            " padding:6px 18px; border-radius:4px; }"
            "QPushButton:disabled { background:#b0bec5; }")
        self.btn_apply.clicked.connect(self._apply)
        row.addWidget(self.btn_apply)
        lay.addLayout(row)

        # --- 태그 복사 ---
        # 한 LCP 의 L코드는 색·크기만 다른 같은 상품이라 태그가 거의 같다.
        # 사람이 하나에 달아둔 것을 나머지에 그대로 넣어준다.
        box = QGroupBox("태그 복사 — 하나에 달린 태그를 나머지 L코드에 그대로 넣는다")
        trow = QHBoxLayout(box)
        trow.addWidget(QLabel("원본"))
        self.cmb_src = QComboBox()
        self.cmb_src.setMinimumWidth(300)
        self.cmb_src.currentIndexChanged.connect(self._sync_tag)
        trow.addWidget(self.cmb_src)

        self.chk_overwrite = QCheckBox("이미 있는 것도 덮어쓰기")
        self.chk_overwrite.setToolTip(
            "체크하지 않으면 태그가 이미 있는 L코드는 건드리지 않습니다.")
        self.chk_overwrite.stateChanged.connect(self._sync_tag)
        trow.addWidget(self.chk_overwrite)

        self.lbl_tag = QLabel("")
        self.lbl_tag.setWordWrap(True)
        trow.addWidget(self.lbl_tag, 1)

        self.btn_tag = QPushButton("선택한 L코드에 태그 복사")
        self.btn_tag.setEnabled(False)
        self.btn_tag.setStyleSheet(
            "QPushButton { background:#2e7d32; color:white; font-weight:bold;"
            " padding:6px 18px; border-radius:4px; }"
            "QPushButton:disabled { background:#b0bec5; }")
        self.btn_tag.clicked.connect(self._copy_tags)
        trow.addWidget(self.btn_tag)
        lay.addWidget(box)

        # --- 자동 태그 입력 (미리보기 -> 확인 -> 저장) ---
        # 바로 저장하지 않는다. 무엇이 들어갈지 먼저 보여주고, 표에서 고친
        # 뒤에 저장하게 한다. AI 판단이 섞이므로 사람이 볼 자리가 필요하다.
        box2 = QGroupBox("자동 태그 입력 — 제안을 먼저 보고 고친 뒤 저장한다")
        v2 = QVBoxLayout(box2)
        arow = QHBoxLayout()
        self.btn_plan = QPushButton("자동 태그 입력 (미리보기)")
        self.btn_plan.setToolTip(
            "로하스 기본 태그 후보에서 규칙대로 골라 제안만 만듭니다."
            + chr(10) + "저장하지 않습니다.")
        self.btn_plan.clicked.connect(self._plan_tags)
        arow.addWidget(self.btn_plan)

        self.chk_ai = QCheckBox("AI로 안 맞는 태그 걸러내기")
        self.chk_ai.setToolTip(
            "타사 브랜드나 이 상품에 없는 기능을 AI 가 빼줍니다."
            + chr(10) + "규칙으로는 판정이 안 되는 부분입니다. LCP당 1회 호출.")
        self.chk_ai.setChecked(True)
        arow.addWidget(self.chk_ai)

        self.chk_plan_over = QCheckBox("태그 있는 것도 다시 제안")
        arow.addWidget(self.chk_plan_over)

        self.lbl_plan = QLabel("")
        self.lbl_plan.setWordWrap(True)
        arow.addWidget(self.lbl_plan, 1)

        self.btn_plan_save = QPushButton("이대로 저장")
        self.btn_plan_save.setEnabled(False)
        self.btn_plan_save.setStyleSheet(
            "QPushButton { background:#ad1457; color:white; font-weight:bold;"
            " padding:6px 18px; border-radius:4px; }"
            "QPushButton:disabled { background:#b0bec5; }")
        self.btn_plan_save.clicked.connect(self._save_plan)
        arow.addWidget(self.btn_plan_save)
        v2.addLayout(arow)

        self.tbl_plan = QTableWidget(0, 5)
        self.tbl_plan.setHorizontalHeaderLabels(
            ["L코드", "현재 태그", "넣을 태그 (수정 가능)", "출처", "링크"])
        self.tbl_plan.verticalHeader().setVisible(False)
        self.tbl_plan.setMinimumHeight(190)
        self.tbl_plan.cellDoubleClicked.connect(self._open_link)
        v2.addWidget(self.tbl_plan)
        lay.addWidget(box2, 1)
        return w

    # ------------------------------------------------------------------ 검색

    def search(self):
        kw = self.txt_find.text().strip()
        only_bad = self.chk_only_bad.isChecked()
        if not kw and not only_bad:
            QMessageBox.information(self, "안내", "검색어를 넣어주세요.")
            return
        self._lcps = db.lcp_category_search(kw, only_split=only_bad)
        self.tbl_lcp.setRowCount(len(self._lcps))
        for i, g in enumerate(self._lcps):
            cat = g["cat_name"] or (g["cats"][0] if g["cats"] else "")
            if len(g["cats"]) > 1:
                cat = f"갈림 {len(g['cats'])}종"
            vals = [g["lcp_code"], g["product_name"], str(g["n"]), cat,
                    f"{g['done']}/{g['n']}"]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                if j == 3 and len(g["cats"]) > 1:
                    it.setForeground(QColor(WARN_COLOR))
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                self.tbl_lcp.setItem(i, j, it)
            self.tbl_lcp.item(i, 0).setData(Qt.UserRole, g["lcp_code"])
        self.tbl_lcp.resizeColumnsToContents()
        self.tbl_lcp.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch)
        n_split = sum(1 for g in self._lcps if len(g["cats"]) > 1)
        self.lbl_sum.setText(
            f"검색 결과 <b>{len(self._lcps):,}</b>종 · L코드 "
            f"<b>{sum(g['n'] for g in self._lcps):,}</b>건"
            + (f" &nbsp;|&nbsp; <b style='color:{WARN_COLOR}'>카테고리 갈림 "
               f"{n_split:,}종</b>" if n_split else ""))

    def current(self):
        r = self.tbl_lcp.currentRow()
        if r < 0:
            return None
        it = self.tbl_lcp.item(r, 0)
        code = it.data(Qt.UserRole) if it else None
        return next((g for g in self._lcps if g["lcp_code"] == code), None)

    def _pick_lcp(self):
        g = self.current()
        self.tbl_row.setRowCount(0)
        self.tbl_cand.setRowCount(0)
        self._rows, self._cands = [], []
        if not g:
            self.lbl_prod.setText("")
            return
        self._rows = db.lcode_rows_of(g["lcp_code"])
        self.lbl_prod.setText(
            f"<b style='font-size:14px'>{g['product_name']}</b>"
            f"<br>{g['lcp_code']} &nbsp;·&nbsp; L코드 {g['n']}건 "
            f"(완료 {g['done']}건)"
            f"<br><span style='color:#607d8b'>현재 카테고리 : "
            f"{' / '.join(g['cats']) or '-'}</span>")
        self._render_rows()
        self._log(f"{g['lcp_code']} 후보와 태그 현황을 불러옵니다...")
        self._run(CategoryFixWorker(g["lcp_code"], []), self._on_cands)

    def _render_rows(self):
        self.tbl_row.blockSignals(True)
        self.tbl_row.setRowCount(len(self._rows))
        for i, r in enumerate(self._rows):
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            # 완료된 것은 기본으로 빼둔다 (사람이 굳이 켜면 확인창이 뜬다)
            chk.setCheckState(Qt.Unchecked if r["title_saved"] else Qt.Checked)
            self.tbl_row.setItem(i, 0, chk)
            n_tag = (len(r["tags"]) if r.get("tags") is not None
                     else (r["tag_count"] or 0))
            vals = [r["l_code"], str(r["etc_category"] or "-"),
                    "저장" if r["title_saved"] else "-", str(n_tag)]
            for j, v in enumerate(vals, 1):
                it = QTableWidgetItem(v)
                if j == 3:
                    it.setForeground(QColor(
                        OK_COLOR if r["title_saved"] else TODO_COLOR))
                if j == 4:
                    it.setForeground(QColor(OK_COLOR if n_tag else TODO_COLOR))
                self.tbl_row.setItem(i, j, it)
        self.tbl_row.blockSignals(False)
        self.tbl_row.resizeColumnsToContents()
        self._sync_pick()

    def _check(self, fn):
        self.tbl_row.blockSignals(True)
        for i, r in enumerate(self._rows):
            self.tbl_row.item(i, 0).setCheckState(
                Qt.Checked if fn(r) else Qt.Unchecked)
        self.tbl_row.blockSignals(False)
        self._sync_pick()

    def picked_rows(self):
        out = []
        for i, r in enumerate(self._rows):
            it = self.tbl_row.item(i, 0)
            if it and it.checkState() == Qt.Checked:
                out.append(r)
        return out

    def picked_cand(self):
        r = self.tbl_cand.currentRow()
        if r < 0:
            return None
        it = self.tbl_cand.item(r, 3)
        code = it.text() if it else ""
        return next((c for c in self._visible_cands()
                     if str(c.get("code")) == code), None)

    def _sync_pick(self):
        rows = self.picked_rows()
        done = sum(1 for r in rows if r["title_saved"])
        self.lbl_pick.setText(
            f"선택 <b>{len(rows)}</b>건"
            + (f" &nbsp;<span style='color:{WARN_COLOR}'>(상품명·태그 있는 것 "
               f"{done}건 포함)</span>" if done else ""))
        self.btn_apply.setEnabled(
            bool(rows) and self.picked_cand() is not None
            and self._thread is None)
        if hasattr(self, "cmb_src"):
            self._sync_tag()

    # ------------------------------------------------------------------ 후보

    def _on_cands(self, res):
        self._cands = res.get("candidates") or []
        self._log(f"후보 {len(self._cands)}개")
        self._render_cands()
        QTimer.singleShot(0, self._load_tags)

    def _load_tags(self):
        g = self.current()
        if g and self._thread is None:
            self._run(TagCopyWorker(g["lcp_code"], []), self._on_tags)

    # ------------------------------------------------------------ 태그 복사

    def _on_tags(self, res):
        """L코드별 태그 현황. 태그가 있는 것만 원본 후보가 된다."""
        rows = res.get("rows") or []
        by = {r["l_code"]: r.get("tags") or [] for r in rows}
        for r in self._rows:
            r["tags"] = by.get(r["l_code"], [])
        self._render_rows()

        self.cmb_src.blockSignals(True)
        self.cmb_src.clear()
        have = [r for r in self._rows if r.get("tags")]
        for r in have:
            self.cmb_src.addItem(
                f"{r['l_code']}  ({len(r['tags'])}개) "
                f"{', '.join(t['text'] for t in r['tags'][:3])}...",
                r["product_no"])
        self.cmb_src.blockSignals(False)
        n_empty = sum(1 for r in self._rows if not r.get("tags"))
        self._log(f"태그 있음 {len(have)}건 / 없음 {n_empty}건")
        self._sync_tag()

    def src_tags(self):
        i = self.cmb_src.currentIndex()
        if i < 0:
            return None, []
        no = str(self.cmb_src.currentData())
        r = next((x for x in self._rows if str(x["product_no"]) == no), None)
        return no, (r.get("tags") if r else []) or []

    def _sync_tag(self):
        no, tags = self.src_tags()
        rows = [r for r in self.picked_rows() if str(r["product_no"]) != no]
        if not self.chk_overwrite.isChecked():
            rows = [r for r in rows if not r.get("tags")]
        self.lbl_tag.setText(
            (f"<span style='color:#2e7d32'>{', '.join(t['text'] for t in tags)}"
             f"</span><br>대상 <b>{len(rows)}</b>건" if tags
             else "태그가 달린 L코드가 없습니다. 먼저 하나를 사람이 달아야 합니다."))
        self.btn_tag.setEnabled(bool(tags) and bool(rows)
                                and self._thread is None)

    # -------------------------------------------------- 자동 태그 (미리보기)

    def _plan_tags(self):
        """제안만 만든다. 저장은 사람이 확인한 뒤 따로 누른다."""
        if self._thread:
            return
        g = self.current()
        if not g:
            QMessageBox.information(self, "안내", "LCP 를 먼저 고르세요.")
            return
        rows = db.lcode_rows_of(g["lcp_code"])
        rows = [r for r in rows if r.get("etc_category")]
        if not rows:
            QMessageBox.warning(
                self, "안내",
                "카테고리가 저장돼야 태그 후보 표가 만들어집니다.")
            return
        for r in rows:
            r["lcp_code"] = g["lcp_code"]
        self._log(f"{g['lcp_code']} 태그 제안을 만듭니다"
                  + (" (AI 검수 포함)" if self.chk_ai.isChecked() else ""))
        self._run(TagPlanWorker(rows,
                                overwrite=self.chk_plan_over.isChecked(),
                                use_ai=self.chk_ai.isChecked()),
                  self._on_plan)

    def _on_plan(self, res):
        self._plan = res.get("rows") or []
        self.tbl_plan.blockSignals(True)
        self.tbl_plan.setRowCount(len(self._plan))
        for i, r in enumerate(self._plan):
            cur = QTableWidgetItem(", ".join(r["current"]))
            cur.setFlags(Qt.ItemIsEnabled)
            cur.setForeground(QColor(OK_COLOR if r["current"] else TODO_COLOR))
            prop = QTableWidgetItem(", ".join(r["proposed"]))
            if not r["proposed"]:
                prop.setForeground(QColor("#90a4ae"))
            lc = QTableWidgetItem(r["l_code"])
            lc.setFlags(Qt.ItemIsEnabled)
            src = QTableWidgetItem(r["source"] or "-")
            src.setFlags(Qt.ItemIsEnabled)
            link = QTableWidgetItem("태그 | 상품명 | OF")
            link.setFlags(Qt.ItemIsEnabled)
            link.setForeground(QColor("#1565c0"))
            link.setData(Qt.UserRole, r["product_no"])
            for j, it in enumerate((lc, cur, prop, src, link)):
                self.tbl_plan.setItem(i, j, it)
        self.tbl_plan.blockSignals(False)
        self.tbl_plan.resizeColumnsToContents()
        self.tbl_plan.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.Stretch)

        n = sum(1 for r in self._plan if r["proposed"])
        msg = (f"제안 <b>{n}</b>건 · 후보 {res.get('pool', 0)}개 · "
               f"{res.get('source') or '-'} · {res.get('mode') or '-'}")
        if res.get("dropped_ai"):
            msg += (f" &nbsp;|&nbsp; <span style='color:{WARN_COLOR}'>AI 제외 "
                    f"{len(res['dropped_ai'])}개</span>: "
                    + ", ".join(res["dropped_ai"][:8]))
        if res.get("dropped_brand"):
            msg += (f" &nbsp;|&nbsp; 브랜드 제외 "
                    + ", ".join(res["dropped_brand"][:4]))
        self.lbl_plan.setText(msg)
        self.btn_plan_save.setEnabled(n > 0 and self._thread is None)

    def _save_plan(self):
        g = self.current()
        if not g or self._thread:
            return
        # 표에서 사람이 고친 값을 그대로 쓴다.
        plan = []
        for i, r in enumerate(self._plan):
            it = self.tbl_plan.item(i, 2)
            txt = (it.text() if it else "").strip()
            names = [x.strip() for x in txt.split(",") if x.strip()]
            if names:
                plan.append({**r, "proposed": names[:10]})
        if not plan:
            QMessageBox.information(self, "안내", "저장할 것이 없습니다.")
            return
        n_over = sum(1 for r in plan if r["current"])
        msg = [f"{g['lcp_code']} 의 L코드 {len(plan)}건에 태그를 저장합니다.", ""]
        if n_over:
            msg += [f"[주의] {n_over}건은 이미 태그가 있어 덮어씁니다.", ""]
        msg += ["진행할까요?"]
        if QMessageBox.question(self, "자동 태그 입력",
                                chr(10).join(msg)) != QMessageBox.Yes:
            return
        self._log(f"태그 저장 {len(plan)}건")
        self._run(TagSaveWorker(plan, g["lcp_code"], db.get_job_folder()),
                  self._on_plan_saved)

    def _on_plan_saved(self, res):
        self._log(f"태그 저장 {res.get('ok', 0)}건"
                  + (f" / 건너뜀 {res['skip']}건" if res.get("skip") else "")
                  + (f" / 실패 {res['fail']}건" if res.get("fail") else ""))
        QTimer.singleShot(0, self._plan_tags)

    def _open_link(self, row, col):
        """링크 칸을 더블클릭하면 사이트 화면을 브라우저로 연다."""
        if col != 4:
            return
        from ..lohas import tabs
        it = self.tbl_plan.item(row, 4)
        no = it.data(Qt.UserRole) if it else None
        if not no:
            return
        u = tabs.urls(no)
        webbrowser.open(u["tag"])
        self._log(f"브라우저로 열기 — {u['tag']}")

    def _copy_tags(self):
        g = self.current()
        no, tags = self.src_tags()
        if not g or not tags:
            return
        rows = [r for r in self.picked_rows() if str(r["product_no"]) != no]
        if not self.chk_overwrite.isChecked():
            rows = [r for r in rows if not r.get("tags")]
        if not rows:
            QMessageBox.information(self, "안내", "복사할 대상이 없습니다.")
            return
        over = [r for r in rows if r.get("tags")]
        nl = chr(10)
        msg = (f"{g['lcp_code']} 의 L코드 {len(rows)}건에 태그 "
               f"{len(tags)}개를 넣습니다." + nl + nl
               + "  " + ", ".join(t["text"] for t in tags) + nl + nl)
        if over:
            msg += (f"[주의] {len(over)}건은 이미 태그가 있어 덮어씁니다."
                    + nl + nl)
        msg += "진행할까요?"
        if QMessageBox.question(self, "태그 복사", msg) != QMessageBox.Yes:
            return
        self._log(f"태그 복사 시작 — {len(rows)}건")
        self._run(TagCopyWorker(
            g["lcp_code"], rows, src_no=no, tags=tags,
            overwrite=self.chk_overwrite.isChecked(),
            folder_name=db.get_job_folder()), self._on_copied)

    def _on_copied(self, res):
        self._log(f"태그 복사 {res.get('ok', 0)}건"
                  + (f" / 건너뜀 {res['skip']}건" if res.get("skip") else "")
                  + (f" / 실패 {res['fail']}건" if res.get("fail") else ""))
        QTimer.singleShot(0, self._load_tags)

    def _visible_cands(self):
        kw = self.txt_cand.text().strip().lower()
        return [c for c in self._cands
                if not kw or kw in (c.get("name") or "").lower()]

    def _render_cands(self):
        g = self.current()
        now = set(g["cats_code"]) if g else set()
        cands = self._visible_cands()
        self.tbl_cand.setRowCount(len(cands))
        for i, c in enumerate(cands):
            code = str(c.get("code") or "")
            vals = [c.get("name") or "", f"{int(str(c.get('cnt') or 0)):,}",
                    c.get("unit") or "", code, "현재" if code in now else ""]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                if code in now:
                    it.setForeground(QColor(OK_COLOR))
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                self.tbl_cand.setItem(i, j, it)
        self.tbl_cand.resizeColumnsToContents()
        self.tbl_cand.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch)
        self.lbl_cand.setText(f"후보 {len(cands):,}개 (건수 내림차순)")
        self._sync_pick()

    # ------------------------------------------------------------------ 저장

    def _apply(self):
        g = self.current()
        rows = self.picked_rows()
        c = self.picked_cand()
        if not g or not rows or not c:
            return
        code = str(c.get("code") or "")
        unit = c.get("unit") or ""
        total = self.txt_total.text().strip()
        if unit and not total:
            if QMessageBox.question(
                    self, "총 용량",
                    f"이 카테고리는 단위({unit})가 있습니다.\n"
                    "총 용량을 비운 채로 저장할까요?\n"
                    "(비워도 카테고리는 정상 저장됩니다)"
            ) != QMessageBox.Yes:
                return

        done = [r for r in rows if r["title_saved"]]
        msg = (f"{g['lcp_code']} 의 L코드 {len(rows)}건을\n\n"
               f"  {c.get('name')}\n  ({code})\n\n로 바꿉니다.\n\n")
        if done:
            n_tag = sum(int(r.get("tag_count") or 0) for r in done)
            msg += (f"[경고] 이 중 {len(done)}건은 상품명·태그가 저장돼 "
                    "있습니다.\n"
                    f"카테고리를 바꾸면 상품명 {len(done)}건과 태그 "
                    f"{n_tag}개가 지워집니다.\n"
                    "(2026-09-05 L0263302 로 실측 확인)\n\n")
        msg += "진행할까요?"
        if QMessageBox.question(self, "카테고리 수정", msg) != QMessageBox.Yes:
            return

        self._log(f"{g['lcp_code']} {len(rows)}건 -> {code} 저장 시작")
        self._run(CategoryFixWorker(
            g["lcp_code"], rows, code=code, capacity=c.get("capacity") or "",
            unit=unit, total_capacity=total,
            folder_name=db.get_job_folder()), self._on_saved)

    def _on_saved(self, res):
        self._log(f"저장 {res.get('ok', 0)}건"
                  + (f" / 실패 {res['fail']}건" if res.get("fail") else "")
                  + f" (확인 {res.get('verified', 0)}건)")
        self.search()

    # ------------------------------------------------------------------ 실행

    def _run(self, worker, on_done):
        if self._thread:
            return
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
        self.btn_apply.setEnabled(False)
        self.btn_tag.setEnabled(False)
        self.btn_plan.setEnabled(False)
        self.btn_plan_save.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self._thread.start()

    def _done(self):
        if self._thread:
            self._thread.quit()
            self._thread.wait(3000)
        self._thread = None
        self._worker = None
        self.btn_stop.setEnabled(False)
        self.btn_plan.setEnabled(True)
        self.btn_plan_save.setEnabled(bool(
            [r for r in self._plan if r.get("proposed")]))
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.progress.setFormat("완료")
        self._sync_pick()

    def _stop(self):
        if self._worker:
            self._worker.stop()

    def _on_progress(self, i, n):
        self.progress.setRange(0, max(1, n))
        self.progress.setValue(i)
        self.progress.setFormat(f"{i}/{n}")

    def _log(self, m):
        self.txt_log.appendPlainText(str(m))
