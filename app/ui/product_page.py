"""
상품정보 화면.

작업폴더의 LCP 를 한 줄씩 보여주고, 펼치면 그 LCP 에 묶인 L코드들의
대표이미지·상품정보 상태가 나온다. 오른쪽에는 선택한 LCP 의 수집 정보
(키워드 / 카테고리 / 옵션 제품명)를 탭으로 붙였다.

데이터는 전부 로컬 DB 에서 읽는다. 새로 받아오려면 위쪽 수집 버튼을 쓴다.
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QPushButton, QSplitter, QTabWidget, QTableWidget,
    QTableWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .. import db

IMG_COLOR = {"이미지승인완료": "#1565c0", "이미지작업": "#6a1b9a", "미작업": "#e65100"}
INFO_COLOR = {"저장완료": "#2e7d32", "미작업": "#e65100",
              "제외": "#616161", "보류": "#6a1b9a"}

COLS = ["LCP / L코드", "상품명", "L코드", "승인완료", "이미지작업",
        "정보저장", "정보미작업", "★대상", "키워드", "카테고리"]


class ProductPage(QWidget):
    """상품정보 화면 (대시보드와 분리)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = []
        self._build()

    # ------------------------------------------------------------------ UI

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)

        # ---- 상단 : 폴더 선택 + 수집 ----
        bar = QGroupBox("작업폴더")
        top = QHBoxLayout(bar)

        top.addWidget(QLabel("폴더"))
        self.cmb_folder = QComboBox()
        self.cmb_folder.setMinimumWidth(260)
        self.cmb_folder.currentIndexChanged.connect(self.reload)
        top.addWidget(self.cmb_folder)

        self.txt_filter = QLineEdit()
        self.txt_filter.setPlaceholderText("LCP코드 · 상품명 검색")
        self.txt_filter.textChanged.connect(self._render)
        self.txt_filter.setMaximumWidth(220)
        top.addWidget(self.txt_filter)

        self.btn_status = QPushButton("① L코드 상태 수집")
        self.btn_status.setToolTip(
            "폴더의 모든 L코드에 대표이미지·상품정보 상태를 매깁니다. (약 4초)")
        top.addWidget(self.btn_status)

        self.btn_basic = QPushButton("② 기본정보 수집")
        self.btn_basic.setToolTip(
            "LCP 별로 포함상품·키워드·카테고리를 받아옵니다. (건당 약 2초)")
        top.addWidget(self.btn_basic)

        top.addStretch(1)
        self.btn_expand = QPushButton("전체 펼치기")
        self.btn_expand.clicked.connect(self._toggle_expand)
        top.addWidget(self.btn_expand)

        btn_reload = QPushButton("새로고침")
        btn_reload.clicked.connect(self.reload)
        top.addWidget(btn_reload)
        root.addWidget(bar)

        self.lbl_sum = QLabel("")
        self.lbl_sum.setStyleSheet(
            "QLabel { background:#eceff1; border:1px solid #cfd8dc;"
            " border-radius:6px; padding:6px 12px; }")
        root.addWidget(self.lbl_sum)

        # ---- 본문 : 좌 트리 / 우 상세 ----
        split = QSplitter(Qt.Horizontal)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(COLS))
        self.tree.setHeaderLabels(COLS)
        self.tree.setRootIsDecorated(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tree.setUniformRowHeights(True)
        self.tree.itemExpanded.connect(self._on_expand)
        self.tree.currentItemChanged.connect(lambda *_: self._render_detail())
        hh = self.tree.header()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setStretchLastSection(True)
        split.addWidget(self.tree)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_keyword(), "키워드")
        self.tabs.addTab(self._tab_category(), "카테고리")
        self.tabs.addTab(self._tab_option(), "옵션 제품명")
        split.addWidget(self.tabs)

        split.setStretchFactor(0, 6)
        split.setStretchFactor(1, 4)
        split.setSizes([900, 560])
        root.addWidget(split, 1)

    def _plain_table(self, headers, heights=None):
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.verticalHeader().setVisible(False)
        t.horizontalHeader().setStretchLastSection(True)
        return t

    def _tab_keyword(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        row = QHBoxLayout()
        row.addWidget(QLabel("구분"))
        self.cmb_src = QComboBox()
        self.cmb_src.addItems(["전체", "used(지마켓)", "recommend(추천)",
                               "token(상품명)", "wish(희망)"])
        self.cmb_src.currentIndexChanged.connect(self._render_detail)
        row.addWidget(self.cmb_src)
        self.lbl_kw = QLabel("")
        row.addWidget(self.lbl_kw, 1)
        lay.addLayout(row)
        self.tbl_kw = self._plain_table(["키워드", "구분", "조회수", "옥션", "지마켓"])
        lay.addWidget(self.tbl_kw)
        return w

    def _tab_category(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.lbl_cat = QLabel("")
        lay.addWidget(self.lbl_cat)
        self.tbl_cat = self._plain_table(["순위", "수량", "코드", "카테고리", "단위/용량"])
        lay.addWidget(self.tbl_cat)
        return w

    def _tab_option(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.lbl_opt = QLabel("")
        lay.addWidget(self.lbl_opt)
        self.tbl_opt = self._plain_table(["선택", "제품명", "하위옵션"])
        lay.addWidget(self.tbl_opt)
        return w

    # ------------------------------------------------------------------ 데이터

    def reload_folders(self):
        cur = self.cmb_folder.currentData()
        self.cmb_folder.blockSignals(True)
        self.cmb_folder.clear()
        job = db.get_job_folder()
        names = db.list_master_folders() or ([job] if job else [])
        for n in names:
            self.cmb_folder.addItem(("★ " if n == job else "") + n, n)
        idx = self.cmb_folder.findData(cur or job)
        self.cmb_folder.setCurrentIndex(max(idx, 0))
        self.cmb_folder.blockSignals(False)

    def current_folder(self) -> str:
        return self.cmb_folder.currentData() or ""

    def reload(self):
        folder = self.current_folder()
        self._rows = db.lcp_overview(folder) if folder else []
        self._render()

    def _render(self):
        kw = self.txt_filter.text().strip().lower()
        rows = [r for r in self._rows
                if not kw or kw in (r["lcp_code"] or "").lower()
                or kw in (r.get("product_name") or "").lower()]

        self.tree.clear()
        for r in rows:
            it = QTreeWidgetItem(self.tree)
            it.setData(0, Qt.UserRole, r["lcp_code"])
            it.setText(0, r["lcp_code"])
            it.setText(1, (r.get("product_name") or "")[:40])
            it.setText(2, f"{r['total']:,}")
            it.setText(3, f"{r['img_done']:,}")
            it.setText(4, f"{r['img_work']:,}")
            it.setText(5, f"{r['info_save']:,}")
            it.setText(6, f"{r['info_todo']:,}")
            it.setText(7, f"{r['target']:,}")
            kwn = (r.get("used_count") or 0) + (r.get("rec_count") or 0) \
                + (r.get("token_count") or 0)
            it.setText(8, f"{kwn:,}" if r.get("collected_at") else "-")
            it.setText(9, f"{r.get('cat_count') or 0:,}" if r.get("collected_at") else "-")

            it.setForeground(3, QColor(IMG_COLOR["이미지승인완료"]))
            it.setForeground(4, QColor(IMG_COLOR["이미지작업"]))
            it.setForeground(5, QColor(INFO_COLOR["저장완료"]))
            it.setForeground(6, QColor(INFO_COLOR["미작업"]))
            if r["target"]:
                f = it.font(7)
                f.setBold(True)
                it.setFont(7, f)
                it.setForeground(7, QColor("#2e7d32"))
            if not r.get("collected_at"):
                it.setForeground(1, QColor("#9e9e9e"))
                it.setToolTip(0, "기본정보 미수집 — [② 기본정보 수집] 을 눌러주세요")
            QTreeWidgetItem(it, ["불러오는 중..."])
            it.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)

        for i in range(len(COLS)):
            self.tree.resizeColumnToContents(i)

        n_col = len(rows)
        got = len([r for r in rows if r.get("collected_at")])
        tgt = sum(r["target"] for r in rows)
        lcodes = sum(r["total"] for r in rows)
        self.lbl_sum.setText(
            f"LCP <b>{n_col:,}</b>종 · L코드 <b>{lcodes:,}</b>개 · "
            f"★작업대상 <b style='color:#2e7d32'>{tgt:,}</b>개 &nbsp;|&nbsp; "
            f"기본정보 수집 <b>{got:,}</b>/{n_col:,}종")

    def _on_expand(self, item):
        lcp = item.data(0, Qt.UserRole)
        if not lcp or item.data(0, Qt.UserRole + 1):
            return
        item.takeChildren()
        rows = db.lcode_rows(self.current_folder(), lcp)
        for r in rows:
            ch = QTreeWidgetItem(item)
            ch.setText(0, "    " + (r["l_code"] or ""))
            ch.setForeground(0, QColor("#546e7a"))
            ch.setText(1, f"대표이미지 : {r['img_status'] or '-'}")
            ch.setForeground(1, QColor(IMG_COLOR.get(r["img_status"], "#616161")))
            ch.setText(2, "")
            ch.setText(3, f"상품정보 : {r['info_status'] or '-'}")
            ch.setForeground(3, QColor(INFO_COLOR.get(r["info_status"], "#616161")))
            ch.setText(9, f"no {r['product_no'] or ''}")
        if not rows:
            QTreeWidgetItem(item, ["    (L코드 없음 — 상태 수집을 먼저 하세요)"])
        item.setData(0, Qt.UserRole + 1, True)

    def _toggle_expand(self):
        opening = self.btn_expand.text() == "전체 펼치기"
        if opening:
            for i in range(self.tree.topLevelItemCount()):
                self.tree.expandItem(self.tree.topLevelItem(i))
            self.btn_expand.setText("전체 접기")
        else:
            self.tree.collapseAll()
            self.btn_expand.setText("전체 펼치기")

    # ------------------------------------------------------------------ 상세

    def current_lcp(self) -> str:
        it = self.tree.currentItem()
        while it is not None:
            v = it.data(0, Qt.UserRole)
            if v:
                return v
            it = it.parent()
        return ""

    def _render_detail(self):
        lcp = self.current_lcp()
        for t in (self.tbl_kw, self.tbl_cat, self.tbl_opt):
            t.setRowCount(0)
        if not lcp:
            return

        src_map = {1: "used", 2: "recommend", 3: "token", 4: "wish"}
        src = src_map.get(self.cmb_src.currentIndex())

        with db.sqlite_conn() as conn:
            sql = "SELECT * FROM lcp_keyword WHERE lcp_code = ?"
            args = [lcp]
            if src:
                sql += " AND source = ?"
                args.append(src)
            sql += " ORDER BY CASE source WHEN 'token' THEN -total ELSE 0 END, " \
                   "COALESCE(total, views, 0) DESC, keyword LIMIT 800"
            kws = [dict(r) for r in conn.execute(sql, args).fetchall()]
            cats = [dict(r) for r in conn.execute(
                "SELECT * FROM lcp_category WHERE lcp_code=? ORDER BY rank",
                (lcp,)).fetchall()]
            opts = [dict(r) for r in conn.execute(
                "SELECT * FROM lcp_option WHERE lcp_code=? ORDER BY seq",
                (lcp,)).fetchall()]

        self.tbl_kw.setRowCount(len(kws))
        for i, k in enumerate(kws):
            vals = [k["keyword"], k["source"],
                    "" if k["views"] is None else f"{k['views']:,}",
                    "" if k["auction"] is None else f"{k['auction']:,}",
                    "" if k["gmarket"] is None else f"{k['gmarket']:,}"]
            for j, v in enumerate(vals):
                self.tbl_kw.setItem(i, j, QTableWidgetItem(str(v)))
        self.tbl_kw.resizeColumnsToContents()
        self.lbl_kw.setText(f"{lcp} · {len(kws):,}개")

        self.tbl_cat.setRowCount(len(cats))
        for i, c in enumerate(cats):
            uc = " / ".join(x for x in (c.get("unit"), c.get("capacity")) if x)
            vals = [c["rank"], f"{c['cnt']:,}", c["code"], c["name"], uc]
            for j, v in enumerate(vals):
                self.tbl_cat.setItem(i, j, QTableWidgetItem(str(v)))
        self.tbl_cat.resizeColumnsToContents()
        self.lbl_cat.setText(f"{lcp} · 카테고리 {len(cats)}개"
                             + ("  (1순위가 과반이 아니면 확인 필요)" if cats else ""))

        self.tbl_opt.setRowCount(len(opts))
        for i, o in enumerate(opts):
            subs = (o.get("subs") or "").strip("[]").replace('"', "")
            vals = [o["seq"], o["name"], subs[:80]]
            for j, v in enumerate(vals):
                self.tbl_opt.setItem(i, j, QTableWidgetItem(str(v)))
        self.tbl_opt.resizeColumnsToContents()
        self.lbl_opt.setText(f"{lcp} · 옵션 {len(opts)}개 "
                             f"(제품명을 띄어쓰기로 쪼갠 것이 token 키워드)")
