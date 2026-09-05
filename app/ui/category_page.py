"""
카테고리 화면 (역방향 조회).

상품정보 화면이 'LCP → 카테고리' 방향이라면, 여기는 반대다.
카테고리를 고르면 그 카테고리에 걸린 LCP 들과, 그 LCP 들이 실제로 쓰고 있는
키워드를 모아서 보여준다. '이 카테고리 상품에는 이런 키워드를 붙인다'는
사전이 되고, 태그·상품명 후보를 고를 때 근거로 쓸 수 있다.

좌측은 카테고리 경로(대 > 중 > 소 > 세부)를 트리로 펼친다.
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QPushButton, QSplitter, QTabWidget,
    QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from .. import db

SEP = " / "
SRC_LABEL = {"used": "지마켓 사용", "recommend": "추천", "token": "상품명",
             "wish": "희망검색어"}


class CategoryPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._cats = []
        self._build()

    # ------------------------------------------------------------------ UI

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)

        bar = QGroupBox("카테고리")
        top = QHBoxLayout(bar)

        self.txt_filter = QLineEdit()
        self.txt_filter.setPlaceholderText("카테고리명 검색 (예: 의자, 스툴)")
        self.txt_filter.textChanged.connect(self._render_tree)
        self.txt_filter.setMaximumWidth(280)
        top.addWidget(self.txt_filter)

        top.addWidget(QLabel("최소 LCP"))
        self.cmb_min = QComboBox()
        self.cmb_min.addItems(["1", "2", "3", "5", "10"])
        self.cmb_min.setCurrentText("2")
        self.cmb_min.currentIndexChanged.connect(self.reload)
        top.addWidget(self.cmb_min)

        self.chk_flat = QCheckBox("평면 목록")
        self.chk_flat.setToolTip("트리 대신 한 줄씩 나열합니다.")
        self.chk_flat.stateChanged.connect(self._render_tree)
        top.addWidget(self.chk_flat)

        top.addStretch(1)
        btn = QPushButton("새로고침")
        btn.clicked.connect(self.reload)
        top.addWidget(btn)
        root.addWidget(bar)

        self.lbl_sum = QLabel("")
        self.lbl_sum.setStyleSheet(
            "QLabel { background:#eceff1; border:1px solid #cfd8dc;"
            " border-radius:6px; padding:6px 12px; }")
        root.addWidget(self.lbl_sum)

        split = QSplitter(Qt.Horizontal)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["카테고리", "LCP", "L코드", "1순위"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tree.setUniformRowHeights(True)
        self.tree.currentItemChanged.connect(lambda *_: self._render_detail())
        hh = self.tree.header()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setStretchLastSection(True)
        split.addWidget(self.tree)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_keyword(), "키워드 사전")
        self.tabs.addTab(self._tab_lcp(), "소속 LCP")
        split.addWidget(self.tabs)

        split.setStretchFactor(0, 5)
        split.setStretchFactor(1, 5)
        split.setSizes([720, 740])
        root.addWidget(split, 1)

    def _tab_keyword(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        row = QHBoxLayout()
        row.addWidget(QLabel("구분"))
        self.cmb_src = QComboBox()
        self.cmb_src.addItem("전체", None)
        for k, v in SRC_LABEL.items():
            self.cmb_src.addItem(v, k)
        self.cmb_src.setCurrentIndex(1)          # 기본 = 지마켓 사용
        self.cmb_src.currentIndexChanged.connect(self._render_detail)
        row.addWidget(self.cmb_src)
        self.lbl_kw = QLabel("")
        row.addWidget(self.lbl_kw, 1)
        self.btn_copy = QPushButton("키워드 복사")
        self.btn_copy.clicked.connect(self._copy_keywords)
        row.addWidget(self.btn_copy)
        lay.addLayout(row)

        self.tbl_kw = QTableWidget(0, 4)
        self.tbl_kw.setHorizontalHeaderLabels(["키워드", "쓰인 LCP", "구분", "조회수"])
        self.tbl_kw.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_kw.verticalHeader().setVisible(False)
        self.tbl_kw.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.tbl_kw)
        return w

    def _tab_lcp(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.lbl_lcp = QLabel("")
        lay.addWidget(self.lbl_lcp)
        self.tbl_lcp = QTableWidget(0, 5)
        self.tbl_lcp.setHorizontalHeaderLabels(
            ["LCP", "상품명", "이 카테고리 L코드", "순위", "키워드"])
        self.tbl_lcp.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_lcp.verticalHeader().setVisible(False)
        self.tbl_lcp.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.tbl_lcp)
        return w

    # ------------------------------------------------------------------ 데이터

    def reload(self):
        self._cats = db.category_list(min_lcp=int(self.cmb_min.currentText()))
        self._render_tree()

    def _render_tree(self):
        kw = self.txt_filter.text().strip().lower()
        cats = [c for c in self._cats
                if not kw or kw in (c["name"] or "").lower()]

        self.tree.clear()
        if self.chk_flat.isChecked():
            for c in cats[:3000]:
                it = QTreeWidgetItem(self.tree)
                self._fill(it, c["name"], c)
        else:
            nodes = {}                       # 경로튜플 -> QTreeWidgetItem
            for c in cats:
                parts = [p.strip() for p in (c["name"] or "").split(SEP) if p.strip()]
                if not parts:
                    continue
                parent = None
                for depth in range(len(parts)):
                    key = tuple(parts[:depth + 1])
                    if key not in nodes:
                        node = (QTreeWidgetItem(self.tree) if parent is None
                                else QTreeWidgetItem(parent))
                        node.setText(0, parts[depth])
                        node.setForeground(0, QColor("#37474f"))
                        nodes[key] = node
                    parent = nodes[key]
                self._fill(parent, parts[-1], c)

        self.tree.expandToDepth(0)
        for i in range(4):
            self.tree.resizeColumnToContents(i)

        tot_lcp = sum(c["lcp_count"] for c in cats)
        st = db.category_stats()
        self.lbl_sum.setText(
            f"카테고리 <b>{len(cats):,}</b>개 표시 "
            f"(수집 전체 {st['categories']:,}개 · 연결 {st['rows']:,}건) &nbsp;|&nbsp; "
            f"LCP 연결 합계 <b>{tot_lcp:,}</b>")

    def _fill(self, item, label, c):
        item.setText(0, label)
        item.setData(0, Qt.UserRole, c["code"])
        item.setData(0, Qt.UserRole + 1, c["name"])
        item.setText(1, f"{c['lcp_count']:,}")
        item.setText(2, f"{c['total_cnt'] or 0:,}")
        item.setText(3, f"{c['top_cnt'] or 0:,}")
        item.setForeground(1, QColor("#1565c0"))
        if c["top_cnt"]:
            item.setForeground(3, QColor("#2e7d32"))
        item.setToolTip(0, f"{c['name']}{chr(10)}코드 {c['code']}")

    # ------------------------------------------------------------------ 상세

    def current_code(self):
        it = self.tree.currentItem()
        while it is not None:
            v = it.data(0, Qt.UserRole)
            if v:
                return v, it.data(0, Qt.UserRole + 1)
            it = it.parent()
        return None, None

    def _render_detail(self):
        code, name = self.current_code()
        self.tbl_kw.setRowCount(0)
        self.tbl_lcp.setRowCount(0)
        if not code:
            self.lbl_kw.setText("")
            return

        src = self.cmb_src.currentData()
        kws = db.category_keywords(code, src, 600)
        self.tbl_kw.setRowCount(len(kws))
        for i, k in enumerate(kws):
            vals = [k["keyword"], f"{k['lcp_count']:,}",
                    SRC_LABEL.get(k["source"], k["source"]),
                    "" if k["views"] is None else f"{k['views']:,}"]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                if j == 1 and k["lcp_count"] >= 5:
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                    it.setForeground(QColor("#2e7d32"))
                self.tbl_kw.setItem(i, j, it)
        self.tbl_kw.resizeColumnsToContents()
        self.lbl_kw.setText(f"{(name or '')[:44]} · {len(kws):,}개")

        lcps = db.category_lcps(code)
        self.tbl_lcp.setRowCount(len(lcps))
        for i, p in enumerate(lcps):
            kwn = ((p.get("used_count") or 0) + (p.get("rec_count") or 0)
                   + (p.get("token_count") or 0))
            vals = [p["lcp_code"], (p.get("product_name") or "")[:34],
                    f"{p['cnt']:,}", p["rank"], f"{kwn:,}"]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                if j == 3 and p["rank"] == 1:
                    it.setForeground(QColor("#2e7d32"))
                self.tbl_lcp.setItem(i, j, it)
        self.tbl_lcp.resizeColumnsToContents()
        self.lbl_lcp.setText(f"{(name or '')[:44]} · LCP {len(lcps):,}종")

    def _copy_keywords(self):
        from PySide6.QtWidgets import QApplication, QMessageBox

        rows = self.tbl_kw.rowCount()
        if not rows:
            QMessageBox.information(self, "안내", "복사할 키워드가 없습니다.")
            return
        words = [self.tbl_kw.item(i, 0).text() for i in range(rows)
                 if self.tbl_kw.item(i, 0)]
        QApplication.clipboard().setText(chr(10).join(words))
        QMessageBox.information(self, "복사 완료", f"{len(words):,}개를 복사했습니다.")
