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
from .ad_page import AdPage
from .todo_page import TodoPage
from .category_fix_page import CategoryFixPage
from .tag_review_page import TagReviewPage
from .mini_window import MiniWindow
from .product_page import ProductPage
from .workers import (AdSpendWorker, AiImageWorker, OrderWatchWorker, AnalysisWorker, BasicCollectWorker,
                      CategoryAutoWorker, DumpWorker, Fix10Worker,
                      FolderScanWorker, InspectWorker, LcodeStatusWorker,
                      SampleWorker, SoldoutSweepWorker, TagAllWorker)

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
NAV_TABS = ["대시보드", "상품정보", "미작업목록", "광고"]
NAV_INDEX = {"대시보드": 0, "상품정보": 1, "미작업목록": 4,
             "광고": 7}
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
        # 제목이 길면 줄바꿈해서 **숫자를 밀어내지 않게** 한다. 예전에는
        # 한 줄로 늘어나 카드가 찌그러지고 값이 잘렸다(2026-09-12).
        self.lbl_title.setWordWrap(True)
        self.setMinimumWidth(180)

        self.lbl_value = QLabel("-")
        self.lbl_value.setTextInteractionFlags(Qt.TextSelectableByMouse)
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
        # AI 이미지 일괄수정은 몇 시간짜리라 **공용 슬롯을 쓰지 않는다**
        self._ai_thread = None
        self._ai_worker = None
        self._ai_prog = None
        self._last_items = []
        self._last_cells = []
        self._last_info_todo = 0
        self._monitor_thread = None
        self._monitor_worker = None
        self._pending_monitor_restart = False

        db.init_db()
        self._build_ui()
        self._reload_folders()
        self._reload_history()
        # 켤 때 **작업폴더의 마지막 점검**을 그려둔다. 아무것도 안 그리면
        # 앞서 본 폴더 숫자가 남아 있는 것처럼 보인다(2026-09-08).
        try:
            self.show_folder_scan()
        except Exception:
            pass

        # 새 주문 알림 — 5분마다 커머스 API 로 오늘 주문을 훑는다
        try:
            self._start_order_watch()
        except Exception as e:
            self._log(f"[주문] 감시 시작 실패 {str(e)[:70]}")

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

        # 광고 - 계정·캠페인 설정과 지출 집계(LCP별/날짜별).
        # 대시보드에는 올리지 않는다 (2026-09-11 사용자).
        self.page_ad = AdPage(self)
        self.stack.addWidget(self.page_ad)          # 7 : 광고

        root.addWidget(self._build_topbar())

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_folder_panel())
        splitter.addWidget(self._build_right_panel())
        # 검은 현황판을 뺀 만큼 오른쪽(카드·수치)을 넓게 쓴다.
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 7)
        splitter.setSizes([480, 1120])  # 폴더명이 잘리지 않을 만큼만 좌측
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
        a = m.addAction("트레이로 내리기	Ctrl+T")
        a.setShortcut("Ctrl+T")
        a.triggered.connect(self.to_tray)

        m = mb.addMenu("설정(&S)")
        a = m.addAction("자동점검 폴더 선택...")
        a.triggered.connect(self.on_pick_monitor_folders)
        a = m.addAction("품절 이동 폴더 지정...")
        a.triggered.connect(self.on_set_soldout_folder)
        m.addSeparator()
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

    def to_tray(self):
        """
        창을 감추고 트레이 아이콘만 남긴다. 오늘 작업량이 늘면 트레이가
        알림을 띄우므로 숫자만 확인하면 된다(2026-09-06 사용자 요청).
        아이콘을 누르면 다시 큰 창이 열린다.
        """
        from .tray import Tray

        if getattr(self, "_tray", None) is None:
            self._tray = Tray(self)
        self._tray.show()
        self._tray.refresh(first=True)
        if getattr(self, "_mini", None) is not None:
            self._mini.hide()
        self.hide()
        self._tray.showMessage(
            "트레이 모드", "오늘 작업량이 늘면 여기서 알려드립니다."
            + chr(10) + "아이콘을 누르면 창이 다시 열립니다.",
            self._tray.Information if hasattr(self._tray, "Information") else 1,
            3000)

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

        self.btn_fix10 = QPushButton("🛠 수정 1.0")
        self.btn_fix10.setMinimumHeight(34)
        self.btn_fix10.setStyleSheet("font-weight:bold; color:#4527a0;")
        self.btn_fix10.setToolTip(
            "작업폴더에서 '정보수정' 이 붙은 광고상품을 찾아 1.0 수정을"
            + chr(10) + "끝까지 눌러 줍니다(로하스 품단종 처리)."
            + chr(10) + "정방향·역방향을 동시에 돌려 시간이 절반으로 줍니다."
            + chr(10) + "묶인 상품이 전부 품절인 LCP 는 건너뜁니다."
        )
        self.btn_fix10.clicked.connect(self.on_run_fix10)
        top.addWidget(self.btn_fix10)

        self.btn_ai_img = QPushButton("🎨 AI 이미지 일괄")
        self.btn_ai_img.setMinimumHeight(34)
        self.btn_ai_img.setStyleSheet("font-weight:bold; color:#6a1b9a;")
        self.btn_ai_img.setToolTip(
            "폴더의 한 페이지(최대 1,000건)를 AI 이미지 일괄수정에 겁니다."
            + chr(10) + "작업명과 질의어를 확인하고 [적용하기] 를 누르면"
            + chr(10) + "로하스에서 이미지 변형 작업이 시작됩니다."
            + chr(10) + "일괄작업은 1회만 — 앞 작업이 끝나야 다음이 돕니다."
        )
        self.btn_ai_img.clicked.connect(self.on_run_ai_image)
        top.addWidget(self.btn_ai_img)

        self.btn_analysis = QPushButton("🔬 ALL 상품분석")
        self.btn_analysis.setMinimumHeight(34)
        self.btn_analysis.setStyleSheet("font-weight:bold; color:#b71c1c;")
        self.btn_analysis.setToolTip(
            "작업폴더의 '대표이미지 승인완료 + 상품정보 미작업' 상품을"
            + chr(10) + "LCP 단위로 골라 상품분석을 실행합니다."
            + chr(10) + "이미 분석한 LCP 는 자동으로 건너뜁니다."
        )
        self.btn_analysis.setToolTip(
            "폴더 전체를 훑어 LCP 마다 1건씩 상품분석을 겁니다."
            + chr(10) + "대표이미지가 아직 안 끝난 것도 포함합니다."
            + chr(10) + "이미 분석한 LCP 는 자동으로 건너뜁니다.")
        self.btn_analysis.clicked.connect(self.on_run_analysis)
        top.addWidget(self.btn_analysis)

        self.btn_category = QPushButton("🗂 ALL 카테고리")
        self.btn_category.setMinimumHeight(34)
        self.btn_category.setStyleSheet("font-weight:bold; color:#00695c;")
        self.btn_category.setToolTip(
            "폴더를 골라 카테고리를 끝까지 채웁니다."
            + chr(10) + "대상은 상품정보 '미작업' 인 것만 (대표이미지 상태 무관)."
            + chr(10) + "카테고리 저장은 상품정보 상태를 바꾸지 않습니다.")
        self.btn_category.clicked.connect(self.on_run_category_all)
        top.addWidget(self.btn_category)

        self.btn_tag_all = QPushButton("🏷 ALL 태그")
        self.btn_tag_all.setMinimumHeight(34)
        self.btn_tag_all.setStyleSheet("font-weight:bold; color:#4527a0;")
        self.btn_tag_all.setToolTip(
            "폴더를 골라 태그를 끝까지 채웁니다."
            + chr(10) + "대상은 카테고리 저장 + 상품정보 '미작업' (이미지 상태 무관)."
            + chr(10) + "태그 저장은 상품정보 상태를 바꾸지 않습니다.")
        self.btn_tag_all.clicked.connect(self.on_run_tag_all)
        top.addWidget(self.btn_tag_all)

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
        # 검은 현황판은 화면만 차지하고 카드와 내용이 겹쳐서 숨긴다.
        # 코드 여기저기서 setText 를 부르므로 위젯 자체는 남겨 둔다
        # (2026-09-12 사용자: 전체화면에서 숫자가 찌그러진다).
        self.lbl_board = QLabel("자동점검을 시작하면 현황이 여기에 표시됩니다.")
        self.lbl_board.hide()
        self.lbl_board.setStyleSheet(
            "QLabel { background:#0d1b2a; color:#e0e1dd; border-radius:6px;"
            " padding:12px 16px; font-family:'Consolas','D2Coding',monospace;"
            " font-size:13px; }")
        self.lbl_board.setWordWrap(True)
        lay.addWidget(self.lbl_board)

        # ---- 요약 카드 ----
        cards = QGroupBox("점검 결과")
        cbox = QVBoxLayout(cards)
        # **이 숫자가 어느 폴더 것인지** 를 크게 적는다. 없어서 폴더를 바꾼
        # 뒤에도 앞 폴더 숫자를 보고 "전부 틀리다" 는 오해가 났다
        # (2026-09-08 사용자 지적).
        self.lbl_card_src = QLabel("아직 점검하지 않았습니다.")
        self.lbl_card_src.setWordWrap(True)
        self.lbl_card_src.setStyleSheet(
            "font-size:14px; font-weight:bold; color:#0d47a1;"
            "background:#e3f2fd; border-radius:6px; padding:7px 10px;")
        cbox.addWidget(self.lbl_card_src)
        gw = QWidget()
        grid = QGridLayout(gw)
        grid.setContentsMargins(0, 0, 0, 0)
        cbox.addWidget(gw)
        self.card_target = StatCard("★ 작업대상 (이미지승인완료+정보미작업)", "#2e7d32")
        self.card_target_lcp = StatCard("★ 작업대상 LCP 종수", "#2e7d32")
        self.card_total = StatCard("전체 행(L코드)", "#263238")
        self.card_done_total = StatCard("전체 작업완료 (상품정보 저장완료)", "#00695c")
        self.card_img_done = StatCard("대표이미지 승인완료", "#1565c0")
        self.card_img_work = StatCard("대표이미지 작업중(승인전)", "#6a1b9a")
        # 「상품정보 미작업」은 **이미지승인완료 상태에서 미작업**인 것이다.
        # 대표이미지가 아직 미작업·이미지작업인 것은 여기 안 든다
        # (2026-09-09 사용자). 예전에는 이미지 상태를 안 가린 전체 미작업
        # (info_todo_rows)을 보여줘 작업대상 0 인데 21 로 떴다.
        self.card_info_todo = StatCard(
            "상품정보 미작업 (이미지승인완료)", "#e65100")
        self.card_today = StatCard("오늘 작업량 (저장완료 기준)", "#c62828")
        # 광고 — 값은 DB(ad_spend/ad_account)에서 읽는다. 점검과 주기가
        # 달라서 [광고 새로고침] 이 눌렸을 때만 API 를 부른다
        # (2026-09-12 사용자: 대시보드에도 띄워달라).
        self.card_ad_today = StatCard("오늘 광고비", "#ad1457")
        self.card_ad_sales = StatCard("오늘 전환매출", "#2e7d32")
        self.card_ad_biz = StatCard("비즈머니 잔액", "#4527a0")
        # 광고 규모 — 등록만 많고 안 도는 것이 많아 **등록/실제**를 나눈다
        self.card_ad_lcp = StatCard("광고 LCP", "#00695c")
        self.card_ad_prod = StatCard("광고 상품(소재)", "#1565c0")
        self.card_ad_kw = StatCard("유입 검색어", "#6a1b9a")

        for i, c in enumerate([
            self.card_target, self.card_target_lcp,
            self.card_total, self.card_done_total,
            self.card_img_done, self.card_img_work,
            self.card_info_todo, self.card_today,
            self.card_ad_today, self.card_ad_sales, self.card_ad_biz,
            self.card_ad_lcp, self.card_ad_prod, self.card_ad_kw,
        ]):
            grid.addWidget(c, i // 5, i % 5)
        # 다섯 칸을 고르게 나눈다. 안 주면 긴 제목이 있는 칸만 넓어진다.
        for col in range(5):
            grid.setColumnStretch(col, 1)

        adrow = QHBoxLayout()
        self.lbl_ad = QLabel("광고 : 아직 받아오지 않았습니다.")
        self.lbl_ad.setStyleSheet("color:#57606a;")
        adrow.addWidget(self.lbl_ad, 1)
        self.btn_ad_stats = QPushButton("📈 광고비 통계")
        self.btn_ad_stats.setStyleSheet("font-weight:bold; color:#ad1457;")
        self.btn_ad_stats.setToolTip(
            "기간별(7·14·21·30일) 그래프·매체별·TOP10·LCP별 매출을"
            + chr(10) + "한 창에서 봅니다. 엑셀로도 받을 수 있습니다.")
        self.btn_ad_stats.clicked.connect(self.on_ad_stats)
        adrow.addWidget(self.btn_ad_stats)

        self.btn_ad_refresh = QPushButton("💰 광고비 새로고침")
        self.btn_ad_refresh.setToolTip(
            "켜 둔 광고계정·캠페인의 오늘 지출과 비즈머니 잔액을 받아옵니다."
            + chr(10) + "읽기 전용입니다 - 광고를 바꾸지 않습니다."
            + chr(10) + "고르는 것은 「광고」 탭에서 합니다.")
        self.btn_ad_refresh.clicked.connect(self.on_refresh_ad)
        adrow.addWidget(self.btn_ad_refresh)
        cbox.addLayout(adrow)
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
                  self.btn_analysis, self.btn_category,
                  self.page_product.btn_status,
                  self.page_product.btn_basic,
                  self.btn_add_master, self.btn_set_job,
                  self.btn_del_master, self.btn_dump):
            b.setEnabled(not busy)
        self.btn_stop.setEnabled(busy)
        if busy:
            self.progress.setRange(0, 0)
            self.progress.setFormat(label or "실행 중...")
            # 앞 작업이 남긴 문구를 지운다. 안 지우면 수정1.0 을 눌렀는데
            # 상품분석/AI 진행 문구가 그대로 떠 있다(2026-09-08 사용자 지적).
            self.lbl_task.setText(label or "실행 중...")
            self.setWindowTitle(f"로하스 오토 - {label or '실행 중'}")
        else:
            self.progress.setRange(0, 1)
            self.progress.setValue(0)
            self.progress.setFormat("대기 중")
            self.lbl_task.setText("")
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

    # ------------------------------------------------------------ 광고비
    def _refresh_ad_cards(self):
        """
        카드 값은 **DB 에서만** 읽는다. 대시보드가 다시 그려질 때마다
        네이버 API 를 부르면 점검 주기(30초)마다 광고 API 를 두들기게 된다.
        받아오는 것은 [광고비 새로고침] 과 「광고」 탭이 한다.
        """
        try:
            from ..lohas import ad_account, ad_spend
            t = ad_spend.today_cost()
            main = ad_account.main_account()
        except Exception:
            return
        cost = int(t["cost"] or 0)
        amt = int(t.get("conv_amt") or 0)
        self.card_ad_today.set_value(f"{cost:,}원")
        self.card_ad_today.lbl_title.setText(
            f"오늘 광고비 ({t['day']})"
            + (f"  ·  클릭 {int(t['clk'] or 0):,}" if t.get("clk") else ""))
        # **네이버 전환매출은 허수다** — 장바구니 담기가 섞여 있다. 실제
        # 매출은 주문 DB(joacham.orders_order)에서 가져온다. 2026-09-11 에
        # 네이버는 66,200원이라 했지만 구매완료는 8,770원뿐이었다
        # (2026-09-12 사용자: "오늘전환매출 이건 허수잔어").
        try:
            from ..lohas import ad_sales
            sale = ad_sales.today()
        except Exception:
            sale = {}
        amt = int(sale.get("amount") or 0)
        self.card_ad_sales.set_value(f"{amt:,}원")
        roas = (amt * 100 // cost) if cost else 0
        self.card_ad_sales.lbl_title.setText(
            f"오늘 실매출(커머스)  ·  {int(sale.get('orders') or 0):,}건"
            + (f"  ·  ROAS {roas:,}%" if cost else "")
            + (f"   [네이버추정 {int(t.get('conv_amt') or 0):,}]"
               if int(t.get("conv_amt") or 0) != amt else ""))
        self.card_ad_sales.lbl_value.setStyleSheet(
            "border:none; color:"
            + ("#2e7d32" if roas >= 300 else
               "#ef6c00" if roas > 0 else "#9e9e9e"))
        # **메인 계정 하나만** 보여준다. 여러 계정을 더하면 그 숫자가
        # 무엇인지 알 수 없다 (2026-09-12 사용자).
        if not main:
            self.card_ad_biz.set_value("-")
            self.card_ad_biz.lbl_title.setText("비즈머니 잔액")
            self.lbl_ad.setText(
                "광고 : 메인 계정이 없습니다 — 「광고」 탭에서 고르십시오.")
            return
        bal = int(main["bizmoney"] or 0)
        self.card_ad_biz.set_value(f"{bal:,}원")
        self.card_ad_biz.lbl_title.setText(
            f"비즈머니 잔액 — {main['label'] or main['customer_id']}"
            + ("  ·  ⚠ 소진" if bal <= 0 else ""))
        # 광고 규모 카드 — `ad_creative.summary`
        try:
            from ..lohas import ad_creative
            s = ad_creative.summary(main["customer_id"])
        except Exception:
            s = {}
        if s:
            self.card_ad_lcp.set_value(f"{s['live_lcp']:,}")
            self.card_ad_lcp.lbl_title.setText(
                f"광고 LCP (최근 {s['days']}일 노출)"
                f"  ·  등록 {s['lcp']:,}  ·  지출 {s['paid_lcp']:,}")
            self.card_ad_prod.set_value(f"{s['ads']:,}")
            self.card_ad_prod.lbl_title.setText(
                f"광고 상품(소재) 등록  ·  노출 {s['live_ads']:,}"
                + (f"  ·  ⚠ 보류 {s['ads_hold']:,}" if s["ads_hold"] else ""))
            self.card_ad_kw.set_value(f"{s['queries']:,}")
            self.card_ad_kw.lbl_title.setText(
                f"유입 검색어 (최근 {s['days']}일)")
        self.lbl_ad.setText(
            f"광고 : {main['label'] or main['customer_id']}"
            f" ({main['customer_id']})"
            + (f"   ·  마지막 수집 {t['upd']}" if t.get("upd")
               else "   ·  아직 지출을 받아오지 않았습니다"))

    def on_ad_stats(self):
        """광고 성과 보고서 창. 한 번 만들고 다시 쓴다."""
        from .ad_report_dialog import AdReportDialog
        if getattr(self, "_ad_report", None) is None:
            self._ad_report = AdReportDialog(self)
        else:
            self._ad_report.refresh()
        self._ad_report.show()
        self._ad_report.raise_()

    # ---------------------------------------------------------- 주문 알림
    def _start_order_watch(self):
        """
        새 주문이 들어오면 알린다. **주문은 실시간으로 잡힌다**
        (2026-09-12 사용자). 5분마다 오늘치를 훑어 늘어난 것만 본다.
        """
        from PySide6.QtWidgets import QSystemTrayIcon
        self._order_ids = set()
        self._order_first = True
        try:
            self._tray = QSystemTrayIcon(self.windowIcon(), self)
            self._tray.setToolTip("로하스 오토 — 새 주문 알림")
            self._tray.show()
        except Exception:
            self._tray = None
        self._order_timer = QTimer(self)
        self._order_timer.timeout.connect(self._check_orders)
        self._order_timer.start(5 * 60 * 1000)
        QTimer.singleShot(4000, self._check_orders)

    def _check_orders(self):
        if getattr(self, "_order_thread", None) is not None:
            return
        worker = OrderWatchWorker(getattr(self, "_order_ids", set()))
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_orders)
        worker.failed.connect(lambda m: self._log(f"[주문] {m[:90]}"))
        for sig in (worker.finished, worker.failed):
            sig.connect(lambda *_: thread.quit())
        thread.finished.connect(self._on_order_thread_done)
        self._order_thread, self._order_worker = thread, worker
        thread.start()

    def _on_orders(self, res: dict):
        if res.get("skip"):
            return
        self._order_ids = set(res.get("ids") or ())
        new = res.get("new") or []
        # 앱을 막 켰을 때는 **이미 있던 주문으로 알림을 띄우지 않는다.**
        if self._order_first:
            self._order_first = False
            if new:
                self._log(f"[주문] 오늘 이미 {len(new)}건 들어와 있습니다")
            self._refresh_ad_cards()
            return
        if not new:
            return
        amt = sum(x["amount"] for x in new)
        head = f"새 주문 {len(new)}건 · {amt:,}원"
        lines = [f"{x['amount']:,}원 x{x['qty']} "
                 f"{'[광고] ' if x['ad'] else ''}{x['name'][:28]}"
                 for x in new[:5]]
        self._log("[주문] " + head)
        for l in lines:
            self._log("        " + l)
        if getattr(self, "_tray", None) is not None:
            from PySide6.QtWidgets import QSystemTrayIcon
            self._tray.showMessage(head, chr(10).join(lines),
                                   QSystemTrayIcon.Information, 12000)
        try:
            from ..lohas import commerce
            r = commerce.sales(res.get("day"), log=lambda *_: None)
            if r["orders"]:
                commerce.save(r, log=lambda *_: None)
        except Exception:
            pass
        self._refresh_ad_cards()

    def _on_order_thread_done(self):
        if getattr(self, "_order_thread", None) is not None:
            self._order_thread.deleteLater()
        if getattr(self, "_order_worker", None) is not None:
            self._order_worker.deleteLater()
        self._order_thread = None
        self._order_worker = None

    def on_refresh_ad(self):
        """오늘 지출과 잔액만 받아온다. 점검 작업과 겹쳐도 되게 별도 슬롯."""
        from ..lohas import searchad
        if not searchad.available():
            QMessageBox.information(
                self, "광고", ".env 에 네이버 검색광고 API 키가 없습니다.")
            return
        if getattr(self, "_ad_thread", None) is not None:
            QMessageBox.information(self, "광고", "이미 받아오는 중입니다.")
            return
        from ..lohas import ad_account
        base = not ad_account.accounts()      # 처음이면 기본정보부터
        worker = AdSpendWorker(days=1, base=base)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.log.connect(self._log)
        worker.failed.connect(
            lambda m: (self._log(f"[광고] {m}"),
                       self.btn_ad_refresh.setEnabled(True)))
        worker.finished.connect(self._on_ad_done)
        for sig in (worker.finished, worker.failed):
            sig.connect(lambda *_: thread.quit())
        thread.finished.connect(self._on_ad_thread_done)
        self._ad_thread, self._ad_worker = thread, worker
        self.btn_ad_refresh.setEnabled(False)
        self.btn_ad_refresh.setText("받는 중…")
        thread.start()

    def _on_ad_done(self, res: dict):
        self._refresh_ad_cards()
        if getattr(self, "page_ad", None) is not None:
            self.page_ad.refresh()
        self._log(f"[광고] 오늘 지출 {res.get('cost', 0):,}원 "
                  f"· {res.get('rows', 0):,}행")

    def _on_ad_thread_done(self):
        if getattr(self, "_ad_thread", None) is not None:
            self._ad_thread.deleteLater()
        if getattr(self, "_ad_worker", None) is not None:
            self._ad_worker.deleteLater()
        self._ad_thread = None
        self._ad_worker = None
        self.btn_ad_refresh.setEnabled(True)
        self.btn_ad_refresh.setText("💰 광고비 새로고침")

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
        # 바뀐 폴더의 마지막 점검을 바로 보여준다 - 앞 폴더 숫자가 남아
        # 있으면 "숫자가 전부 틀리다" 로 보인다(2026-09-08)
        if not self.show_folder_scan(name):
            self._log(f"'{name}' 은 점검 기록이 없습니다.")
        # **자동점검도 바뀐 폴더를 따라간다.** 안 따라가면 작업량(work_log)이
        # 앞 폴더로만 쌓여 '오늘 작업량' 이 0 으로 보인다 — 엑사로 바꿨는데
        # 594 를 계속 재고 있었다(2026-09-10 사용자).
        # 현황판(검은 창)은 자동점검이 그린다. 폴더를 바꾸면 앞 폴더 숫자가
        # 그대로 남아 헷갈리므로 그 자리에서 비운다(2026-09-10 사용자).
        self.lbl_board.setText(
            f"[{name}] 자동점검을 기다리는 중..."
            if self._monitor_thread is None
            else f"[{name}] 폴더를 옮기는 중...")

        picked_now = [x for x in db.get_setting(self.SETTING_MONITOR, "").split("|")
                      if x]
        if self._monitor_thread is not None and not picked_now:
            # 자동점검 폴더를 따로 고르지 않았으면 작업폴더를 따라간다.
            # 골라뒀으면 그 목록이 사용자의 뜻이니 건드리지 않는다.
            self._log(f"자동점검을 '{name}' 로 옮깁니다")
            self._pending_monitor_restart = True
            self._stop_monitor()
        elif self._monitor_thread is not None and name not in picked_now:
            self._log(f"※ 자동점검 폴더 목록에 '{name}' 이 없습니다."
                      " [자동점검 폴더 선택] 에서 넣어야 작업량이 쌓입니다.")

        # **바로 다시 잰다.** HTTP 점검은 5초면 끝나므로 기다릴 것이 없고,
        # 며칠 전 숫자를 그대로 보여주면 "안 맞는다" 가 된다(2026-09-10).
        if self._thread is None:
            self._log(f"'{name}' 최신 상태로 다시 점검합니다...")
            self.on_inspect(quick=False)
        else:
            self._log("다른 작업이 도는 중이라 점검은 건너뜁니다."
                      " 끝나면 [④ 전체 점검] 을 눌러주세요.")

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

    def show_folder_scan(self, folder: str = "") -> bool:
        """
        그 폴더의 **마지막 점검 결과**를 카드·매트릭스에 그린다.

        폴더를 바꿔도 카드가 그대로여서 앞 폴더 숫자를 보고 있게 되던 것을
        고친 것이다(2026-09-08 사용자 지적). 기록이 없으면 비운다.
        """
        folder = folder or db.get_job_folder()
        if not folder:
            return False
        scan = db.latest_scan(folder)
        if not scan:
            self.lbl_card_src.setText(
                f"[{folder}]  아직 점검한 기록이 없습니다."
                "   ④ 전체 점검을 눌러주세요.")
            self.lbl_card_src.setStyleSheet(
                "font-size:14px; font-weight:bold; padding:7px 10px;"
                "border-radius:6px; color:#e65100; background:#fff3e0;")
            for c in (self.card_target, self.card_target_lcp, self.card_total,
                      self.card_done_total, self.card_img_done,
                      self.card_img_work, self.card_info_todo):
                c.set_value("-")
            self._last_cells, self._last_items = [], []
            self._render_matrix([])
            self._render_items()
            return False
        self._last_items = db.list_scan_items(scan["id"])
        self._last_cells = db.list_scan_cells(scan["id"])
        self._apply_summary(scan)
        self._render_matrix(self._last_cells)
        self._render_items()
        return True

    def _apply_summary(self, s_: dict):
        work = db.get_job_folder() or ""
        src = s_.get("folder_name") or ""
        mark = "" if (not work or src == work) else "   ⚠ 작업폴더와 다른 폴더입니다"
        # **언제 잰 숫자인지 밝힌다.** 폴더를 바꾸면 그 폴더의 마지막 점검을
        # 보여주는데, 그게 며칠 전 것이면 "숫자가 안 맞는다" 가 된다 —
        # 엑사로 바꾸니 이틀 전 기록(승인완료 43)이 떴고 실제는 989였다
        # (2026-09-10 사용자).
        age = ""
        try:
            import datetime as _dt
            t = _dt.datetime.strptime(str(s_.get("scanned_at") or ""),
                                      "%Y-%m-%d %H:%M:%S")
            m = (_dt.datetime.now() - t).total_seconds() / 60
            if m >= 60 * 24:
                age = f"   ⚠ {int(m // (60 * 24))}일 전 기록입니다"
            elif m >= 30:
                age = f"   ⚠ {int(m // 60)}시간 {int(m % 60)}분 전 기록입니다"
        except Exception:
            pass
        if age:
            mark += age
        self.lbl_card_src.setText(
            f"[{src or '-'}]  {s_.get('scanned_at', '-')} 점검"
            + ("  ·  빠른 점검(작업대상만)" if s_.get("mode") == "quick" else "")
            + mark)
        self.lbl_card_src.setStyleSheet(
            "font-size:14px; font-weight:bold; padding:7px 10px;"
            "border-radius:6px;"
            + ("color:#b71c1c; background:#ffebee;" if mark
               else "color:#0d47a1; background:#e3f2fd;"))
        if s_.get("mode") != "quick":
            # '남은 N개' 도 실제로 지금 할 수 있는 것 = 작업대상이다
            self._last_info_todo = s_.get("target_rows") or 0
        # 빠른 점검은 작업대상 한 칸만 재므로 나머지 합계는 '-' 로 표시한다
        quick = (s_.get("mode") == "quick")

        def val(key):
            return "-" if quick else f"{s_[key]:,}"

        self.card_target.set_value(f"{s_['target_rows']:,}")
        self.card_target_lcp.set_value(f"{s_['target_lcps']:,}")
        self.card_total.set_value(val("total_rows"))
        self.card_img_done.set_value(val("img_done_rows"))
        self.card_img_work.set_value(val("img_work_rows"))
        self.card_info_todo.set_value(f"{s_['target_rows']:,}")
        # 이미지가 아직 안 끝나 작업대상에 못 드는 미작업도 같이 알려준다.
        # 숫자가 사라지면 "어디 갔냐" 가 되므로 부제로 남긴다.
        not_ready = 0 if quick else max(
            0, (s_.get("info_todo_rows") or 0) - (s_.get("target_rows") or 0))
        self.card_info_todo.lbl_title.setText(
            "상품정보 미작업 (이미지승인완료)"
            + (f"   ·  이미지 미완료 {not_ready:,}" if not_ready else ""))
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
        self._refresh_ad_cards()   # 광고비 카드 (DB 에서만 읽는다)
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
        picked = self.monitor_folders()
        work = picked[0] if picked else ""
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
            folder_name=picked, interval=interval,
            headless=self.chk_headless.isChecked(),
            monitor=self.cmb_monitor.currentData(),
        )
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.log.connect(self._log)
        worker.tick.connect(self._on_monitor_tick)
        # 걸어둔 AI 이미지 작업이 끝나면 팝업으로 알린다(2026-09-07 사용자)
        worker.ai_done.connect(self._on_ai_job_done)
        worker.failed.connect(self._on_monitor_failed)
        worker.finished.connect(lambda *_: thread.quit())
        thread.finished.connect(self._on_monitor_thread_done)

        self._monitor_thread, self._monitor_worker = thread, worker
        self.lbl_monitor_state.setText("● 실행중")
        self.lbl_monitor_state.setStyleSheet("color:#2e7d32; font-weight:bold;")
        self.lbl_board.setText(f"[{work}] 첫 점검 중...")
        self._log(f"자동점검 시작 : {' / '.join(picked)} ({interval}초 주기)")
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
        # 폴더를 바꾸느라 멈춘 것이면 새 폴더로 다시 켠다
        if getattr(self, "_pending_monitor_restart", False):
            self._pending_monitor_restart = False
            self.chk_monitor.blockSignals(True)
            self.chk_monitor.setChecked(True)
            self.chk_monitor.blockSignals(False)
            self._start_monitor()

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

        # 상품정보 미작업 / 미분석
        #
        # **이미지승인완료 상태에서 미작업인 것**만 센다(= target_rows).
        # 대표이미지가 아직 미작업·이미지작업인 것은 지금 할 수 없는 일이라
        # 여기 들면 안 된다 — 작업대상 0 인데 21 로 떠 헷갈렸다
        # (2026-09-09 사용자).
        _todo = st.get("target_rows", st.get("info_todo_rows", 0)) or 0
        _later = max(0, (st.get("info_todo_rows") or 0) - _todo)
        rows.append(
            f"<tr><td style='padding:2px 12px 2px 6px; color:#b0bec5;'>"
            f"상품정보 미작업</td>"
            f"<td align='right' style='padding:2px 6px;'>"
            f"{big(_todo, '#ff8a65')}</td>"
            f"<td style='padding:2px 6px; color:#78909c;'>개"
            + (f" &nbsp;&nbsp;(이미지 미완료 {_later:,}개는 제외)"
               if _later else "")
            + f"</td></tr>")
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
            f"남은 {_todo:,}개 예상</td>"
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

        # 카드/매트릭스는 **작업폴더 차례일 때만** 갱신한다.
        # 폴더를 여럿 돌리면 30초마다 다른 폴더 숫자로 바뀌어 화면이 튄다
        # (2026-09-08 사용자 지적).
        work = db.get_job_folder()
        if work and st.get("folder_name") != work:
            self._refresh_chart()
            self._refresh_rate_panel()
            return
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

    def on_run_fix10(self):
        """수정사항 1.0 창을 띄운다 — 바로 실행하거나 요일·시각을 예약한다."""
        from .fix10_dialog import Fix10Dialog

        Fix10Dialog(self, on_run=self._fix10_start,
                    on_sweep=self._soldout_sweep).exec()

    def _fix10_start(self, folders=None):
        folders = [f for f in (folders or [db.get_job_folder()]) if f]
        if not folders:
            QMessageBox.information(
                self, "안내",
                "작업폴더가 지정되지 않았습니다." + chr(10)
                + "작업리스트에서 폴더를 선택하고 [③ 작업폴더로 지정] 을 눌러주세요.")
            return
        # 품절부터 정리하고 시작한다. 전상품품절은 1.0 수정이 안 되므로
        # 먼저 품절 폴더로 넘겨두면 뒤가 깔끔하다(2026-09-06 사용자).
        if not self._sweep_first(folders):
            return
        worker = Fix10Worker(folder_name=folders, both=True)
        label = (folders[0] if len(folders) == 1
                 else f"폴더 {len(folders)}개")
        self._start_worker(worker, self._on_fix10_done,
                           f"'{label}' 수정사항 1.0 일괄 진행 중...")

    # -------------------------------------------------------- AI 이미지 일괄

    def on_run_ai_image(self):
        """
        AI 이미지 일괄수정.

        **이미 돌고 있으면 진행 창을 다시 열어준다.** 예전에는 다시 누르면
        "이미 실행 중인 작업이 있습니다" 만 떴다(2026-09-07 사용자 지적).
        """
        from .ai_image_dialog import AiImageDialog
        from ..lohas import ai_image, session as ses

        if getattr(self, "_ai_thread", None) is not None:
            if getattr(self, "_ai_prog", None) is not None:
                self._ai_prog.show()
                self._ai_prog.raise_()
                self._ai_prog.activateWindow()
            else:
                QMessageBox.information(
                    self, "AI 이미지 일괄수정",
                    "이미 진행 중입니다." + chr(10)
                    + str(db.ai_job_get()))
            return

        def read_jobs():
            return ai_image.jobs(ses.get_client().session)

        AiImageDialog(self, on_run=self._ai_image_start,
                      jobs=read_jobs).exec()

    def _ai_image_start(self, folder, page_from, page_to, title, query):
        """
        AI 작업은 **제 스레드에서 따로 돈다.**

        몇 시간짜리라 공용 작업 슬롯을 쓰면 그동안 점검·상품분석·수정1.0 이
        전부 "이미 실행 중" 으로 막힌다(2026-09-07 사용자 지적).
        """
        from PySide6.QtCore import QThread

        from .ai_image_progress import AiImageProgress

        worker = AiImageWorker(
            folder_name=folder, page_from=page_from, page_to=page_to,
            title=title, query=query,
            headless=self.chk_headless.isChecked(),
            monitor=self.cmb_monitor.currentData())
        prog = AiImageProgress(self, on_stop=worker.stop)
        prog.set_plan(folder, page_from, page_to, query)
        prog.show()

        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.log.connect(self._log)
        worker.log.connect(prog.append)
        worker.stat.connect(prog.on_stat)
        worker.stat.connect(self._on_ai_stat)
        worker.finished.connect(self._on_ai_image_done)
        worker.finished.connect(prog.done_all)
        worker.failed.connect(lambda m: prog.append(f"!! {m}"))
        worker.failed.connect(self._on_ai_image_failed)
        for sig in (worker.finished, worker.failed):
            sig.connect(lambda *_: thread.quit())
        thread.finished.connect(self._on_ai_thread_done)

        self._ai_thread, self._ai_worker, self._ai_prog = thread, worker, prog
        self.btn_ai_img.setText("🎨 AI 이미지 (진행중)")
        self.btn_ai_img.setStyleSheet(
            "font-weight:bold; color:#fff; background:#6a1b9a;")
        self._log(f"[AI이미지] '{folder}' {page_from}~{page_to}페이지 시작")
        thread.start()

    def _on_ai_stat(self, st: dict):
        """진행 상태를 버튼에도 적어 어디서든 보이게 한다."""
        self.btn_ai_img.setText(
            f"🎨 AI {st.get('page')}p {st.get('state', '')}"[:28])

    def _on_ai_thread_done(self):
        if getattr(self, "_ai_thread", None) is not None:
            self._ai_thread.deleteLater()
        if getattr(self, "_ai_worker", None) is not None:
            self._ai_worker.deleteLater()
        self._ai_thread = None
        self._ai_worker = None
        self.btn_ai_img.setText("🎨 AI 이미지 일괄")
        self.btn_ai_img.setStyleSheet("font-weight:bold; color:#6a1b9a;")

    def _on_ai_image_failed(self, msg: str):
        self._log(f"[AI이미지] 실패 : {msg}")
        QMessageBox.warning(self, "AI 이미지 일괄수정", msg)

    def _on_ai_image_done(self, res: dict):
        lines = [
            f"폴더 : {res.get('folder', '')}",
            f"페이지 : {res.get('from')} ~ {res.get('to')} "
            f"({res.get('pages', 0)}회 완료)",
            f"질의어 : {res.get('query', '')}",
            f"대상 : {res.get('count', 0):,}건",
            f"소요 : {res.get('seconds', 0) / 60:.1f}분",
        ]
        for r in (res.get("results") or [])[-8:]:
            j = r.get("job") or {}
            lines.append(f"  · {r.get('title')} {r.get('count', 0):,}건 "
                         f"{j.get('상태', '')} {j.get('완료일시') or ''}")
        QMessageBox.information(self, "AI 이미지 일괄수정 완료",
                                chr(10).join(lines))

    def _on_ai_job_done(self, rec: dict):
        """자동점검이 '걸어둔 AI 작업이 끝났다' 고 알려올 때 띄운다."""
        msg = chr(10).join([
            f"No.{rec.get('no', '')}  {rec.get('title', '')}",
            f"폴더 : {rec.get('folder', '')} / {rec.get('page', '')}페이지",
            f"대상 : {rec.get('count', 0):,}건",
            f"상태 : {rec.get('state', '')}",
            f"시작 {rec.get('started') or '-'} ~ 완료 {rec.get('ended') or '-'}",
        ])
        self._log(f"[AI이미지] 완료 — {rec.get('title', '')}")
        tray = getattr(self, "_tray", None)
        if tray is not None and tray.isVisible():
            tray.showMessage("AI 이미지 일괄수정 완료", msg,
                             tray.Information if hasattr(tray, "Information")
                             else 1, 6000)
        QMessageBox.information(self, "AI 이미지 일괄수정 완료", msg)

    SETTING_MONITOR = "monitor_folders"

    def monitor_folders(self) -> list:
        """자동점검이 돌 폴더. 안 골랐으면 작업폴더 하나."""
        v = db.get_setting(self.SETTING_MONITOR, "")
        picked = [x for x in v.split("|") if x]
        master = db.list_master_folders()
        picked = [f for f in picked if f in master]
        return picked or [f for f in [db.get_job_folder()] if f]

    def on_pick_monitor_folders(self):
        """
        자동점검이 돌 폴더를 고른다. 여럿 고르면 주기마다 번갈아 본다.

        폴더가 늘었는데 작업폴더 하나만 보면 나머지 작업량이 안 잡힌다
        (2026-09-06 사용자 요청).
        """
        from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox,
                                       QVBoxLayout)

        master = db.list_master_folders()
        if not master:
            QMessageBox.information(self, "안내", "작업대상 폴더가 없습니다.")
            return
        now = set(self.monitor_folders())
        dlg = QDialog(self)
        dlg.setWindowTitle("자동점검 폴더 선택")
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel("자동점검이 돌 폴더를 고르세요."
                           + chr(10) + "여럿 고르면 주기마다 번갈아 점검합니다."))
        boxes = []
        for f in master:
            c = QCheckBox(f)
            c.setChecked(f in now)
            boxes.append(c)
            v.addWidget(c)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)
        if dlg.exec() != QDialog.Accepted:
            return
        picked = [c.text() for c in boxes if c.isChecked()]
        db.set_setting(self.SETTING_MONITOR, "|".join(picked))
        QMessageBox.information(
            self, "저장",
            ("자동점검 폴더 : " + chr(10) + chr(10).join("  " + f for f in picked))
            if picked else "고른 폴더가 없어 작업폴더만 점검합니다.")

    def on_set_soldout_folder(self):
        """
        전상품품절을 옮길 임의분류를 고른다.

        폴더 이름은 사용자마다 다르다. '품절' 이 든 폴더를 먼저 보여줘
        고르기 쉽게 한다(2026-09-06 사용자 요청).
        """
        from PySide6.QtWidgets import QInputDialog
        from .fix10_dialog import SETTING_SOLDOUT

        with db.sqlite_conn() as c:
            allf = [r["name"] for r in c.execute(
                "SELECT name FROM folder ORDER BY sort_order, name")]
        hot = [f for f in allf if "품절" in f]
        items = ["(옮기지 않음)"] + hot + [f for f in allf if f not in hot]
        cur = db.get_setting(SETTING_SOLDOUT, "")
        idx = items.index(cur) if cur in items else (1 if hot else 0)
        msg = ("전상품품절인 광고상품을 어느 임의분류로 넘길까요?" + chr(10)
               + ("'품절' 이 든 폴더를 위에 뒀습니다: "
                  + ", ".join(hot) if hot else ""))
        v, ok = QInputDialog.getItem(self, "품절 이동 폴더 지정", msg,
                                     items, idx, False)
        if not ok:
            return
        v = "" if v.startswith("(") else v
        db.set_setting(SETTING_SOLDOUT, v)
        QMessageBox.information(
            self, "저장",
            (f"전상품품절은 '{v}' 로 넘깁니다." if v
             else "품절 상품을 옮기지 않습니다.")
            + chr(10) + "로컬 DB 와 서버 DB 양쪽에 저장했습니다.")

    def _sweep_first(self, folders) -> bool:
        """
        1.0 을 돌리기 전에 품절부터 찾아 보여주고, 옮길지 물어본다.

        돌려주는 값이 False 면 사용자가 취소한 것이다.
        """
        from PySide6.QtWidgets import QApplication
        from ..lohas import fix10
        from ..lohas.session import get_client
        from .fix10_dialog import SETTING_SOLDOUT

        target = db.get_setting(SETTING_SOLDOUT, "")
        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            cli = get_client(False, 0, log=self._log)
            found = []
            for f in folders:
                for r in fix10.soldout_rows(cli.session, f):
                    found.append((f, r))
        except Exception as e:
            QMessageBox.warning(self, "품절 조회 실패", str(e)[:200])
            return True                      # 조회가 안 돼도 본 작업은 진행
        finally:
            QApplication.restoreOverrideCursor()

        if not found:
            self._log("[품절] 옮길 품절 상품이 없습니다.")
            return True

        codes = [r["product_code"] for _, r in found]
        head = chr(10).join(f"  {c}" for c in codes[:15])
        more = (chr(10) + f"  ... 외 {len(codes) - 15}건") if len(codes) > 15 else ""
        if not target:
            QMessageBox.information(
                self, "품절 상품",
                f"품절 {len(codes)}건이 있습니다." + chr(10) + head + more
                + chr(10) + chr(10)
                + "옮길 폴더가 지정되지 않아 그대로 두고 진행합니다."
                + chr(10) + "설정 > 품절 이동 폴더 지정 에서 정할 수 있습니다.")
            return True

        ret = QMessageBox.question(
            self, "품절 상품 정리",
            f"품절 {len(codes)}건을 찾았습니다." + chr(10) + head + more
            + chr(10) + chr(10)
            + f"'{target}' 로 옮기고 수정 1.0 을 시작할까요?"
            + chr(10) + "[아니오] 를 누르면 옮기지 않고 바로 시작합니다.",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            QMessageBox.Yes)
        if ret == QMessageBox.Cancel:
            return False
        if ret == QMessageBox.Yes:
            try:
                QApplication.setOverrideCursor(Qt.WaitCursor)
                n = 0
                for f in folders:
                    rows = [r for g, r in found if g == f]
                    if rows:
                        n += fix10.move_rows(cli.session, rows, target, f,
                                             log=self._log)
                self._log(f"[품절] 모두 {n}건을 '{target}' 로 넘겼습니다.")
            except Exception as e:
                QMessageBox.warning(self, "품절 이동 실패", str(e)[:200])
            finally:
                QApplication.restoreOverrideCursor()
        return True

    def _soldout_sweep(self, folders, target):
        """품절 상품을 지정한 폴더로 넘긴다."""
        worker = SoldoutSweepWorker(folders, target)
        self._start_worker(
            worker, self._on_sweep_done,
            f"품절 상품을 '{target}' 로 넘기는 중...")

    def _on_sweep_done(self, res: dict):
        QMessageBox.information(
            self, "품절 처리",
            f"품절 {res.get('found', 0):,}건 중 "
            f"{res.get('moved', 0):,}건을 '{res.get('target', '')}' 로 "
            "넘겼습니다.")

    def _on_fix10_done(self, res: dict):
        QMessageBox.information(
            self, "수정사항 1.0", chr(10).join([
                f"대상 {res.get('total', 0):,}건",
                f"완료 {res.get('ok', 0):,} / 품절 건너뜀 {res.get('soldout', 0):,}"
                f" / 실패 {res.get('fail', 0):,}",
                f"소요 {res.get('seconds', 0) / 60:.1f}분",
            ]))

    def pick_folders(self, title: str, note: str, preset=None) -> list:
        """
        폴더를 골라 받는다(체크박스). 취소하면 빈 목록.

        작업폴더 하나만 돌던 기능들을 폴더가 늘어난 뒤에도 쓰게 하려고
        만든 공용 고르기 창이다(2026-09-07).
        """
        from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox,
                                       QVBoxLayout)

        master = db.list_master_folders()
        if not master:
            QMessageBox.information(self, "안내", "작업대상 폴더가 없습니다.")
            return []
        now = set(preset or [f for f in [db.get_job_folder()] if f])
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel(note))
        boxes = []
        for f in master:
            c = QCheckBox(f)
            c.setChecked(f in now)
            boxes.append(c)
            v.addWidget(c)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)
        if dlg.exec() != QDialog.Accepted:
            return []
        return [c.text() for c in boxes if c.isChecked()]

    def on_run_analysis(self):
        """
        ALL 상품분석 — 폴더 선택과 진행 상황을 한 창에서 본다.

        **한 번에 한 폴더(계정)만** 돈다. 돌고 있으면 창만 다시 띄워
        어디까지 갔는지 보여주고, 폴더 선택·시작은 잠긴다
        (2026-09-08 사용자 지시).
        """
        from .analysis_dialog import AnalysisDialog

        dlg = getattr(self, "_an_dlg", None)
        if dlg is None:
            dlg = AnalysisDialog(
                self, self._folders or [],
                on_start=self._analysis_start,
                on_stop=lambda: (getattr(self, "_an_worker", None)
                                 and self._an_worker.stop()))
            self._an_dlg = dlg
        if getattr(self, "_an_thread", None) is not None:
            dlg.mark_running(getattr(self, "_an_folder", ""),
                             getattr(self, "_an_stat", None) or {})
        else:
            # 다른 창·CLI 가 돌고 있으면 그것도 막는다. 표식은 DB 에 있어
            # 프로세스가 달라도 서로 본다 (2026-09-08).
            from ..lohas.analysis_batch import running_now
            busy = running_now()
            if busy:
                dlg.mark_external(busy)
            else:
                dlg.mark_idle()
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _analysis_start(self, folder: str):
        """
        분석은 **제 스레드에서 따로 돈다.** 몇 시간짜리라 공용 작업 슬롯을
        쓰면 그동안 점검·수정1.0 이 전부 '이미 실행 중' 으로 막힌다.
        """
        from PySide6.QtCore import QThread

        if getattr(self, "_an_thread", None) is not None:
            QMessageBox.information(
                self, "ALL 상품분석",
                "이미 다른 폴더를 분석 중입니다." + chr(10)
                + f"'{getattr(self, '_an_folder', '')}' 가 끝난 뒤에 하세요.")
            return

        dlg = self._an_dlg
        worker = AnalysisWorker(
            folder_name=folder,
            batch_size=config.ANALYSIS_BATCH,
            poll_interval=config.ANALYSIS_POLL,
            batch_timeout=config.ANALYSIS_TIMEOUT,
            headless=self.chk_headless.isChecked(),
            monitor=self.cmb_monitor.currentData(),
        )
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.log.connect(self._log)
        worker.log.connect(dlg.append)
        worker.progress.connect(dlg.on_progress)
        worker.stat.connect(self._on_an_stat)
        worker.stat.connect(dlg.on_stat)
        worker.finished.connect(self._on_analysis_done)
        worker.finished.connect(dlg.done_all)
        worker.failed.connect(lambda m: dlg.append(f"!! {m}"))
        worker.failed.connect(self._on_failed)
        for sig in (worker.finished, worker.failed):
            sig.connect(lambda *_: thread.quit())
        thread.finished.connect(self._on_an_thread_done)

        self._an_thread, self._an_worker = thread, worker
        self._an_folder, self._an_stat = folder, {}
        dlg.mark_running(folder, {})
        self.btn_analysis.setText("🔬 상품분석 (진행중)")
        self.btn_analysis.setStyleSheet(
            "font-weight:bold; color:#fff; background:#ef6c00;")
        self._log(f"[상품분석] '{folder}' 시작")
        thread.start()

    def _on_an_stat(self, st: dict):
        """진행 상태를 버튼에도 적어 창을 닫아도 보이게 한다."""
        self._an_stat = st
        d, t = st.get("processed", 0), st.get("total", 0)
        if t:
            self.btn_analysis.setText(f"🔬 상품분석 {d:,}/{t:,}")

    # -------------------------------------------------------- ALL 카테고리
    def on_run_category_all(self):
        """
        폴더를 골라 카테고리를 끝까지 채운다. 상품분석과 같은 규칙이다 —
        한 번에 한 폴더, 창을 닫아도 계속 (2026-09-09 사용자 지시).
        """
        from .category_dialog import CategoryDialog

        def count(folder):
            from ..lohas import category_plan as cp
            g = cp.pending(db, folder, todo_only=True)
            return len(g), sum(len(v) for v in g.values())

        dlg = getattr(self, "_cat_dlg", None)
        if dlg is None:
            dlg = CategoryDialog(
                self, self._folders or [], counter=count,
                on_start=self._category_all_start,
                on_stop=lambda: (getattr(self, "_cat_worker", None)
                                 and self._cat_worker.stop()))
            self._cat_dlg = dlg
        if getattr(self, "_cat_thread", None) is not None:
            dlg.mark_running(getattr(self, "_cat_folder", ""),
                             getattr(self, "_cat_stat", None) or {})
        else:
            dlg.mark_idle()
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _category_all_start(self, folder: str):
        from PySide6.QtCore import QThread

        if getattr(self, "_cat_thread", None) is not None:
            QMessageBox.information(
                self, "ALL 카테고리",
                "이미 다른 폴더를 처리 중입니다." + chr(10)
                + f"'{getattr(self, '_cat_folder', '')}' 가 끝난 뒤에 하세요.")
            return
        dlg = self._cat_dlg
        worker = CategoryAutoWorker(
            folder_name=folder,
            headless=self.chk_headless.isChecked(),
            monitor=self.cmb_monitor.currentData())
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.log.connect(self._log)
        worker.log.connect(dlg.append)
        worker.progress.connect(dlg.on_progress)
        worker.stat.connect(self._on_cat_stat)
        worker.stat.connect(dlg.on_stat)
        worker.finished.connect(self._on_category_all_done)
        worker.finished.connect(dlg.done_all)
        worker.failed.connect(lambda m: dlg.append(f"!! {m}"))
        worker.failed.connect(self._on_failed)
        for sig in (worker.finished, worker.failed):
            sig.connect(lambda *_: thread.quit())
        thread.finished.connect(self._on_cat_thread_done)

        self._cat_thread, self._cat_worker = thread, worker
        self._cat_folder, self._cat_stat = folder, {}
        dlg.mark_running(folder, {})
        self.btn_category.setText("🗂 카테고리 (진행중)")
        self.btn_category.setStyleSheet(
            "font-weight:bold; color:#fff; background:#00695c;")
        self._log(f"[카테고리] '{folder}' 시작")
        thread.start()

    def _on_cat_stat(self, st: dict):
        self._cat_stat = st
        d, t = st.get("processed", 0), st.get("total", 0)
        if t:
            self.btn_category.setText(
                f"🗂 {st.get('phase', '카테고리')} {d:,}/{t:,}"[:26])

    def _on_category_all_done(self, res: dict):
        self._reload_folders()
        self._log(
            f"[카테고리] 완료 — LCP {res.get('lcps', 0):,}종 / "
            f"저장 {res.get('ok', 0):,}건 · 실패 {res.get('fail', 0):,}건")

    # ------------------------------------------------------------ ALL 태그
    def on_run_tag_all(self):
        from .tag_dialog import TagDialog

        def count(folder):
            from ..lohas import tag_batch
            g = tag_batch.targets(db, folder)
            return len(g), sum(len(v) for v in g.values())

        dlg = getattr(self, "_tag_dlg", None)
        if dlg is None:
            dlg = TagDialog(
                self, self._folders or [], counter=count,
                on_start=self._tag_all_start,
                on_stop=lambda: (getattr(self, "_tag_worker", None)
                                 and self._tag_worker.stop()))
            self._tag_dlg = dlg
        if getattr(self, "_tag_thread", None) is not None:
            dlg.mark_running(getattr(self, "_tag_folder", ""),
                             getattr(self, "_tag_stat", None) or {})
        else:
            dlg.mark_idle()
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _tag_all_start(self, folder: str):
        from PySide6.QtCore import QThread

        if getattr(self, "_tag_thread", None) is not None:
            QMessageBox.information(
                self, "ALL 태그",
                "이미 다른 폴더를 처리 중입니다." + chr(10)
                + f"'{getattr(self, '_tag_folder', '')}' 가 끝난 뒤에 하세요.")
            return
        dlg = self._tag_dlg
        worker = TagAllWorker(
            folder_name=folder,
            headless=self.chk_headless.isChecked(),
            monitor=self.cmb_monitor.currentData())
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.log.connect(self._log)
        worker.log.connect(dlg.append)
        worker.progress.connect(dlg.on_progress)
        worker.stat.connect(self._on_tag_stat)
        worker.stat.connect(dlg.on_stat)
        worker.finished.connect(self._on_tag_all_done)
        worker.finished.connect(dlg.done_all)
        worker.failed.connect(lambda m: dlg.append(f"!! {m}"))
        worker.failed.connect(self._on_failed)
        for sig in (worker.finished, worker.failed):
            sig.connect(lambda *_: thread.quit())
        thread.finished.connect(self._on_tag_thread_done)

        self._tag_thread, self._tag_worker = thread, worker
        self._tag_folder, self._tag_stat = folder, {}
        dlg.mark_running(folder, {})
        self.btn_tag_all.setText("🏷 태그 (진행중)")
        self.btn_tag_all.setStyleSheet(
            "font-weight:bold; color:#fff; background:#4527a0;")
        self._log(f"[태그] '{folder}' 시작")
        thread.start()

    def _on_tag_stat(self, st: dict):
        self._tag_stat = st
        d, t = st.get("processed", 0), st.get("total", 0)
        if t:
            self.btn_tag_all.setText(f"🏷 태그 {d:,}/{t:,}")

    def _on_tag_all_done(self, res: dict):
        self._log(f"[태그] 완료 — 묶음 {res.get('groups', 0):,}개 / "
                  f"저장 {res.get('ok', 0):,}건 · 실패 {res.get('fail', 0):,}건")

    def _on_tag_thread_done(self):
        if getattr(self, "_tag_thread", None) is not None:
            self._tag_thread.deleteLater()
        if getattr(self, "_tag_worker", None) is not None:
            self._tag_worker.deleteLater()
        self._tag_thread = None
        self._tag_worker = None
        self._tag_folder = ""
        if getattr(self, "_tag_dlg", None) is not None:
            self._tag_dlg.mark_idle()
        self.btn_tag_all.setText("🏷 ALL 태그")
        self.btn_tag_all.setStyleSheet("font-weight:bold; color:#4527a0;")

    def _on_cat_thread_done(self):
        if getattr(self, "_cat_thread", None) is not None:
            self._cat_thread.deleteLater()
        if getattr(self, "_cat_worker", None) is not None:
            self._cat_worker.deleteLater()
        self._cat_thread = None
        self._cat_worker = None
        self._cat_folder = ""
        if getattr(self, "_cat_dlg", None) is not None:
            self._cat_dlg.mark_idle()
        self.btn_category.setText("🗂 ALL 카테고리")
        self.btn_category.setStyleSheet("font-weight:bold; color:#00695c;")

    def _on_an_thread_done(self):
        if getattr(self, "_an_thread", None) is not None:
            self._an_thread.deleteLater()
        if getattr(self, "_an_worker", None) is not None:
            self._an_worker.deleteLater()
        self._an_thread = None
        self._an_worker = None
        self._an_folder = ""
        if getattr(self, "_an_dlg", None) is not None:
            self._an_dlg.mark_idle()
        self.btn_analysis.setText("🔬 ALL 상품분석")
        self.btn_analysis.setStyleSheet("font-weight:bold; color:#ef6c00;")

    def _on_analysis_done(self, stats: dict):
        # 결과는 진행 창이 그대로 보여준다. 팝업으로 또 막지 않는다 —
        # 창을 닫아두고 다른 일을 하다가 갑자기 튀어나오면 방해가 된다
        # (2026-09-08).
        self._reload_folders()
        self._log(
            f"[상품분석] 완료 — 대상 {stats.get('total', 0):,}종 / "
            f"완료 {stats.get('done', 0):,} · "
            f"이미완료 {stats.get('already', 0):,} · "
            f"오류 {stats.get('error', 0):,} · "
            f"시간초과 {stats.get('timeout', 0):,} · "
            f"{stats.get('elapsed', 0)}초")

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
