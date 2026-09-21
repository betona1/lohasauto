"""
광고 성과 보고서 창 — 기간 탭(7·14·21·30일) · 그래프 · 매체별 · TOP10 · 엑셀.

숫자는 세 곳에서 온다(`ad_stats`).
  광고비·클릭   `/stats` — **오늘 포함**
  매체·소재별   대용량 보고서 — **어제까지**
  실매출        커머스 API — 실결제금액

그래서 **매체별 합계가 총 광고비보다 작다.** 자료가 하루 늦는 것이라
화면에 그대로 적어 둔다 (2026-09-12).
"""
import datetime

from PySide6.QtCharts import (QBarCategoryAxis, QBarSeries, QBarSet, QChart,
                              QChartView, QLineSeries, QPieSeries,
                              QStackedBarSeries, QValueAxis)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog,
                               QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QMessageBox, QPushButton,
                               QTabBar, QTableWidget, QTableWidgetItem,
                               QTabWidget, QVBoxLayout, QWidget)

from ..lohas import ad_account, ad_sales, ad_schedule, ad_stats
from .numitem import make_item

NL = chr(10)
PERIODS = [7, 14, 21, 30]
PIE = ["#1565c0", "#ef6c00", "#2e7d32", "#6a1b9a", "#c62828", "#00838f"]


def _it(v, right=False, color="", bold=False, num=None) -> QTableWidgetItem:
    """정렬은 숫자로 한다 — `numitem` 참고."""
    return make_item(v, right, color, bold, num)


def _roas_color(r: int) -> str:
    return "#2e7d32" if r >= 300 else "#c62828" if r == 0 else "#ef6c00"


class Card(QFrame):
    def __init__(self, title, accent="#37474f"):
        super().__init__()
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet("QFrame{border:1px solid #d0d7de;border-radius:8px;"
                           "background:#ffffff;}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 9, 12, 9)
        lay.setSpacing(2)
        self.t = QLabel(title)
        self.t.setStyleSheet("color:#57606a;border:none;")
        self.t.setWordWrap(True)
        self.v = QLabel("-")
        f = QFont()
        f.setPointSize(17)
        f.setBold(True)
        self.v.setFont(f)
        self.v.setStyleSheet(f"color:{accent};border:none;")
        lay.addWidget(self.t)
        lay.addWidget(self.v)

    def set(self, value, sub=""):
        self.v.setText(str(value))
        if sub:
            self.t.setText(sub)


class AdReportDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("광고 성과 보고서")
        self.setWindowFlag(Qt.Window, True)
        self.setModal(False)
        self.resize(1360, 900)
        self.days = 7

        v = QVBoxLayout(self)

        head = QHBoxLayout()
        acc = ad_account.main_account() or {}
        t = QLabel(f"광고 성과 — {acc.get('label') or '(계정 없음)'}")
        t.setStyleSheet("font-size:17px;font-weight:bold;")
        head.addWidget(t)
        head.addStretch(1)
        self.lbl_note = QLabel("")
        self.lbl_note.setStyleSheet("color:#78909c;")
        head.addWidget(self.lbl_note)
        btn_x = QPushButton("📊 엑셀로 저장")
        btn_x.setStyleSheet("font-weight:bold;")
        btn_x.clicked.connect(self.on_excel)
        head.addWidget(btn_x)
        v.addLayout(head)

        self.bar = QTabBar()
        self.bar.setExpanding(False)
        for d in PERIODS:
            self.bar.addTab(f"  최근 {d}일  ")
        self.bar.currentChanged.connect(self._period)
        v.addWidget(self.bar)

        cw = QWidget()
        self.cards = QGridLayout(cw)
        self.cards.setContentsMargins(0, 4, 0, 4)
        self.c_cost = Card("광고비", "#ad1457")
        self.c_sales = Card("실매출 (커머스)", "#2e7d32")
        self.c_roas = Card("ROAS", "#1565c0")
        self.c_clk = Card("클릭", "#6a1b9a")
        self.c_cpc = Card("평균 CPC", "#00695c")
        self.c_ord = Card("주문", "#ef6c00")
        for i, c in enumerate([self.c_cost, self.c_sales, self.c_roas,
                               self.c_clk, self.c_cpc, self.c_ord]):
            self.cards.addWidget(c, 0, i)
            self.cards.setColumnStretch(i, 1)
        v.addWidget(cw)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_trend(), "추이")
        self.tabs.addTab(self._tab_media(), "매체별")
        self.tabs.addTab(self._tab_top(), "TOP 10")
        self.tabs.addTab(self._tab_hour(), "시간대")
        self.tabs.addTab(self._tab_sched(), "⏱ 광고시간대")
        self.tabs.addTab(self._tab_lcp(), "LCP별 매출·ROAS")
        v.addWidget(self.tabs, 1)

        self.bar.setCurrentIndex(0)
        self.refresh()

    # ------------------------------------------------------------ 탭
    def _tab_trend(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.ch_trend = QChart()
        self.ch_trend.legend().setAlignment(Qt.AlignBottom)
        vw = QChartView(self.ch_trend)
        vw.setRenderHint(QPainter.Antialiasing)
        lay.addWidget(vw, 3)
        self.t_day = QTableWidget(0, 7)
        self.t_day.setHorizontalHeaderLabels(
            ["날짜", "광고비", "클릭", "노출", "실매출", "주문", "ROAS"])
        self.t_day.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.t_day.verticalHeader().setVisible(False)
        self.t_day.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch)
        lay.addWidget(self.t_day, 2)
        return w

    def _tab_media(self):
        """
        매체별 — 위는 비중, 아래는 **매체(행) × 날짜(열)** 표.

        매체는 4개뿐이라 스크롤이 필요 없다. 대신 **날짜가 늘어난다.**
        그래서 표를 눕혀 놓고 매체·합계는 왼쪽에 고정하고 **날짜만 좌우로
        흐르게** 한다(2026-09-12 사용자). 매체를 누르면 그 매체의 날짜별
        그래프가 새 창으로 뜬다.
        """
        w = QWidget()
        lay = QVBoxLayout(w)

        topbox = QHBoxLayout()
        self.ch_pie = QChart()
        self.ch_pie.legend().setAlignment(Qt.AlignRight)
        vw = QChartView(self.ch_pie)
        vw.setRenderHint(QPainter.Antialiasing)
        vw.setMinimumHeight(230)
        topbox.addWidget(vw, 4)

        self.t_media = QTableWidget(0, 6)
        self.t_media.setHorizontalHeaderLabels(
            ["매체", "광고비", "비중", "클릭", "노출", "CPC"])
        self.t_media.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.t_media.verticalHeader().setVisible(False)
        self.t_media.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch)
        self.t_media.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.t_media.cellDoubleClicked.connect(
            lambda r, _c: self._media_chart(
                self.t_media.item(r, 0).text() if self.t_media.item(r, 0)
                else ""))
        # 4줄뿐이라 스크롤이 없도록 높이를 맞춘다
        self.t_media.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.t_media.setMaximumHeight(230)
        topbox.addWidget(self.t_media, 5)
        lay.addLayout(topbox, 3)

        lay.addWidget(QLabel(
            "매체 × 날짜  —  매체 이름을 누르면 그 매체의 날짜별 그래프가"
            " 열립니다 (날짜가 많으면 오른쪽으로 밀어 보십시오)"))

        wrap = QHBoxLayout()
        wrap.setSpacing(0)
        # 왼쪽 고정 — 매체 이름과 합계
        self.t_fix = QTableWidget(0, 2)
        self.t_fix.setHorizontalHeaderLabels(["매체", "합계"])
        self.t_fix.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.t_fix.verticalHeader().setVisible(False)
        self.t_fix.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.t_fix.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.t_fix.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.t_fix.setFixedWidth(300)
        self.t_fix.cellClicked.connect(
            lambda r, _c: self._media_chart(
                self.t_fix.item(r, 0).text() if self.t_fix.item(r, 0) else ""))
        wrap.addWidget(self.t_fix)
        # 오른쪽 — 날짜만 좌우로 흐른다
        self.t_days = QTableWidget(0, 0)
        self.t_days.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.t_days.verticalHeader().setVisible(False)
        self.t_days.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.t_days.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.t_days.setSelectionBehavior(QAbstractItemView.SelectRows)
        wrap.addWidget(self.t_days, 1)
        lay.addLayout(wrap, 2)
        return w

    def _media_chart(self, name: str):
        """매체 하나의 날짜별 광고비·클릭·실매출 그래프 (새 창)."""
        if not name or name in ("합계", "실매출", "ROAS"):
            return
        MediaDayDialog(self, name, self.days, self._rows_media_day).show()

    def _tab_top(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        row = QHBoxLayout()
        row.addWidget(QLabel("기준"))
        self.cmb_top = QComboBox()
        self.cmb_top.addItem("LCP", "lcp")
        self.cmb_top.addItem("소재(상품)", "ad")
        self.cmb_top.currentIndexChanged.connect(self._fill_top)
        row.addWidget(self.cmb_top)
        row.addStretch(1)
        self.lbl_top = QLabel("")
        self.lbl_top.setStyleSheet("color:#57606a;")
        row.addWidget(self.lbl_top)
        lay.addLayout(row)
        self.ch_top = QChart()
        self.ch_top.legend().setVisible(False)
        vw = QChartView(self.ch_top)
        vw.setRenderHint(QPainter.Antialiasing)
        lay.addWidget(vw, 3)
        self.t_top = QTableWidget(0, 8)
        self.t_top.setHorizontalHeaderLabels(
            ["순위", "상품명", "LCP", "광고비", "클릭", "실매출", "주문",
             "ROAS"])
        self.t_top.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.t_top.verticalHeader().setVisible(False)
        self.t_top.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch)
        lay.addWidget(self.t_top, 2)
        return w

    def _tab_hour(self):
        """
        **시간대별 광고비.** 0~23시를 늘 다 그린다 — 돈이 안 나간 시간도
        비어 있는 채로 보여야 언제 비는지 알 수 있다(2026-09-12 사용자).

        대용량 보고서의 8열이 시간대다. 처음엔 9열(지역)을 시간으로 읽어
        `99시` 가 나왔었다.
        """
        w = QWidget()
        lay = QVBoxLayout(w)
        self.lbl_hour = QLabel("")
        self.lbl_hour.setWordWrap(True)
        self.lbl_hour.setStyleSheet(
            "font-weight:bold; color:#4527a0; background:#ede7f6;"
            " border-radius:6px; padding:8px;")
        lay.addWidget(self.lbl_hour)
        self.ch_hour = QChart()
        self.ch_hour.legend().setAlignment(Qt.AlignBottom)
        vw = QChartView(self.ch_hour)
        vw.setRenderHint(QPainter.Antialiasing)
        lay.addWidget(vw, 3)
        self.t_hour = QTableWidget(0, 9)
        self.t_hour.setHorizontalHeaderLabels(
            ["시간", "노출시간", "광고비", "비중", "클릭", "노출", "CPC",
             "주문", "실매출"])
        self.t_hour.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.t_hour.verticalHeader().setVisible(False)
        self.t_hour.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch)
        lay.addWidget(self.t_hour, 2)
        return w

    def _fill_hour(self):
        rows = {r["k"]: r for r in
                ad_stats.by_dim("hour", self.days, self.cu, limit=24)}
        data = []
        for h in range(24):
            r = rows.get(f"{h:02d}", {})
            data.append({"h": h, "cost": int(r.get("cost") or 0),
                         "clk": int(r.get("clk") or 0),
                         "imp": int(r.get("imp") or 0)})
        self._rows_hour = data
        tot = sum(d["cost"] for d in data) or 1
        ch = self.ch_hour
        ch.removeAllSeries()
        for ax in list(ch.axes()):
            ch.removeAxis(ax)
        bs = QBarSet("광고비")
        bs.setColor(QColor("#4527a0"))
        ln = QLineSeries()
        ln.setName("클릭")
        ln.setColor(QColor("#ef6c00"))
        for i, d in enumerate(data):
            bs.append(d["cost"])
            ln.append(i, d["clk"])
        ser = QBarSeries()
        ser.append(bs)
        ch.addSeries(ser)
        ch.addSeries(ln)
        ax = QBarCategoryAxis()
        ax.append([f"{d['h']:02d}" for d in data])
        ch.addAxis(ax, Qt.AlignBottom)
        ser.attachAxis(ax)
        ay = QValueAxis()
        ay.setLabelFormat("%d")
        ay.setTitleText("광고비(원)")
        ay.setRange(0, max(1, max(d["cost"] for d in data)) * 1.2)
        ch.addAxis(ay, Qt.AlignLeft)
        ser.attachAxis(ay)
        a2 = QValueAxis()
        a2.setLabelFormat("%d")
        a2.setTitleText("클릭")
        a2.setRange(0, max(1, max(d["clk"] for d in data)) * 1.4)
        ch.addAxis(a2, Qt.AlignRight)
        ln.attachAxis(a2)
        ax2 = QValueAxis()
        ax2.setRange(-0.5, 23.5)
        ax2.setVisible(False)
        ch.addAxis(ax2, Qt.AlignBottom)
        ln.attachAxis(ax2)
        sale_h2 = {r["hour"]: int(r["amount"] or 0)
                   for r in ad_sales.by_hour(self.days)}
        ln2 = QLineSeries()
        ln2.setName("실매출")
        ln2.setColor(QColor("#2e7d32"))
        for i, d in enumerate(data):
            ln2.append(i, sale_h2.get(f"{d['h']:02d}", 0))
        ch.addSeries(ln2)
        a3 = QValueAxis()
        a3.setLabelFormat("%d")
        a3.setTitleText("실매출(원)")
        a3.setRange(0, max(1, max(list(sale_h2.values()) or [1])) * 1.3)
        ch.addAxis(a3, Qt.AlignRight)
        ln2.attachAxis(a3)
        ln2.attachAxis(ax2)
        ch.setTitle(f"시간대별 광고비 + 주문 (최근 {self.days}일 · {tot:,}원)")

        # 주문 시간대 + 지금 노출시간 설정을 같이 적는다
        sale_h = {r["hour"]: r for r in ad_sales.by_hour(self.days)}
        sc = ad_schedule.current(self.cu)
        s_, e_ = sc.get("start_hour"), sc.get("end_hour")
        self.t_hour.setRowCount(24)
        for i, d in enumerate(data):
            inside = True if s_ is None else (s_ <= d["h"] < e_)
            v = sale_h.get(f"{d['h']:02d}", {})
            self.t_hour.setItem(i, 0, _it(f"{d['h']:02d}시"))
            self.t_hour.setItem(i, 1, _it("○" if inside else "—", True,
                                          "" if inside else "#9e9e9e"))
            self.t_hour.setItem(i, 2, _it(f"{d['cost']:,}원", True,
                                          "" if inside else "#c62828"))
            self.t_hour.setItem(i, 3, _it(f"{d['cost'] * 100 // tot}%", True))
            self.t_hour.setItem(i, 4, _it(f"{d['clk']:,}", True))
            self.t_hour.setItem(i, 5, _it(f"{d['imp']:,}", True))
            self.t_hour.setItem(i, 6, _it(
                f"{d['cost'] // d['clk'] if d['clk'] else 0:,}원", True))
            self.t_hour.setItem(i, 7, _it(f"{int(v.get('orders') or 0):,}",
                                          True))
            self.t_hour.setItem(i, 8, _it(f"{int(v.get('amount') or 0):,}원",
                                          True, "#2e7d32"))
            d["orders"] = int(v.get("orders") or 0)
            d["amount"] = int(v.get("amount") or 0)
        night = sum(d["cost"] for d in data if d["h"] >= 19 or d["h"] < 2)
        peak = max(data, key=lambda d: d["cost"])
        self.lbl_hour.setText(
            f"가장 많이 쓴 시간 {peak['h']:02d}시 {peak['cost']:,}원"
            f"   ·   저녁 19~01시 {night:,}원 ({night * 100 // tot}%)"
            f"   ·   합계 {tot:,}원"
            + NL + "※ 대용량 보고서 기준이라 **어제까지**입니다.")

    def _tab_sched(self):
        """
        **광고 노출시간 설정과 실제 지출을 맞대본다.**

        노출시간을 바꾸면 그날부터 곡선이 바뀐다. 기록이 없으면 나중에
        "왜 줄었지?" 를 못 푼다. 설정 밖에서 나간 돈도 같이 본다 —
        설정이 안 먹었는지 바로 드러난다(2026-09-16 사용자).
        """
        w = QWidget()
        lay = QVBoxLayout(w)

        row = QHBoxLayout()
        row.addWidget(QLabel("적용일"))
        from PySide6.QtWidgets import QDateEdit, QSpinBox, QLineEdit
        from PySide6.QtCore import QDate
        self.sc_day = QDateEdit()
        self.sc_day.setCalendarPopup(True)
        self.sc_day.setDisplayFormat("yyyy-MM-dd")
        self.sc_day.setDate(QDate.currentDate())
        row.addWidget(self.sc_day)
        row.addWidget(QLabel("시작"))
        self.sc_s = QSpinBox()
        self.sc_s.setRange(0, 23)
        self.sc_s.setValue(8)
        self.sc_s.setSuffix("시")
        row.addWidget(self.sc_s)
        row.addWidget(QLabel("끝"))
        self.sc_e = QSpinBox()
        self.sc_e.setRange(1, 24)
        self.sc_e.setValue(22)
        self.sc_e.setSuffix("시")
        row.addWidget(self.sc_e)
        self.sc_memo = QLineEdit()
        self.sc_memo.setPlaceholderText("메모 (왜 바꿨는지)")
        row.addWidget(self.sc_memo, 1)
        b = QPushButton("기록 추가")
        b.clicked.connect(self._add_sched)
        row.addWidget(b)
        lay.addLayout(row)

        self.lbl_sched = QLabel("")
        self.lbl_sched.setWordWrap(True)
        self.lbl_sched.setStyleSheet(
            "font-weight:bold; color:#4527a0; background:#ede7f6;"
            " border-radius:6px; padding:8px;")
        lay.addWidget(self.lbl_sched)

        lay.addWidget(QLabel("변경 이력"))
        self.t_sched = QTableWidget(0, 4)
        self.t_sched.setHorizontalHeaderLabels(
            ["적용일", "노출시간", "메모", "기록시각"])
        self.t_sched.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.t_sched.verticalHeader().setVisible(False)
        self.t_sched.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.Stretch)
        self.t_sched.setMaximumHeight(150)
        lay.addWidget(self.t_sched)

        lay.addWidget(QLabel("설정 시간 안/밖 지출  —  밖에서 나가면 설정이"
                             " 안 먹은 것입니다"))
        self.t_out = QTableWidget(0, 5)
        self.t_out.setHorizontalHeaderLabels(
            ["날짜", "노출시간", "시간 안", "시간 밖", "밖에서 나간 시각"])
        self.t_out.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.t_out.verticalHeader().setVisible(False)
        self.t_out.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.Stretch)
        lay.addWidget(self.t_out, 1)
        return w

    def _add_sched(self):
        if not self.cu:
            QMessageBox.warning(self, "광고시간대", "메인 계정을 정하십시오.")
            return
        ad_schedule.add(self.cu, self.sc_day.date().toString("yyyy-MM-dd"),
                        self.sc_s.value(), self.sc_e.value(), "매일",
                        self.sc_memo.text().strip())
        self.sc_memo.clear()
        self._fill_sched()

    def _fill_sched(self):
        hist = ad_schedule.history(self.cu, 50)
        self.t_sched.setRowCount(len(hist))
        for i, r in enumerate(hist):
            self.t_sched.setItem(i, 0, _it(r["from_day"]))
            self.t_sched.setItem(i, 1, _it(ad_schedule.label(r)))
            self.t_sched.setItem(i, 2, _it(r.get("memo") or ""))
            self.t_sched.setItem(i, 3, _it(r.get("created_at") or ""))
        rows = ad_schedule.outside_spend(self.cu, self.days)
        self.t_out.setRowCount(len(rows))
        out_tot = 0
        for i, r in enumerate(rows):
            out_tot += r["out_cost"]
            self.t_out.setItem(i, 0, _it(r["day"]))
            self.t_out.setItem(i, 1, _it(r["sched"]))
            self.t_out.setItem(i, 2, _it(f"{r['in_cost']:,}원", True))
            self.t_out.setItem(i, 3, _it(f"{r['out_cost']:,}원", True,
                                         "#c62828" if r["out_cost"] else ""))
            self.t_out.setItem(i, 4, _it(r["out_hours"]))
        cur = ad_schedule.current(self.cu)
        self.lbl_sched.setText(
            f"지금 설정 : {ad_schedule.label(cur)}"
            + (f"   ·   {cur.get('from_day')} 부터" if cur else "")
            + NL + f"최근 {self.days}일 중 설정 시간 밖 지출 {out_tot:,}원"
            + ("  ← 설정 전 기간이 섞여 있으면 정상입니다" if out_tot else ""))

    def _tab_lcp(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.lbl_lcp = QLabel("")
        self.lbl_lcp.setStyleSheet(
            "font-weight:bold;color:#1b5e20;background:#e8f5e9;"
            "border-radius:6px;padding:8px;")
        self.lbl_lcp.setWordWrap(True)
        lay.addWidget(self.lbl_lcp)
        self.t_lcp = QTableWidget(0, 7)
        self.t_lcp.setHorizontalHeaderLabels(
            ["LCP 코드", "상품명", "광고비", "클릭", "실매출", "주문",
             "ROAS"])
        self.t_lcp.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.t_lcp.verticalHeader().setVisible(False)
        self.t_lcp.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch)
        self.t_lcp.setSortingEnabled(True)
        lay.addWidget(self.t_lcp, 1)
        return w

    # ------------------------------------------------------------ 채우기
    def _period(self, i):
        self.days = PERIODS[i]
        self.refresh()

    def refresh(self):
        self.cu = (ad_account.main_account() or {}).get("customer_id") or ""
        self._fill_cards()
        self._fill_trend()
        self._fill_media()
        self._fill_top()
        self._fill_hour()
        self._fill_sched()
        self._fill_lcp()

    def _fill_cards(self):
        s = ad_stats.summary(self.days, self.cu)
        self._sum = s
        self.c_cost.set(f"{s['cost']:,}원",
                        f"광고비  ·  집행 {s['spend_days']}일")
        self.c_sales.set(f"{s['amount']:,}원",
                         f"실매출 (커머스)  ·  수량 {s['qty']:,}")
        self.c_roas.set(f"{s['roas']:,}%",
                        f"ROAS  ·  네이버추정 {s['conv_amt']:,}원")
        self.c_roas.v.setStyleSheet(
            f"border:none;color:{_roas_color(s['roas'])}")
        self.c_clk.set(f"{s['clk']:,}", f"클릭  ·  노출 {s['imp']:,}")
        self.c_cpc.set(f"{s['cpc']:,}원", f"평균 CPC  ·  CTR {s['ctr']}%")
        self.c_ord.set(f"{s['orders']:,}건", "주문 (실결제)")
        # **매출을 관리코드로 갈라 적는다.** W코드 상품은 광고와 무관한데
        # 가게 전체 매출에 섞여 있어 광고 성과처럼 보인다(2026-09-12 사용자).
        sp = {r["kind"]: int(r["amount"] or 0) for r in
              ad_sales.split(self.days)}
        tot = sum(sp.values())
        self.c_sales.t.setText(
            "실매출 (광고상품)  ·  가게 전체 " + f"{tot:,}원")
        self.lbl_note.setText(
            f"{s['since']} ~ 오늘 ({s['eff_days']}일)"
            + (f"   ·   광고 시작 {s['start']}" if s.get("start") else
               "   ·   광고 시작일 미지정")
            + "   ·   광고비는 당일 포함, 매체·소재별은 어제까지" + NL
            + "가게 전체 매출 " + " · ".join(
                f"{k} {v:,}원" for k, v in sorted(
                    sp.items(), key=lambda kv: -kv[1])))

    def _fill_trend(self):
        rows = [r for r in ad_stats.by_day(self.days, self.cu)
                if r["cost"] or r["amount"]]
        self._rows_day = rows
        ch = self.ch_trend
        ch.removeAllSeries()
        for ax in list(ch.axes()):
            ch.removeAxis(ax)
        self.t_day.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self.t_day.setItem(i, 0, _it(r["day"]))
            self.t_day.setItem(i, 1, _it(f"{r['cost']:,}원", True))
            self.t_day.setItem(i, 2, _it(f"{r['clk']:,}", True))
            self.t_day.setItem(i, 3, _it(f"{r['imp']:,}", True))
            self.t_day.setItem(i, 4, _it(f"{r['amount']:,}원", True))
            self.t_day.setItem(i, 5, _it(f"{r['orders']:,}", True))
            self.t_day.setItem(i, 6, _it(f"{r['roas']:,}%", True,
                                         _roas_color(r["roas"])))
        if not rows:
            ch.setTitle("자료가 없습니다")
            return
        b_cost = QBarSet("광고비")
        b_cost.setColor(QColor("#ad1457"))
        b_sale = QBarSet("실매출")
        b_sale.setColor(QColor("#2e7d32"))
        line = QLineSeries()
        line.setName("ROAS(%)")
        line.setColor(QColor("#1565c0"))
        cats = []
        for i, r in enumerate(rows):
            b_cost.append(r["cost"])
            b_sale.append(r["amount"])
            line.append(i, r["roas"])
            cats.append(r["day"][5:])
        bs = QBarSeries()
        bs.append(b_cost)
        bs.append(b_sale)
        ch.addSeries(bs)
        ch.addSeries(line)
        ax = QBarCategoryAxis()
        ax.append(cats)
        ch.addAxis(ax, Qt.AlignBottom)
        bs.attachAxis(ax)
        ay = QValueAxis()
        ay.setLabelFormat("%d")
        ay.setTitleText("원")
        ay.setRange(0, max(1, max(max(r["cost"], r["amount"])
                                  for r in rows)) * 1.25)
        ch.addAxis(ay, Qt.AlignLeft)
        bs.attachAxis(ay)
        a2 = QValueAxis()
        a2.setLabelFormat("%d")
        a2.setTitleText("ROAS %")
        a2.setRange(0, max(100, max(r["roas"] for r in rows)) * 1.3)
        ch.addAxis(a2, Qt.AlignRight)
        line.attachAxis(a2)
        ax2 = QValueAxis()
        ax2.setRange(-0.5, len(rows) - 0.5)
        ax2.setVisible(False)
        ch.addAxis(ax2, Qt.AlignBottom)
        line.attachAxis(ax2)
        tot = sum(r["cost"] for r in rows)
        amt = sum(r["amount"] for r in rows)
        ch.setTitle(f"날짜별 광고비 {tot:,}원 · 실매출 {amt:,}원 · "
                    f"ROAS {amt * 100 // tot if tot else 0:,}%")

    def _fill_media(self):
        rows = ad_stats.by_media(self.days, self.cu)
        self._rows_media = rows
        tot = sum(int(r["cost"] or 0) for r in rows) or 1
        pie = QPieSeries()
        self.t_media.setRowCount(len(rows))
        for i, r in enumerate(rows):
            c = int(r["cost"] or 0)
            k = int(r["clk"] or 0)
            sl = pie.append(f"{r['name']} {c * 100 // tot}%", c)
            sl.setBrush(QColor(PIE[i % len(PIE)]))
            sl.setLabelVisible(True)
            self.t_media.setItem(i, 0, _it(r["name"]))
            self.t_media.setItem(i, 1, _it(f"{c:,}원", True))
            self.t_media.setItem(i, 2, _it(f"{c * 100 // tot}%", True))
            self.t_media.setItem(i, 3, _it(f"{k:,}", True))
            self.t_media.setItem(i, 4, _it(f"{int(r['imp'] or 0):,}", True))
            self.t_media.setItem(i, 5, _it(f"{c // k if k else 0:,}원", True))
        self.ch_pie.removeAllSeries()
        self.ch_pie.addSeries(pie)
        self.ch_pie.setTitle(f"매체별 광고비 (최근 {self.days}일 · {tot:,}원)")

        daily = ad_stats.by_media(self.days, self.cu, daily=True)
        sale = {r["day"]: int(r["amount"] or 0)
                for r in ad_sales.by_day(self.days)}
        days_ = sorted({r["day"] for r in daily})
        grid = {(r["day"], r["name"]): int(r["cost"] or 0) for r in daily}
        MEDIA = ["네이버 쇼핑 - PC", "네이버 쇼핑 - 모바일",
                 "네이버플러스 스토어 - PC", "네이버플러스 스토어 - 모바일"]
        self._rows_media_day = [
            {"day": d, **{m: grid.get((d, m), 0) for m in MEDIA},
             "광고비": sum(grid.get((d, m), 0) for m in MEDIA),
             "실매출": sale.get(d, 0)}
            for d in sorted(days_, reverse=True)]

        # ---- 눕힌 표 : 행 = 매체 4 + 합계 + 실매출 + ROAS
        labels = MEDIA + ["합계", "실매출", "ROAS"]
        self.t_fix.setRowCount(len(labels))
        self.t_days.setRowCount(len(labels))
        self.t_days.setColumnCount(len(days_))
        self.t_days.setHorizontalHeaderLabels([d[5:] for d in days_])
        for j in range(len(days_)):
            self.t_days.setColumnWidth(j, 92)
        for i, nm in enumerate(labels):
            bold = nm in ("합계", "실매출", "ROAS")
            it = _it(nm)
            if bold:
                f = it.font()
                f.setBold(True)
                it.setFont(f)
            self.t_fix.setItem(i, 0, it)
            if nm == "ROAS":
                c_ = sum(grid.get((d, m), 0) for d in days_ for m in MEDIA)
                a_ = sum(sale.get(d, 0) for d in days_)
                v = (a_ * 100 // c_) if c_ else 0
                self.t_fix.setItem(i, 1, _it(f"{v:,}%", True,
                                             _roas_color(v)))
            elif nm == "실매출":
                self.t_fix.setItem(i, 1, _it(
                    f"{sum(sale.get(d, 0) for d in days_):,}원", True))
            elif nm == "합계":
                self.t_fix.setItem(i, 1, _it(
                    f"{sum(grid.get((d, m), 0) for d in days_ for m in MEDIA):,}원",
                    True))
            else:
                self.t_fix.setItem(i, 1, _it(
                    f"{sum(grid.get((d, nm), 0) for d in days_):,}원", True))
            for j, d in enumerate(days_):
                if nm == "ROAS":
                    c_ = sum(grid.get((d, m), 0) for m in MEDIA)
                    v = (sale.get(d, 0) * 100 // c_) if c_ else 0
                    cell = _it(f"{v:,}%", True, _roas_color(v))
                elif nm == "실매출":
                    cell = _it(f"{sale.get(d, 0):,}", True, "#2e7d32")
                elif nm == "합계":
                    cell = _it(f"{sum(grid.get((d, m), 0) for m in MEDIA):,}",
                               True)
                else:
                    cell = _it(f"{grid.get((d, nm), 0):,}", True)
                if bold:
                    f = cell.font()
                    f.setBold(True)
                    cell.setFont(f)
                self.t_days.setItem(i, j, cell)
        h = 26 * len(labels) + 30
        self.t_fix.setFixedHeight(h)
        self.t_days.setFixedHeight(h + 16)
        # 날짜가 최근 쪽부터 보이게 오른쪽 끝으로
        self.t_days.horizontalScrollBar().setValue(
            self.t_days.horizontalScrollBar().maximum())

    def _fill_top(self):
        by = self.cmb_top.currentData() or "lcp"
        rows = ad_stats.top(self.days, by=by, limit=10, customer=self.cu)
        self._rows_top = rows
        ch = self.ch_top
        ch.removeAllSeries()
        for ax in list(ch.axes()):
            ch.removeAxis(ax)
        self.t_top.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self.t_top.setItem(i, 0, _it(i + 1, True))
            self.t_top.setItem(i, 1, _it(str(r["name"])[:46]))
            self.t_top.setItem(i, 2, _it(r.get("lcp_code", "")))
            self.t_top.setItem(i, 3, _it(f"{int(r['cost'] or 0):,}원", True))
            self.t_top.setItem(i, 4, _it(f"{int(r['clk'] or 0):,}", True))
            self.t_top.setItem(i, 5, _it(f"{r['amount']:,}원", True))
            self.t_top.setItem(i, 6, _it(f"{r['orders']:,}", True))
            self.t_top.setItem(i, 7, _it(f"{r['roas']:,}%", True,
                                         _roas_color(r["roas"])))
        if not rows:
            ch.setTitle("자료가 없습니다")
            self.lbl_top.setText("")
            return
        bs_c = QBarSet("광고비")
        bs_c.setColor(QColor("#ad1457"))
        bs_s = QBarSet("실매출")
        bs_s.setColor(QColor("#2e7d32"))
        cats = []
        for r in rows:
            bs_c.append(int(r["cost"] or 0))
            bs_s.append(r["amount"])
            cats.append(str(r["name"])[-10:])
        ser = QBarSeries()
        ser.append(bs_c)
        ser.append(bs_s)
        ch.addSeries(ser)
        ax = QBarCategoryAxis()
        ax.append(cats)
        ax.setLabelsAngle(-40)
        ch.addAxis(ax, Qt.AlignBottom)
        ser.attachAxis(ax)
        ay = QValueAxis()
        ay.setLabelFormat("%d")
        ch.addAxis(ay, Qt.AlignLeft)
        ser.attachAxis(ay)
        ch.legend().setVisible(True)
        ch.legend().setAlignment(Qt.AlignBottom)
        ch.setTitle(f"광고비 TOP 10 ({'소재' if by == 'ad' else 'LCP'})")
        dead = [r for r in rows if r["cost"] and not r["amount"]]
        self.lbl_top.setText(
            f"TOP10 광고비 {sum(int(r['cost'] or 0) for r in rows):,}원 · "
            f"실매출 {sum(r['amount'] for r in rows):,}원"
            + (f"   ·   매출 0원 {len(dead)}개" if dead else ""))

    def _fill_lcp(self):
        rows = ad_stats.by_lcp(self.days, 500, self.cu)
        self._rows_lcp = rows
        self.t_lcp.setSortingEnabled(False)
        self.t_lcp.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self.t_lcp.setItem(i, 0, _it(r["lcp_code"]))
            self.t_lcp.setItem(i, 1, _it((r.get("name") or "")[:46]))
            self.t_lcp.setItem(i, 2, _it(f"{r['cost']:,}원", True))
            self.t_lcp.setItem(i, 3, _it(f"{r['clk']:,}", True))
            self.t_lcp.setItem(i, 4, _it(f"{r['amount']:,}원", True))
            self.t_lcp.setItem(i, 5, _it(f"{r['orders']:,}", True))
            self.t_lcp.setItem(i, 6, _it(f"{r['roas']:,}%", True,
                                         _roas_color(r["roas"])))
        self.t_lcp.setSortingEnabled(True)
        cost = sum(r["cost"] for r in rows)
        amt = sum(r["amount"] for r in rows)
        dead = [r for r in rows if r["cost"] and not r["amount"]]
        self.lbl_lcp.setText(
            f"최근 {self.days}일 — LCP {len(rows):,}종 · 광고비 {cost:,}원 · "
            f"실매출 {amt:,}원 · ROAS {amt * 100 // cost if cost else 0:,}%"
            + NL + f"⚠ 매출 0원인데 광고비 쓴 LCP {len(dead):,}종 "
                   f"({sum(r['cost'] for r in dead):,}원 · "
                   f"전체의 {sum(r['cost'] for r in dead) * 100 // (cost or 1)}%)")

    # ------------------------------------------------------------ 엑셀
    MEDIA4 = ["네이버 쇼핑 - PC", "네이버 쇼핑 - 모바일",
              "네이버플러스 스토어 - PC", "네이버플러스 스토어 - 모바일"]

    def on_excel(self):
        """
        **화면에 보이는 그대로** 시트로 떨군다 — 추이 / 매체별 /
        매체×날짜(눕힌 표) / TOP10 / LCP별 / 매출구분.

        매체별은 화면처럼 **행=매체, 열=날짜** 로 눕혀야 비교가 된다
        (2026-09-12 사용자).
        """
        import os
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter

        name = (f"광고보고서_{self.days}일_"
                f"{datetime.date.today():%Y%m%d}.xlsx")
        path, _ = QFileDialog.getSaveFileName(self, "엑셀로 저장", name,
                                              "Excel (*.xlsx)")
        if not path:
            return
        wb = Workbook()
        wb.remove(wb.active)
        HEAD = PatternFill("solid", fgColor="263238")
        HF = Font(color="FFFFFF", bold=True)
        SUM_F = PatternFill("solid", fgColor="ECEFF1")

        def sheet(title, cols, rows, money=(), pct=(), bold_rows=()):
            ws = wb.create_sheet(title[:31])
            ws.append(cols)
            for c in ws[1]:
                c.fill = HEAD
                c.font = HF
                c.alignment = Alignment(horizontal="center",
                                        vertical="center", wrap_text=True)
            for r in rows:
                ws.append(r)
            for i in range(1, len(cols) + 1):
                L = get_column_letter(i)
                w = max([len(str(cols[i - 1]))]
                        + [len(str(r[i - 1])) for r in rows
                           if i <= len(r)] or [8])
                ws.column_dimensions[L].width = min(46, max(11, w + 3))
                for j in range(2, len(rows) + 2):
                    cell = ws.cell(row=j, column=i)
                    if i in money:
                        cell.number_format = '#,##0'
                    elif i in pct:
                        cell.number_format = '#,##0"%"'
            for j in bold_rows:
                for c in ws[j + 2]:
                    c.font = Font(bold=True)
                    c.fill = SUM_F
            ws.freeze_panes = "B2"
            return ws

        s = self._sum
        sheet("요약", ["항목", "값"], [
            ["기간", f"{s['since']} ~ {datetime.date.today()}"
                     f" ({s['eff_days']}일)"],
            ["광고 시작일", s.get("start") or "(미지정)"],
            ["광고비", s["cost"]], ["클릭", s["clk"]], ["노출", s["imp"]],
            ["평균 CPC", s["cpc"]], ["CTR(%)", s["ctr"]],
            ["실매출(커머스·광고상품)", s["amount"]], ["주문", s["orders"]],
            ["수량", s["qty"]], ["ROAS(%)", s["roas"]],
            ["네이버 추정 전환매출", s["conv_amt"]],
        ], money=(2,))

        sheet("추이", ["날짜", "광고비", "클릭", "노출", "실매출", "주문",
                     "ROAS(%)"],
              [[r["day"], r["cost"], r["clk"], r["imp"], r["amount"],
                r["orders"], r["roas"]] for r in self._rows_day],
              money=(2, 3, 4, 5, 6), pct=(7,))

        tot_m = sum(int(r["cost"] or 0) for r in self._rows_media) or 1
        sheet("매체별", ["매체", "광고비", "비중(%)", "클릭", "노출", "CPC"],
              [[r["name"], int(r["cost"] or 0),
                int(r["cost"] or 0) * 100 // tot_m, int(r["clk"] or 0),
                int(r["imp"] or 0),
                int(r["cost"] or 0) // int(r["clk"] or 1)]
               for r in self._rows_media],
              money=(2, 4, 5, 6), pct=(3,))

        # 화면과 같은 눕힌 표 : 행=매체, 열=날짜
        days_ = [r["day"] for r in reversed(self._rows_media_day)]
        get = {r["day"]: r for r in self._rows_media_day}
        rows_t = []
        for m in self.MEDIA4:
            rows_t.append([m, sum(get[d].get(m, 0) for d in days_)]
                          + [get[d].get(m, 0) for d in days_])
        rows_t.append(["합계", sum(get[d]["광고비"] for d in days_)]
                      + [get[d]["광고비"] for d in days_])
        rows_t.append(["실매출", sum(get[d]["실매출"] for d in days_)]
                      + [get[d]["실매출"] for d in days_])
        c_all = sum(get[d]["광고비"] for d in days_)
        a_all = sum(get[d]["실매출"] for d in days_)
        rows_t.append(["ROAS(%)", (a_all * 100 // c_all) if c_all else 0]
                      + [(get[d]["실매출"] * 100 // get[d]["광고비"])
                         if get[d]["광고비"] else 0 for d in days_])
        sheet("매체×날짜", ["매체", "합계"] + days_, rows_t,
              money=tuple(range(2, len(days_) + 3)),
              bold_rows=(4, 5, 6))

        sheet("시간대", ["시간", "광고비", "비중(%)", "클릭", "노출", "CPC"],
              [[f"{d['h']:02d}시", d["cost"],
                d["cost"] * 100 // (sum(x["cost"] for x in self._rows_hour)
                                    or 1),
                d["clk"], d["imp"],
                d["cost"] // d["clk"] if d["clk"] else 0]
               for d in self._rows_hour],
              money=(2, 4, 5, 6), pct=(3,))

        sheet("TOP10", ["순위", "상품명", "LCP", "광고비", "클릭", "실매출",
                        "주문", "ROAS(%)"],
              [[i + 1, r["name"], r.get("lcp_code", ""), int(r["cost"] or 0),
                int(r["clk"] or 0), r["amount"], r["orders"], r["roas"]]
               for i, r in enumerate(self._rows_top)],
              money=(4, 5, 6, 7), pct=(8,))

        sheet("LCP별", ["LCP", "상품명", "광고비", "클릭", "실매출", "주문",
                      "ROAS(%)"],
              [[r["lcp_code"], r.get("name", ""), r["cost"], r["clk"],
                r["amount"], r["orders"], r["roas"]]
               for r in self._rows_lcp],
              money=(3, 4, 5, 6), pct=(7,))

        sheet("매출구분", ["구분", "주문", "수량", "실매출"],
              [[r["kind"], int(r["orders"] or 0), int(r["qty"] or 0),
                int(r["amount"] or 0)] for r in ad_sales.split(self.days)],
              money=(2, 3, 4))

        try:
            wb.save(path)
        except Exception as e:
            QMessageBox.critical(self, "엑셀", str(e)[:250])
            return
        if QMessageBox.question(
                self, "엑셀",
                f"저장했습니다.{NL}{path}{NL}{NL}지금 열까요?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes) == QMessageBox.Yes:
            try:
                os.startfile(path)
            except Exception as e:
                QMessageBox.warning(self, "엑셀",
                                    f"열지 못했습니다: {str(e)[:120]}")


class MediaDayDialog(QDialog):
    """
    매체 하나의 **날짜별 광고비·클릭·실매출** 그래프.

    매체별 표에서 그 매체를 누르면 열린다(2026-09-12 사용자).
    실매출은 매체별로 나눌 수 없다 — 네이버가 주문을 매체로 안 갈라 준다.
    그래서 **그날 전체 실매출**을 참고선으로 같이 그린다.
    """

    def __init__(self, parent, name: str, days: int, rows: list):
        super().__init__(parent)
        self.setWindowTitle(f"매체별 추이 — {name}")
        self.setWindowFlag(Qt.Window, True)
        self.setModal(False)
        self.resize(940, 620)

        v = QVBoxLayout(self)
        t = QLabel(f"{name}  ·  최근 {days}일")
        t.setStyleSheet("font-size:16px; font-weight:bold;")
        v.addWidget(t)

        data = sorted(rows, key=lambda r: r["day"])
        cost = [int(r.get(name, 0) or 0) for r in data]
        sale = [int(r.get("실매출", 0) or 0) for r in data]
        cats = [r["day"][5:] for r in data]

        ch = QChart()
        ch.legend().setAlignment(Qt.AlignBottom)
        bs = QBarSet(f"{name} 광고비")
        bs.setColor(QColor("#1565c0"))
        for c in cost:
            bs.append(c)
        ser = QBarSeries()
        ser.append(bs)
        ch.addSeries(ser)
        ln = QLineSeries()
        ln.setName("그날 전체 실매출")
        ln.setColor(QColor("#2e7d32"))
        for i, s in enumerate(sale):
            ln.append(i, s)
        ch.addSeries(ln)

        ax = QBarCategoryAxis()
        ax.append(cats)
        ch.addAxis(ax, Qt.AlignBottom)
        ser.attachAxis(ax)
        ay = QValueAxis()
        ay.setLabelFormat("%d")
        ay.setTitleText("광고비(원)")
        ay.setRange(0, max(1, max(cost or [1])) * 1.25)
        ch.addAxis(ay, Qt.AlignLeft)
        ser.attachAxis(ay)
        a2 = QValueAxis()
        a2.setLabelFormat("%d")
        a2.setTitleText("실매출(원)")
        a2.setRange(0, max(1, max(sale or [1])) * 1.3)
        ch.addAxis(a2, Qt.AlignRight)
        ln.attachAxis(a2)
        ax2 = QValueAxis()
        ax2.setRange(-0.5, len(data) - 0.5)
        ax2.setVisible(False)
        ch.addAxis(ax2, Qt.AlignBottom)
        ln.attachAxis(ax2)
        tot = sum(cost)
        ch.setTitle(f"{name} — {days}일 광고비 {tot:,}원 "
                    f"(하루 평균 {tot // max(len(data), 1):,}원)")
        vw = QChartView(ch)
        vw.setRenderHint(QPainter.Antialiasing)
        v.addWidget(vw, 1)

        tb = QTableWidget(len(data), 4)
        tb.setHorizontalHeaderLabels(["날짜", "이 매체 광고비", "비중",
                                      "그날 전체 실매출"])
        tb.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tb.verticalHeader().setVisible(False)
        tb.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for i, r in enumerate(reversed(data)):
            c = int(r.get(name, 0) or 0)
            allc = int(r.get("광고비", 0) or 0)
            tb.setItem(i, 0, _it(r["day"]))
            tb.setItem(i, 1, _it(f"{c:,}원", True))
            tb.setItem(i, 2, _it(f"{c * 100 // allc if allc else 0}%", True))
            tb.setItem(i, 3, _it(f"{int(r.get('실매출', 0) or 0):,}원", True))
        v.addWidget(tb, 1)
