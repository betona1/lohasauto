"""
상품정보 화면.

작업폴더의 LCP 를 한 줄씩 보여주고, 펼치면 그 LCP 에 묶인 L코드들의
대표이미지·상품정보 상태가 나온다. 오른쪽에는 선택한 LCP 의 수집 정보
(키워드 / 카테고리 / 옵션 제품명)를 탭으로 붙였다.

데이터는 전부 로컬 DB 에서 읽는다. 새로 받아오려면 위쪽 수집 버튼을 쓴다.
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QCheckBox,
    QAbstractItemView, QComboBox, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QPushButton, QSplitter, QTabWidget, QTableWidget,
    QTableWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .. import db

IMG_COLOR = {"이미지승인완료": "#1565c0", "이미지작업": "#6a1b9a", "미작업": "#e65100"}
INFO_COLOR = {"저장완료": "#2e7d32", "미작업": "#e65100",
              "제외": "#616161", "보류": "#6a1b9a"}

COLS = ["LCP / L코드", "상품명", "L코드", "승인완료", "이미지작업",
        "정보저장", "정보미작업", "★대상", "키워드", "카테고리", "소재보류"]


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

        # 광고 소재가 보류된 상품만 추려 본다. 보류면 광고가 안 나간다
        # (2026-09-12 사용자). 자료는 `ad_creative` 가 채운다.
        self.chk_hold = QCheckBox("소재보류만")
        self.chk_hold.setToolTip(
            "네이버 광고에서 소재가 보류된 상품만 보여줍니다." + chr(10)
            + "「광고」 탭 → 소재 수집을 먼저 돌려야 채워집니다.")
        self.chk_hold.stateChanged.connect(lambda *_: self._render())
        top.addWidget(self.chk_hold)

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
        # 대표이미지 탭이 열려 있으면 고른 행을 따라 이미지도 바꾼다
        self.tree.currentItemChanged.connect(lambda *_: self._on_tab())
        hh = self.tree.header()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setStretchLastSection(True)
        split.addWidget(self.tree)

        # 카테고리가 먼저다 — 카테고리를 정해야 상품명·태그로 갈 수 있고,
        # 사람이 제일 먼저 보는 것도 그것이다(2026-09-10 사용자).
        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_category(), "카테고리")
        self.tabs.addTab(self._tab_image(), "대표이미지")
        self.tabs.addTab(self._tab_keyword(), "키워드")
        self.tabs.addTab(self._tab_option(), "옵션 제품명")
        self.tabs.currentChanged.connect(self._on_tab)
        split.addWidget(self.tabs)

        split.setStretchFactor(0, 6)
        split.setStretchFactor(1, 4)
        split.setSizes([900, 560])
        root.addWidget(split, 1)

    # -------------------------------------------------------- 대표이미지
    def _on_tab(self, *_):
        if self.tabs.tabText(self.tabs.currentIndex()) == "대표이미지":
            self._render_images()

    def _img_rows_of(self, lcp):
        """
        고른 것이 **L코드면 그 한 건**, LCP 면 그 아래 전부.
        상품 하나를 눌러 크게 보고 싶을 때가 많다(2026-09-10 사용자).
        """
        it = self.tree.currentItem()
        one = it.data(0, Qt.UserRole + 2) if it is not None else None
        if one:
            return [dict(one)]
        with db.sqlite_conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT l_code, product_no FROM lcode_attr WHERE lcp_code=? "
                "ORDER BY l_code LIMIT 12", (lcp,))]

    def _render_images(self):
        """
        원본과 현재(AI)를 나란히 건다. **[이미지 보기] 를 켰을 때만** 받는다.

        받아온 URL 은 `lcode_image` 에 남겨 다시 열 때 빠르게 뜨게 하고,
        AI 이미지가 새로 생겼을 수 있으므로 **열 때마다 다시 물어본다**
        (건당 0.3초, 2026-09-10 사용자).
        """
        from PySide6.QtWidgets import QLabel as _L

        while self.img_lay.count():
            it = self.img_lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self._img_rows = []
        self.btn_img_approve.setEnabled(False)
        lcp = self.current_lcp()
        if not lcp:
            self.lbl_img.setText("")
            return
        if not self.chk_img.isChecked():
            self.lbl_img.setText("[이미지 보기] 를 켜면 대표이미지를 받아옵니다.")
            return

        from ..lohas import prod_image as pi, session as ses
        rows = self._img_rows_of(lcp)
        self.lbl_img.setText(f"{lcp} · {len(rows)}건 받는 중...")
        self.lbl_img.repaint()
        try:
            s = ses.get_client(allow_login=False).session
        except Exception as e:
            self.lbl_img.setText(f"세션 없음 : {str(e)[:50]}")
            return

        # **받아둔 URL 을 먼저 쓴다**(`tools/collect_images.py` 로 모아둔 것).
        # 바로 떠서 목록을 훑기 좋다. 없는 건만 사이트에 물어본다.
        cached = {}
        with db.sqlite_conn() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS lcode_image (
                l_code TEXT PRIMARY KEY, product_no TEXT, uid TEXT, ai INTEGER,
                org_main TEXT, edit_main TEXT, org_n INTEGER, edit_n INTEGER,
                updated_at TEXT)""")
            marks = ",".join("?" * len(rows))
            for x in conn.execute(
                    f"SELECT * FROM lcode_image WHERE l_code IN ({marks})",
                    [r["l_code"] for r in rows]):
                cached[x["l_code"]] = dict(x)

        n_ai = 0
        for r in rows:
            cv = cached.get(r["l_code"])
            if cv and (cv.get("org_main") or cv.get("edit_main")):
                d = {"uid": cv.get("uid"), "ai": bool(cv.get("ai")),
                     "org": {"main1": cv.get("org_main"), "detail": []},
                     "edit": {"main1": cv.get("edit_main"), "detail": []},
                     "cached_at": cv.get("updated_at")}
            else:
                try:
                    d = pi.fetch_images(s, r["product_no"])
                    db.save_lcode_image(r["l_code"], r["product_no"], d)
                except Exception as e:
                    self.lbl_img.setText(f"실패 {r['l_code']} : {str(e)[:40]}")
                    continue
            n_ai += bool(d.get("ai"))
            self._img_rows.append({**r, **d})
            col = QWidget()
            cl = QVBoxLayout(col)
            cl.setContentsMargins(4, 4, 4, 4)
            head = _L(f"{r['l_code']}" + ("   ✦AI" if d.get("ai") else ""))
            head.setStyleSheet("font-weight:bold;"
                               + ("color:#6a1b9a;" if d.get("ai") else ""))
            cl.addWidget(head)
            big = len(rows) == 1
            for lab, url in (("원본", d["org"].get("main1")),
                             ("현재(AI)" if d.get("ai") else "현재",
                              d["edit"].get("main1"))):
                cl.addWidget(_L(lab))
                cl.addWidget(self._img_label(s, url, 420 if big else 200))
            cl.addStretch(1)
            self.img_lay.addWidget(col)
        self.img_lay.addStretch(1)
        self.btn_img_approve.setEnabled(bool(self._img_rows))
        self.lbl_img.setText(
            f"{lcp} · {len(self._img_rows)}건  ·  AI 이미지 {n_ai}건"
            + ("   (원본 = images3 / 현재 = images_ai)" if n_ai else ""))

    def _img_label(self, session, url: str, size: int = 200):
        from PySide6.QtGui import QPixmap
        from PySide6.QtWidgets import QLabel as _L

        lab = _L()
        lab.setFixedSize(size, size)
        lab.setAlignment(Qt.AlignCenter)
        lab.setStyleSheet("border:1px solid #d0d7de; background:#fafafa;")
        if not url:
            lab.setText("(없음)")
            return lab
        try:
            raw = session.get(url, timeout=15).content
            pm = QPixmap()
            if pm.loadFromData(raw):
                lab.setPixmap(pm.scaled(size - 4, size - 4, Qt.KeepAspectRatio,
                                        Qt.SmoothTransformation))
            else:
                lab.setText("(못 읽음)")
        except Exception:
            lab.setText("(받기 실패)")
        lab.setToolTip(url)
        return lab

    def _approve_image(self):
        """
        [현재이미지 승인적용] — 되돌릴 수 없으므로 **한 번 물어본다**
        (2026-09-10 사용자).
        """
        from PySide6.QtWidgets import QMessageBox

        if not self._img_rows:
            return
        lcp = self.current_lcp()
        codes = [r["l_code"] for r in self._img_rows]
        if QMessageBox.question(
                self, "현재이미지 승인적용",
                f"{lcp}{chr(10)}{len(codes)}건의 대표이미지를 승인합니다."
                + chr(10) + ", ".join(codes[:8])
                + (" ..." if len(codes) > 8 else "")
                + chr(10) + chr(10) + "되돌릴 수 없습니다. 진행할까요?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No) != QMessageBox.Yes:
            return
        from ..lohas import prod_image as pi, session as ses
        s = ses.get_client(allow_login=False).session
        ok = fail = 0
        for r in self._img_rows:
            try:
                ok += 1 if pi.approve(s, r["product_no"])["ok"] else 0
            except Exception:
                fail += 1
        self.lbl_img.setText(f"승인 {ok}건 / 실패 {fail}건 — 점검하면 반영됩니다")

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
        self.lbl_cat.setWordWrap(True)
        lay.addWidget(self.lbl_cat)
        # 첫 칸에 체크를 둬 **지금 저장된 카테고리**가 어느 것인지 바로 보이게
        # 한다(2026-09-10 사용자: "선정된 카테고리 명칭 체크로").
        self.tbl_cat = self._plain_table(
            ["선정", "순위", "수량", "코드", "카테고리", "단위/용량"])
        lay.addWidget(self.tbl_cat)
        return w

    def _tab_image(self):
        """
        대표이미지 — 원본과 현재(AI)를 나란히 보여주고, 그 자리에서 승인한다.

        이미지는 **[이미지 보기] 를 켰을 때만** 받아온다. LCP 를 옮길 때마다
        받으면 목록을 훑기만 해도 느려진다(2026-09-10 사용자).
        """
        from PySide6.QtWidgets import QCheckBox, QScrollArea

        w = QWidget()
        lay = QVBoxLayout(w)
        row = QHBoxLayout()
        self.chk_img = QCheckBox("이미지 보기")
        self.chk_img.setToolTip(
            "켜면 LCP 를 누를 때 대표이미지를 받아 보여줍니다."
            + chr(10) + "끄면 받지 않습니다(목록만 빠르게 훑을 때).")
        self.chk_img.stateChanged.connect(lambda *_: self._render_images())
        row.addWidget(self.chk_img)
        self.btn_img_approve = QPushButton("현재이미지 승인적용")
        self.btn_img_approve.setStyleSheet("font-weight:bold; color:#00695c;")
        self.btn_img_approve.setEnabled(False)
        self.btn_img_approve.clicked.connect(self._approve_image)
        row.addWidget(self.btn_img_approve)
        row.addStretch(1)
        self.lbl_img = QLabel("")
        row.addWidget(self.lbl_img, 1)
        lay.addLayout(row)

        self.img_box = QWidget()
        self.img_lay = QHBoxLayout(self.img_box)
        self.img_lay.setContentsMargins(0, 0, 0, 0)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(self.img_box)
        lay.addWidget(sc, 1)
        self._img_rows = []          # 지금 화면에 뜬 L코드들
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

    def _hold_map(self) -> dict:
        """{LCP: [L코드...]} — 광고 소재가 보류된 것."""
        out = {}
        try:
            with db.sqlite_conn() as c:
                c.execute("CREATE TABLE IF NOT EXISTS ad_creative ("
                          "customer_id TEXT, ad_id TEXT, adgroup_id TEXT,"
                          " campaign_id TEXT, lcp_code TEXT, l_code TEXT,"
                          " match_score REAL, status TEXT, status_ko TEXT,"
                          " enabled INTEGER, product_name TEXT,"
                          " mall_product_id TEXT, product_url TEXT,"
                          " bid INTEGER, updated_at TEXT)")
                for r in c.execute(
                        "SELECT lcp_code, l_code FROM ad_creative"
                        " WHERE status IN ('PENDING','REJECTED',"
                        "'UNDER_REVIEW','NOT_YET_RECEIVED')"):
                    out.setdefault(r["lcp_code"] or "", []).append(
                        r["l_code"] or "")
        except Exception:
            pass
        return out

    def _render(self):
        kw = self.txt_filter.text().strip().lower()
        self._hold = self._hold_map()
        rows = [r for r in self._rows
                if not kw or kw in (r["lcp_code"] or "").lower()
                or kw in (r.get("product_name") or "").lower()]
        if self.chk_hold.isChecked():
            rows = [r for r in rows if self._hold.get(r["lcp_code"])]

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
            hold = self._hold.get(r["lcp_code"]) or []
            it.setText(10, f"{len(hold):,}" if hold else "")
            if hold:
                fh = it.font(10)
                fh.setBold(True)
                it.setFont(10, fh)
                it.setForeground(10, QColor("#c62828"))
                it.setToolTip(10, "소재보류 : " + ", ".join(hold[:12]))

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
            if r["l_code"] in (getattr(self, "_hold", {}).get(lcp) or []):
                ch.setText(10, "소재보류")
                ch.setForeground(10, QColor("#c62828"))
            # 대표이미지 탭이 이 행을 집어 쓴다
            ch.setData(0, Qt.UserRole + 2,
                       {"l_code": r["l_code"], "product_no": r["product_no"]})
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

        # 지금 그 LCP 에 **실제로 저장돼 있는** 카테고리
        with db.sqlite_conn() as conn:
            saved = [dict(r) for r in conn.execute(
                "SELECT etc_category, COUNT(*) n FROM lcode_attr "
                "WHERE lcp_code=? AND IFNULL(etc_category,'')<>'' "
                "GROUP BY etc_category ORDER BY n DESC", (lcp,))]
        cur_codes = {str(r["etc_category"]): r["n"] for r in saved}

        self.tbl_cat.setRowCount(len(cats))
        for i, c in enumerate(cats):
            uc = " / ".join(x for x in (c.get("unit"), c.get("capacity")) if x)
            n = cur_codes.get(str(c["code"]))
            it = QTableWidgetItem("")
            it.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            it.setCheckState(Qt.Checked if n else Qt.Unchecked)
            self.tbl_cat.setItem(i, 0, it)
            vals = [c["rank"], f"{c['cnt']:,}", c["code"], c["name"], uc]
            for j, v in enumerate(vals, start=1):
                cell = QTableWidgetItem(str(v))
                if n:
                    f = cell.font(); f.setBold(True); cell.setFont(f)
                    cell.setForeground(QColor("#00695c"))
                self.tbl_cat.setItem(i, j, cell)
        self.tbl_cat.resizeColumnsToContents()

        # 후보에 없는 코드가 저장돼 있을 수도 있다(사람이 손으로 넣은 경우)
        extra = [code for code in cur_codes
                 if code not in {str(c["code"]) for c in cats}]
        head = f"{lcp} · 카테고리 후보 {len(cats)}개"
        if cur_codes:
            names = ", ".join(
                f"{db.category_name(code) or code} ({n}건)"
                for code, n in cur_codes.items())
            head += f"{chr(10)}✔ 지금 저장된 것 : {names}"
            if extra:
                head += "   ※ 후보에 없는 코드입니다"
        else:
            head += f"{chr(10)}✘ 아직 저장된 카테고리가 없습니다"
        self.lbl_cat.setText(head)

        self.tbl_opt.setRowCount(len(opts))
        for i, o in enumerate(opts):
            subs = (o.get("subs") or "").strip("[]").replace('"', "")
            vals = [o["seq"], o["name"], subs[:80]]
            for j, v in enumerate(vals):
                self.tbl_opt.setItem(i, j, QTableWidgetItem(str(v)))
        self.tbl_opt.resizeColumnsToContents()
        self.lbl_opt.setText(f"{lcp} · 옵션 {len(opts)}개 "
                             f"(제품명을 띄어쓰기로 쪼갠 것이 token 키워드)")
