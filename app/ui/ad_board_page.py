"""
광고상황판 — 광고 전용 대시보드.

광고비만 보면 많이 쓴 날이 나쁜 날처럼 보인다. **광고비·실매출·수량을
한 화면에 겹쳐** 놓아야 그 돈이 팔렸는지가 보인다(2026-09-16 사용자).

    기간   최근 7일 / 30일 / 직접 지정(시작~끝)
    카드   광고비 · 실매출 · ROAS · 주문 · 수량 · 객단가
    그래프 날짜별 광고비·실매출 막대 + 수량 숫자
           시간대별 광고비 + 주문 (노출시간 설정과 함께)
    표     팔린 상품 목록 · LCP별 통계

설정(계정·수집·보고서)은 「설정」 탭에 있다. 여기는 **보는 곳**이다.
"""
import datetime

from PySide6.QtCharts import (QBarCategoryAxis, QBarSeries, QBarSet, QChart,
                              QChartView, QLineSeries, QValueAxis)
from PySide6.QtCore import QDate, QThread, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDateEdit,
                               QFrame, QGridLayout, QHBoxLayout, QHeaderView,
                               QLabel, QPushButton, QTableWidget,
                               QTableWidgetItem, QTabWidget, QVBoxLayout,
                               QWidget)

from .. import db
from .numitem import make_item
from ..lohas import (ad_account, ad_profit, ad_sales, ad_schedule,
                     ad_spend, ad_stats)

NL = chr(10)


def _it(v, right=False, color="", bold=False, num=None):
    """
    표 칸 하나. **정렬은 숫자로** 한다 — 글자로 비교하면 `10,356원` 이
    `9,000원` 보다 앞에 온다(2026-09-16 사용자). `numitem` 참고.
    """
    return make_item(v, right, color, bold, num)


def _roas_color(r):
    return "#2e7d32" if r >= 300 else "#c62828" if r == 0 else "#ef6c00"


class Card(QFrame):
    def __init__(self, title, accent="#37474f"):
        super().__init__()
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            "QFrame{border:1px solid #d0d7de;border-radius:8px;"
            "background:#ffffff;}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 9, 12, 9)
        lay.setSpacing(2)
        self.t = QLabel(title)
        self.t.setStyleSheet("color:#57606a;border:none;")
        self.t.setWordWrap(True)
        self.v = QLabel("-")
        f = QFont()
        f.setPointSize(18)
        f.setBold(True)
        self.v.setFont(f)
        self.v.setStyleSheet(f"color:{accent};border:none;")
        lay.addWidget(self.t)
        lay.addWidget(self.v)

    def set(self, value, sub=None, color=None):
        self.v.setText(str(value))
        if sub is not None:
            self.t.setText(sub)
        if color:
            self.v.setStyleSheet(f"color:{color};border:none;")


class PullWorker(QThread):
    """
    **오늘치를 받아온다.**

    상황판은 SQLite 에 쌓인 것만 읽는데 수집은 새벽 3시 한 번뿐이라
    오늘 칸이 늘 비어 있었다(2026-09-16 사용자: 오늘 매출은 안 뜨나?).
    화면을 여는 김에 오늘·어제치만 다시 받는다 — 커머스 7초, 주문 DB 1초.
    """
    done = Signal(str)

    def __init__(self, days=3):
        super().__init__()
        self.days = days

    def run(self):
        msg = []
        try:
            r = ad_sales.collect(days=self.days, log=lambda *_: None)
            msg.append(f"매출 {r['amount']:,}원")
        except Exception as e:
            msg.append("매출 실패 " + str(e)[:60])
        try:
            r = ad_profit.collect(days=max(self.days, 7),
                                  log=lambda *_: None)
            msg.append(f"이익 {r['profit_ad']:,}원")
        except Exception as e:
            msg.append("이익 실패 " + str(e)[:60])
        self.done.emit("  ·  ".join(msg))


class AdBoardPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 10, 10, 10)

        head = QHBoxLayout()
        t = QLabel("광고상황판")
        t.setStyleSheet("font-size:17px; font-weight:bold;")
        head.addWidget(t)
        self.lbl_acc = QLabel("")
        self.lbl_acc.setStyleSheet("color:#57606a;")
        head.addWidget(self.lbl_acc)
        head.addStretch(1)

        head.addWidget(QLabel("기간"))
        self.cmb = QComboBox()
        self.cmb.addItem("최근 7일", 7)
        self.cmb.addItem("최근 14일", 14)
        self.cmb.addItem("최근 30일", 30)
        self.cmb.addItem("직접 지정", 0)
        self.cmb.currentIndexChanged.connect(self._period)
        head.addWidget(self.cmb)
        self.d1 = QDateEdit()
        self.d1.setCalendarPopup(True)
        self.d1.setDisplayFormat("yyyy-MM-dd")
        self.d2 = QDateEdit()
        self.d2.setCalendarPopup(True)
        self.d2.setDisplayFormat("yyyy-MM-dd")
        self.d2.setDate(QDate.currentDate())
        self.d1.setDate(QDate.currentDate().addDays(-6))
        for w in (self.d1, self.d2):
            w.setEnabled(False)
            head.addWidget(w)
        self.btn_go = QPushButton("보기")
        self.btn_go.setEnabled(False)
        self.btn_go.clicked.connect(self.refresh)
        head.addWidget(self.btn_go)
        head.addWidget(QLabel("구분"))
        self.cmb_only = QComboBox()
        self.cmb_only.addItem("광고상품만", True)
        self.cmb_only.addItem("가게 전체", False)
        self.cmb_only.setToolTip(
            "광고비는 광고상품에만 나갑니다. 실수입을 광고비와 맞대보려면"
            + NL + "광고상품만 보는 것이 맞습니다."
            + NL + "'가게 전체' 는 W코드·비광고 상품까지 더한 값입니다.")
        self.cmb_only.currentIndexChanged.connect(self.refresh)
        head.addWidget(self.cmb_only)

        self.btn_pull = QPushButton("⬇ 오늘치 받기")
        self.btn_pull.setToolTip(
            "커머스 API 와 주문 DB 에서 오늘·어제 매출과 원가를 다시"
            " 받아옵니다 (약 10초)")
        self.btn_pull.clicked.connect(self._pull)
        head.addWidget(self.btn_pull)
        b = QPushButton("🔄 새로고침")
        b.clicked.connect(self.refresh)
        head.addWidget(b)
        v.addLayout(head)

        # **언제 받은 자료인지** 를 늘 보여준다. 0원이 '안 팔린 것' 인지
        # '아직 안 받은 것' 인지 구분이 안 됐다(2026-09-16).
        self.lbl_fresh = QLabel("")
        self.lbl_fresh.setStyleSheet(
            "color:#57606a; background:#f6f8fa; border-radius:6px;"
            " padding:5px 9px;")
        v.addWidget(self.lbl_fresh)

        # **실수입 줄** — 기간을 바꿔도 늘 오늘·7일·30일을 같이 보여준다.
        # 남는 돈이 얼마인지가 제일 먼저 보여야 한다(2026-09-16 사용자).
        nw = QWidget()
        ng = QGridLayout(nw)
        ng.setContentsMargins(0, 0, 0, 2)
        self.n_today = Card("오늘 실수입", "#00838f")
        self.n_7 = Card("최근 7일 실수입", "#00838f")
        self.n_30 = Card("최근 30일 실수입", "#00838f")
        self.n_month = Card("이번 달 실수입", "#00838f")
        for i, c in enumerate([self.n_today, self.n_7, self.n_30,
                               self.n_month]):
            ng.addWidget(c, 0, i)
            ng.setColumnStretch(i, 1)
        v.addWidget(nw)

        cw = QWidget()
        g = QGridLayout(cw)
        g.setContentsMargins(0, 4, 0, 4)
        self.c_cost = Card("광고비", "#ad1457")
        self.c_sale = Card("실매출", "#2e7d32")
        self.c_roas = Card("ROAS", "#1565c0")
        self.c_ord = Card("주문", "#ef6c00")
        self.c_qty = Card("수량", "#6a1b9a")
        self.c_avg = Card("객단가", "#00695c")
        for i, c in enumerate([self.c_cost, self.c_sale, self.c_roas,
                               self.c_ord, self.c_qty, self.c_avg]):
            g.addWidget(c, 0, i)
            g.setColumnStretch(i, 1)
        v.addWidget(cw)

        tabs = QTabWidget()
        tabs.addTab(self._tab_day(), "📊 날짜별")
        tabs.addTab(self._tab_hour(), "🕘 시간대")
        tabs.addTab(self._tab_item(), "🛒 팔린 상품")
        tabs.addTab(self._tab_lcp(), "📦 LCP별")
        tabs.addTab(self._tab_profit(), "💵 실수입")
        tabs.addTab(self._tab_hist(), "⏱ 변경이력")
        v.addWidget(tabs, 1)
        self.tabs = tabs
        self.refresh()

    # ------------------------------------------------------------ 수집
    def _pull(self):
        if getattr(self, "_w", None) and self._w.isRunning():
            return
        self.btn_pull.setEnabled(False)
        self.btn_pull.setText("⏳ 받는 중…")
        self._w = PullWorker(3)
        self._w.done.connect(self._pulled)
        self._w.start()

    def _pulled(self, msg):
        self.btn_pull.setEnabled(True)
        self.btn_pull.setText("⬇ 오늘치 받기")
        self.refresh()
        self.lbl_fresh.setText(self.lbl_fresh.text() + "   ·   받음: " + msg)

    def _fresh(self):
        """마지막 수집 시각과 '오늘은 아직 0건' 을 말로 적는다."""
        today = datetime.date.today().isoformat()
        up = ad_profit.last_collected()
        sale = ad_sales.today()
        sn = int(sale.get("orders") or 0)
        pf = ad_profit.by_day(today, today, self._ad_only())
        p0 = pf[0] if pf else {}
        old = (not up) or up[:10] < today
        self.lbl_fresh.setText(
            f"오늘 {today}  ·  광고상품 매출 {int(sale.get('amount') or 0):,}원"
            f" ({sn}건)  ·  이익 {int(p0.get('profit') or 0):,}원"
            f"  ·  광고비 {int(p0.get('ad') or 0):,}원"
            f"  →  실수입 {int(p0.get('net') or 0):,}원"
            + (f"        마지막 수집 {up}" if up else "        수집 기록 없음")
            + ("   ⚠ 오늘 받은 적이 없습니다 — [⬇ 오늘치 받기]"
               if old else ""))
        self.lbl_fresh.setStyleSheet(
            "border-radius:6px; padding:5px 9px; font-weight:bold;"
            + ("background:#fff3e0; color:#e65100;" if old
               else "background:#f6f8fa; color:#24292f;"))

    # ------------------------------------------------------------ 기간
    def _period(self):
        custom = (self.cmb.currentData() == 0)
        for w in (self.d1, self.d2, self.btn_go):
            w.setEnabled(custom)
        if not custom:
            self.refresh()

    def _range(self):
        n = self.cmb.currentData()
        if n:
            e = datetime.date.today()
            s = e - datetime.timedelta(days=n - 1)
        else:
            s = datetime.date.fromisoformat(
                self.d1.date().toString("yyyy-MM-dd"))
            e = datetime.date.fromisoformat(
                self.d2.date().toString("yyyy-MM-dd"))
        st = ad_account.start_of(self.cu) if getattr(self, "cu", "") else ""
        if st:                       # 광고 시작 전은 보지 않는다
            s = max(s, datetime.date.fromisoformat(st))
        return s, e

    # ------------------------------------------------------------ 탭
    def _tab_day(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.ch_day = QChart()
        self.ch_day.legend().setAlignment(Qt.AlignBottom)
        vw = QChartView(self.ch_day)
        vw.setRenderHint(QPainter.Antialiasing)
        lay.addWidget(vw, 3)
        self.t_day = QTableWidget(0, 10)
        self.t_day.setHorizontalHeaderLabels(
            ["날짜", "광고비", "실매출", "ROAS", "이익", "실수입",
             "주문", "수량", "객단가", "클릭"])
        self.t_day.setToolTip(
            "이익 = 정산금액 − 원가 − 배송비" + NL
            + "실수입 = 이익 − 광고비" + NL * 2
            + "실매출은 커머스 API(스토어 결제금액), 이익은 주문 DB"
            + NL + "(정산·원가·배송비) 에서 옵니다. 출처가 달라 같은 날"
            + NL + "숫자가 조금 어긋날 수 있습니다.")
        self.t_day.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.t_day.verticalHeader().setVisible(False)
        self.t_day.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch)
        self.t_day.setSortingEnabled(True)
        lay.addWidget(self.t_day, 2)
        return w

    def _tab_hour(self):
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
        self.t_hour = QTableWidget(0, 7)
        self.t_hour.setHorizontalHeaderLabels(
            ["시간", "노출", "광고비", "클릭", "주문", "실매출", "ROAS"])
        self.t_hour.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.t_hour.verticalHeader().setVisible(False)
        self.t_hour.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch)
        self.t_hour.setSortingEnabled(True)
        lay.addWidget(self.t_hour, 2)
        return w

    def _tab_item(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        row = QHBoxLayout()
        row.addWidget(QLabel("구분"))
        self.cmb_kind = QComboBox()
        self.cmb_kind.addItem("전체", "")
        for k in ad_sales.KINDS:
            self.cmb_kind.addItem(k, k)
        self.cmb_kind.setCurrentIndex(1)       # 광고상품
        self.cmb_kind.currentIndexChanged.connect(self._fill_item)
        row.addWidget(self.cmb_kind)
        row.addStretch(1)
        self.lbl_item = QLabel("")
        self.lbl_item.setStyleSheet("font-weight:bold; color:#1b5e20;")
        row.addWidget(self.lbl_item)
        lay.addLayout(row)
        self.t_item = QTableWidget(0, 8)
        self.t_item.setHorizontalHeaderLabels(
            ["상품명", "구분", "LCP", "수량", "주문", "실매출", "단가",
             "상품번호"])
        self.t_item.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.t_item.verticalHeader().setVisible(False)
        self.t_item.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch)
        self.t_item.setSortingEnabled(True)
        lay.addWidget(self.t_item, 1)
        return w

    def _tab_lcp(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.lbl_lcp = QLabel("")
        self.lbl_lcp.setWordWrap(True)
        self.lbl_lcp.setStyleSheet(
            "font-weight:bold; color:#1b5e20; background:#e8f5e9;"
            " border-radius:6px; padding:8px;")
        lay.addWidget(self.lbl_lcp)
        self.t_lcp = QTableWidget(0, 7)
        self.t_lcp.setHorizontalHeaderLabels(
            ["LCP", "상품명", "광고비", "클릭", "실매출", "주문", "ROAS"])
        self.t_lcp.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.t_lcp.verticalHeader().setVisible(False)
        self.t_lcp.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch)
        self.t_lcp.setSortingEnabled(True)
        lay.addWidget(self.t_lcp, 1)
        return w

    def _tab_profit(self):
        """
        **실수입** — 매출이 아니라 남는 돈.

        정산금액 − 원가 − 배송비 = 이익, 거기서 광고비를 빼면 실수입이다.
        매출만 보면 잘 판 것처럼 보이지만 원가가 크면 남는 게 없다
        (2026-09-16 사용자).
        """
        w = QWidget()
        lay = QVBoxLayout(w)
        row = QHBoxLayout()
        self.lbl_prof = QLabel("")
        self.lbl_prof.setWordWrap(True)
        row.addWidget(self.lbl_prof, 1)
        b = QPushButton("주문·원가 받기")
        b.setToolTip("주문 DB 에서 정산금액·원가·배송비를 다시 받아옵니다.")
        b.clicked.connect(self._collect_profit)
        row.addWidget(b)
        lay.addLayout(row)

        self.ch_prof = QChart()
        self.ch_prof.legend().setAlignment(Qt.AlignBottom)
        vw = QChartView(self.ch_prof)
        vw.setRenderHint(QPainter.Antialiasing)
        lay.addWidget(vw, 3)

        self.t_prof = QTableWidget(0, 8)
        self.t_prof.setHorizontalHeaderLabels(
            ["날짜", "정산금액", "원가", "배송비", "이익", "광고비",
             "실수입", "이익률"])
        self.t_prof.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.t_prof.verticalHeader().setVisible(False)
        self.t_prof.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch)
        self.t_prof.setSortingEnabled(True)
        lay.addWidget(self.t_prof, 2)

        lay.addWidget(QLabel("상품별 이익"))
        self.t_pitem = QTableWidget(0, 8)
        self.t_pitem.setHorizontalHeaderLabels(
            ["상품명", "LCP", "수량", "정산금액", "원가", "이익", "마진율",
             "광고비"])
        self.t_pitem.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.t_pitem.verticalHeader().setVisible(False)
        self.t_pitem.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch)
        self.t_pitem.setSortingEnabled(True)
        lay.addWidget(self.t_pitem, 2)
        return w

    def _collect_profit(self):
        try:
            ad_profit.collect(days=60, log=lambda *_: None)
        except Exception as e:
            self.lbl_prof.setText("받기 실패: " + str(e)[:150])
            return
        self._fill_profit()

    def _fill_profit(self):
        s, e = self.s.isoformat(), self.e.isoformat()
        ao = self._ad_only()
        rows = ad_profit.by_day(s, e, ao)
        sm = ad_profit.summary(s, e, ao)
        ch = self.ch_prof
        ch.removeAllSeries()
        for ax in list(ch.axes()):
            ch.removeAxis(ax)
        self.t_prof.setSortingEnabled(False)
        self.t_prof.setRowCount(len(rows))
        for i, r in enumerate(rows):
            m = (r["profit"] * 100 // r["settle"]) if r["settle"] else 0
            self.t_prof.setItem(i, 0, _it(r["day"]))
            self.t_prof.setItem(i, 1, _it(f"{r['settle']:,}원", True))
            self.t_prof.setItem(i, 2, _it(f"{r['cost']:,}원", True))
            self.t_prof.setItem(i, 3, _it(f"{r['ship']:,}원", True))
            self.t_prof.setItem(i, 4, _it(f"{r['profit']:,}원", True))
            self.t_prof.setItem(i, 5, _it(f"{r['ad']:,}원", True))
            self.t_prof.setItem(i, 6, _it(
                f"{r['net']:,}원", True,
                "#2e7d32" if r["net"] > 0 else "#c62828", True))
            self.t_prof.setItem(i, 7, _it(f"{m:,}%", True))
        self.t_prof.setSortingEnabled(True)
        if rows:
            b1 = QBarSet("이익")
            b1.setColor(QColor("#2e7d32"))
            b2 = QBarSet("광고비")
            b2.setColor(QColor("#ad1457"))
            ln = QLineSeries()
            ln.setName("실수입")
            ln.setColor(QColor("#1565c0"))
            for i, r in enumerate(rows):
                b1.append(r["profit"])
                b2.append(r["ad"])
                ln.append(i, r["net"])
            ser = QBarSeries()
            ser.append(b1)
            ser.append(b2)
            ch.addSeries(ser)
            ch.addSeries(ln)
            ax = QBarCategoryAxis()
            ax.append([r["day"][5:] for r in rows])
            ch.addAxis(ax, Qt.AlignBottom)
            ser.attachAxis(ax)
            ay = QValueAxis()
            ay.setLabelFormat("%d")
            ay.setRange(min(0, min(r["net"] for r in rows) * 1.2),
                        max(1, max(max(r["profit"], r["ad"])
                                   for r in rows)) * 1.2)
            ch.addAxis(ay, Qt.AlignLeft)
            ser.attachAxis(ay)
            ln.attachAxis(ay)
            ax2 = QValueAxis()
            ax2.setRange(-0.5, len(rows) - 0.5)
            ax2.setVisible(False)
            ch.addAxis(ax2, Qt.AlignBottom)
            ln.attachAxis(ax2)
        if rows:
            zero = QLineSeries()
            zero.setName("손익분기 (0원)")
            zero.setColor(QColor("#90a4ae"))
            for i in range(len(rows)):
                zero.append(i, 0)
            ch.addSeries(zero)
            zero.attachAxis(ay)
            zero.attachAxis(ax2)
        be2 = ad_profit.breakeven(s, e, ao)
        ch.setTitle(
            f"이익 vs 광고비 — 실수입 {sm['net']:,}원"
            f"   (마진 {be2['margin']}% → 손익분기 ROAS {be2['be_roas']:,}%,"
            f" 지금 {be2['roas_settle']:,}%)")
        self.lbl_prof.setText(
            f"정산 {sm['settle']:,}원 − 원가 {sm['cost']:,}원 − 배송 "
            f"{sm['ship']:,}원 = 이익 {sm['profit']:,}원 ({sm['margin']}%)"
            + NL + f"이익 {sm['profit']:,}원 − 광고비 {sm['ad']:,}원 = "
                   f"실수입 {sm['net']:,}원 ({sm['net_margin']}%)"
            + (f"   ·   정산금액이 없어 결제금액으로 센 건 {sm['ns']}건"
               if sm["ns"] else "")
            + NL + "구분별 이익 — " + ("  ·  ".join(
                f"{r['kind']} {int(r['profit'] or 0):,}원"
                f"({int(r['orders'] or 0)}건)"
                for r in ad_profit.split(s, e)) or "없음")
            + ("      ← 지금은 광고상품만 세고 있습니다"
               if ao else "      ← 지금은 가게 전체를 세고 있습니다"))
        self.lbl_prof.setStyleSheet(
            "font-weight:bold; border-radius:6px; padding:8px; background:"
            + ("#e8f5e9; color:#1b5e20;" if sm["net"] > 0
               else "#ffebee; color:#b71c1c;"))

        items = ad_profit.by_item(s, e, 300, ao)
        self.t_pitem.setSortingEnabled(False)
        self.t_pitem.setRowCount(len(items))
        for i, r in enumerate(items):
            self.t_pitem.setItem(i, 0, _it(str(r["name"] or "")[:46]))
            self.t_pitem.setItem(i, 1, _it(r["lcp_code"] or "-"))
            self.t_pitem.setItem(i, 2, _it(f"{r['qty']:,}", True))
            self.t_pitem.setItem(i, 3, _it(f"{r['settle']:,}원", True))
            self.t_pitem.setItem(i, 4, _it(f"{r['cost']:,}원", True))
            self.t_pitem.setItem(i, 5, _it(
                f"{r['profit']:,}원", True,
                "#2e7d32" if r["profit"] > 0 else "#c62828", True))
            self.t_pitem.setItem(i, 6, _it(f"{r['margin']:,}%", True))
            self.t_pitem.setItem(i, 7, _it(f"{r['ad']:,}원", True))
        self.t_pitem.setSortingEnabled(True)

    def _tab_hist(self):
        """노출시간·입찰가를 언제 바꿨는지."""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("광고 노출시간 변경 이력"))
        self.t_sc = QTableWidget(0, 4)
        self.t_sc.setHorizontalHeaderLabels(
            ["적용일", "노출시간", "메모", "기록시각"])
        self.t_sc.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.t_sc.verticalHeader().setVisible(False)
        self.t_sc.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.Stretch)
        self.t_sc.setMaximumHeight(170)
        lay.addWidget(self.t_sc)
        lay.addWidget(QLabel("입찰가 변경 이력  —  묶어서 본 것"))
        self.t_bsum = QTableWidget(0, 6)
        self.t_bsum.setHorizontalHeaderLabels(
            ["바뀐 시각", "계정", "이전", "이후", "그룹 수", "예시"])
        self.t_bsum.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.t_bsum.verticalHeader().setVisible(False)
        self.t_bsum.horizontalHeader().setSectionResizeMode(
            5, QHeaderView.Stretch)
        self.t_bsum.setSortingEnabled(True)
        self.t_bsum.setMaximumHeight(180)
        self.t_bsum.setToolTip(
            "740개 그룹을 한꺼번에 내리면 낱건으로는 740줄이 됩니다."
            + NL + "같은 시각·같은 값 변화를 한 줄로 묶었습니다."
            + NL * 2
            + "시각은 **바꾼 때가 아니라 확인한 때**입니다 — 3시간마다"
            + NL + "확인하므로 실제로 바꾼 시각과 최대 3시간 차이납니다.")
        lay.addWidget(self.t_bsum)
        lay.addWidget(QLabel(
            "낱건  —  수집할 때 전 값과 다르면 남습니다"))
        self.t_bid = QTableWidget(0, 5)
        self.t_bid.setHorizontalHeaderLabels(
            ["바뀐 시각", "LCP / 광고그룹", "이전", "이후", "변화"])
        self.t_bid.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.t_bid.verticalHeader().setVisible(False)
        self.t_bid.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch)
        self.t_bid.setSortingEnabled(True)
        lay.addWidget(self.t_bid, 1)
        return w

    def _fill_hist(self):
        hs = ad_schedule.history(self.cu, 100)
        self.t_sc.setRowCount(len(hs))
        for i, r in enumerate(hs):
            self.t_sc.setItem(i, 0, _it(r["from_day"]))
            self.t_sc.setItem(i, 1, _it(ad_schedule.label(r)))
            self.t_sc.setItem(i, 2, _it(r.get("memo") or ""))
            self.t_sc.setItem(i, 3, _it(r.get("created_at") or ""))
        bs = ad_account.bid_changes()
        self.t_bsum.setSortingEnabled(False)
        self.t_bsum.setRowCount(max(len(bs), 1))
        for i, r in enumerate(bs):
            o, n = int(r["old_bid"] or 0), int(r["new_bid"] or 0)
            self.t_bsum.setItem(i, 0, _it(r["changed_at"]))
            self.t_bsum.setItem(i, 1, _it(r["label"]))
            self.t_bsum.setItem(i, 2, _it(f"{o:,}원", True))
            self.t_bsum.setItem(i, 3, _it(
                f"{n:,}원", True, "#2e7d32" if n > o else "#c62828", True))
            self.t_bsum.setItem(i, 4, _it(f"{r['n']:,}개", True))
            self.t_bsum.setItem(i, 5, _it(r["sample"]))
        if not bs:
            self.t_bsum.setItem(0, 5, _it("아직 기록이 없습니다"))
        self.t_bsum.setSortingEnabled(True)

        hb = ad_account.bid_history(self.cu, 500)
        self.t_bid.setSortingEnabled(False)
        self.t_bid.setRowCount(max(len(hb), 1))
        for i, r in enumerate(hb):
            o, n = int(r["old_bid"] or 0), int(r["new_bid"] or 0)
            self.t_bid.setItem(i, 0, _it(r["changed_at"]))
            self.t_bid.setItem(i, 1, _it(r.get("name") or r["adgroup_id"]))
            self.t_bid.setItem(i, 2, _it(f"{o:,}원", True))
            self.t_bid.setItem(i, 3, _it(f"{n:,}원", True))
            self.t_bid.setItem(i, 4, _it(
                f"{n - o:+,}원", True, "#2e7d32" if n > o else "#c62828"))
        if not hb:
            self.t_bid.setItem(0, 1, _it(
                "아직 기록이 없습니다 — 입찰가를 바꾸고 「설정」 탭에서"
                " [기본정보 수집] 을 누르면 여기 남습니다"))
        self.t_bid.setSortingEnabled(True)

    # ------------------------------------------------------------ 채우기
    def refresh(self):
        acc = ad_account.main_account() or {}
        self.cu = acc.get("customer_id") or ""
        self.lbl_acc.setText(
            f"— {acc.get('label') or '(계정 없음)'}"
            + (f"  ·  광고 시작 {ad_account.start_of(self.cu)}"
               if ad_account.start_of(self.cu) else ""))
        s, e = self._range()
        self.s, self.e = s, e
        self.days = (e - s).days + 1
        for fn in (self._fresh, self._fill_net, self._fill_cards,
                   self._fill_day, self._fill_hour,
                   self._fill_item, self._fill_lcp, self._fill_profit,
                   self._fill_hist):
            try:
                fn()
            except Exception as ex:
                print(f"[광고상황판] {fn.__name__} {ex}")

    def _rows_range(self):
        """
        기간 안의 날짜별 광고비 + 매출 **+ 이익 · 실수입**.

        매출만 있고 이익이 없으면 잘 판 날인지 알 수 없다. 9/13 은 매출
        154,620원에 실수입 1,189원이었다 — 거의 본전이다(2026-09-16 사용자:
        날짜별로 실제 이익금도 같이).
        """
        cost = {r["day"]: r for r in ad_spend.by_day(400)}
        sale = {r["day"]: r for r in ad_sales.by_day(400)}
        prof = {r["day"]: r for r in ad_profit.by_day(
            self.s.isoformat(), self.e.isoformat(), self._ad_only())}
        out, d = [], self.s
        while d <= self.e:
            k = d.isoformat()
            c = cost.get(k, {})
            v = sale.get(k, {})
            pf = prof.get(k, {})
            cst = int(c.get("cost") or 0)
            amt = int(v.get("amount") or 0)
            out.append({"day": k, "cost": cst, "clk": int(c.get("clk") or 0),
                        "amount": amt, "orders": int(v.get("orders") or 0),
                        "qty": int(v.get("qty") or 0),
                        "roas": (amt * 100 // cst) if cst else 0,
                        "settle": int(pf.get("settle") or 0),
                        "profit": int(pf.get("profit") or 0),
                        "net": int(pf.get("net") or 0)})
            d += datetime.timedelta(days=1)
        return out

    def _ad_only(self) -> bool:
        """광고상품만 셀지. 광고비는 광고상품에만 나가니 기본은 '만'."""
        return bool(getattr(self, "cmb_only", None) is None
                    or self.cmb_only.currentData())

    def _fill_net(self):
        """오늘·7일·30일·이번달 실수입. 기간 선택과 무관하게 늘 같은 창구."""
        today = datetime.date.today()
        spans = [
            (self.n_today, today, today, "오늘"),
            (self.n_7, today - datetime.timedelta(days=6), today, "최근 7일"),
            (self.n_30, today - datetime.timedelta(days=29), today,
             "최근 30일"),
            (self.n_month, today.replace(day=1), today, "이번 달"),
        ]
        st = ad_account.start_of(self.cu)
        for card, s, e, name in spans:
            if st:
                s = max(s, datetime.date.fromisoformat(st))
            try:
                sm = ad_profit.summary(s.isoformat(), e.isoformat(),
                                       self._ad_only())
                tip = ad_profit.tip(s.isoformat(), e.isoformat(),
                                    self._ad_only())
            except Exception:
                sm, tip = {}, ""
            net = int(sm.get("net") or 0)
            card.set(
                f"{net:,}원",
                f"{name} 실수입  ·  이익 {int(sm.get('profit') or 0):,}"
                f" − 광고비 {int(sm.get('ad') or 0):,}",
                "#2e7d32" if net > 0 else "#c62828" if net < 0 else "#9e9e9e")
            if tip:
                for w in (card, card.v, card.t):
                    w.setToolTip(tip)

    def _fill_cards(self):
        rows = self._rows_range()
        cost = sum(r["cost"] for r in rows)
        amt = sum(r["amount"] for r in rows)
        qty = sum(r["qty"] for r in rows)
        odr = sum(r["orders"] for r in rows)
        roas = (amt * 100 // cost) if cost else 0
        # **손익분기 ROAS** — 마진율의 역수다. 마진 21% 면 476% 를 넘겨야
        # 본전이다. 숫자만 보면 144% 도 좋아 보여 오해가 난다
        # (2026-09-16 사용자: ROAS 에 마우스 올리면 설명이 뜨게).
        try:
            be = ad_profit.breakeven(self.s.isoformat(),
                                     self.e.isoformat(), self._ad_only())
            tip = ad_profit.tip(self.s.isoformat(), self.e.isoformat(),
                                self._ad_only())
        except Exception:
            be, tip = {}, ""
        self.c_cost.set(f"{cost:,}원",
                        f"광고비  ·  {self.s} ~ {self.e} ({self.days}일)")
        self.c_sale.set(f"{amt:,}원", "실매출 (광고상품·커머스)")
        bez = be.get("be_roas_paid") or 0
        self.c_roas.set(
            f"{roas:,}%",
            "ROAS  ·  실매출 ÷ 광고비"
            + (f"   ·   손익분기 {bez:,}%" if bez else ""),
            "#2e7d32" if (bez and roas >= bez) else
            "#c62828" if bez else _roas_color(roas))
        if tip:
            for w in (self.c_roas, self.c_roas.v, self.c_roas.t):
                w.setToolTip(tip)
        self.c_ord.set(f"{odr:,}건", "주문")
        self.c_qty.set(f"{qty:,}개", "수량")
        self.c_avg.set(f"{amt // qty if qty else 0:,}원",
                       f"객단가 (금액/수량)  ·  건당 "
                       f"{amt // odr if odr else 0:,}원")

    def _fill_day(self):
        rows = self._rows_range()
        self._rows_day = rows
        self.t_day.setSortingEnabled(False)
        ch = self.ch_day
        ch.removeAllSeries()
        for ax in list(ch.axes()):
            ch.removeAxis(ax)
        self.t_day.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self.t_day.setItem(i, 0, _it(r["day"]))
            self.t_day.setItem(i, 1, _it(f"{r['cost']:,}원", True))
            self.t_day.setItem(i, 2, _it(f"{r['amount']:,}원", True))
            self.t_day.setItem(i, 3, _it(f"{r['roas']:,}%", True,
                                         _roas_color(r["roas"])))
            self.t_day.setItem(i, 4, _it(f"{r['profit']:,}원", True))
            self.t_day.setItem(i, 5, _it(
                f"{r['net']:,}원", True,
                "#2e7d32" if r["net"] > 0 else
                "#c62828" if r["net"] < 0 else "#9e9e9e", True))
            self.t_day.setItem(i, 6, _it(f"{r['orders']:,}", True))
            self.t_day.setItem(i, 7, _it(f"{r['qty']:,}", True))
            self.t_day.setItem(i, 8, _it(
                f"{r['amount'] // r['qty'] if r['qty'] else 0:,}원", True))
            self.t_day.setItem(i, 9, _it(f"{r['clk']:,}", True))
        self.t_day.setSortingEnabled(True)
        if not rows:
            ch.setTitle("자료가 없습니다")
            return
        b1 = QBarSet("광고비")
        b1.setColor(QColor("#ad1457"))
        b2 = QBarSet("실매출")
        b2.setColor(QColor("#2e7d32"))
        b3 = QBarSet("이익")
        b3.setColor(QColor("#1565c0"))
        ln = QLineSeries()
        ln.setName("수량")
        ln.setColor(QColor("#6a1b9a"))
        # **실수입 선.** 0 아래로 내려가면 그날은 광고로 손해를 본 것이다
        net = QLineSeries()
        net.setName("실수입")
        net.setColor(QColor("#e65100"))
        zero = QLineSeries()
        zero.setName("본전")
        zero.setColor(QColor("#b0bec5"))
        cats = []
        for i, r in enumerate(rows):
            b1.append(r["cost"])
            b2.append(r["amount"])
            b3.append(r["profit"])
            ln.append(i, r["qty"])
            net.append(i, r["net"])
            zero.append(i, 0)
            cats.append(r["day"][5:])
        ser = QBarSeries()
        ser.append(b1)
        ser.append(b2)
        ser.append(b3)
        # **수량을 막대 위에 숫자로** 적는다 (사용자: 수치로 수량)
        ser.setLabelsVisible(False)
        ch.addSeries(ser)
        ch.addSeries(ln)
        ax = QBarCategoryAxis()
        ax.append(cats)
        ch.addAxis(ax, Qt.AlignBottom)
        ser.attachAxis(ax)
        ay = QValueAxis()
        ay.setLabelFormat("%d")
        ay.setTitleText("원")
        ay.setRange(min(0, min(r["net"] for r in rows) * 1.25),
                    max(1, max(max(r["cost"], r["amount"], r["profit"])
                               for r in rows)) * 1.25)
        ch.addAxis(ay, Qt.AlignLeft)
        ser.attachAxis(ay)
        for s2 in (net, zero):
            ch.addSeries(s2)
            s2.attachAxis(ay)
        a2 = QValueAxis()
        a2.setLabelFormat("%d")
        a2.setTitleText("수량")
        a2.setRange(0, max(1, max(r["qty"] for r in rows)) * 1.6)
        ch.addAxis(a2, Qt.AlignRight)
        ln.attachAxis(a2)
        ax2 = QValueAxis()
        ax2.setRange(-0.5, len(rows) - 0.5)
        ax2.setVisible(False)
        ch.addAxis(ax2, Qt.AlignBottom)
        for s2 in (ln, net, zero):
            s2.attachAxis(ax2)
        cost = sum(r["cost"] for r in rows)
        amt = sum(r["amount"] for r in rows)
        qty = sum(r["qty"] for r in rows)
        nets = sum(r["net"] for r in rows)
        ch.setTitle(f"광고비 {cost:,}원 · 실매출 {amt:,}원 · 수량 {qty:,}개"
                    f"  ·  ROAS {amt * 100 // cost if cost else 0:,}%"
                    f"  ·  실수입 {nets:,}원")

    def _fill_hour(self):
        from ..lohas import ad_detail
        rows = {r["k"]: r for r in
                ad_detail.by("hour", self.cu, days=self.days, limit=24)}
        sale = {r["hour"]: r for r in ad_sales.by_hour(self.days)}
        sc = ad_schedule.current(self.cu)
        s_, e_ = sc.get("start_hour"), sc.get("end_hour")
        data = []
        for h in range(24):
            k = f"{h:02d}"
            r = rows.get(k, {})
            v = sale.get(k, {})
            data.append({"h": h, "cost": int(r.get("cost") or 0),
                         "clk": int(r.get("clk") or 0),
                         "orders": int(v.get("orders") or 0),
                         "amount": int(v.get("amount") or 0),
                         "on": True if s_ is None else (s_ <= h < e_)})
        self._rows_hour = data
        tot = sum(d["cost"] for d in data) or 1
        amt = sum(d["amount"] for d in data)
        ch = self.ch_hour
        ch.removeAllSeries()
        for ax in list(ch.axes()):
            ch.removeAxis(ax)
        bs = QBarSet("광고비")
        bs.setColor(QColor("#4527a0"))
        ln = QLineSeries()
        ln.setName("실매출")
        ln.setColor(QColor("#2e7d32"))
        for i, d in enumerate(data):
            bs.append(d["cost"])
            ln.append(i, d["amount"])
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
        a2.setTitleText("실매출(원)")
        a2.setRange(0, max(1, max(d["amount"] for d in data)) * 1.3)
        ch.addAxis(a2, Qt.AlignRight)
        ln.attachAxis(a2)
        ax2 = QValueAxis()
        ax2.setRange(-0.5, 23.5)
        ax2.setVisible(False)
        ch.addAxis(ax2, Qt.AlignBottom)
        ln.attachAxis(ax2)
        ch.setTitle(f"시간대별 광고비 + 실매출 ({self.days}일)")

        self.t_hour.setSortingEnabled(False)
        self.t_hour.setRowCount(24)
        for i, d in enumerate(data):
            r_ = (d["amount"] * 100 // d["cost"]) if d["cost"] else 0
            self.t_hour.setItem(i, 0, _it(f"{d['h']:02d}시"))
            self.t_hour.setItem(i, 1, _it("○" if d["on"] else "—", True,
                                          "" if d["on"] else "#9e9e9e"))
            self.t_hour.setItem(i, 2, _it(f"{d['cost']:,}원", True,
                                          "" if d["on"] else "#c62828"))
            self.t_hour.setItem(i, 3, _it(f"{d['clk']:,}", True))
            self.t_hour.setItem(i, 4, _it(f"{d['orders']:,}", True))
            self.t_hour.setItem(i, 5, _it(f"{d['amount']:,}원", True,
                                          "#2e7d32"))
            self.t_hour.setItem(i, 6, _it(f"{r_:,}%", True, _roas_color(r_)))
        self.t_hour.setSortingEnabled(True)
        off = sum(d["cost"] for d in data if not d["on"])
        best = max(data, key=lambda d: d["amount"])
        self.lbl_hour.setText(
            f"노출시간 {ad_schedule.label(sc)}"
            f"   ·   설정 밖 지출 {off:,}원"
            f"   ·   매출이 가장 큰 시간 {best['h']:02d}시 "
            f"{best['amount']:,}원 (광고비 {best['cost']:,}원)")

    def _fill_item(self):
        kind = self.cmb_kind.currentData() or ""
        rows = ad_sales.items(self.s.isoformat(), self.e.isoformat(),
                              kind=kind, limit=500)
        self.t_item.setSortingEnabled(False)
        self.t_item.setRowCount(len(rows))
        for i, r in enumerate(rows):
            q = int(r["qty"] or 0)
            a = int(r["amount"] or 0)
            self.t_item.setItem(i, 0, _it(str(r["name"] or "")[:52]))
            self.t_item.setItem(i, 1, _it(r["kind"] or ""))
            self.t_item.setItem(i, 2, _it(r["lcp_code"] or "-"))
            self.t_item.setItem(i, 3, _it(f"{q:,}", True))
            self.t_item.setItem(i, 4, _it(f"{int(r['orders'] or 0):,}", True))
            self.t_item.setItem(i, 5, _it(f"{a:,}원", True, "#2e7d32", True))
            self.t_item.setItem(i, 6, _it(f"{a // q if q else 0:,}원", True))
            self.t_item.setItem(i, 7, _it(r["product_code"]))
        self.t_item.setSortingEnabled(True)
        self.lbl_item.setText(
            f"{len(rows):,}개 상품 · 수량 {sum(int(r['qty'] or 0) for r in rows):,} · "
            f"{sum(int(r['amount'] or 0) for r in rows):,}원")

    def _fill_lcp(self):
        rows = ad_stats.by_lcp(self.days, 500, self.cu)
        self.t_lcp.setSortingEnabled(False)
        self.t_lcp.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self.t_lcp.setItem(i, 0, _it(r["lcp_code"]))
            self.t_lcp.setItem(i, 1, _it(str(r.get("name") or "")[:46]))
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
            f"LCP {len(rows):,}종 · 광고비 {cost:,}원 · 실매출 {amt:,}원 · "
            f"ROAS {amt * 100 // cost if cost else 0:,}%"
            + NL + f"⚠ 매출 0원인데 광고비 쓴 LCP {len(dead):,}종 "
                   f"({sum(r['cost'] for r in dead):,}원 · "
                   f"{sum(r['cost'] for r in dead) * 100 // (cost or 1)}%)")
