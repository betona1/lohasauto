"""
미작업목록 — 대표이미지는 끝났는데 상품정보가 미작업인 상품들.

이미지 승인이 끝났으니 곧바로 상품정보 작업에 들어갈 수 있는 것들이다. 여기서
LCP 하나를 고르면 그 상품의 카테고리 상태와 키워드가 한 화면에 모인다.

  카테고리   로하스가 준 후보 + 지금 저장된 값(파란색 = 저장완료)
  로하스     그 LCP 가 이미 갖고 있는 키워드 (지마켓 사용 / 추천 / 상품명 / 희망)
  데이터랩   네이버 카테고리 인기키워드 최근 30일 500위

저장된 카테고리가 있어야 데이터랩을 부를 수 있다. cid 체계가 로하스 카테고리
코드와 같아서(50007769 등) 그대로 넘기면 된다.
"""
from PySide6.QtCore import Qt, QThread
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QSplitter, QTabWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from .. import db
from .. import config
from .workers import (AnalysisWorker, CatKeywordWorker, DatalabWorker,
                      StatusSyncWorker, TagAutoWorker)

SRC_LABEL = {"used": "지마켓 사용", "recommend": "추천", "token": "상품명",
             "wish": "희망검색어"}
OK_COLOR = "#1565c0"        # 저장완료 = 사이트의 파란 버튼
TODO_COLOR = "#ef6c00"      # 미저장

# 목록 필터 — 작업 단계로 나눈다.
#   카테고리 저장이 끝나야 상품명·태그 탭이 열린다(사이트가 서버에서 막는다).
#   그래서 '상품명·태그 미작업' 은 카테고리가 완료된 것 중에서만 고른다.
#   그 조건을 빼면 아직 손도 못 대는 LCP 까지 섞여 목록이 쓸모없어진다.
STAGE_FILTERS = [
    ("전체", ""),
    ("카테고리 미저장", "cat_todo"),
    ("카테고리 일부 저장", "cat_part"),
    ("카테고리 저장완료", "cat_done"),
    ("상품명·태그 미작업 ★", "tt_todo"),
    ("상품명·태그 진행중", "tt_part"),
    ("이미 저장완료 (목록에서 빠질 것)", "saved"),
    ("아직 안 끝난 것만", "unsaved"),
]

# 컬럼별 정렬 기준. (키 함수, 처음 눌렀을 때 내림차순인가)
#   상태 컬럼은 오름차순이 곧 '미작업 먼저' 다 (0=미작업 … 2=완료).
#   같은 상태끼리는 L코드가 많은 LCP 를 위로 올린다 — 한 번에 처리되는 양이 많다.
SORT_RULES = {
    0: (lambda g: g["lcp_code"], False),
    1: (lambda g: g["product_name"], False),
    2: (lambda g: g["n"], True),
    3: (lambda g: (1 if g["analyzed"] else 0, -g["n"]), False),
    4: (lambda g: (g["cat_state"], -g["n"]), False),
    5: (lambda g: (g["tt_state"], -g["n"]), False),
    6: (lambda g: (g["saved"], -g["n"]), False),
    7: (lambda g: (g["kw_tag"] + g["kw_title"], -g["n"]), False),
}
COLUMNS = ["LCP", "상품명", "L", "상품분석", "카테고리", "상품명·태그",
           "상품정보", "키워드"]


class TodoPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = []          # L코드 단위 원본
        self._lcps = []          # LCP 단위로 묶은 것
        self._sort_col = 2       # 기본 정렬 = L코드 건수 많은 순
        self._sort_desc = True
        self._detail_code = None  # 오른쪽에 지금 그려져 있는 LCP
        self._thread = None
        self._worker = None
        self._build()

    # ------------------------------------------------------------------ UI

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)

        bar = QGroupBox("미작업 목록 (대표이미지 승인완료 · 상품정보 미작업)")
        top = QHBoxLayout(bar)

        self.txt_find = QLineEdit()
        self.txt_find.setPlaceholderText("상품명 / LCP 코드 검색")
        self.txt_find.textChanged.connect(self._render_list)
        self.txt_find.setMaximumWidth(260)
        top.addWidget(self.txt_find)

        # 작업 단계 필터. 카테고리 저장이 상품명·태그의 선행 조건이므로
        # 두 단계를 갈라서 고를 수 있게 했다 (사이트가 강제하는 순서 그대로).
        self.cmb_cat = QComboBox()
        for label, key in STAGE_FILTERS:
            self.cmb_cat.addItem(label, key)
        self.cmb_cat.setToolTip(
            "카테고리 저장 -> 상품명·태그 순으로만 작업할 수 있습니다.\n"
            "'상품명·태그 미작업' 은 카테고리가 끝나 바로 들어갈 수 있는 것들입니다.")
        self.cmb_cat.currentIndexChanged.connect(self._render_list)
        top.addWidget(self.cmb_cat)

        self.chk_tag_over = QCheckBox("태그 덮어쓰기")
        self.chk_tag_over.setToolTip(
            "체크하지 않으면 태그가 이미 있는 L코드는 건드리지 않습니다.")
        top.addWidget(self.chk_tag_over)

        self.chk_dl = QCheckBox("데이터랩 없는 것만")
        self.chk_dl.setToolTip("아직 인기키워드를 안 받아온 카테고리만 봅니다.")
        self.chk_dl.stateChanged.connect(self._render_list)
        top.addWidget(self.chk_dl)

        btn = QPushButton("새로고침")
        btn.clicked.connect(self.reload)
        top.addWidget(btn)

        self.btn_sync = QPushButton("상태 동기화")
        self.btn_sync.setToolTip(
            "상품정보 상태를 사이트 현재값으로 맞춥니다 (검색 4번, 5초쯤)."
            + chr(10)
            + "상품명·태그를 저장하면 사이트가 '저장완료' 로 바꾸는데,"
            + chr(10)
            + "이 목록은 점검 당시 스냅샷이라 끝난 것이 계속 남아 있습니다.")
        self.btn_sync.clicked.connect(self._sync_status)
        top.addWidget(self.btn_sync)

        top.addStretch(1)
        self.btn_analysis = QPushButton("ALL 상품분석")
        self.btn_analysis.setToolTip(
            "미분석 LCP 를 찾아 상품분석을 요청합니다. 대시보드의 것과 같은 동작입니다.")
        self.btn_analysis.clicked.connect(self._run_analysis)
        top.addWidget(self.btn_analysis)

        self.btn_kw = QPushButton("카테고리 키워드 수집")
        self.btn_kw.setToolTip(
            "카테고리가 저장된 LCP 의 태그·상품명 후보를 긁어 저장합니다.")
        self.btn_kw.clicked.connect(self._collect_kw)
        top.addWidget(self.btn_kw)

        self.btn_dl_all = QPushButton("데이터랩 키워드 일괄 수집")
        self.btn_dl_all.setToolTip(
            "목록에 있는 카테고리 전부의 인기키워드 500개씩을 받아 저장합니다.")
        self.btn_dl_all.clicked.connect(lambda: self._collect(all_cats=True))
        top.addWidget(self.btn_dl_all)

        self.btn_tag_auto = QPushButton("키워드 자동추가")
        self.btn_tag_auto.setToolTip(
            "선택한 LCP 의 카테고리로 데이터랩 인기키워드를 받아 태그로 넣습니다."
            + chr(10)
            + "조회수 1000 미만을 먼저 채우고 모자라면 그 이상으로 10개를 채웁니다."
            + chr(10)
            + "Ctrl 을 누른 채 누르면 목록에 보이는 LCP 전부에 적용합니다.")
        self.btn_tag_auto.setStyleSheet(
            "QPushButton { background:#2e7d32; color:white; font-weight:bold;"
            " padding:4px 14px; border-radius:4px; }"
            "QPushButton:disabled { background:#b0bec5; }")
        self.btn_tag_auto.clicked.connect(self._tag_auto)
        top.addWidget(self.btn_tag_auto)

        self.btn_stop = QPushButton("중지")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop)
        top.addWidget(self.btn_stop)
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
        split.setSizes([640, 900])
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
        self.tbl_lcp = QTableWidget(0, len(COLUMNS))
        self.tbl_lcp.setHorizontalHeaderLabels(COLUMNS)
        # QTableWidget 자체 정렬(setSortingEnabled)은 쓰지 않는다. 셀이 문자열이라
        # "일부 3/12" 같은 값이 사전순으로 섞인다. 원본 데이터를 직접 정렬한다.
        hh = self.tbl_lcp.horizontalHeader()
        hh.setSectionsClickable(True)
        hh.sectionClicked.connect(self._sort_by)
        hh.setToolTip("머리글을 누르면 그 기준으로 정렬합니다."
                      " 상태 칸은 미작업이 위로 옵니다. 다시 누르면 역순.")
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

        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_cat(), "카테고리")
        self.tabs.addTab(self._tab_kw(), "로하스 키워드")
        self.tabs.addTab(self._tab_dl(), "데이터랩 500")
        self.tabs.addTab(self._tab_ck(), "카테고리 키워드")
        lay.addWidget(self.tabs, 1)

        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMaximumHeight(110)
        lay.addWidget(self.txt_log)
        return w

    def _tab_cat(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.lbl_cat = QLabel("")
        lay.addWidget(self.lbl_cat)
        self.tbl_cat = QTableWidget(0, 5)
        self.tbl_cat.setHorizontalHeaderLabels(
            ["카테고리", "건수", "단위", "코드", "상태"])
        self.tbl_cat.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_cat.verticalHeader().setVisible(False)
        lay.addWidget(self.tbl_cat)
        return w

    def _tab_kw(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        row = QHBoxLayout()
        row.addWidget(QLabel("구분"))
        self.cmb_src = QComboBox()
        self.cmb_src.addItem("전체", None)
        for k, v in SRC_LABEL.items():
            self.cmb_src.addItem(v, k)
        self.cmb_src.currentIndexChanged.connect(self._render_kw)
        row.addWidget(self.cmb_src)
        self.lbl_kw = QLabel("")
        row.addWidget(self.lbl_kw, 1)
        lay.addLayout(row)

        self.tbl_kw = QTableWidget(0, 3)
        self.tbl_kw.setHorizontalHeaderLabels(["키워드", "구분", "조회수"])
        self.tbl_kw.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_kw.verticalHeader().setVisible(False)
        lay.addWidget(self.tbl_kw)
        return w

    def _tab_ck(self):
        """
        로하스가 이 카테고리에 대해 뽑아준 키워드를 태그용/상품명용으로
        갈라서 보여준다. 같은 카테고리의 다른 LCP 것까지 합쳐 보므로,
        '이 카테고리 상품에는 이런 태그를 단다'는 사전이 된다.
        """
        w = QWidget()
        lay = QVBoxLayout(w)
        row = QHBoxLayout()
        row.addWidget(QLabel("범위"))
        self.cmb_ck_scope = QComboBox()
        self.cmb_ck_scope.addItem("이 카테고리 전체", "cid")
        self.cmb_ck_scope.addItem("이 LCP 만", "lcp")
        self.cmb_ck_scope.currentIndexChanged.connect(self._render_ck)
        row.addWidget(self.cmb_ck_scope)

        self.cmb_ck_kind = QComboBox()
        self.cmb_ck_kind.addItem("태그 + 상품명", None)
        self.cmb_ck_kind.addItem("태그만", "tag")
        self.cmb_ck_kind.addItem("상품명만", "title")
        self.cmb_ck_kind.currentIndexChanged.connect(self._render_ck)
        row.addWidget(self.cmb_ck_kind)

        self.chk_ck_low = QCheckBox("조회수 1000 미만만")
        self.chk_ck_low.setToolTip(
            "로하스 지침 - 태그는 조회수 1000 미만에서만 고른다.")
        self.chk_ck_low.setChecked(True)
        self.chk_ck_low.stateChanged.connect(self._render_ck)
        row.addWidget(self.chk_ck_low)

        self.chk_ck_ok = QCheckBox("금지어 제외")
        self.chk_ck_ok.setChecked(True)
        self.chk_ck_ok.stateChanged.connect(self._render_ck)
        row.addWidget(self.chk_ck_ok)

        self.lbl_ck = QLabel("")
        row.addWidget(self.lbl_ck, 1)
        self.btn_ck_one = QPushButton("이 LCP 수집")
        self.btn_ck_one.clicked.connect(self._collect_kw_one)
        row.addWidget(self.btn_ck_one)
        lay.addLayout(row)

        self.tbl_ck = QTableWidget(0, 6)
        self.tbl_ck.setHorizontalHeaderLabels(
            ["키워드", "구분", "조회수", "쓰는 LCP", "금지어", "라벨"])
        self.tbl_ck.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_ck.verticalHeader().setVisible(False)
        lay.addWidget(self.tbl_ck)
        return w

    def _tab_dl(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        row = QHBoxLayout()
        self.lbl_dl = QLabel("")
        row.addWidget(self.lbl_dl, 1)
        self.btn_dl_one = QPushButton("이 카테고리 수집")
        self.btn_dl_one.clicked.connect(lambda: self._collect(all_cats=False))
        row.addWidget(self.btn_dl_one)
        self.btn_dl_copy = QPushButton("복사")
        self.btn_dl_copy.clicked.connect(self._copy_dl)
        row.addWidget(self.btn_dl_copy)
        lay.addLayout(row)

        self.tbl_dl = QTableWidget(0, 3)
        self.tbl_dl.setHorizontalHeaderLabels(["순위", "키워드", "로하스 보유"])
        self.tbl_dl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_dl.verticalHeader().setVisible(False)
        lay.addWidget(self.tbl_dl)
        return w

    # ------------------------------------------------------------------ 데이터

    def reload(self):
        folder = db.get_job_folder()
        self._rows = db.todo_lcodes(folder)
        have = db.datalab_have()
        analyzed = db.analyzed_lcp_set(folder)
        kw_have = db.cat_keyword_lcps()

        groups = {}
        for r in self._rows:
            g = groups.setdefault(r["lcp_code"], {
                "lcp_code": r["lcp_code"],
                "product_name": r.get("product_name") or "",
                "rows": [], "cat": "", "cat_saved": 0, "title_saved": 0,
                "saved": 0})
            g["rows"].append(r)
            if r.get("etc_category"):
                g["cat"] = str(r["etc_category"])
            g["cat_saved"] += 1 if r.get("cat_saved") else 0
            g["title_saved"] += 1 if r.get("title_saved") else 0
            # 상품명·태그가 다 들어가면 사이트가 상품정보를 저장완료로 바꾼다.
            # 목록은 점검 당시 스냅샷이라 그 전환이 안 보인다. 표시해준다.
            g["saved"] += 1 if r.get("next_step") == "완료" else 0
        for g in groups.values():
            g["n"] = len(g["rows"])
            g["dl"] = have.get(g["cat"], {}).get("n", 0) if g["cat"] else 0
            g["analyzed"] = g["lcp_code"] in analyzed
            k = kw_have.get(g["lcp_code"]) or {}
            g["kw_tag"] = k.get("tag", 0)
            g["kw_title"] = k.get("title", 0)
            # 정렬·필터가 같은 값을 보게 여기서 한 번만 판정한다.
            #   0 = 미작업 / 1 = 일부 / 2 = 완료
            g["cat_state"] = (0 if not g["cat"]
                              else 2 if g["cat_saved"] >= g["n"] else 1)
            g["tt_state"] = (0 if not g["title_saved"]
                             else 2 if g["title_saved"] >= g["n"] else 1)
        self._lcps = list(groups.values())
        self._render_list()

    def _visible(self):
        kw = self.txt_find.text().strip().lower()
        mode = self.cmb_cat.currentData()
        out = []
        for g in self._lcps:
            if kw and kw not in g["lcp_code"].lower()                     and kw not in g["product_name"].lower():
                continue
            if not self._pass_stage(g, mode):
                continue
            if self.chk_dl.isChecked() and g["dl"]:
                continue
            out.append(g)
        fn, _ = SORT_RULES[self._sort_col]
        out.sort(key=fn, reverse=self._sort_desc)
        return out

    @staticmethod
    def _pass_stage(g, mode):
        """작업 단계 필터. 빈 값이면 전부 통과."""
        if not mode:
            return True
        if mode == "cat_todo":
            return g["cat_state"] == 0
        if mode == "cat_part":
            return g["cat_state"] == 1
        if mode == "cat_done":
            return g["cat_state"] == 2
        if mode == "tt_todo":
            # 카테고리가 끝난 것 중 상품명·태그가 아직인 것 = 지금 바로 할 일
            return g["cat_state"] == 2 and g["tt_state"] == 0
        if mode == "tt_part":
            return g["tt_state"] == 1
        if mode == "saved":
            return g["saved"] >= g["n"]
        if mode == "unsaved":
            return g["saved"] < g["n"]
        return True

    def _sort_by(self, col):
        """머리글 클릭. 같은 칸을 다시 누르면 역순으로 뒤집는다."""
        if col not in SORT_RULES:
            return
        if col == self._sort_col:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_col = col
            self._sort_desc = SORT_RULES[col][1]
        self._render_list()

    def _sync_header(self):
        labels = list(COLUMNS)
        labels[self._sort_col] += " ▼" if self._sort_desc else " ▲"
        self.tbl_lcp.setHorizontalHeaderLabels(labels)

    def _render_list(self):
        rows = self._visible()
        self._sync_header()
        # 정렬·필터로 행이 통째로 다시 그려진다. 보고 있던 LCP 를 놓치지 않게
        # 코드로 기억해뒀다가 되찾는다 (행 번호는 정렬로 바뀌므로 못 쓴다).
        keep = self._detail_code
        self.tbl_lcp.blockSignals(True)
        self.tbl_lcp.setRowCount(len(rows))
        for i, g in enumerate(rows):
            cat = "저장완료" if g["cat"] else "미저장"
            if g["cat"] and g["cat_saved"] < g["n"]:
                cat = f"일부 {g['cat_saved']}/{g['n']}"
            tt = (f"완료 {g['title_saved']}/{g['n']}" if g["title_saved"]
                  else "미작업")
            ana = "완료" if g["analyzed"] else "미분석"
            kw = (f"태그 {g['kw_tag']}/상품명 {g['kw_title']}"
                  if g["kw_tag"] or g["kw_title"] else "-")
            sv = ("저장완료" if g["saved"] >= g["n"]
                  else f"완료 {g['saved']}/{g['n']}" if g["saved"] else "미작업")
            vals = [g["lcp_code"], g["product_name"], str(g["n"]), ana, cat,
                    tt, sv, kw]
            # 사이트와 같은 색 규칙 — 파란색 = 완료, 주황색 = 미작업
            for j, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                if j in (3, 4, 5, 6):
                    done = (g["analyzed"] if j == 3
                            else bool(g["cat"]) if j == 4
                            else bool(g["title_saved"]) if j == 5
                            else g["saved"] >= g["n"])
                    it.setForeground(QColor(OK_COLOR if done else TODO_COLOR))
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                if j == 7 and kw != "-":
                    it.setForeground(QColor("#2e7d32"))
                self.tbl_lcp.setItem(i, j, it)
            self.tbl_lcp.item(i, 0).setData(Qt.UserRole, g["lcp_code"])
        found = next((n for n, g in enumerate(rows)
                      if g["lcp_code"] == keep), -1)
        if found >= 0:
            self.tbl_lcp.selectRow(found)
        else:
            # 보던 LCP 가 필터에서 빠졌다. 그냥 두면 같은 행 번호에 있는 엉뚱한
            # 상품이 오른쪽에 남는다. 선택을 비워 상세를 지운다.
            self.tbl_lcp.clearSelection()
            self.tbl_lcp.setCurrentCell(-1, -1)
        self.tbl_lcp.blockSignals(False)
        if (rows[found]["lcp_code"] if found >= 0 else None) != self._detail_code:
            self._render_detail()
        self.tbl_lcp.resizeColumnsToContents()
        self.tbl_lcp.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch)

        n_l = sum(g["n"] for g in rows)
        n_cat = sum(1 for g in rows if g["cat_state"] == 2)
        n_tt = sum(1 for g in rows if g["cat_state"] == 2 and g["tt_state"] == 0)
        n_saved = sum(1 for g in rows if g["saved"] >= g["n"])
        n_dl = sum(1 for g in rows if g["dl"])
        cids = {g["cat"] for g in rows if g["cat"]}
        self.lbl_sum.setText(
            f"미작업 <b>{n_l:,}</b>건 / LCP <b>{len(rows):,}</b>종"
            f" &nbsp;|&nbsp; 카테고리 저장완료 "
            f"<b style='color:{OK_COLOR}'>{n_cat:,}</b>종 · 미저장 "
            f"<b style='color:{TODO_COLOR}'>{len(rows) - n_cat:,}</b>종"
            f" &nbsp;|&nbsp; 상품명·태그 대기 "
            f"<b style='color:{TODO_COLOR}'>{n_tt:,}</b>종"
            f" &nbsp;|&nbsp; <b style='color:{OK_COLOR}'>이미 저장완료 "
            f"{n_saved:,}</b>종"
            f" &nbsp;|&nbsp; 상품분석 완료 "
            f"<b style='color:{OK_COLOR}'>{sum(1 for g in rows if g['analyzed']):,}</b>종"
            f" &nbsp;|&nbsp; 카테고리 {len(cids):,}개 · 데이터랩 {n_dl:,}종"
            f" · 키워드수집 {sum(1 for g in rows if g['kw_tag'] or g['kw_title']):,}종")

    def current(self):
        r = self.tbl_lcp.currentRow()
        if r < 0:
            return None
        it = self.tbl_lcp.item(r, 0)
        code = it.data(Qt.UserRole) if it else None
        return next((g for g in self._lcps if g["lcp_code"] == code), None)

    # ------------------------------------------------------------------ 상세

    def _render_detail(self):
        g = self.current()
        self._detail_code = g["lcp_code"] if g else None
        for t in (self.tbl_cat, self.tbl_kw, self.tbl_dl):
            t.setRowCount(0)
        if not g:
            self.lbl_prod.setText("")
            return

        r0 = g["rows"][0]
        lcodes = ", ".join(r["l_code"] for r in g["rows"][:10])
        if g["n"] > 10:
            lcodes += f" 외 {g['n'] - 10}건"
        cat_txt = (f"<span style='color:{OK_COLOR}'><b>카테고리 저장완료</b>"
                   f" ({g['cat']})</span>" if g["cat"]
                   else f"<span style='color:{TODO_COLOR}'>"
                        f"<b>카테고리 미저장</b></span>")
        self.lbl_prod.setText(
            f"<b style='font-size:14px'>{g['product_name']}</b>"
            f"<br>{g['lcp_code']} &nbsp;·&nbsp; 미작업 L코드 {g['n']}건"
            f" &nbsp;·&nbsp; {cat_txt}"
            f"<br><span style='color:#607d8b'>희망검색어 : "
            f"{(r0.get('wish_keywords') or '')[:110]}</span>"
            f"<br><span style='color:#607d8b'>{lcodes}</span>")

        cats = db.lcp_categories(g["lcp_code"])
        self.tbl_cat.setRowCount(len(cats))
        for i, c in enumerate(cats):
            saved = str(c.get("code")) == g["cat"]
            vals = [c.get("name") or "", f"{c.get('cnt') or 0:,}",
                    c.get("unit") or "", c.get("code") or "",
                    "저장됨" if saved else ""]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                if saved:
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                    it.setForeground(QColor(OK_COLOR))
                self.tbl_cat.setItem(i, j, it)
        self.tbl_cat.resizeColumnsToContents()
        self.tbl_cat.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch)
        self.lbl_cat.setText(
            f"후보 {len(cats):,}개" +
            (f" · 저장된 값 {g['cat']}" if g["cat"] else " · 아직 저장 안 됨"))

        self._render_kw()
        self._render_dl()
        self._render_ck()

    def _render_kw(self):
        g = self.current()
        self.tbl_kw.setRowCount(0)
        if not g:
            return
        kws = db.lcp_keywords(g["lcp_code"], self.cmb_src.currentData())
        self.tbl_kw.setRowCount(len(kws))
        for i, k in enumerate(kws):
            vals = [k["keyword"], SRC_LABEL.get(k["source"], k["source"]),
                    "" if k["views"] is None else f"{k['views']:,}"]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                # 로하스 지침 — 조회수 1000 이상 태그는 쓰지 않는다
                if j == 2 and k["views"] and k["views"] >= 1000:
                    it.setForeground(QColor("#c62828"))
                self.tbl_kw.setItem(i, j, it)
        self.tbl_kw.resizeColumnsToContents()
        self.tbl_kw.horizontalHeader().setStretchLastSection(True)
        self.lbl_kw.setText(f"{len(kws):,}개")

    def _render_dl(self):
        g = self.current()
        self.tbl_dl.setRowCount(0)
        self.btn_dl_one.setEnabled(bool(g and g["cat"]) and self._thread is None)
        if not g:
            self.lbl_dl.setText("")
            return
        if not g["cat"]:
            self.lbl_dl.setText(
                "카테고리가 저장돼야 데이터랩을 부를 수 있습니다.")
            return

        rows = db.datalab_keywords(g["cat"])
        mine = {k["keyword"] for k in db.lcp_keywords(g["lcp_code"])}
        self.tbl_dl.setRowCount(len(rows))
        dup = 0
        for i, r in enumerate(rows):
            has = r["keyword"] in mine
            dup += 1 if has else 0
            for j, v in enumerate([r["rank"], r["keyword"],
                                   "보유" if has else ""]):
                it = QTableWidgetItem(str(v))
                if has:
                    it.setForeground(QColor("#2e7d32"))
                self.tbl_dl.setItem(i, j, it)
        self.tbl_dl.resizeColumnsToContents()
        self.tbl_dl.horizontalHeader().setStretchLastSection(True)
        self.lbl_dl.setText(
            f"cid {g['cat']} · {len(rows):,}개"
            + (f" (로하스가 이미 가진 것 {dup:,}개, 새 후보 "
               f"{len(rows) - dup:,}개)" if rows else " — 아직 수집 안 됨"))

    def _render_ck(self):
        g = self.current()
        self.tbl_ck.setRowCount(0)
        self.btn_ck_one.setEnabled(bool(g and g["cat"]) and self._thread is None)
        if not g:
            self.lbl_ck.setText("")
            return
        if not g["cat"]:
            self.lbl_ck.setText("카테고리가 저장돼야 키워드 표가 나옵니다.")
            return

        by_lcp = self.cmb_ck_scope.currentData() == "lcp"
        rows = db.cat_keywords(
            cid=None if by_lcp else g["cat"],
            lcp_code=g["lcp_code"] if by_lcp else None,
            kind=self.cmb_ck_kind.currentData(),
            max_views=1000 if self.chk_ck_low.isChecked() else 0,
            usable_only=self.chk_ck_ok.isChecked())

        self.tbl_ck.setRowCount(len(rows))
        n_tag = 0
        for i, r in enumerate(rows):
            is_tag = r["kind"] == "tag"
            n_tag += 1 if is_tag else 0
            label = " ".join(x for x in [
                "태그사전" if r.get("is_dict") else "",
                "추천" if r.get("is_rec") else ""] if x)
            vals = [r["keyword"], "태그" if is_tag else "상품명",
                    f"{r['views']:,}" if r["views"] else "",
                    f"{r['lcp_count']:,}", r.get("banned") or "", label]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                if j == 1:
                    it.setForeground(QColor("#6a1b9a" if is_tag else "#00695c"))
                if j == 2 and r["views"] and r["views"] >= 1000:
                    it.setForeground(QColor("#c62828"))
                if j == 3 and r["lcp_count"] >= 3:
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                    it.setForeground(QColor("#2e7d32"))
                if j == 4 and v:
                    it.setForeground(QColor("#c62828"))
                self.tbl_ck.setItem(i, j, it)
        self.tbl_ck.resizeColumnsToContents()
        self.tbl_ck.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch)

        st = db.cat_keyword_stats(None if by_lcp else g["cat"])
        src = "이 LCP" if by_lcp else f"cid {g['cat']}"
        self.lbl_ck.setText(
            f"{src} · 태그 {n_tag:,} / 상품명 {len(rows) - n_tag:,}"
            + (f" (수집 LCP {st.get('tag', {}).get('lcps', 0):,}종)"
               if not by_lcp else ""))

    def _copy_dl(self):
        from PySide6.QtWidgets import QApplication

        n = self.tbl_dl.rowCount()
        if not n:
            QMessageBox.information(self, "안내", "복사할 키워드가 없습니다.")
            return
        words = [self.tbl_dl.item(i, 1).text() for i in range(n)
                 if self.tbl_dl.item(i, 1)]
        QApplication.clipboard().setText(chr(10).join(words))
        QMessageBox.information(self, "복사 완료", f"{len(words):,}개 복사")

    # ------------------------------------------------------------------ 수집

    def _collect(self, all_cats: bool):
        if self._thread:
            return
        if all_cats:
            seen = {}
            for g in self._visible():
                if g["cat"]:
                    seen.setdefault(g["cat"], _cat_name(g))
            cids = list(seen.items())
            if not cids:
                QMessageBox.information(
                    self, "안내", "카테고리가 저장된 LCP 가 없습니다.")
                return
            if QMessageBox.question(
                    self, "데이터랩 수집",
                    f"카테고리 {len(cids):,}개의 인기키워드를 받아옵니다.\n"
                    f"(카테고리당 최대 500개) 진행할까요?") != QMessageBox.Yes:
                return
        else:
            g = self.current()
            if not g or not g["cat"]:
                return
            cids = [(g["cat"], _cat_name(g))]

        self._log(f"데이터랩 수집 시작 — 카테고리 {len(cids):,}개")
        self._run(DatalabWorker(cids), self._on_collected)

    def _on_collected(self, res):
        self._log(f"카테고리 {res.get('ok', 0):,}개 / "
                  f"키워드 {res.get('keywords', 0):,}개 저장")
        self.reload()

    def _run_analysis(self):
        if self._thread:
            return
        folder = db.get_job_folder()
        if not folder:
            QMessageBox.warning(self, "안내", "작업폴더가 지정되지 않았습니다.")
            return
        todo = sum(1 for g in self._lcps if not g["analyzed"])
        msg = [f"작업폴더 : {folder}",
               f"미분석 LCP : {todo:,}종",
               f"배치 : {config.ANALYSIS_BATCH}건씩 요청 후 완료 대기",
               "",
               "실제로 상품분석 작업이 생성됩니다. 진행할까요?"]
        if QMessageBox.question(
                self, "ALL 상품분석", chr(10).join(msg)) != QMessageBox.Yes:
            return
        self._log(f"ALL 상품분석 시작 - 미분석 {todo:,}종")
        self._run(AnalysisWorker(
            folder_name=folder,
            batch_size=config.ANALYSIS_BATCH,
            poll_interval=config.ANALYSIS_POLL,
            batch_timeout=config.ANALYSIS_TIMEOUT), self._on_analysis)

    def _on_analysis(self, st):
        self._log(f"분석 완료 {st.get('done', 0):,} / 이미완료 "
                  f"{st.get('already', 0):,} / 오류 {st.get('error', 0):,}")
        self.reload()

    def _sync_status(self):
        """상품정보 상태를 사이트 현재값으로 맞춘다."""
        if self._thread:
            return
        self._log("상태 동기화 — 사이트에서 현재 상태를 읽습니다")
        self._run(StatusSyncWorker(db.get_job_folder()), self._on_sync)

    def _on_sync(self, res):
        n = res.get("total", 0)
        c = res.get("counts") or {}
        self._log("사이트 현황 — " + " / ".join(
            f"{k} {v:,}행" for k, v in c.items()))
        if n:
            self._log(f"DB {n:,}건을 사이트 상태로 맞췄습니다. 목록을 다시 읽습니다.")
        self.reload()

    def _tag_auto(self):
        """
        키워드 자동추가. 선택한 LCP(Ctrl 이면 목록 전부)의 카테고리로
        데이터랩 인기키워드를 받아 태그로 넣는다.

        로하스 태그 후보는 저장된 카테고리에서 만들어지므로, 카테고리가
        틀리면 후보도 틀린다. 데이터랩은 네이버 cid 로 직접 물어 그 문제를
        비껴간다 (2026-09-05 모형 CCTV 건으로 실측).
        """
        if self._thread:
            return
        from PySide6.QtWidgets import QApplication
        from ..lohas import datalab

        if not datalab.base():
            QMessageBox.warning(
                self, "데이터랩",
                "데이터랩 주소가 없습니다. 사내망이거나 SSH 터널이 필요합니다.")
            return

        bulk = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
        if bulk:
            items = [g for g in self._visible() if g["cat"]]
        else:
            g = self.current()
            if not g:
                QMessageBox.information(self, "안내", "LCP 를 먼저 고르세요.")
                return
            if not g["cat"]:
                QMessageBox.warning(
                    self, "안내",
                    "카테고리가 저장돼야 태그를 넣을 수 있습니다."
                    + chr(10) + "「카테고리 검토」 에서 먼저 저장하세요.")
                return
            items = [g]
        if not items:
            QMessageBox.information(self, "안내", "대상이 없습니다.")
            return

        over = self.chk_tag_over.isChecked()
        n_rows = sum(g["n"] for g in items)
        msg = [f"{len(items):,}종 / L코드 {n_rows:,}건에 태그를 넣습니다.",
               "",
               "데이터랩에서 그 카테고리의 인기키워드와 조회수를 받아",
               "금지어를 걸러내고 로하스 태그검증을 거친 뒤",
               "조회수 1000 미만을 먼저 10개까지 채웁니다.",
               ""]
        if over:
            msg.append("[주의] 이미 태그가 있는 L코드도 덮어씁니다.")
        else:
            msg.append("이미 태그가 있는 L코드는 건드리지 않습니다.")
        msg += ["", "진행할까요?"]
        if QMessageBox.question(self, "키워드 자동추가",
                                chr(10).join(msg)) != QMessageBox.Yes:
            return
        self._log(f"키워드 자동추가 시작 - {len(items):,}종")
        self._run(TagAutoWorker(items, overwrite=over,
                                folder_name=db.get_job_folder()),
                  self._on_tag_auto)

    def _on_tag_auto(self, res):
        self._log(f"키워드 자동추가 완료 - LCP {res.get('lcps', 0):,}종 / "
                  f"태그 저장 {res.get('ok', 0):,}건"
                  + (f" / 건너뜀 {res['skip']:,}건" if res.get("skip") else "")
                  + (f" / 실패 {res['fail']:,}건" if res.get("fail") else ""))
        self.reload()

    def _collect_kw(self):
        if self._thread:
            return
        from ..lohas import cat_keyword

        rows = cat_keyword.targets(db, db.get_job_folder())
        if not rows:
            QMessageBox.information(
                self, "안내", "새로 수집할 LCP 가 없습니다. (모두 수집됨)")
            return
        msg = [f"카테고리가 저장된 LCP {len(rows):,}종의",
               "태그·상품명 후보를 긁어옵니다. (건당 0.4초쯤)",
               "읽기만 하고 사이트에 저장하지 않습니다.",
               "",
               "진행할까요?"]
        if QMessageBox.question(
                self, "카테고리 키워드 수집",
                chr(10).join(msg)) != QMessageBox.Yes:
            return
        self._log(f"카테고리 키워드 수집 시작 - {len(rows):,}종")
        self._run(CatKeywordWorker(db.get_job_folder()), self._on_kw)

    def _collect_kw_one(self):
        g = self.current()
        if self._thread or not g or not g["cat"]:
            return
        self._log(f"{g['lcp_code']} 키워드 수집")
        self._run(CatKeywordWorker(db.get_job_folder(), only=g["lcp_code"],
                                   redo=True), self._on_kw)

    def _on_kw(self, res):
        self._log(f"수집 {res.get('ok', 0):,}종 · 태그 {res.get('tag', 0):,}개"
                  f" · 상품명 {res.get('title', 0):,}개")
        self.reload()

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
        for b in (self.btn_dl_all, self.btn_dl_one, self.btn_analysis,
                  self.btn_kw, self.btn_ck_one, self.btn_tag_auto,
                  self.btn_sync):
            b.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self._thread.start()

    def _done(self):
        if self._thread:
            self._thread.quit()
            self._thread.wait(3000)
        self._thread = None
        self._worker = None
        for b in (self.btn_dl_all, self.btn_analysis, self.btn_kw,
                  self.btn_tag_auto, self.btn_sync):
            b.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.progress.setFormat("완료")
        self._render_dl()
        self._render_ck()

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


def _cat_name(g: dict) -> str:
    """저장된 카테고리의 이름. 후보 목록에서 찾아 쓴다."""
    for c in db.lcp_categories(g["lcp_code"]):
        if str(c.get("code")) == g["cat"]:
            return c.get("name") or ""
    return ""
