"""로하스 오토 메인 윈도우."""
import re
from datetime import datetime

from PySide6.QtCore import Qt, QThread, QTimer
from PySide6.QtCharts import (QBarCategoryAxis, QBarSeries, QBarSet,
                              QChart, QChartView, QLineSeries,
                              QValueAxis)
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QProgressBar, QPushButton, QSplitter, QTabWidget, QTableWidget,
    QTableWidgetItem, QTextEdit, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget, QDialog, QDialogButtonBox, QStackedWidget,
    QButtonGroup,
)

from .. import config, db
from ..lohas.monitors import list_monitors
from .monitor_worker import MonitorWorker
from .category_page import CategoryPage
from .category_review_page import CategoryReviewPage
from .todo_page import TodoPage
from .category_fix_page import CategoryFixPage
from .tag_review_page import TagReviewPage
from .mini_window import MiniWindow
from .product_page import ProductPage
from .workers import (AnalysisWorker, BasicCollectWorker, DumpWorker,
                      FolderScanWorker, InspectWorker,
                      LcodeStatusWorker, SampleWorker)

# 메인상품 폴더 = 앞 번호가 51~59 로 시작 (51., 541., 594., 5952., 598. ...)
MAIN_FOLDER_RE = re.compile(r"^\s*5[1-9]\d*\s*\.")


def is_main_folder(name: str) -> bool:
    return bool(MAIN_FOLDER_RE.match(name or ""))


STATUS_COLORS = {
    "이미지승인완료": QColor("#1565c0"),
    "저장완료": QColor("#1565c0"),
    "이미지작업": QColor("#6a1b9a"),
    "보류": QColor("#6a1b9a"),
    "제외": QColor("#616161"),
    "미작업": QColor("#e65100"),
}


# 상단 탭에는 매일 쓰는 것만 둔다. 검토·수정용은 [검토중] 메뉴로 뺐다
# (2026-09-05 사용자 요청). 인덱스는 stack 에 넣은 순서와 같아야 한다.
NAV_TABS = ["대시보드", "상품정보", "미작업목록"]
NAV_INDEX = {"대시보드": 0, "상품정보": 1, "미작업목록": 4}
REVIEW_MENU = [("카테고리", 2), ("카테고리 검토", 3),
               ("카테고리 수정", 5), ("태그 검수", 6)]


class StatCard(QFrame):
    """요약 수치 카드."""

    def __init__(self, title: str, accent: str = "#37474f"):
        super().__init__()
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            f"QFrame {{ border:1px solid #d0d7de; border-radius:6px;"
            f" background:#ffffff; }}"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(2)

        self.lbl_title = QLabel(title)
        self.lbl_title.setStyleSheet("color:#57606a; border:none;")

        self.lbl_value = QLabel("-")
        f = QFont()
        f.setPointSize(18)
        f.setBold(True)
        self.lbl_value.setFont(f)
        self.lbl_value.setStyleSheet(f"color:{accent}; border:none;")

        lay.addWidget(self.lbl_title)
        lay.addWidget(self.lbl_value)

    def set_value(self, value):
        self.lbl_value.setText(str(value))


class FolderStatsDialog(QDialog):
    """폴더의 일별 작업량 그래프 (대표이미지 승인 / 저장완료)."""

    def __init__(self, folder_name: str, daily: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"작업량 통계 - {folder_name}")
        self.resize(980, 660)
        lay = QVBoxLayout(self)

        since = daily[0]["day"]
        t_img = sum(r["img_delta"] for r in daily)
        t_info = sum(r["info_delta"] for r in daily)
        t_an = sum(r["analyzed_delta"] for r in daily)
        head = QLabel(
            f"<b style='font-size:14px'>{folder_name}</b><br>"
            f"<span style='color:#546e7a'>통계 시작 {since} · {len(daily)}일 기록"
            f" &nbsp;|&nbsp; 누적 이미지승인 <b style='color:#1565c0'>{t_img:,}</b>개"
            f" · 저장완료 <b style='color:#2e7d32'>{t_info:,}</b>개"
            f" · 상품분석 <b style='color:#ef6c00'>{t_an:,}</b>개</span>")
        lay.addWidget(head)

        # ---- 일별 작업량 막대 ----
        chart = QChart()
        chart.setTitle("일별 작업량")
        chart.legend().setAlignment(Qt.AlignBottom)

        s_img = QBarSet("대표이미지 승인"); s_img.setColor(QColor("#1565c0"))
        s_info = QBarSet("저장완료");      s_info.setColor(QColor("#2e7d32"))
        s_an = QBarSet("상품분석");        s_an.setColor(QColor("#ef6c00"))
        cats = []
        for r in daily:
            s_img.append(r["img_delta"])
            s_info.append(r["info_delta"])
            s_an.append(r["analyzed_delta"])
            cats.append(r["day"][5:])          # MM-DD

        ser = QBarSeries()
        ser.append(s_img); ser.append(s_info); ser.append(s_an)
        chart.addSeries(ser)

        ax_x = QBarCategoryAxis(); ax_x.append(cats)
        chart.addAxis(ax_x, Qt.AlignBottom); ser.attachAxis(ax_x)
        ax_y = QValueAxis(); ax_y.setLabelFormat("%d")
        top = max(1, max(max(r["img_delta"], r["info_delta"],
                             r["analyzed_delta"]) for r in daily))
        ax_y.setRange(0, top * 1.2)
        chart.addAxis(ax_y, Qt.AlignLeft); ser.attachAxis(ax_y)

        view = QChartView(chart)
        view.setRenderHint(QPainter.Antialiasing)
        view.setMinimumHeight(300)
        lay.addWidget(view, 1)

        # ---- 일별 표 ----
        tbl = QTableWidget(len(daily), 7)
        tbl.setHorizontalHeaderLabels(
            ["날짜", "전체수량", "이미지승인완료", "(당일)", "저장완료",
             "(당일)", "미완료"])
        tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tbl.verticalHeader().setVisible(False)
        for i, r in enumerate(reversed(daily)):
            vals = [r["day"], f"{r['total_rows']:,}",
                    f"{r['img_done_rows']:,}", f"+{r['img_delta']:,}",
                    f"{r['info_save_rows']:,}", f"+{r['info_delta']:,}",
                    f"{r['info_todo_rows']:,}"]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                if j == 3 and r["img_delta"]:
                    it.setForeground(QColor("#1565c0"))
                if j == 5 and r["info_delta"]:
                    it.setForeground(QColor("#2e7d32"))
                tbl.setItem(i, j, it)
        tbl.resizeColumnsToContents()
        tbl.horizontalHeader().setStretchLastSection(True)
        tbl.setMaximumHeight(200)
        lay.addWidget(tbl)

        btn = QDialogButtonBox(QDialogButtonBox.Close)
        btn.rejected.connect(self.reject)
        lay.addWidget(btn)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("로하스 오토 - 상품정보관리 폴더 수량 점검")
        self.resize(1520, 880)

        self._thread = None
        self._worker = None
        self._last_items = []
        self._last_cells = []
        self._last_info_todo = 0
        self._monitor_thread = None
        self._monitor_worker = None

        db.init_db()
        self._build_ui()
        self._reload_folders()
        self._reload_history()

        self._log(f"자체 DB(SQLite) : {config.SQLITE_PATH}")
        self._log(db.mysql_status())
        mons = list_monitors()
        if mons:
            self._log(f"모니터 {len(mons)}개 : "
                      + " / ".join(m["label"] for m in mons))
        if not config.credentials_ok():
            self._log("⚠ .env 에 LOHAS_ID / LOHAS_PW 가 없습니다.")

        # 창이 다 그려진 뒤에 자동점검을 켠다. 생성 도중에 켜면 아직 만들어지지
        # 않은 현황판을 건드리게 된다.
        QTimer.singleShot(800, self._autostart_monitor)

    def _autostart_monitor(self):
        """프로그램을 켜면 자동점검을 기본으로 시작한다.
        작업폴더가 없으면 안내창 대신 로그만 남기고 조용히 넘어간다."""
        if self._monitor_thread is not None:
            return
        if not db.get_job_folder():
            self._log("자동점검 대기 : 작업폴더가 지정되지 않았습니다.")
            return
        self.chk_monitor.setChecked(True)      # toggled -> _start_monitor

    # ------------------------------------------------------------------ UI

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._build_menubar()
        outer.addWidget(self._build_menu())

        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)

        dash = QWidget()
        root = QVBoxLayout(dash)
        root.setContentsMargins(10, 10, 10, 10)
        self.stack.addWidget(dash)                 # 0 : 대시보드

        self.page_product = ProductPage(self)
        self.page_product.btn_status.clicked.connect(self.on_collect_lcode)
        self.page_product.btn_basic.clicked.connect(self.on_collect_basic)
        self.stack.addWidget(self.page_product)    # 1 : 상품정보

        self.page_category = CategoryPage(self)
        self.stack.addWidget(self.page_category)   # 2 : 카테고리

        self.page_cat_review = CategoryReviewPage(self)
        self.stack.addWidget(self.page_cat_review)  # 3 : 카테고리 검토

        self.page_todo = TodoPage(self)
        self.stack.addWidget(self.page_todo)        # 4 : 미작업목록

        self.page_cat_fix = CategoryFixPage(self)
        self.stack.addWidget(self.page_cat_fix)     # 5 : 카테고리 수정

        self.page_tag_review = TagReviewPage(self)
        self.stack.addWidget(self.page_tag_review)  # 6 : 태그 검수

        root.addWidget(self._build_topbar())

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_folder_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 6)
        splitter.setSizes([560, 800])   # 폴더명이 잘리지 않을 만큼 좌측 확보
        root.addWidget(splitter, 1)

        self.lbl_task = QLabel("")
        self.lbl_task.setStyleSheet("color:#1565c0; font-weight:bold;")
        root.addWidget(self.lbl_task)

        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.setFormat("대기 중")
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        root.addWidget(self.progress)

    def _build_menubar(self):
        """
        창 맨 위 메뉴줄. 파일 / 설정 / 검토중 / 종료.

        검토·수정용 화면은 매일 쓰는 게 아니라 상단 탭에서 빼고 여기 넣었다.
        탭은 대시보드·상품정보·미작업목록 셋만 남긴다.
        """
        mb = self.menuBar()

        m = mb.addMenu("파일(&F)")
        a = m.addAction("작업폴더 새로고침")
        a.triggered.connect(self.on_scan_folders)
        a = m.addAction("데이터 폴더 열기")
        a.triggered.connect(self._open_data_dir)
        m.addSeparator()
        a = m.addAction("미니 모드로 전환	Ctrl+M")
        a.setShortcut("Ctrl+M")
        a.triggered.connect(self.to_mini)

        m = mb.addMenu("설정(&S)")
        a = m.addAction("접속 환경 점검")
        a.triggered.connect(self._check_env)
        a = m.addAction(".env 열기")
        a.triggered.connect(self._open_env)

        m = mb.addMenu("검토중(&R)")
        for name, idx in REVIEW_MENU:
            a = m.addAction(name)
            a.triggered.connect(lambda _=False, i=idx: self._go_page(i))

        m = mb.addMenu("종료(&X)")
        a = m.addAction("프로그램 종료")
        a.triggered.connect(self.close)

    def _open_data_dir(self):
        import subprocess
        subprocess.Popen(["explorer", str(config.SQLITE_PATH.parent)])

    def _open_env(self):
        import subprocess
        subprocess.Popen(["notepad", str(config.ROOT / ".env")])

    def _check_env(self):
        from ..lohas import datalab
        msg = [f"위치       : {config.net_profile()}",
               db.mysql_status(),
               f"데이터랩   : {datalab.base() or '꺼짐'}",
               f"SQLite     : {config.SQLITE_PATH}",
               f"작업폴더   : {db.get_job_folder() or '(미지정)'}"]
        QMessageBox.information(self, "접속 환경", chr(10).join(msg))

    def _build_menu(self) -> QWidget:
        """상단 메뉴 — 대시보드 / 상품정보 전환."""
        bar = QWidget()
        bar.setStyleSheet(
            "QWidget { background:#263238; }"
            "QPushButton { background:transparent; color:#b0bec5; border:none;"
            " padding:9px 22px; font-size:13px; font-weight:bold; }"
            "QPushButton:hover { color:#ffffff; background:#37474f; }"
            "QPushButton:checked { color:#ffffff; background:#0d47a1; }")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(8, 0, 8, 0)
        lay.setSpacing(2)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        for i, name in enumerate(
                NAV_TABS):
            b = QPushButton(name)
            b.setCheckable(True)
            b.setChecked(i == 0)
            self.nav_group.addButton(b, i)
            lay.addWidget(b)
        self.nav_group.idClicked.connect(
            lambda i: self._go_page(NAV_INDEX[NAV_TABS[i]]))

        lay.addStretch(1)
        btn_mini = QPushButton("🗕 미니")
        btn_mini.setToolTip(
            "오늘 작업량 · 저장완료 · ALL 상품분석만 남긴 작은 창으로 바꿉니다."
            + chr(10) + "Ctrl+M 로도 됩니다. 작은 창의 「크게」로 돌아옵니다.")
        btn_mini.setCheckable(False)
        btn_mini.clicked.connect(self.to_mini)
        lay.addWidget(btn_mini)

        self.lbl_nav_info = QLabel("")
        self.lbl_nav_info.setStyleSheet("color:#78909c; padding-right:10px;")
        lay.addWidget(self.lbl_nav_info)
        return bar

    # ------------------------------------------------------------ 미니 모드

    def to_mini(self):
        """
        작은 창으로 바꾼다. 큰 창은 닫지 않고 숨기기만 한다 —
        자동점검·진행 중인 워커가 그대로 살아 있어야 하기 때문이다.
        """
        if getattr(self, "_mini", None) is None:
            self._mini = MiniWindow(self)
        self._mini.refresh()
        # 큰 창이 있던 자리 오른쪽 위에 띄운다
        g = self.geometry()
        self._mini.move(g.x() + max(0, g.width() - self._mini.width() - 40),
                        g.y() + 60)
        self._mini.show()
        self._mini.raise_()
        self.hide()

    def to_big(self):
        """큰 창으로 돌아온다."""
        if getattr(self, "_mini", None) is not None:
            self._mini.hide()
        self.show()
        self.raise_()
        self.activateWindow()

    def _sync_mini(self):
        """점검이 끝날 때마다 작은 창 숫자도 맞춘다."""
        m = getattr(self, "_mini", None)
        if m is not None and m.isVisible():
            m.refresh()

    def _go_page(self, idx: int):
        self.stack.setCurrentIndex(idx)
        if idx == 1:
            self.page_product.reload_folders()
            self.page_product.reload()
        elif idx == 2:
            if not self.page_category._cats:
                self.page_category.reload()
        elif idx == 3:
            self.page_cat_review.refresh_summary()
        elif idx == 4:
            self.page_todo.reload()
        elif idx == 6:
            self.page_tag_review.reload()

    def _build_topbar(self) -> QWidget:
        box = QGroupBox("접속 / 실행 설정")
        lay = QHBoxLayout(box)

        lay.addWidget(QLabel(f"계정 : <b>{config.masked_id()}</b> (.env)"))
        lay.addSpacing(16)

        self.chk_http = QCheckBox("빠른조회(HTTP)")
        self.chk_http.setChecked(True)
        self.chk_http.setToolTip(
            "저장된 로그인 쿠키로 검색 요청만 직접 보냅니다. 브라우저가 뜨지 않습니다."
            + chr(10) + "검색 1회 60초 → 0.3~2초, 1000행 상한도 없습니다."
            + chr(10) + "쿠키가 없거나 만료되면 자동으로 브라우저 로그인 후 갱신합니다."
        )
        lay.addWidget(self.chk_http)

        self.chk_headless = QCheckBox("헤드리스(창 없이 실행)")
        self.chk_headless.setChecked(config.HEADLESS)
        self.chk_headless.setToolTip(
            "체크하면 브라우저 창 없이 실행합니다.\n"
            "헤드리스에서는 로그인 입력이 클립보드 대신 직접입력 방식으로 바뀝니다."
        )
        lay.addWidget(self.chk_headless)

        lay.addWidget(QLabel("모니터"))
        self.cmb_monitor = QComboBox()
        self.cmb_monitor.addItem("기본(자동)", 0)
        for m in list_monitors():
            self.cmb_monitor.addItem(m["label"], m["index"])
        idx = self.cmb_monitor.findData(config.BROWSER_MONITOR)
        self.cmb_monitor.setCurrentIndex(idx if idx >= 0 else 0)
        self.cmb_monitor.setToolTip(
            "브라우저 창을 띄울 모니터를 고릅니다. "
            "작업 중인 모니터를 피해서 지정하면 방해받지 않습니다."
        )
        lay.addWidget(self.cmb_monitor)

        lay.addWidget(QLabel("페이지당"))
        self.cmb_page_size = QComboBox()
        self.cmb_page_size.addItems(["1000", "500", "200", "100", "50"])
        self.cmb_page_size.setCurrentText(str(config.PAGE_SIZE))
        lay.addWidget(self.cmb_page_size)

        lay.addStretch(1)

        self.btn_scan_folders = QPushButton("① 마스터 폴더 스캔")
        self.btn_scan_folders.setToolTip(
            "상품정보관리 페이지의 '마스터' 콤보에서 폴더명을 전부 읽어 DB에 저장합니다."
        )
        self.btn_scan_folders.clicked.connect(self.on_scan_folders)
        lay.addWidget(self.btn_scan_folders)

        self.btn_dump = QPushButton("페이지 구조 덤프")
        self.btn_dump.setToolTip(
            "상태 텍스트/색상이 예상과 다를 때, 실제 그리드 구조를 파일로 덤프합니다."
        )
        self.btn_dump.clicked.connect(self.on_dump)
        lay.addWidget(self.btn_dump)

        self.btn_stop = QPushButton("중단")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.on_stop)
        lay.addWidget(self.btn_stop)

        return box

    def _build_folder_panel(self) -> QWidget:
        box = QGroupBox("작업리스트 (마스터폴더)")
        lay = QVBoxLayout(box)

        # --- 1줄 : 모두표시 + 검색 ---
        row1 = QHBoxLayout()
        self.chk_main_only = QCheckBox("메인상품(51~59)")
        self.chk_main_only.setChecked(True)          # 기본 체크
        self.chk_main_only.setToolTip(
            "폴더번호가 51~59 로 시작하는 광고진행 폴더만 표시합니다."
            + chr(10) + "예) 51. 광고진행중-OOO ~ 598. 광고진행중-OOO"
        )
        self.chk_main_only.stateChanged.connect(self._on_filter_changed)
        row1.addWidget(self.chk_main_only)

        self.chk_show_all = QCheckBox("모두 표시")
        self.chk_show_all.setToolTip(
            "체크하면 스캔한 전체 폴더가 보입니다. "
            "해제하면 마스터폴더로 지정한 폴더만 보입니다."
        )
        self.chk_show_all.stateChanged.connect(self._on_filter_changed)
        row1.addWidget(self.chk_show_all)

        self.txt_filter = QLineEdit()
        self.txt_filter.setPlaceholderText("폴더명 검색 (예: 비트마인드)")
        self.txt_filter.textChanged.connect(self._apply_filter)
        row1.addWidget(self.txt_filter, 1)
        lay.addLayout(row1)

        # --- 2줄 : 나머지 폴더 드롭다운 + 마스터폴더로 지정 ---
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("폴더 추가"))
        self.cmb_pool = QComboBox()
        self.cmb_pool.setMinimumWidth(280)
        self.cmb_pool.setToolTip("아직 마스터폴더가 아닌 폴더 목록입니다.")
        row2.addWidget(self.cmb_pool, 1)

        self.btn_add_master = QPushButton("② 마스터폴더로 지정")
        self.btn_add_master.clicked.connect(self.on_add_master_folder)
        row2.addWidget(self.btn_add_master)
        lay.addLayout(row2)

        # --- 테이블 ---
        self.tbl_folders = QTreeWidget()
        self.tbl_folders.setColumnCount(5)
        self.tbl_folders.setHeaderLabels(
            ["폴더명", "전체", "이미지승인완료", "저장완료", "작업대상"])
        self.tbl_folders.setRootIsDecorated(True)     # 펼침 화살표 표시
        self.tbl_folders.setAlternatingRowColors(True)
        self.tbl_folders.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tbl_folders.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_folders.setUniformRowHeights(True)
        self.tbl_folders.setExpandsOnDoubleClick(False)
        hh = self.tbl_folders.header()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setStretchLastSection(True)
        self.tbl_folders.itemExpanded.connect(self._on_folder_expanded)
        self.tbl_folders.itemDoubleClicked.connect(self.on_set_job_folder)
        lay.addWidget(self.tbl_folders, 1)

        # --- 3줄 : 작업폴더 지정 / 마스터 해제 ---
        row3 = QHBoxLayout()
        self.btn_set_job = QPushButton("③ 작업폴더로 지정")
        self.btn_set_job.setToolTip(
            "선택한 폴더를 실제 점검·작업 대상(작업폴더)으로 지정합니다. (더블클릭도 동일)"
        )
        self.btn_set_job.clicked.connect(self.on_set_job_folder)
        row3.addWidget(self.btn_set_job)

        self.btn_del_master = QPushButton("마스터폴더 해제")
        self.btn_del_master.clicked.connect(self.on_remove_master_folder)
        row3.addWidget(self.btn_del_master)

        self.btn_stats = QPushButton("📊 통계")
        self.btn_stats.setToolTip("선택한 폴더의 일별 작업량 그래프를 봅니다.")
        self.btn_stats.clicked.connect(self.on_show_stats)
        row3.addWidget(self.btn_stats)
        row3.addStretch(1)
        lay.addLayout(row3)

        return box

    def _build_right_panel(self) -> QWidget:
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)

        # ---- 작업폴더 + 실행 ----
        box = QGroupBox("작업폴더 점검")
        top = QHBoxLayout(box)
        self.lbl_work = QLabel("작업폴더 : (미지정)")
        f = QFont()
        f.setPointSize(11)
        f.setBold(True)
        self.lbl_work.setFont(f)
        top.addWidget(self.lbl_work, 1)

        self.spn_interval = QComboBox()
        self.spn_interval.addItems(["10", "20", "30", "60", "120"])
        self.spn_interval.setCurrentText("30")
        self.spn_interval.setToolTip("자동 점검 주기(초)")
        top.addWidget(QLabel("주기(초)"))
        top.addWidget(self.spn_interval)

        self.chk_monitor = QCheckBox("자동점검")
        self.chk_monitor.setStyleSheet(
            "QCheckBox { font-weight:bold; color:#00695c; font-size:14px; }")
        self.chk_monitor.setToolTip(
            "체크하면 지정 주기마다 작업폴더 1개를 백그라운드로 점검해 현황을 갱신합니다."
            + chr(10) + "미분석 LCP 목록도 함께 저장되어 ALL 상품분석이 바로 씁니다."
        )
        self.chk_monitor.toggled.connect(self.on_monitor_toggled)
        top.addWidget(self.chk_monitor)

        self.lbl_monitor_state = QLabel("● 정지")
        self.lbl_monitor_state.setStyleSheet("color:#9e9e9e; font-weight:bold;")
        top.addWidget(self.lbl_monitor_state)

        self.btn_quick = QPushButton("⚡ 빠른 점검")
        self.btn_quick.setMinimumHeight(34)
        self.btn_quick.setToolTip(
            "작업대상 한 칸(대표이미지 승인완료 + 상품정보 미작업)만 조회합니다."
            + chr(10) + "약 1~2분. 나머지 상태별 수량은 채워지지 않습니다."
        )
        self.btn_quick.clicked.connect(lambda: self.on_inspect(quick=True))
        top.addWidget(self.btn_quick)

        self.btn_sample = QPushButton("🧪 샘플 1건 분석")
        self.btn_sample.setMinimumHeight(34)
        self.btn_sample.setStyleSheet("font-weight:bold; color:#00838f;")
        self.btn_sample.setToolTip(
            "작업대상 1건을 읽어 태그·상품명으로 무엇을 고를지 계산해 보여줍니다."
            + chr(10) + "실제 저장은 하지 않습니다. 브라우저도 뜨지 않습니다(HTTP)."
        )
        self.btn_sample.clicked.connect(self.on_run_sample)
        top.addWidget(self.btn_sample)

        self.btn_analysis = QPushButton("🔬 ALL 상품분석")
        self.btn_analysis.setMinimumHeight(34)
        self.btn_analysis.setStyleSheet("font-weight:bold; color:#b71c1c;")
        self.btn_analysis.setToolTip(
            "작업폴더의 '대표이미지 승인완료 + 상품정보 미작업' 상품을"
            + chr(10) + "LCP 단위로 골라 상품분석을 실행합니다."
            + chr(10) + "이미 분석한 LCP 는 자동으로 건너뜁니다."
        )
        self.btn_analysis.clicked.connect(self.on_run_analysis)
        top.addWidget(self.btn_analysis)

        self.btn_inspect = QPushButton("④ 전체 점검 (12칸)")
        self.btn_inspect.setMinimumHeight(34)
        self.btn_inspect.setToolTip(
            "대표이미지 3상태 x 상품정보 4상태 = 12칸을 모두 조회합니다."
            + chr(10) + "약 14~16분 소요."
        )
        self.btn_inspect.clicked.connect(lambda: self.on_inspect(quick=False))
        top.addWidget(self.btn_inspect)
        lay.addWidget(box)

        # ---- 실시간 현황판 ----
        self.lbl_board = QLabel("자동점검을 시작하면 현황이 여기에 표시됩니다.")
        self.lbl_board.setStyleSheet(
            "QLabel { background:#0d1b2a; color:#e0e1dd; border-radius:6px;"
            " padding:12px 16px; font-family:'Consolas','D2Coding',monospace;"
            " font-size:13px; }")
        self.lbl_board.setWordWrap(True)
        lay.addWidget(self.lbl_board)

        # ---- 요약 카드 ----
        cards = QGroupBox("점검 결과")
        grid = QGridLayout(cards)
        self.card_target = StatCard("★ 작업대상 (이미지승인완료+정보미작업)", "#2e7d32")
        self.card_target_lcp = StatCard("★ 작업대상 LCP 종수", "#2e7d32")
        self.card_total = StatCard("전체 행(L코드)", "#263238")
        self.card_done_total = StatCard("전체 작업완료 (상품정보 저장완료)", "#00695c")
        self.card_img_done = StatCard("대표이미지 승인완료", "#1565c0")
        self.card_img_work = StatCard("대표이미지 작업중(승인전)", "#6a1b9a")
        self.card_info_todo = StatCard("상품정보 미작업", "#e65100")
        self.card_today = StatCard("오늘 작업량 (저장완료 기준)", "#c62828")

        for i, c in enumerate([
            self.card_target, self.card_target_lcp,
            self.card_total, self.card_done_total,
            self.card_img_done, self.card_img_work,
            self.card_info_todo, self.card_today,
        ]):
            grid.addWidget(c, i // 4, i % 4)
        lay.addWidget(cards)

        self.lbl_capped = QLabel("")
        self.lbl_capped.setStyleSheet("color:#c62828;")
        self.lbl_capped.setWordWrap(True)
        lay.addWidget(self.lbl_capped)

        # ---- 탭 ----
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_matrix_tab(), "상태 매트릭스")
        self.tabs.addTab(self._build_detail_tab(), "작업대상 목록")
        self.tabs.addTab(self._build_chart_tab(), "작업 추이")
        self.tabs.addTab(self._build_history_tab(), "점검 이력")
        self.tabs.addTab(self._build_log_tab(), "로그")
        lay.addWidget(self.tabs, 1)

        return wrap

    def _build_matrix_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        # ---- 처리속도 패널 ----
        self.lbl_matrix_rate = QLabel("자동점검을 켜면 처리속도가 표시됩니다.")
        self.lbl_matrix_rate.setStyleSheet(
            "QLabel { background:#eceff1; border:1px solid #cfd8dc;"
            " border-radius:6px; padding:8px 12px;"
            " font-family:'Consolas','D2Coding',monospace; font-size:12px; }")
        self.lbl_matrix_rate.setWordWrap(True)
        lay.addWidget(self.lbl_matrix_rate)

        lay.addWidget(QLabel(
            "행 = 대표이미지 상태 / 열 = 상품정보 상태.  칸 값은 '행수 (LCP종수)' 입니다."
        ))
        self.tbl_matrix = QTableWidget(0, 0)
        self.tbl_matrix.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_matrix.verticalHeader().setVisible(True)
        lay.addWidget(self.tbl_matrix, 1)
        return w

    def _refresh_rate_panel(self):
        """상태 매트릭스 탭 상단의 처리속도 패널 갱신."""
        folder = db.get_job_folder()
        if not folder:
            return
        r30 = db.rate_stats(folder, 30)
        r60 = db.rate_stats(folder, 60)
        today = db.today_totals(folder)

        def p10(v):
            return "-" if v is None else f"{v:,.1f}분"

        def hm(v):
            if v is None:
                return "-"
            h, m = divmod(int(v), 60)
            return f"{h}시간 {m}분" if h else f"{m}분"

        eta = None
        if r60["info"] > 0 and r60["span_min"] >= 3:
            eta = self._last_info_todo / (r60["info"] / r60["span_min"])

        nl = chr(10)
        self.lbl_matrix_rate.setText(nl.join([
            f"처리속도    30분 {r30['info']:>4,}개   1시간 {r60['info']:>4,}개"
            f"   →  10개당 {p10(db.per10_minutes(r60['info'], r60['span_min']))}"
            + ("   ※ 표본부족" if r60["span_min"] < 3 else ""),
            f"            이미지승인 1시간 {r60['img']:,}개"
            f"   ·  상품분석 1시간 {r60['analyzed']:,}개"
            f"   ·  실관측 {r60['span_min']:,.1f}분",
            f"오늘 누적   상품정보 {today['info']:,}개  ·  이미지승인 {today['img']:,}개"
            f"  ·  상품분석 {today['analyzed']:,}개"
            f"   (활동 {today['active_hours']}시간 / 관측 {today['span_min']:,.1f}분"
            f", 10개당 {p10(today['per10_info'])})",
            f"남은 {self._last_info_todo:,}개 예상 소요   {hm(eta)}",
        ]))

    def _render_matrix(self, cells: list):
        imgs, infos = [], []
        for c in cells:
            if c["image_status"] not in imgs:
                imgs.append(c["image_status"])
            if c["info_status"] not in infos:
                infos.append(c["info_status"])

        self.tbl_matrix.setRowCount(len(imgs))
        self.tbl_matrix.setColumnCount(len(infos))
        self.tbl_matrix.setHorizontalHeaderLabels(infos)
        self.tbl_matrix.setVerticalHeaderLabels(imgs)

        index = {(c["image_status"], c["info_status"]): c for c in cells}
        for r, im in enumerate(imgs):
            for col, inf in enumerate(infos):
                c = index.get((im, inf))
                if not c:
                    continue
                txt = f"{c['row_count']:,} ({c['lcp_count']:,})"
                if c["capped"]:
                    txt += " ⚠"
                it = QTableWidgetItem(txt)
                it.setTextAlignment(Qt.AlignCenter)
                if c["is_target"]:
                    fnt = it.font(); fnt.setBold(True); it.setFont(fnt)
                    it.setForeground(QColor("#2e7d32"))
                    it.setToolTip("★ 작업대상 : 대표이미지 승인완료 + 상품정보 미작업")
                elif c["capped"]:
                    it.setToolTip("조회 상한(1000행)에 걸려 실제 수량이 더 많을 수 있습니다.")
                self.tbl_matrix.setItem(r, col, it)
        self.tbl_matrix.resizeColumnsToContents()
        self._refresh_rate_panel()

    def _build_detail_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        row = QHBoxLayout()
        self.chk_uniq_lcp = QCheckBox("LCP 중복 제거 (종수만 보기)")
        self.chk_uniq_lcp.stateChanged.connect(self._render_items)
        row.addWidget(self.chk_uniq_lcp)

        self.btn_copy_lcp = QPushButton("작업대상 LCP코드 복사")
        self.btn_copy_lcp.clicked.connect(self.on_copy_lcp)
        row.addWidget(self.btn_copy_lcp)
        row.addStretch(1)
        lay.addLayout(row)

        self.tbl_items = QTableWidget(0, 4)
        self.tbl_items.setHorizontalHeaderLabels(
            ["광고상품코드(LCP)", "로하스상품코드(L)", "대표이미지", "상품정보"]
        )
        self.tbl_items.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_items.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_items.verticalHeader().setVisible(False)
        self.tbl_items.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch)
        lay.addWidget(self.tbl_items, 1)
        return w

    def _build_chart_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        row = QHBoxLayout()
        row.addWidget(QLabel("표시 범위"))
        self.cmb_chart_hours = QComboBox()
        self.cmb_chart_hours.addItems(["6", "12", "24", "48", "72"])
        self.cmb_chart_hours.setCurrentText("24")
        self.cmb_chart_hours.currentTextChanged.connect(self._refresh_chart)
        row.addWidget(self.cmb_chart_hours)
        row.addWidget(QLabel("시간"))

        btn = QPushButton("새로고침")
        btn.clicked.connect(self._refresh_chart)
        row.addWidget(btn)
        self.lbl_rate = QLabel("")
        self.lbl_rate.setStyleSheet("font-weight:bold; color:#1565c0;")
        row.addWidget(self.lbl_rate, 1)
        lay.addLayout(row)

        self.lbl_today = QLabel("")
        self.lbl_today.setStyleSheet(
            "QLabel { background:#e8f5e9; border:1px solid #a5d6a7;"
            " border-radius:6px; padding:6px 10px; font-weight:bold;"
            " color:#1b5e20; }")
        lay.addWidget(self.lbl_today)

        # ---- 시간당 처리량 (막대) ----
        self.chart_hourly = QChart()
        self.chart_hourly.setTitle("시간당 완료 건수")
        self.chart_hourly.legend().setAlignment(Qt.AlignBottom)
        view1 = QChartView(self.chart_hourly)
        view1.setRenderHint(QPainter.Antialiasing)
        view1.setMinimumHeight(210)
        lay.addWidget(view1)

        # ---- 오늘 작업량 (24시간 막대) ----
        self.chart_today = QChart()
        self.chart_today.setTitle("오늘 작업량 (시간대별)")
        self.chart_today.legend().setAlignment(Qt.AlignBottom)
        view0 = QChartView(self.chart_today)
        view0.setRenderHint(QPainter.Antialiasing)
        view0.setMinimumHeight(210)
        lay.addWidget(view0)

        # ---- 잔여 추이 (선) ----
        self.chart_trend = QChart()
        self.chart_trend.setTitle("잔여 추이 (상품정보 미완료 / 미분석 LCP)")
        self.chart_trend.legend().setAlignment(Qt.AlignBottom)
        view2 = QChartView(self.chart_trend)
        view2.setRenderHint(QPainter.Antialiasing)
        view2.setMinimumHeight(210)
        lay.addWidget(view2)

        # ---- 시간별 표 ----
        sub = QTabWidget()

        self.tbl_hourly = QTableWidget(0, 5)
        self.tbl_hourly.setHorizontalHeaderLabels(
            ["시간", "상품정보 완료", "이미지승인", "상품분석", "점검횟수"])
        self.tbl_hourly.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_hourly.verticalHeader().setVisible(False)
        self.tbl_hourly.horizontalHeader().setStretchLastSection(True)
        sub.addTab(self.tbl_hourly, "시간대별")

        self.tbl_rate = QTableWidget(0, 7)
        self.tbl_rate.setHorizontalHeaderLabels(
            ["기록시각", "30분(정보)", "1시간(정보)", "10개당(분)",
             "1시간(이미지)", "1시간(분석)", "잔여예상"])
        self.tbl_rate.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_rate.verticalHeader().setVisible(False)
        self.tbl_rate.horizontalHeader().setStretchLastSection(True)
        sub.addTab(self.tbl_rate, "처리속도 로그")

        sub.setMaximumHeight(200)
        lay.addWidget(sub)
        return w

    def _refresh_chart(self):
        folder = db.get_job_folder()
        hours = int(self.cmb_chart_hours.currentText())
        rows = db.hourly_stats(folder, hours)

        # ---------- 시간당 막대 ----------
        self.chart_hourly.removeAllSeries()
        for ax in list(self.chart_hourly.axes()):
            self.chart_hourly.removeAxis(ax)

        if rows:
            s_info = QBarSet("상품정보 완료")
            s_img = QBarSet("이미지승인")
            s_an = QBarSet("상품분석")
            s_info.setColor(QColor("#1565c0"))
            s_img.setColor(QColor("#2e7d32"))
            s_an.setColor(QColor("#ef6c00"))
            cats = []
            for r in rows:
                s_info.append(r["info_save"] or 0)
                s_img.append(r["img_done"] or 0)
                s_an.append(r["analyzed"] or 0)
                cats.append((r["hour"] or "")[-2:] + "시")

            series = QBarSeries()
            series.append(s_info)
            series.append(s_img)
            series.append(s_an)
            self.chart_hourly.addSeries(series)

            ax_x = QBarCategoryAxis()
            ax_x.append(cats)
            self.chart_hourly.addAxis(ax_x, Qt.AlignBottom)
            series.attachAxis(ax_x)

            ax_y = QValueAxis()
            top = max(1, max(max(r["info_save"] or 0, r["img_done"] or 0,
                                 r["analyzed"] or 0) for r in rows))
            ax_y.setRange(0, top * 1.2)
            ax_y.setLabelFormat("%d")
            self.chart_hourly.addAxis(ax_y, Qt.AlignLeft)
            series.attachAxis(ax_y)

        # ---------- 오늘 작업량 (24시간) ----------
        self.chart_today.removeAllSeries()
        for ax in list(self.chart_today.axes()):
            self.chart_today.removeAxis(ax)

        th = db.today_hourly(folder)
        tt = db.today_totals(folder)
        active = [r for r in th if r["samples"] > 0]
        if active:
            lo = min(r["hour"] for r in active)
            hi = max(r["hour"] for r in active)
            show = [r for r in th if lo <= r["hour"] <= hi]

            t_info = QBarSet("상품정보 완료"); t_info.setColor(QColor("#1565c0"))
            t_img = QBarSet("이미지승인");   t_img.setColor(QColor("#2e7d32"))
            t_an = QBarSet("상품분석");     t_an.setColor(QColor("#ef6c00"))
            cats = []
            for r in show:
                t_info.append(r["info"])
                t_img.append(r["img"])
                t_an.append(r["analyzed"])
                cats.append(f"{r['hour']:02d}시")

            ser = QBarSeries()
            ser.append(t_info); ser.append(t_img); ser.append(t_an)
            self.chart_today.addSeries(ser)

            ax_x = QBarCategoryAxis(); ax_x.append(cats)
            self.chart_today.addAxis(ax_x, Qt.AlignBottom); ser.attachAxis(ax_x)

            ax_y = QValueAxis(); ax_y.setLabelFormat("%d")
            top = max(1, max(max(r["info"], r["img"], r["analyzed"])
                             for r in show))
            ax_y.setRange(0, top * 1.2)
            self.chart_today.addAxis(ax_y, Qt.AlignLeft); ser.attachAxis(ax_y)

        self.chart_today.setTitle(
            f"오늘 작업량 ({tt['date']})  ·  상품정보 {tt['info']:,}개 / "
            f"이미지승인 {tt['img']:,}개 / 상품분석 {tt['analyzed']:,}개")

        p10t = tt["per10_info"]
        self.lbl_today.setText(
            f"오늘({tt['date']}) 누적  상품정보 {tt['info']:,}개 · "
            f"이미지승인 {tt['img']:,}개 · 상품분석 {tt['analyzed']:,}개   |   "
            f"활동 {tt['active_hours']}시간 · 실관측 {tt['span_min']:,.1f}분 · "
            f"10개당 " + ("-" if p10t is None else f"{p10t:,.1f}분"))

        # ---------- 잔여 추이 ----------
        self.chart_trend.removeAllSeries()
        for ax in list(self.chart_trend.axes()):
            self.chart_trend.removeAxis(ax)

        logs = db.recent_work_log(folder, limit=300)
        if logs:
            l_todo = QLineSeries(); l_todo.setName("상품정보 미완료")
            l_pend = QLineSeries(); l_pend.setName("미분석 LCP")
            l_todo.setColor(QColor("#e65100"))
            l_pend.setColor(QColor("#6a1b9a"))
            for i, r in enumerate(logs):
                l_todo.append(i, r["info_todo_rows"] or 0)
                l_pend.append(i, r["pending_lcps"] or 0)
            self.chart_trend.addSeries(l_todo)
            self.chart_trend.addSeries(l_pend)

            ax_x = QValueAxis(); ax_x.setRange(0, max(len(logs) - 1, 1))
            ax_x.setLabelFormat("%d"); ax_x.setTitleText("점검 회차")
            self.chart_trend.addAxis(ax_x, Qt.AlignBottom)
            ax_y = QValueAxis(); ax_y.setLabelFormat("%d")
            top = max(1, max(max(r["info_todo_rows"] or 0,
                                 r["pending_lcps"] or 0) for r in logs))
            ax_y.setRange(0, top * 1.15)
            self.chart_trend.addAxis(ax_y, Qt.AlignLeft)
            for ser in (l_todo, l_pend):
                ser.attachAxis(ax_x); ser.attachAxis(ax_y)

        # ---------- 표 + 시간당 평균 ----------
        self.tbl_hourly.setRowCount(len(rows))
        for i, r in enumerate(rows):
            vals = [r["hour"] or "", f"{r['info_save'] or 0:,}",
                    f"{r['img_done'] or 0:,}", f"{r['analyzed'] or 0:,}",
                    f"{r['samples'] or 0:,}"]
            for j, v in enumerate(vals):
                self.tbl_hourly.setItem(i, j, QTableWidgetItem(str(v)))
        self.tbl_hourly.resizeColumnsToContents()

        # ---------- 처리속도 로그 표 ----------
        rlogs = db.recent_rate_log(folder, limit=100)
        self.tbl_rate.setRowCount(len(rlogs))
        for i, r in enumerate(rlogs):
            def f(v, unit=""):
                return "-" if v is None else f"{v:,.1f}{unit}" if isinstance(
                    v, float) else f"{v:,}{unit}"
            eta = r.get("eta_min")
            eta_txt = "-" if eta is None else (
                f"{int(eta)//60}시간 {int(eta)%60}분" if eta >= 60
                else f"{int(eta)}분")
            vals = [r["ts"], f(r["m30_info"]), f(r["h1_info"]),
                    f(r["per10_info"]), f(r["h1_img"]), f(r["h1_analyzed"]),
                    eta_txt]
            for j, v in enumerate(vals):
                self.tbl_rate.setItem(i, j, QTableWidgetItem(str(v)))
        self.tbl_rate.resizeColumnsToContents()

        if rows:
            n = len(rows)
            avg_info = sum(r["info_save"] or 0 for r in rows) / n
            avg_an = sum(r["analyzed"] or 0 for r in rows) / n
            last = rows[-1]
            r30 = db.rate_stats(folder, 30)
            r60 = db.rate_stats(folder, 60)
            p10 = db.per10_minutes(r60["info"], r60["span_min"])
            p10_txt = "-" if p10 is None else f"{p10:,.1f}분"
            self.lbl_rate.setText(
                f"30분 {r30['info']:,}개 · 1시간 {r60['info']:,}개 · "
                f"10개당 {p10_txt}  (실관측 {r60['span_min']:,.1f}분)   |   "
                f"{n}시간 평균 {avg_info:.1f}건/시 · 분석 {avg_an:.1f}건/시")
        else:
            self.lbl_rate.setText("아직 기록이 없습니다. 자동점검을 켜면 쌓입니다.")

    def _build_history_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        row = QHBoxLayout()
        btn = QPushButton("새로고침")
        btn.clicked.connect(self._reload_history)
        row.addWidget(btn)
        row.addStretch(1)
        lay.addLayout(row)

        self.tbl_history = QTableWidget(0, 7)
        self.tbl_history.setHorizontalHeaderLabels(
            ["점검일시", "폴더명", "전체행", "이미지승인완료", "정보미작업",
             "★작업대상(행/LCP)", "소요(초)"]
        )
        self.tbl_history.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_history.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_history.verticalHeader().setVisible(False)
        self.tbl_history.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch)
        self.tbl_history.doubleClicked.connect(self.on_history_open)
        lay.addWidget(self.tbl_history, 1)
        return w

    def _build_log_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setLineWrapMode(QTextEdit.NoWrap)
        lay.addWidget(self.txt_log)
        return w

    # ------------------------------------------------------------------ 공통

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        for line in str(msg).rstrip().splitlines():
            self.txt_log.append(f"[{ts}] {line}")
        self.txt_log.ensureCursorVisible()

    def _set_busy(self, busy: bool, label: str = ""):
        for b in (self.btn_scan_folders, self.btn_inspect, self.btn_quick,
                  self.btn_analysis, self.btn_sample,
                  self.page_product.btn_status,
                  self.page_product.btn_basic,
                  self.btn_add_master, self.btn_set_job,
                  self.btn_del_master, self.btn_dump):
            b.setEnabled(not busy)
        self.btn_stop.setEnabled(busy)
        if busy:
            self.progress.setRange(0, 0)
            self.progress.setFormat(label or "실행 중...")
        else:
            self.progress.setRange(0, 1)
            self.progress.setValue(0)
            self.progress.setFormat("대기 중")
            self.setWindowTitle("로하스 오토 - 상품정보관리 폴더 수량 점검")

    def _start_worker(self, worker, on_finished, busy_label: str):
        if self._thread is not None:
            QMessageBox.information(self, "안내", "이미 실행 중인 작업이 있습니다.")
            return

        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)

        worker.log.connect(self._log)
        worker.progress.connect(self._on_progress)
        worker.failed.connect(self._on_failed)
        if hasattr(worker, 'stat'):
            worker.stat.connect(self._on_stat)
        worker.finished.connect(on_finished)

        for sig in (worker.finished, worker.failed):
            sig.connect(lambda *_: thread.quit())
        thread.finished.connect(self._on_thread_done)

        self._thread, self._worker = thread, worker
        self._set_busy(True, busy_label)
        thread.start()

    def _on_thread_done(self):
        if self._thread is not None:
            self._thread.deleteLater()
        if self._worker is not None:
            self._worker.deleteLater()
        self._thread = None
        self._worker = None
        self._set_busy(False)

    def _on_progress(self, done: int, total: int):
        if total and total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(done)
            self.progress.setFormat(f"{done} / {total}")
        else:
            self.progress.setFormat(f"수집 {done}건...")

    def _on_stat(self, st: dict):
        """ALL 상품분석 진행 중 작업수량 표시."""
        text = (f"진행 {st.get('processed', 0):,} / {st.get('total', 0):,}"
                f"   (남음 {st.get('remain', 0):,})"
                f"   ·  완료 {st.get('done', 0):,}"
                f" · 이미완료 {st.get('already', 0):,}"
                f" · 오류 {st.get('error', 0):,}"
                f" · 시간초과 {st.get('timeout', 0):,}")
        self.lbl_task.setText(text)
        self.setWindowTitle(
            f"로하스 오토 - 상품분석 {st.get('processed', 0):,}/{st.get('total', 0):,}")

    def _on_failed(self, msg: str):
        QMessageBox.critical(self, "오류", msg)
        self._log(f"[오류] {msg}")

    def on_stop(self):
        if self._worker is not None:
            self._worker.stop()
            self._log("중단 요청 - 현재 페이지까지 마치고 멈춥니다.")

    # ------------------------------------------------------------------ 폴더

    def on_scan_folders(self):
        if not config.credentials_ok():
            QMessageBox.warning(
                self, "안내",
                ".env 에 LOHAS_ID / LOHAS_PW 를 먼저 설정해주세요.")
            return
        worker = FolderScanWorker(
            headless=self.chk_headless.isChecked(),
            monitor=self.cmb_monitor.currentData(),
            use_http=self.chk_http.isChecked(),
        )
        self._start_worker(worker, self._on_folders_scanned, "폴더 스캔 중...")

    def _on_folders_scanned(self, result: dict):
        self._reload_folders()
        QMessageBox.information(
            self, "완료",
            f"마스터 폴더 {result['total']}개를 자체 DB에 저장했습니다.\n"
            f"신규 {result['new']} / 갱신 {result['updated']} / "
            f"비활성 {result['deactivated']}",
        )

    def _reload_folders(self):
        self._folders = db.list_folders(active_only=True)
        self._render_folders()
        self._rebuild_pool_combo()
        self._update_job_label()

    def _update_job_label(self):
        job = db.get_job_folder()
        masters = db.list_master_folders()
        if job:
            self.lbl_work.setText(f"작업폴더 : {job}   (마스터 {len(masters)}개)")
        else:
            self.lbl_work.setText(
                f"작업폴더 : (미지정)   (마스터 {len(masters)}개)")

    def _visible_folders(self) -> list:
        """메인상품(51~59) + 모두표시 여부 + 검색어를 반영한 표시 대상."""
        keyword = self.txt_filter.text().strip().lower()
        rows = self._folders
        if self.chk_main_only.isChecked():
            rows = [f for f in rows if is_main_folder(f["name"])]
        if not self.chk_show_all.isChecked():
            rows = [f for f in rows if f.get("is_work")]
        if keyword:
            rows = [f for f in rows if keyword in (f["name"] or "").lower()]
        return rows

    def _render_folders(self):
        job = db.get_job_folder()
        rows = self._visible_folders()

        self.tbl_folders.clear()
        for f in rows:
            name = f["name"]
            ov = db.folder_overview(name)

            item = QTreeWidgetItem(self.tbl_folders)
            item.setData(0, Qt.UserRole, name)          # 실제 폴더명 보관

            label = ("★ " + name) if name == job else name
            item.setText(0, label)
            if name == job:
                fnt = item.font(0); fnt.setBold(True); item.setFont(0, fnt)
                item.setForeground(0, QColor("#2e7d32"))
                item.setToolTip(0, "현재 작업폴더 (점검 대상)")
            elif f.get("is_work"):
                item.setForeground(0, QColor("#1565c0"))

            total = ov["total_rows"] if ov["has_data"] else f.get("site_count")
            item.setText(1, "" if total is None else f"{total:,}")

            def cell(base, today):
                if base is None:
                    return ""
                return f"{base:,}" + (f"  (+{today:,})" if today else "")

            item.setText(2, cell(ov["img_done_rows"], ov["today_img"]))
            item.setText(3, cell(ov["info_save_rows"], ov["today_info"]))
            if ov["today_img"]:
                item.setForeground(2, QColor("#1565c0"))
            if ov["today_info"]:
                item.setForeground(3, QColor("#2e7d32"))

            tgt = f.get("last_target")
            item.setText(4, "" if tgt is None else f"{tgt:,}")

            if ov["has_data"]:
                item.setToolTip(
                    0, f"{name}{chr(10)}통계 시작 {ov['since']} · "
                       f"표본 {ov['samples']:,}회{chr(10)}"
                       f"마지막 {ov['last_ts']}")
            # 펼침 화살표가 보이도록 자리표시 자식을 하나 넣어둔다
            QTreeWidgetItem(item, ["불러오는 중..."])
            item.setChildIndicatorPolicy(
                QTreeWidgetItem.ShowIndicator)

        for i in range(5):
            self.tbl_folders.resizeColumnToContents(i)

        mode = "전체" if self.chk_show_all.isChecked() else "마스터폴더"
        if self.chk_main_only.isChecked():
            mode += " · 메인상품 51~59"
        self.tbl_folders.parentWidget().setTitle(
            f"작업리스트 ({mode}) - {len(rows)}개")

    def _on_folder_expanded(self, item: QTreeWidgetItem):
        """폴더를 펼치면 통계 시작일부터의 일자별 작업량을 채운다."""
        name = item.data(0, Qt.UserRole)
        if not name or item.data(0, Qt.UserRole + 1):
            return                                  # 이미 채움
        item.takeChildren()

        daily = db.folder_daily(name)
        if not daily:
            empty = QTreeWidgetItem(item)
            empty.setText(0, "통계 없음 — 자동점검을 켜면 일자별로 쌓입니다")
            empty.setForeground(0, QColor("#9e9e9e"))
            item.setData(0, Qt.UserRole + 1, True)
            return

        for r in reversed(daily):                   # 최근 날짜가 위로
            ch = QTreeWidgetItem(item)
            ch.setText(0, f"   {r['day']}")
            ch.setForeground(0, QColor("#546e7a"))
            ch.setText(1, f"{r['total_rows']:,}")
            ch.setText(2, f"{r['img_done_rows']:,}"
                          + (f"  (+{r['img_delta']:,})" if r["img_delta"] else ""))
            ch.setText(3, f"{r['info_save_rows']:,}"
                          + (f"  (+{r['info_delta']:,})" if r["info_delta"] else ""))
            ch.setText(4, f"미완료 {r['info_todo_rows']:,}")
            if r["img_delta"]:
                ch.setForeground(2, QColor("#1565c0"))
            if r["info_delta"]:
                ch.setForeground(3, QColor("#2e7d32"))
            ch.setToolTip(0, f"{r['day']} · 점검 {r['samples']:,}회{chr(10)}"
                             f"이미지승인 +{r['img_delta']:,} / "
                             f"저장완료 +{r['info_delta']:,} / "
                             f"상품분석 +{r['analyzed_delta']:,}")

        # 합계 줄
        tot = QTreeWidgetItem(item)
        tot.setText(0, "   ── 합계")
        tot.setText(2, f"+{sum(r['img_delta'] for r in daily):,}")
        tot.setText(3, f"+{sum(r['info_delta'] for r in daily):,}")
        tot.setText(4, f"{len(daily)}일")
        f = tot.font(0); f.setBold(True)
        for c in range(5):
            tot.setFont(c, f)
        item.setData(0, Qt.UserRole + 1, True)

    def _rebuild_pool_combo(self):
        """아직 마스터폴더가 아닌 폴더들을 드롭다운에 채운다."""
        prev = self.cmb_pool.currentText()
        self.cmb_pool.blockSignals(True)
        self.cmb_pool.clear()
        pool = [f for f in self._folders if not f.get("is_work")]
        if self.chk_main_only.isChecked():
            pool = [f for f in pool if is_main_folder(f["name"])]
        for f in pool:
            cnt = f.get("site_count")
            label = f["name"] if cnt is None else f"{f['name']}  ({cnt:,})"
            self.cmb_pool.addItem(label, f["name"])
        idx = self.cmb_pool.findText(prev)
        if idx >= 0:
            self.cmb_pool.setCurrentIndex(idx)
        self.cmb_pool.blockSignals(False)
        self.btn_add_master.setEnabled(self.cmb_pool.count() > 0)

    def _apply_filter(self):
        self._render_folders()

    def _on_filter_changed(self):
        self._render_folders()
        self._rebuild_pool_combo()

    def _selected_folder_name(self) -> str:
        """선택 항목(자식이면 부모)의 폴더명."""
        item = self.tbl_folders.currentItem()
        while item is not None:
            name = item.data(0, Qt.UserRole)
            if name:
                return name
            item = item.parent()
        return ""

    # ---- 마스터폴더 지정/해제 ----

    def on_add_master_folder(self):
        """드롭다운(또는 모두표시 상태의 선택행)에서 고른 폴더를 마스터폴더로."""
        name = ""
        if self.chk_show_all.isChecked():
            name = self._selected_folder_name()
        if not name:
            name = self.cmb_pool.currentData() or ""
        if not name:
            QMessageBox.information(
                self, "안내",
                "추가할 폴더를 드롭다운에서 고르거나, "
                "[모두 표시] 상태에서 목록의 폴더를 선택해주세요.")
            return

        db.add_master_folder(name)
        self._log(f"마스터폴더 추가 : {name}")
        self._reload_folders()

        if self.chk_show_all.isChecked():
            ret = QMessageBox.question(
                self, "마스터폴더 지정",
                f"'{name}' 을(를) 마스터폴더로 지정했습니다."
                + chr(10) + chr(10) + "마스터폴더만 보이게 할까요?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
            )
            if ret == QMessageBox.Yes:
                self.chk_show_all.setChecked(False)   # -> _render_folders 재호출

    def on_remove_master_folder(self):
        name = self._selected_folder_name()
        if not name:
            QMessageBox.information(self, "안내", "목록에서 폴더를 선택해주세요.")
            return
        ret = QMessageBox.question(
            self, "마스터폴더 해제",
            f"'{name}' 을(를) 작업리스트에서 제외할까요?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        db.remove_master_folder(name)
        self._log(f"마스터폴더 해제 : {name}")
        self._reload_folders()

    # ---- 작업폴더 지정 ----

    def on_set_job_folder(self, *args):
        name = self._selected_folder_name()
        if not name:
            QMessageBox.information(
                self, "안내", "작업폴더로 지정할 폴더를 목록에서 선택해주세요.")
            return
        db.set_job_folder(name)
        self._log(f"작업폴더 지정 : {name}")
        self._reload_folders()

    # ------------------------------------------------------------------ 점검

    def on_inspect(self, quick: bool = False):
        work = db.get_job_folder()
        if not work:
            QMessageBox.information(
                self, "안내",
                "작업폴더가 지정되지 않았습니다." + chr(10)
                + "작업리스트에서 폴더를 선택하고 [③ 작업폴더로 지정] 을 눌러주세요.")
            return

        http = self.chk_http.isChecked()
        if quick:
            title, detail = "빠른 점검", [
                "작업대상 한 칸만 조회합니다.",
                "(대표이미지 승인완료 + 상품정보 미작업)",
                "예상 소요 : 약 1~3초" if http else "예상 소요 : 약 1~2분",
            ]
        else:
            title, detail = "전체 점검", [
                "12칸 매트릭스를 모두 조회합니다.",
                "(대표이미지 3상태 x 상품정보 4상태)",
                "예상 소요 : 약 10~20초" if http else "예상 소요 : 약 14~16분",
            ]
        if http:
            detail.append("조회방식 : HTTP (1000행 상한 없음)")
        else:
            detail.append("조회방식 : 브라우저 (1000행 상한 있음)")

        msg = [f"작업폴더 : {work}",
               f"페이지당 : {self.cmb_page_size.currentText()}개", ""] + detail +               ["", "진행할까요?"]
        ret = QMessageBox.question(
            self, title, chr(10).join(msg),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        )
        if ret != QMessageBox.Yes:
            return

        worker = InspectWorker(
            folder_name=work,
            page_size=self.cmb_page_size.currentText(),
            max_pages=config.MAX_PAGES,
            headless=self.chk_headless.isChecked(),
            monitor=self.cmb_monitor.currentData(),
            quick=quick,
            use_http=self.chk_http.isChecked(),
        )
        self._start_worker(
            worker, self._on_inspected,
            f"'{work}' {'빠른' if quick else '전체'} 점검 중...")

    def _on_inspected(self, result: dict):
        s_ = result["summary"]
        self._last_items = result["items"]
        self._last_cells = result["cells"]

        self._apply_summary(s_)
        self._render_matrix(self._last_cells)
        self._render_items()
        self._reload_folders()
        self._reload_history()
        self.tabs.setCurrentIndex(0)

        lines = [
            f"폴더 : {s_['folder_name']}",
            f"점검일시 : {s_['scanned_at']}  ({s_['elapsed_sec']}초)",
            "",
            "★ 작업대상 (대표이미지 승인완료 + 상품정보 미작업)",
            f"      {s_['target_rows']:,} 행   /   LCP {s_['target_lcps']:,} 종",
            "─" * 34,
            f"전체 : {s_['total_rows']:,} 행 / LCP {s_['total_lcps']:,} 종",
            "",
            "[대표이미지]",
            f"   미작업 {s_['img_todo_rows']:,} / "
            f"이미지작업 {s_['img_work_rows']:,} / "
            f"승인완료 {s_['img_done_rows']:,}",
            "[상품정보]",
            f"   미작업 {s_['info_todo_rows']:,} / "
            f"저장완료 {s_['info_save_rows']:,} / "
            f"제외 {s_['info_exclude_rows']:,} / "
            f"보류 {s_['info_hold_rows']:,}",
        ]
        if s_.get("capped"):
            lines += ["", "※ 일부 조합이 조회 상한(1000행)에 걸려",
                      "   실제 수량이 더 많을 수 있습니다."]
        lines += ["", f"자체 DB 저장 완료 (scan_id={result['scan_id']})"]

        QMessageBox.information(self, "점검 완료", chr(10).join(lines))

    def _apply_summary(self, s_: dict):
        if s_.get("mode") != "quick":
            self._last_info_todo = s_.get("info_todo_rows") or 0
        # 빠른 점검은 작업대상 한 칸만 재므로 나머지 합계는 '-' 로 표시한다
        quick = (s_.get("mode") == "quick")

        def val(key):
            return "-" if quick else f"{s_[key]:,}"

        self.card_target.set_value(f"{s_['target_rows']:,}")
        self.card_target_lcp.set_value(f"{s_['target_lcps']:,}")
        self.card_total.set_value(val("total_rows"))
        self.card_img_done.set_value(val("img_done_rows"))
        self.card_img_work.set_value(val("img_work_rows"))
        self.card_info_todo.set_value(val("info_todo_rows"))
        self.card_done_total.set_value(val("info_save_rows"))

        # 오늘 작업량 (기준행 이후 실제 증가분)
        folder = s_.get("folder_name") or db.get_job_folder()
        try:
            t = db.today_totals(folder)
            self.card_today.set_value(f"{t['info']:,}")
            self.card_today.lbl_title.setText(
                f"오늘 작업량 (저장완료)  ·  이미지승인 {t['img']:,}"
                + (f" · 분석 {t['analyzed']:,}" if t["analyzed"] else ""))
        except Exception:
            self.card_today.set_value("-")
        self._sync_mini()          # 작은 창을 띄워둔 채여도 숫자가 맞게
        if s_.get("capped"):
            self.lbl_capped.setText(
                "⚠ 일부 상태조합이 조회 상한(1000행)에 걸렸습니다. "
                "해당 칸(⚠ 표시)의 수량은 실제보다 적을 수 있습니다.")
        elif quick:
            self.lbl_capped.setText(
                "ℹ 빠른 점검은 ★작업대상만 측정합니다. "
                "'-' 항목은 [④ 전체 점검]에서 확인하세요 (약 4~5초).")
        else:
            self.lbl_capped.setText("")

    def _render_items(self):
        items = self._last_items
        if self.chk_uniq_lcp.isChecked():
            seen, uniq = set(), []
            for it in items:
                key = it.get("lcp_code")
                if key in seen:
                    continue
                seen.add(key)
                uniq.append(it)
            items = uniq

        self.tbl_items.setRowCount(len(items))
        for i, it in enumerate(items):
            self.tbl_items.setItem(i, 0, QTableWidgetItem(it.get("lcp_code") or ""))
            self.tbl_items.setItem(i, 1, QTableWidgetItem(it.get("l_code") or ""))
            for col, key in ((2, "image_status"), (3, "info_status")):
                val = it.get(key) or ""
                cell = QTableWidgetItem(val)
                cell.setForeground(STATUS_COLORS.get(val, QColor("#616161")))
                self.tbl_items.setItem(i, col, cell)

        self.tbl_items.resizeColumnsToContents()
        self.tbl_items.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)

    def on_copy_lcp(self):
        codes = list(dict.fromkeys(
            it.get("lcp_code") for it in self._last_items if it.get("lcp_code")))
        if not codes:
            QMessageBox.information(self, "안내", "복사할 작업대상 LCP코드가 없습니다.")
            return
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText("\n".join(codes))
        self._log(f"작업대상 LCP코드 {len(codes)}건 클립보드 복사")
        QMessageBox.information(self, "복사 완료", f"{len(codes)}건을 복사했습니다.")

    # ------------------------------------------------------------------ 이력

    def _reload_history(self):
        self._history = db.list_scans(limit=100)
        self.tbl_history.setRowCount(len(self._history))
        for i, s in enumerate(self._history):
            vals = [
                s["scanned_at"], s["folder_name"], f"{s['total_rows']:,}",
                f"{s['img_done_rows']:,}", f"{s['info_todo_rows']:,}",
                f"{s['target_rows']:,} / {s['target_lcps']:,}",
                "" if s["elapsed_sec"] is None else str(s["elapsed_sec"]),
            ]
            for j, v in enumerate(vals):
                self.tbl_history.setItem(i, j, QTableWidgetItem(str(v)))
        self.tbl_history.resizeColumnsToContents()
        self.tbl_history.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)

    def on_history_open(self):
        row = self.tbl_history.currentRow()
        if row < 0 or row >= len(self._history):
            return
        scan = self._history[row]
        self._last_items = db.list_scan_items(scan["id"])
        self._last_cells = db.list_scan_cells(scan["id"])

        self._apply_summary(scan)
        self._render_matrix(self._last_cells)
        self._render_items()
        self.tabs.setCurrentIndex(0)
        self._log(f"이력 불러오기 : scan_id={scan['id']} ({scan['folder_name']})")

    # ------------------------------------------------------------------ 상품정보 수집

    def _product_folder(self) -> str:
        f = self.page_product.current_folder() or db.get_job_folder()
        if not f:
            QMessageBox.information(self, "안내", "작업폴더를 먼저 지정해주세요.")
        return f

    def on_collect_lcode(self):
        f = self._product_folder()
        if not f:
            return
        worker = LcodeStatusWorker(f, headless=self.chk_headless.isChecked(),
                                   monitor=self.cmb_monitor.currentData())
        self._start_worker(worker, self._on_lcode_done, f"'{f}' L코드 상태 수집 중...")

    def _on_lcode_done(self, res: dict):
        self.page_product.reload()
        QMessageBox.information(
            self, "L코드 상태 수집 완료",
            chr(10).join([
                f"폴더 : {res.get('folder_name')}",
                f"L코드 {res.get('rows', 0):,}행 / LCP {res.get('lcp_count', 0):,}종",
                f"소요 {res.get('elapsed_sec')}초",
                res.get("mirror", ""),
            ]))

    def on_collect_basic(self):
        f = self._product_folder()
        if not f:
            return
        left = len([r for r in db.lcp_overview(f) if not r.get("collected_at")])
        ret = QMessageBox.question(
            self, "기본정보 수집",
            chr(10).join([
                f"폴더 : {f}",
                f"미수집 LCP : {left:,}종",
                f"예상 소요 : 약 {max(left * 2 // 60, 1)}분 (건당 약 2초)",
                "",
                "포함상품 · 키워드 · 카테고리를 받아 저장합니다.",
                "이미 수집한 LCP 는 건너뜁니다. 진행할까요?",
            ]),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if ret != QMessageBox.Yes:
            return
        worker = BasicCollectWorker(f, headless=self.chk_headless.isChecked(),
                                    monitor=self.cmb_monitor.currentData())
        self._start_worker(worker, self._on_basic_done, f"'{f}' 기본정보 수집 중...")

    def _on_basic_done(self, res: dict):
        self.page_product.reload()
        QMessageBox.information(
            self, "기본정보 수집 완료",
            chr(10).join([
                f"완료 {res.get('ok', 0):,}건 / 실패 {res.get('fail', 0):,}건",
                "",
                f"옵션 {res.get('options', 0):,} · 사용키워드 {res.get('used', 0):,}",
                f"추천 {res.get('recommend', 0):,} · 상품명토큰 {res.get('tokens', 0):,}",
                f"희망 {res.get('wish', 0):,} · 카테고리 {res.get('categories', 0):,}",
            ]))

    # ------------------------------------------------------------------ 샘플 분석

    def on_run_sample(self):
        work = db.get_job_folder()
        if not work:
            QMessageBox.information(
                self, "안내",
                "작업폴더가 지정되지 않았습니다." + chr(10)
                + "작업리스트에서 폴더를 선택하고 [③ 작업폴더로 지정] 을 눌러주세요.")
            return
        worker = SampleWorker(
            folder_name=work,
            headless=self.chk_headless.isChecked(),
            monitor=self.cmb_monitor.currentData(),
        )
        self._start_worker(worker, self._on_sample_done, "샘플 1건 분석 중...")

    def _on_sample_done(self, res: dict):
        if res.get("error"):
            QMessageBox.information(self, "샘플 분석", res["error"])
            return

        nl = chr(10)
        t = res["target"]
        a = res.get("attr") or {}
        g = res.get("gemini") or {}
        lines = [
            f"상품 : {t['lcp_code']}   (L코드 {t.get('l_code')} / no {t['product_no']})",
            f"원상품명 : {(a.get('product_name') or '')[:60]}",
            "",
            f"상품분석 : {'완료' if a.get('already_done') else '미완료'}"
            f"   ({a.get('analysis_date') or '-'})",
            f"카테고리 : {'저장됨' if a.get('category_saved') else '미저장'}",
            f"탭 저장상태 : {res.get('saved_tabs')}",
        ]
        if res.get("blocked"):
            lines += ["", "⚠ 선행 필요 : " + " / ".join(res["blocked"])]

        for key, label in (("tag", "태그"), ("title1", "상품명1")):
            d = res.get(key)
            lines.append("")
            if not d:
                lines.append(f"[{label}] 이미 저장됨 - 건너뜀")
                continue
            lines.append(f"[{label}] 표 {d['rows']}행 → 후보 {d['cands']}개 "
                         f"→ {len(d['picked'])}개 선택 ({d['source']})")
            for w in d["picked"]:
                lines.append(f"    · {w}")

        lines += [
            "",
            f"소요 {res.get('elapsed_sec')}초   |   "
            f"Gemini 호출 {g.get('call', 0)} / 성공 {g.get('ok', 0)} / "
            f"429 {g.get('http429', 0)}",
        ]
        if not config.GEMINI_API_KEY:
            lines += ["",
                      "※ GEMINI_API_KEY 가 비어 있어 규칙 기반으로 골랐습니다.",
                      "   .env 에 키를 넣으면 이미지·상품명 기반으로 고릅니다."]
        lines += ["", "저장은 하지 않았습니다. 기록은 task_log(로컬+서버)에 남았습니다."]

        QMessageBox.information(self, "샘플 1건 분석 결과", nl.join(lines))

    # ------------------------------------------------------------------ 폴더 통계

    def on_show_stats(self):
        name = self._selected_folder_name()
        if not name:
            QMessageBox.information(self, "안내", "폴더를 먼저 선택해주세요.")
            return
        daily = db.folder_daily(name)
        if not daily:
            QMessageBox.information(
                self, "통계 없음",
                f"'{name}' 의 기록이 아직 없습니다." + chr(10)
                + "작업폴더로 지정하고 [자동점검] 을 켜두면 일자별로 쌓입니다.")
            return
        FolderStatsDialog(name, daily, self).exec()

    # ------------------------------------------------------------------ 자동점검

    def on_monitor_toggled(self, checked: bool):
        if checked:
            self._start_monitor()
        else:
            self._stop_monitor()

    def _start_monitor(self):
        if self._monitor_thread is not None:
            return
        work = db.get_job_folder()
        if not work:
            QMessageBox.information(
                self, "안내",
                "작업폴더가 지정되지 않았습니다." + chr(10)
                + "작업리스트에서 폴더를 선택하고 [③ 작업폴더로 지정] 을 눌러주세요.")
            self.chk_monitor.blockSignals(True)
            self.chk_monitor.setChecked(False)
            self.chk_monitor.blockSignals(False)
            return

        interval = int(self.spn_interval.currentText())
        worker = MonitorWorker(
            folder_name=work, interval=interval,
            headless=self.chk_headless.isChecked(),
            monitor=self.cmb_monitor.currentData(),
        )
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.log.connect(self._log)
        worker.tick.connect(self._on_monitor_tick)
        worker.failed.connect(self._on_monitor_failed)
        worker.finished.connect(lambda *_: thread.quit())
        thread.finished.connect(self._on_monitor_thread_done)

        self._monitor_thread, self._monitor_worker = thread, worker
        self.lbl_monitor_state.setText("● 실행중")
        self.lbl_monitor_state.setStyleSheet("color:#2e7d32; font-weight:bold;")
        self.lbl_board.setText(f"[{work}] 첫 점검 중...")
        self._log(f"자동점검 시작 : {work} ({interval}초 주기)")
        thread.start()

    def _stop_monitor(self):
        if self._monitor_worker is None:
            return
        self._monitor_worker.stop()
        self.lbl_monitor_state.setText("● 중지중...")
        self.lbl_monitor_state.setStyleSheet("color:#ef6c00; font-weight:bold;")
        self._log("자동점검 중지 요청")

    def _on_monitor_thread_done(self):
        if self._monitor_thread is not None:
            self._monitor_thread.deleteLater()
        if self._monitor_worker is not None:
            self._monitor_worker.deleteLater()
        self._monitor_thread = None
        self._monitor_worker = None
        self.lbl_monitor_state.setText("● 정지")
        self.lbl_monitor_state.setStyleSheet("color:#9e9e9e; font-weight:bold;")
        self.chk_monitor.blockSignals(True)
        self.chk_monitor.setChecked(False)
        self.chk_monitor.blockSignals(False)

    def _on_monitor_failed(self, msg: str):
        self._log(f"[모니터 오류] {msg}")
        self.lbl_board.setText("자동점검 오류 : " + msg[:200])

    # ---- 현황판 렌더 ----

    @staticmethod
    def _fmt_min(v):
        if v is None:
            return "-"
        return f"{v:,.1f}분"

    @staticmethod
    def _fmt_eta(v):
        if v is None:
            return "-"
        h, m = divmod(int(v), 60)
        return f"{h}시간 {m}분" if h else f"{m}분"

    def _rate_row(self, label: str, m30: int, h1: int, per10, color: str) -> str:
        """'30분 N개 · 1시간 N개 · 10개당 N분' 한 줄."""
        return (
            f"<tr>"
            f"<td style='padding:1px 10px 6px 26px; color:#78909c;'>{label}</td>"
            f"<td colspan='2' style='padding:1px 0 6px 0;'>"
            f"<span style='color:#78909c;'>30분</span> "
            f"<b style='color:{color};'>{m30:,}</b>"
            f"<span style='color:#78909c;'>개 &nbsp;·&nbsp; 1시간</span> "
            f"<b style='color:{color};'>{h1:,}</b>"
            f"<span style='color:#78909c;'>개 &nbsp;·&nbsp; 10개당</span> "
            f"<b style='color:#ffd54f;'>{self._fmt_min(per10)}</b>"
            f"</td></tr>"
        )

    def _board_html(self, st: dict) -> str:
        r30 = st.get("r30") or {}
        r60 = st.get("r60") or {}
        warn = ("  <span style='color:#ef9a9a;'>※ 표본부족</span>"
                if r60.get("span_min", 0) < 3 else "")

        def big(v, color="#eceff1"):
            return f"<b style='font-size:15px; color:{color};'>{v:,}</b>"

        rows = []
        rows.append(
            f"<tr><td style='padding:2px 12px 2px 6px; color:#b0bec5;'>전체 상품수</td>"
            f"<td align='right' style='padding:2px 6px;'>{big(st['total_rows'])}</td>"
            f"<td style='padding:2px 6px; color:#78909c;'>개 &nbsp; "
            f"(LCP {st['total_lcps']:,}종)</td></tr>")

        rows.append("<tr><td colspan='3'><hr style='border:0; "
                    "border-top:1px solid #2c3e50;'></td></tr>")

        # 이미지승인완료  +  저장완료
        rows.append(
            f"<tr><td style='padding:2px 12px 2px 6px; color:#b0bec5;'>"
            f"이미지승인완료</td>"
            f"<td align='right' style='padding:2px 6px;'>"
            f"{big(st['img_done_rows'], '#64b5f6')}</td>"
            f"<td style='padding:2px 6px; color:#78909c;'>개 &nbsp;&nbsp;|&nbsp;&nbsp; "
            f"저장완료 &nbsp;{big(st['info_save_rows'], '#81c784')}"
            f"<span style='color:#78909c;'> 개</span></td></tr>")
        rows.append(self._rate_row("└ 저장완료 속도", r30.get("info", 0),
                                   r60.get("info", 0), st.get("per10_info"),
                                   "#81c784"))

        # 이미지승인중
        rows.append(
            f"<tr><td style='padding:2px 12px 2px 6px; color:#b0bec5;'>"
            f"이미지승인중</td>"
            f"<td align='right' style='padding:2px 6px;'>"
            f"{big(st['img_work_rows'], '#ffb74d')}</td>"
            f"<td style='padding:2px 6px; color:#78909c;'>개 &nbsp;&nbsp;"
            f"(미작업 {st['img_todo_rows']:,})</td></tr>")
        rows.append(self._rate_row("└ 이미지승인 속도", r30.get("img", 0),
                                   r60.get("img", 0), st.get("per10_img"),
                                   "#ffb74d"))

        rows.append("<tr><td colspan='3'><hr style='border:0; "
                    "border-top:1px solid #2c3e50;'></td></tr>")

        # 상품정보 미완료 / 미분석
        rows.append(
            f"<tr><td style='padding:2px 12px 2px 6px; color:#b0bec5;'>"
            f"상품정보 미완료</td>"
            f"<td align='right' style='padding:2px 6px;'>"
            f"{big(st['info_todo_rows'], '#ff8a65')}</td>"
            f"<td style='padding:2px 6px; color:#78909c;'>개</td></tr>")
        rows.append(
            f"<tr><td style='padding:2px 12px 2px 6px; color:#b0bec5;'>"
            f"미완료중 미분석LCP</td>"
            f"<td align='right' style='padding:2px 6px;'>"
            f"{big(st['pending_lcps'], '#ce93d8')}</td>"
            f"<td style='padding:2px 6px; color:#78909c;'>종 &nbsp;&nbsp;"
            f"(작업대상 {st['target_lcps']:,}종 중 분석완료 "
            f"{st['analyzed_lcps']:,}종)</td></tr>")
        rows.append(self._rate_row("└ 상품분석 속도", r30.get("analyzed", 0),
                                   r60.get("analyzed", 0),
                                   st.get("per10_analyzed"), "#ce93d8"))

        rows.append("<tr><td colspan='3'><hr style='border:0; "
                    "border-top:1px solid #2c3e50;'></td></tr>")
        rows.append(
            f"<tr><td style='padding:2px 12px 2px 6px; color:#b0bec5;'>"
            f"남은 {st['info_todo_rows']:,}개 예상</td>"
            f"<td colspan='2' style='padding:2px 6px;'>"
            f"<b style='color:#ffd54f; font-size:14px;'>"
            f"{self._fmt_eta(st.get('eta_min'))}</b>"
            f"<span style='color:#607d8b;'> &nbsp;&nbsp;실관측 "
            f"{r60.get('span_min', 0):,.1f}분 기준"
            + (f" · 중단 {r60.get('gaps', 0)}구간 제외"
               if r60.get("gaps") else "") + f"{warn}</span></td></tr>")

        if not st.get("first"):
            rows.append(
                f"<tr><td style='padding:6px 12px 2px 6px; color:#607d8b;'>"
                f"직전 대비</td><td colspan='2' style='padding:6px 6px 2px 0; "
                f"color:#90a4ae;'>이미지승인 {st['d_img_done']:+,} &nbsp;·&nbsp; "
                f"저장완료 {st['d_info_save']:+,} &nbsp;·&nbsp; "
                f"미완료 {st['d_info_todo']:+,} &nbsp;·&nbsp; "
                f"분석 {st['d_analyzed']:+,}</td></tr>")

        head = (f"<div style='color:#4fc3f7; font-size:14px;'><b>"
                f"[{st['folder_name']}]</b>"
                f"<span style='color:#607d8b; font-size:12px;'> &nbsp; "
                f"{st['scanned_at']} &nbsp;·&nbsp; {st['elapsed_sec']}초 "
                f"&nbsp;·&nbsp; {st['cycle']}회차</span></div>")
        return (head + "<table cellspacing='0' cellpadding='0'>"
                + "".join(rows) + "</table>")

    def _on_monitor_tick(self, st: dict):
        """주기마다 들어오는 현황을 보드에 그린다."""
        self.lbl_board.setText(self._board_html(st))

        # 카드/매트릭스도 같이 갱신
        self._apply_summary(st["_summary"])
        self._last_cells = st["_cells"]
        self._last_items = st["_items"]
        self._render_matrix(self._last_cells)
        self._render_items()
        self.setWindowTitle(
            f"로하스 오토 - {st['folder_name']} 미분석 {st['pending_lcps']:,}종")
        self._refresh_chart()
        self._refresh_rate_panel()

    # ------------------------------------------------------------------ 상품분석

    def on_run_analysis(self):
        work = db.get_job_folder()
        if not work:
            QMessageBox.information(
                self, "안내",
                "작업폴더가 지정되지 않았습니다." + chr(10)
                + "작업리스트에서 폴더를 선택하고 [③ 작업폴더로 지정] 을 눌러주세요.")
            return

        done = db.done_lcp_set()
        stats = db.analysis_stats()
        msg = [
            f"작업폴더 : {work}",
            "",
            "대상 : 대표이미지 승인완료 + 상품정보 미작업",
            "        (같은 LCP 는 1건만 처리)",
            f"이미 분석 기록된 LCP : {len(done):,}종 → 자동 스킵",
            f"배치 : {config.ANALYSIS_BATCH}건씩 요청 후 완료 대기",
            "",
            "실제로 상품분석 작업이 생성됩니다. 진행할까요?",
        ]
        if stats:
            msg.insert(-2, f"기록 상태 : {stats}")

        ret = QMessageBox.question(
            self, "ALL 상품분석", chr(10).join(msg),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return

        worker = AnalysisWorker(
            folder_name=work,
            batch_size=config.ANALYSIS_BATCH,
            poll_interval=config.ANALYSIS_POLL,
            batch_timeout=config.ANALYSIS_TIMEOUT,
            headless=self.chk_headless.isChecked(),
            monitor=self.cmb_monitor.currentData(),
        )
        self._start_worker(worker, self._on_analysis_done,
                           f"'{work}' ALL 상품분석 중...")

    def _on_analysis_done(self, stats: dict):
        self._reload_folders()
        lines = [
            f"검색 {stats.get('rows', 0):,}행 → LCP {stats.get('lcps', 0):,}종",
            f"기존 완료 스킵 : {stats.get('skipped', 0):,}종",
            "─" * 30,
            f"분석 대상 : {stats.get('total', 0):,}종",
            f"   완료      : {stats.get('done', 0):,}",
            f"   이미완료  : {stats.get('already', 0):,}",
            f"   오류      : {stats.get('error', 0):,}",
            f"   시간초과  : {stats.get('timeout', 0):,}",
            "",
            f"소요 : {stats.get('elapsed', 0)}초",
            "",
            "완료된 LCP 는 DB와 로컬파일에 기록되어",
            "다음 실행 때 다시 분석하지 않습니다.",
        ]
        QMessageBox.information(self, "ALL 상품분석 완료", chr(10).join(lines))

    # ------------------------------------------------------------------ 덤프

    def on_dump(self):
        work = db.get_job_folder() or ""
        worker = DumpWorker(
            folder_name=work,
            page_size=self.cmb_page_size.currentText(),
            headless=self.chk_headless.isChecked(),
            monitor=self.cmb_monitor.currentData(),
        )
        self._start_worker(worker, self._on_dumped, "페이지 구조 덤프 중...")

    def _on_dumped(self, result: dict):
        QMessageBox.information(
            self, "덤프 완료",
            f"페이지 구조를 저장했습니다.\n\n{result['path']}\n\n"
            "상태 텍스트/색상이 예상과 다르면 이 파일을 알려주세요.",
        )

    # ------------------------------------------------------------------

    def closeEvent(self, event):
        if self._monitor_worker is not None:
            self._monitor_worker.stop()
        if self._monitor_thread is not None:
            self._monitor_thread.quit()
            self._monitor_thread.wait(5000)
        if self._thread is not None:
            ret = QMessageBox.question(
                self, "종료", "작업이 실행 중입니다. 종료할까요?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ret != QMessageBox.Yes:
                event.ignore()
                return
            if self._worker is not None:
                self._worker.stop()
            self._thread.quit()
            self._thread.wait(5000)
        event.accept()
