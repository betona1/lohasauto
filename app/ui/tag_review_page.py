"""
태그 검수 — 자동으로 넣은 태그를 사람이 훑어보는 화면.

자동 투입은 규칙과 AI 판단이 섞여 있어 사람이 눈으로 봐야 한다. 그런데
사이트에서 태그만 다시 읽으면 사람이 넣은 것과 구분이 안 된다. 그래서
저장할 때 `task_log` 에 남기고(`tag_auto.log_tag_work`) 여기서 그걸 읽는다.

  왼쪽   LCP 목록 (상품명 / L코드 수 / 태그 수)
  오른쪽 그 LCP 의 L코드별 태그

행을 더블클릭하면 그 상품의 OF(attr) 팝업을 브라우저로 연다. 상품번호는
이미 갖고 있으므로 주소를 만들기만 하면 된다 (`tabs.urls`).
"""
import webbrowser

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from .. import db
from ..lohas import tabs

OK_COLOR = "#1565c0"
TODO_COLOR = "#ef6c00"


class TagReviewPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = []          # task_log 기반 전체
        self._lcps = []          # LCP 로 묶은 것
        self._build()

    # ------------------------------------------------------------------ UI

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)

        bar = QGroupBox("태그 검수 — 자동으로 넣은 태그를 확인한다")
        top = QHBoxLayout(bar)

        self.cmb_day = QComboBox()
        self.cmb_day.addItem("전체 기간", "")
        self.cmb_day.addItem("오늘", "now")
        self.cmb_day.currentIndexChanged.connect(self.reload)
        top.addWidget(self.cmb_day)

        self.txt_find = QLineEdit()
        self.txt_find.setPlaceholderText("상품명 / LCP / L코드 / 태그 검색")
        self.txt_find.setMaximumWidth(300)
        self.txt_find.textChanged.connect(self._render_list)
        top.addWidget(self.txt_find)

        b = QPushButton("새로고침")
        b.clicked.connect(self.reload)
        top.addWidget(b)

        top.addStretch(1)
        self.btn_open = QPushButton("이 상품 OF 열기")
        self.btn_open.setToolTip(
            "선택한 L코드의 상품정보(attr) 팝업을 브라우저로 엽니다."
            + chr(10) + "표를 더블클릭해도 같습니다.")
        self.btn_open.clicked.connect(lambda: self._open("of"))
        top.addWidget(self.btn_open)

        b = QPushButton("태그 탭")
        b.clicked.connect(lambda: self._open("tag"))
        top.addWidget(b)
        b = QPushButton("상품명 탭")
        b.clicked.connect(lambda: self._open("product"))
        top.addWidget(b)
        root.addWidget(bar)

        self.lbl_sum = QLabel("")
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

    def _left(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(QLabel("작업한 LCP"))
        self.tbl_lcp = QTableWidget(0, 4)
        self.tbl_lcp.setHorizontalHeaderLabels(["LCP", "상품명", "L", "태그"])
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

        lay.addWidget(QLabel("L코드별 태그 (행을 더블클릭하면 OF 가 열립니다)"))
        self.tbl_row = QTableWidget(0, 6)
        self.tbl_row.setHorizontalHeaderLabels(
            ["L코드", "상품번호", "태그", "개수", "출처", "넣은 시각"])
        self.tbl_row.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_row.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_row.verticalHeader().setVisible(False)
        self.tbl_row.cellDoubleClicked.connect(lambda *_: self._open("of"))
        lay.addWidget(self.tbl_row, 1)
        return w

    # ------------------------------------------------------------------ 데이터

    def reload(self):
        day = "now" if self.cmb_day.currentData() == "now" else ""
        self._rows = db.tag_work_rows(db.get_job_folder(), day=day)
        groups = {}
        for r in self._rows:
            g = groups.setdefault(r["lcp_code"], {
                "lcp_code": r["lcp_code"],
                "product_name": r["product_name"] or "",
                "rows": []})
            g["rows"].append(r)
        self._lcps = sorted(groups.values(), key=lambda x: x["lcp_code"])
        self._render_list()

    def _visible(self):
        kw = self.txt_find.text().strip().lower()
        if not kw:
            return self._lcps
        out = []
        for g in self._lcps:
            hay = (g["lcp_code"] + " " + g["product_name"] + " "
                   + " ".join(r["l_code"] + " " + (r["picked"] or "")
                              for r in g["rows"])).lower()
            if kw in hay:
                out.append(g)
        return out

    def _render_list(self):
        rows = self._visible()
        self.tbl_lcp.setRowCount(len(rows))
        for i, g in enumerate(rows):
            n_tag = sum(int(r["picked_count"] or 0) for r in g["rows"])
            vals = [g["lcp_code"], g["product_name"], str(len(g["rows"])),
                    str(n_tag)]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if j == 3:
                    it.setForeground(QColor(OK_COLOR))
                self.tbl_lcp.setItem(i, j, it)
            self.tbl_lcp.item(i, 0).setData(Qt.UserRole, g["lcp_code"])
        self.tbl_lcp.resizeColumnsToContents()
        self.tbl_lcp.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch)

        n_l = sum(len(g["rows"]) for g in rows)
        n_t = sum(int(r["picked_count"] or 0) for g in rows for r in g["rows"])
        self.lbl_sum.setText(
            f"자동 태그 입력 <b>{len(rows):,}</b>종 / L코드 "
            f"<b>{n_l:,}</b>건 / 태그 <b style='color:{OK_COLOR}'>{n_t:,}</b>개"
            f" &nbsp;|&nbsp; 건당 평균 {n_t / max(1, n_l):.1f}개")
        if rows:
            self.tbl_lcp.selectRow(0)

    def current(self):
        r = self.tbl_lcp.currentRow()
        if r < 0:
            return None
        it = self.tbl_lcp.item(r, 0)
        code = it.data(Qt.UserRole) if it else None
        return next((g for g in self._lcps if g["lcp_code"] == code), None)

    def _render_detail(self):
        g = self.current()
        self.tbl_row.setRowCount(0)
        if not g:
            self.lbl_prod.setText("")
            return
        r0 = g["rows"][0]
        self.lbl_prod.setText(
            f"<b style='font-size:14px'>{g['product_name']}</b>"
            f"<br>{g['lcp_code']} &nbsp;·&nbsp; L코드 {len(g['rows'])}건"
            f" &nbsp;·&nbsp; 카테고리 {r0.get('etc_category') or '-'}")

        self.tbl_row.setRowCount(len(g["rows"]))
        for i, r in enumerate(g["rows"]):
            vals = [r["l_code"], str(r["product_no"]), r["picked"] or "",
                    str(r["picked_count"] or 0), r["source"] or "",
                    (r["ts"] or "")[:19]]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if j == 2:
                    it.setForeground(QColor(OK_COLOR))
                self.tbl_row.setItem(i, j, it)
            self.tbl_row.item(i, 0).setData(Qt.UserRole, r["product_no"])
        self.tbl_row.resizeColumnsToContents()
        self.tbl_row.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.Stretch)
        if g["rows"]:
            self.tbl_row.selectRow(0)

    # ------------------------------------------------------------------ 링크

    def _open(self, kind: str):
        r = self.tbl_row.currentRow()
        if r < 0:
            return
        it = self.tbl_row.item(r, 0)
        no = it.data(Qt.UserRole) if it else None
        if not no:
            return
        url = tabs.urls(no).get(kind)
        webbrowser.open(url)
