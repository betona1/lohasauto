"""
광고 — 광고계정 · 캠페인 고르기와 지출 집계 (LCP별 / 날짜별).

계정이 여러 개고 캠페인 10개 중 실제로 쓰는 것은 하나뿐이라, **여기서
켜고 끈 것만** 지출 수집이 돈다(2026-09-11 사용자).

계정 목록을 주는 API 는 없다 — `/customer-links` 는 대행사 전용이고 직접
광고주 계정에서는 404 다. 계정 번호의 출처는 `.env` 의
`NAVER_AD_CUSTOMERS` 이고, [기본정보 수집] 이 그 번호로 접속해 캠페인과
광고그룹을 받아 온다.
"""
import datetime

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox,
                               QDateEdit,
                               QHBoxLayout, QHeaderView, QLabel, QMessageBox,
                               QPushButton, QSpinBox, QTableWidget,
                               QTableWidgetItem, QTabWidget, QVBoxLayout,
                               QWidget)

from ..lohas import ad_account, ad_sales, ad_spend, commerce, searchad

NL = chr(10)


def _item(text, right=False) -> QTableWidgetItem:
    it = QTableWidgetItem(str(text))
    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
    if right:
        it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    return it


class AdPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 10, 10, 10)

        head = QHBoxLayout()
        t = QLabel("광고 — 계정·캠페인 설정과 지출 집계")
        t.setStyleSheet("font-size:16px; font-weight:bold;")
        head.addWidget(t)
        head.addStretch(1)
        self.btn_base = QPushButton("① 기본정보 수집")
        self.btn_base.setToolTip(
            "계정·캠페인·광고그룹을 받아옵니다." + NL
            + "켜고 끈 선택은 그대로 둡니다.")
        self.btn_base.clicked.connect(self.on_collect_base)
        head.addWidget(self.btn_base)
        head.addWidget(QLabel("최근"))
        self.spin_days = QSpinBox()
        self.spin_days.setRange(1, 90)
        self.spin_days.setValue(14)
        self.spin_days.setSuffix("일")
        head.addWidget(self.spin_days)
        self.btn_report = QPushButton("📈 성과 보고서")
        self.btn_report.setStyleSheet("font-weight:bold; color:#ad1457;")
        self.btn_report.setToolTip(
            "기간별 그래프·매체별·TOP10·LCP별 매출을 한 창에서 봅니다."
            + NL + "엑셀로도 받을 수 있습니다.")
        self.btn_report.clicked.connect(self.on_report)
        head.addWidget(self.btn_report)
        self.btn_spend = QPushButton("② 지출 수집")
        self.btn_spend.setToolTip("켜 둔 계정·캠페인만 날짜별로 받아옵니다.")
        self.btn_spend.clicked.connect(self.on_collect_spend)
        head.addWidget(self.btn_spend)
        v.addLayout(head)

        self.lbl_today = QLabel("")
        self.lbl_today.setStyleSheet(
            "font-size:15px; font-weight:bold; color:#0d47a1;"
            " background:#e3f2fd; border-radius:6px; padding:10px;")
        v.addWidget(self.lbl_today)

        self.lbl_note = QLabel("")
        self.lbl_note.setWordWrap(True)
        self.lbl_note.setStyleSheet("color:#666; padding:2px 4px;")
        v.addWidget(self.lbl_note)

        tabs = QTabWidget()
        tabs.addTab(self._tab_setup(), "설정 (계정·캠페인)")
        tabs.addTab(self._tab_chart(), "📊 그래프")
        tabs.addTab(self._tab_day(), "날짜별 지출")
        tabs.addTab(self._tab_lcp(), "LCP별 지출")
        tabs.addTab(self._tab_detail(), "상세 (검색어·소재·기기)")
        tabs.addTab(self._tab_sales(), "💰 실매출·ROAS")
        v.addWidget(tabs, 1)
        self.tabs = tabs
        self.refresh()

    # ---------------------------------------------------------- 설정 탭
    def _tab_setup(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        arow = QHBoxLayout()
        arow.addWidget(QLabel(
            "광고계정 — 쓸 계정만 켜고, 대시보드에 띄울 것을 [메인] 으로"))
        arow.addStretch(1)
        btn_add = QPushButton("＋ 계정 추가")
        btn_add.setToolTip(
            "계정 목록을 주는 API 가 없습니다 (대행사 계정 전용)." + NL
            + "광고주센터 우측 위에 나오는 Customer ID 를 넣어 주십시오.")
        btn_add.clicked.connect(self.on_add_account)
        arow.addWidget(btn_add)
        lay.addLayout(arow)

        self.tbl_acc = QTableWidget(0, 7)
        self.tbl_acc.setHorizontalHeaderLabels(
            ["사용", "메인", "계정번호", "이름", "비즈머니", "캠페인",
             "광고그룹"])
        self.tbl_acc.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_acc.verticalHeader().setVisible(False)
        self.tbl_acc.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.Stretch)
        self.tbl_acc.setMaximumHeight(170)
        lay.addWidget(self.tbl_acc)

        # 광고 시작일 — 이 날 이전은 통계에서 뺀다
        srow = QHBoxLayout()
        srow.addWidget(QLabel("광고 시작일"))
        self.dt_start = QDateEdit()
        self.dt_start.setCalendarPopup(True)
        self.dt_start.setDisplayFormat("yyyy-MM-dd")
        srow.addWidget(self.dt_start)
        btn_s = QPushButton("저장")
        btn_s.clicked.connect(self._save_start)
        srow.addWidget(btn_s)
        btn_d = QPushButton("자동 찾기")
        btn_d.setToolTip("지출이 처음 잡힌 날을 찾아 넣습니다.")
        btn_d.clicked.connect(self._detect_start)
        srow.addWidget(btn_d)
        self.lbl_start = QLabel("")
        self.lbl_start.setWordWrap(True)
        self.lbl_start.setStyleSheet(
            "color:#555; background:#fff8e1; border-radius:6px; padding:6px;")
        srow.addWidget(self.lbl_start, 1)
        lay.addLayout(srow)

        row = QHBoxLayout()
        row.addWidget(QLabel("캠페인 보기"))
        self.cmb_acc = QComboBox()
        self.cmb_acc.setMinimumWidth(200)
        self.cmb_acc.currentIndexChanged.connect(self._fill_campaigns)
        row.addWidget(self.cmb_acc)
        row.addStretch(1)
        lay.addLayout(row)

        lay.addWidget(QLabel(
            "캠페인 — **실제로 쓰는 것만** 켜십시오. 켠 것만 지출을 받아옵니다."))
        self.tbl_camp = QTableWidget(0, 6)
        self.tbl_camp.setHorizontalHeaderLabels(
            ["사용", "캠페인", "유형", "상태", "광고그룹", "캠페인ID"])
        self.tbl_camp.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_camp.verticalHeader().setVisible(False)
        self.tbl_camp.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch)
        lay.addWidget(self.tbl_camp, 1)
        return w

    def _tab_chart(self) -> QWidget:
        """
        날짜별 광고비 막대 + 클릭 선.

        **오늘치도 같이 보여준다.** 상세 보고서는 어제까지만 나오지만
        지출(`/stats`)은 당일도 실시간이라, 그래프는 지출 쪽을 쓴다
        (2026-09-12 사용자: 어제·그저께 것을 그래프로).
        """
        from PySide6.QtCharts import (QBarCategoryAxis, QBarSeries, QBarSet,
                                      QChart, QChartView, QLineSeries,
                                      QValueAxis)
        from PySide6.QtGui import QPainter

        self._Q = (QBarCategoryAxis, QBarSeries, QBarSet, QLineSeries,
                   QValueAxis)
        w = QWidget()
        lay = QVBoxLayout(w)
        row = QHBoxLayout()
        row.addWidget(QLabel("표시 범위"))
        self.cmb_chart_days = QComboBox()
        self.cmb_chart_days.addItems(["7", "14", "30", "60", "90"])
        self.cmb_chart_days.setCurrentText("14")
        self.cmb_chart_days.currentTextChanged.connect(self._fill_chart)
        row.addWidget(self.cmb_chart_days)
        row.addWidget(QLabel("일"))
        self.chk_zero = QCheckBox("지출 0원인 날도")
        self.chk_zero.stateChanged.connect(self._fill_chart)
        row.addWidget(self.chk_zero)
        row.addStretch(1)
        self.lbl_chart = QLabel("")
        self.lbl_chart.setStyleSheet("font-weight:bold; color:#ad1457;")
        row.addWidget(self.lbl_chart)
        lay.addLayout(row)

        self.chart_cost = QChart()
        self.chart_cost.setTitle("날짜별 광고비")
        self.chart_cost.legend().setAlignment(Qt.AlignBottom)
        view = QChartView(self.chart_cost)
        view.setRenderHint(QPainter.Antialiasing)
        view.setMinimumHeight(300)
        lay.addWidget(view, 1)
        return w

    def _fill_chart(self):
        if not hasattr(self, "chart_cost"):
            return
        (QBarCategoryAxis, QBarSeries, QBarSet, QLineSeries,
         QValueAxis) = self._Q
        days = int(self.cmb_chart_days.currentText() or 14)
        rows = list(reversed(ad_spend.by_day(days)))
        if not self.chk_zero.isChecked():
            rows = [r for r in rows if int(r["cost"] or 0)]
        self.chart_cost.removeAllSeries()
        for ax in list(self.chart_cost.axes()):
            self.chart_cost.removeAxis(ax)
        if not rows:
            self.chart_cost.setTitle("날짜별 광고비 — 아직 기록이 없습니다")
            self.lbl_chart.setText("")
            return
        cost = QBarSet("광고비(원)")
        clk = QLineSeries()
        clk.setName("클릭")
        cats = []
        for i, r in enumerate(rows):
            cost.append(int(r["cost"] or 0))
            clk.append(i, int(r["clk"] or 0))
            cats.append(r["day"][5:])          # MM-DD
        bar = QBarSeries()
        bar.append(cost)
        self.chart_cost.addSeries(bar)
        self.chart_cost.addSeries(clk)

        ax_x = QBarCategoryAxis()
        ax_x.append(cats)
        self.chart_cost.addAxis(ax_x, Qt.AlignBottom)
        bar.attachAxis(ax_x)

        ax_y = QValueAxis()
        ax_y.setLabelFormat("%d")
        ax_y.setTitleText("광고비(원)")
        ax_y.setRange(0, max(1, max(int(r["cost"] or 0)
                                    for r in rows)) * 1.25)
        self.chart_cost.addAxis(ax_y, Qt.AlignLeft)
        bar.attachAxis(ax_y)

        ax_c = QValueAxis()
        ax_c.setLabelFormat("%d")
        ax_c.setTitleText("클릭")
        ax_c.setRange(0, max(1, max(int(r["clk"] or 0) for r in rows)) * 1.4)
        self.chart_cost.addAxis(ax_c, Qt.AlignRight)
        clk.attachAxis(ax_c)
        # 선은 막대 가운데에 오도록 x 축을 따로 준다
        ax_lx = QValueAxis()
        ax_lx.setRange(-0.5, len(rows) - 0.5)
        ax_lx.setVisible(False)
        self.chart_cost.addAxis(ax_lx, Qt.AlignBottom)
        clk.attachAxis(ax_lx)

        tot = sum(int(r["cost"] or 0) for r in rows)
        self.chart_cost.setTitle(
            f"날짜별 광고비 — {len(rows)}일 합계 {tot:,}원")
        self.lbl_chart.setText(
            f"합계 {tot:,}원 · 하루 평균 {tot // max(len(rows), 1):,}원")

    def _tab_day(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.tbl_day = QTableWidget(0, 7)
        self.tbl_day.setHorizontalHeaderLabels(
            ["날짜", "광고비", "클릭", "노출", "전환", "전환매출", "ROAS"])
        self.tbl_day.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_day.verticalHeader().setVisible(False)
        self.tbl_day.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch)
        lay.addWidget(self.tbl_day)
        return w

    def _tab_lcp(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.tbl_lcp = QTableWidget(0, 7)
        self.tbl_lcp.setHorizontalHeaderLabels(
            ["LCP 코드", "광고비", "클릭", "노출", "전환매출", "ROAS",
             "집행일수"])
        self.tbl_lcp.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_lcp.verticalHeader().setVisible(False)
        self.tbl_lcp.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch)
        lay.addWidget(self.tbl_lcp)
        return w

    def _tab_sales(self) -> QWidget:
        """
        **주문 DB 의 실매출**과 광고비를 붙여 진짜 ROAS 를 본다.

        네이버 전환매출은 장바구니 담기가 섞여 있어 3배쯤 부풀려진다
        (2026-09-11: 네이버 66,200원 / 구매완료 8,770원).
        """
        w = QWidget()
        lay = QVBoxLayout(w)
        row = QHBoxLayout()
        self.lbl_sales = QLabel("")
        self.lbl_sales.setWordWrap(True)
        self.lbl_sales.setStyleSheet(
            "font-weight:bold; color:#1b5e20; background:#e8f5e9;"
            " border-radius:6px; padding:8px;")
        row.addWidget(self.lbl_sales, 1)
        self.btn_sales = QPushButton("④ 실매출 받기")
        self.btn_sales.setToolTip(
            "네이버 커머스 API(스토어 bitmind)에서 실결제 주문을 받아" + NL
            + "광고 상품에 붙입니다. 취소·반품·품절은 뺍니다.")
        self.btn_sales.clicked.connect(self.on_collect_sales)
        row.addWidget(self.btn_sales)
        lay.addLayout(row)

        self.tbl_sales = QTableWidget(0, 6)
        self.tbl_sales.setHorizontalHeaderLabels(
            ["LCP 코드", "실매출", "주문", "수량", "광고비", "ROAS"])
        self.tbl_sales.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_sales.verticalHeader().setVisible(False)
        self.tbl_sales.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch)
        lay.addWidget(self.tbl_sales, 1)
        return w

    def _fill_sales(self):
        from PySide6.QtGui import QColor
        days = self.spin_days.value()
        rows = ad_sales.by_lcp(days, 300)
        self.tbl_sales.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self.tbl_sales.setItem(i, 0, _item(r["lcp_code"]))
            self.tbl_sales.setItem(i, 1, _item(f"{r['amount']:,}원", True))
            self.tbl_sales.setItem(i, 2, _item(f"{r['orders']:,}", True))
            self.tbl_sales.setItem(i, 3, _item(f"{r['qty']:,}", True))
            self.tbl_sales.setItem(i, 4, _item(f"{r['cost']:,}원", True))
            cell = _item(f"{r['roas']:,}%" if r["cost"] else "-", True)
            if r["cost"]:
                cell.setForeground(QColor(
                    "#2e7d32" if r["roas"] >= 300 else
                    "#c62828" if r["roas"] == 0 else "#ef6c00"))
            self.tbl_sales.setItem(i, 5, cell)
        amt = sum(r["amount"] for r in rows)
        cost = sum(r["cost"] for r in rows)
        dead = [r for r in rows if r["cost"] and not r["amount"]]
        s = ad_sales.today()
        self.lbl_sales.setText(
            f"최근 {days}일 — 실매출 {amt:,}원 · 광고비 {cost:,}원 · "
            f"ROAS {amt * 100 // cost if cost else 0:,}%"
            + NL + f"오늘 실매출 {int(s['amount'] or 0):,}원 "
                   f"({int(s['orders'] or 0):,}건)"
            + f"   ·   매출 0원인데 광고비 쓴 LCP {len(dead):,}종 "
              f"({sum(r['cost'] for r in dead):,}원)")

    def on_collect_sales(self):
        self.btn_sales.setEnabled(False)
        self.btn_sales.setText("받는 중…")
        try:
            r = ad_sales.collect(days=max(self.spin_days.value(), 14),
                                 log=lambda *_: None)
            # 오늘·어제는 커머스 API 가 더 빠르다 (주문 DB 는 수집 주기가 있다)
            if commerce.available():
                import datetime as _dt
                for back in (1, 0):
                    cr = commerce.sales(_dt.date.today()
                                        - _dt.timedelta(days=back),
                                        log=lambda *_: None)
                    if cr["orders"]:
                        commerce.save(cr, log=lambda *_: None)
        except Exception as e:
            QMessageBox.critical(self, "실매출", str(e)[:300])
            return
        finally:
            self.btn_sales.setEnabled(True)
            self.btn_sales.setText("④ 실매출 받기")
        self._fill_sales()
        QMessageBox.information(
            self, "실매출",
            f"광고 상품 주문 {r['matched']:,}건 · {r['amount']:,}원" + NL
            + f"(가게 전체 {r['orders']:,}건 · {r['amount_all']:,}원)")

    def _tab_detail(self) -> QWidget:
        """
        대용량 보고서로 쌓은 차원별 지출.

        `/stats` 는 `breakdown` 을 줘도 무시한다 — day·pcMobile·hourly·
        region 을 다 넣어봤지만 같은 한 줄만 왔다(2026-09-12 실측).
        그래서 여기 값은 보고서를 받아 직접 집계한 것이다.
        **당일치는 보고서가 안 나온다.** 어제부터 쌓인다.
        """
        w = QWidget()
        lay = QVBoxLayout(w)
        row = QHBoxLayout()
        row.addWidget(QLabel("보는 기준"))
        self.cmb_dim = QComboBox()
        for key, title in (("query", "검색어"), ("ad_id", "소재(상품)"),
                           ("lcp_code", "LCP"), ("device", "PC / 모바일"),
                           ("media", "매체"), ("hour", "시간대"),
                           ("region", "지역"), ("channel_id", "비즈채널")):
            self.cmb_dim.addItem(title, key)
        self.cmb_dim.currentIndexChanged.connect(self._fill_detail)
        row.addWidget(self.cmb_dim)
        self.chk_daily = QCheckBox("날짜별로 나누기")
        self.chk_daily.setToolTip(
            "켜면 '채널 × 날짜' 처럼 날짜마다 한 줄씩 보여줍니다.")
        self.chk_daily.stateChanged.connect(self._fill_detail)
        row.addWidget(self.chk_daily)
        row.addStretch(1)
        self.lbl_detail = QLabel("")
        self.lbl_detail.setStyleSheet("color:#57606a;")
        row.addWidget(self.lbl_detail)
        self.btn_detail = QPushButton("③ 상세 보고서 받기")
        self.btn_detail.setToolTip(
            "대용량 보고서를 만들어 받아 검색어·소재별로 쌓습니다." + NL
            + "당일치는 네이버가 안 만들어 줍니다 — 어제부터 됩니다.")
        self.btn_detail.clicked.connect(self.on_collect_detail)
        row.addWidget(self.btn_detail)
        lay.addLayout(row)

        self.tbl_detail = QTableWidget(0, 5)
        self.tbl_detail.setHorizontalHeaderLabels(
            ["항목", "광고비", "클릭", "노출", "전환"])
        self.tbl_detail.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_detail.verticalHeader().setVisible(False)
        self.tbl_detail.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch)
        lay.addWidget(self.tbl_detail, 1)
        return w

    def _fill_detail(self):
        from ..lohas import ad_account as aa, ad_detail as ad
        cu = (aa.main_account() or {}).get("customer_id") or ""
        dim = self.cmb_dim.currentData() or "query"
        days = max(self.spin_days.value(), 7)
        daily = self.chk_daily.isChecked()
        rows = (ad.by_day(dim, cu, days=days, limit=500) if daily
                else ad.by(dim, cu, days=days, limit=200))
        # 비즈채널은 ID 대신 이름으로 보여준다 (bsn-a001-... 은 못 읽는다)
        names = ad.channel_names(cu) if dim == "channel_id" else {}
        self.tbl_detail.setColumnCount(6 if daily else 5)
        self.tbl_detail.setHorizontalHeaderLabels(
            (["날짜"] if daily else [])
            + ["항목", "광고비", "클릭", "노출", "전환"])
        self.tbl_detail.setRowCount(len(rows))
        for i, r in enumerate(rows):
            k = r["k"]
            if dim == "device":
                k = ad.DEVICE.get(k, k)
            elif dim == "hour":
                k = f"{k}시"
            elif dim == "channel_id":
                k = names.get(k, k)
            elif dim == "media":
                k = ad.media_name(k)
            c = 0
            if daily:
                self.tbl_detail.setItem(i, 0, _item(r["day"]))
                c = 1
            self.tbl_detail.setItem(i, c, _item(k or "(없음)"))
            self.tbl_detail.setItem(i, c + 1, _item(
                f"{int(r['cost'] or 0):,}원", True))
            self.tbl_detail.setItem(i, c + 2, _item(
                f"{int(r['clk'] or 0):,}", True))
            self.tbl_detail.setItem(i, c + 3, _item(
                f"{int(r['imp'] or 0):,}", True))
            self.tbl_detail.setItem(i, c + 4, _item(
                f"{int(r['conv'] or 0):,}", True))
        got = ad.days_in_db(cu)
        self.lbl_detail.setText(
            f"쌓인 날짜 {len(got)}일" + (f" ({got[-1]['day']} ~ {got[0]['day']})"
                                    if got else " — 아직 없습니다"))

    def on_collect_detail(self):
        from ..lohas import ad_account as aa, ad_detail as ad
        cu = (aa.main_account() or {}).get("customer_id") or ""
        if not cu:
            QMessageBox.warning(self, "광고", "메인 계정을 정하십시오.")
            return
        days = max(self.spin_days.value(), 2)
        if QMessageBox.question(
                self, "상세 보고서",
                f"어제부터 {days}일치 보고서를 만들어 받아옵니다." + NL
                + "하루에 수만 행이라 몇 분 걸립니다." + NL * 2
                + "당일치는 네이버가 만들어 주지 않습니다." + NL * 2
                + "시작할까요?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes) != QMessageBox.Yes:
            return
        self.btn_detail.setEnabled(False)
        self.btn_detail.setText("받는 중…")
        try:
            n = ad.collect_days(cu, days=days, log=lambda *_: None)
        except Exception as e:
            QMessageBox.critical(self, "광고", str(e)[:300])
            return
        finally:
            self.btn_detail.setEnabled(True)
            self.btn_detail.setText("③ 상세 보고서 받기")
        self._fill_detail()
        QMessageBox.information(self, "상세 보고서", f"{n:,}행 저장했습니다.")

    # ---------------------------------------------------------- 채우기
    def refresh(self):
        self._fill_accounts()
        try:
            self._fill_start()
        except Exception:
            pass
        self._fill_campaigns()
        self._fill_spend()
        for fn in (self._fill_detail, self._fill_sales):
            try:
                fn()
            except Exception:
                pass

    def _fill_accounts(self):
        rows = ad_account.accounts()
        self.tbl_acc.setRowCount(len(rows))
        cur = self.cmb_acc.currentData() if self.cmb_acc.count() else None
        self.cmb_acc.blockSignals(True)
        self.cmb_acc.clear()
        for i, a in enumerate(rows):
            cb = QCheckBox()
            cb.setChecked(bool(a["enabled"]))
            cb.stateChanged.connect(
                lambda st, c=a["customer_id"]:
                (ad_account.set_account(c, enabled=bool(st)), self.refresh()))
            box = QWidget()
            bl = QHBoxLayout(box)
            bl.setContentsMargins(0, 0, 0, 0)
            bl.setAlignment(Qt.AlignCenter)
            bl.addWidget(cb)
            self.tbl_acc.setCellWidget(i, 0, box)
            btn = QPushButton("★ 메인" if a.get("is_main") else "메인으로")
            btn.setFlat(not a.get("is_main"))
            if a.get("is_main"):
                btn.setStyleSheet("font-weight:bold; color:#ad1457;")
            btn.clicked.connect(
                lambda _=False, c=a["customer_id"]:
                (ad_account.set_main(c), self.refresh()))
            self.tbl_acc.setCellWidget(i, 1, btn)
            self.tbl_acc.setItem(i, 2, _item(a["customer_id"]))
            self.tbl_acc.setItem(i, 3, _item(
                (a["label"] or "") + ("" if a["ok"] else "   (접속 실패)")))
            self.tbl_acc.setItem(i, 4, _item(
                f"{int(a['bizmoney'] or 0):,}원", True))
            self.tbl_acc.setItem(i, 5, _item(a["campaigns"] or 0, True))
            self.tbl_acc.setItem(i, 6, _item(a["adgroups"] or 0, True))
            self.cmb_acc.addItem(
                f"{a['customer_id']}  ({a['campaigns'] or 0}캠페인)",
                a["customer_id"])
        if cur:
            j = self.cmb_acc.findData(cur)
            if j >= 0:
                self.cmb_acc.setCurrentIndex(j)
        self.cmb_acc.blockSignals(False)
        if not rows:
            self.lbl_note.setText(
                "아직 받아온 것이 없습니다 — [① 기본정보 수집] 을 누르십시오."
                + (NL + "※ .env 에 네이버 검색광고 API 키가 없습니다."
                   if not searchad.available() else ""))

    def _fill_campaigns(self):
        cu = self.cmb_acc.currentData() or ""
        rows = ad_account.campaigns(cu) if cu else []
        self.tbl_camp.setRowCount(len(rows))
        for i, x in enumerate(rows):
            cb = QCheckBox()
            cb.setChecked(bool(x["enabled"]))
            cb.stateChanged.connect(
                lambda st, c=x["customer_id"], k=x["campaign_id"]:
                (ad_account.set_campaign(c, k, bool(st)), self._note()))
            box = QWidget()
            bl = QHBoxLayout(box)
            bl.setContentsMargins(0, 0, 0, 0)
            bl.setAlignment(Qt.AlignCenter)
            bl.addWidget(cb)
            self.tbl_camp.setCellWidget(i, 0, box)
            self.tbl_camp.setItem(i, 1, _item(x["name"] or ""))
            self.tbl_camp.setItem(i, 2, _item(x["tp"] or ""))
            self.tbl_camp.setItem(i, 3, _item(x["status"] or ""))
            self.tbl_camp.setItem(i, 4, _item(x["adgroups"] or 0, True))
            self.tbl_camp.setItem(i, 5, _item(x["campaign_id"]))
        self._note()

    def _note(self):
        picks = ad_account.selection()
        n_c = sum(len(p["campaigns"]) for p in picks)
        if not picks:
            self.lbl_note.setText("쓸 계정을 하나도 켜지 않았습니다.")
        elif not n_c:
            self.lbl_note.setText(
                "캠페인을 고르지 않았습니다 — 켠 계정의 **캠페인 전부**를"
                " 받아옵니다. 하나만 쓰신다면 그 캠페인만 켜십시오.")
        else:
            self.lbl_note.setText(
                f"수집 대상 : 계정 {len(picks)}개 · 캠페인 {n_c}개")

    def _fill_spend(self):
        days = self.spin_days.value()
        t = ad_spend.today_cost()
        self.lbl_today.setText(
            f"오늘({t['day']}) 광고비  {int(t['cost'] or 0):,}원"
            f"      클릭 {int(t['clk'] or 0):,} · 노출 {int(t['imp'] or 0):,}"
            + f"      전환 {int(t.get('conv') or 0):,}건 · 전환매출 "
              f"{int(t.get('conv_amt') or 0):,}원"
            + (f"      마지막 수집 {t['upd']}" if t.get("upd") else
               "      (아직 수집 전)"))

        rows = ad_spend.by_day(days)
        try:
            self._fill_chart()
        except Exception:
            pass
        self.tbl_day.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self.tbl_day.setItem(i, 0, _item(r["day"]))
            self.tbl_day.setItem(i, 1, _item(f"{int(r['cost'] or 0):,}원", True))
            self.tbl_day.setItem(i, 2, _item(f"{int(r['clk'] or 0):,}", True))
            self.tbl_day.setItem(i, 3, _item(f"{int(r['imp'] or 0):,}", True))
            cv = int(r.get("conv") or 0)
            amt = int(r.get("conv_amt") or 0)
            cost = int(r.get("cost") or 0)
            self.tbl_day.setItem(i, 4, _item(f"{cv:,}", True))
            self.tbl_day.setItem(i, 5, _item(f"{amt:,}원", True))
            roas = _item(f"{amt * 100 // cost:,}%" if cost else "-", True)
            if cost and amt * 100 // cost >= 300:
                from PySide6.QtGui import QColor
                roas.setForeground(QColor("#2e7d32"))
            self.tbl_day.setItem(i, 6, roas)

        rows = ad_spend.by_lcp(days)
        self.tbl_lcp.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self.tbl_lcp.setItem(i, 0, _item(r["lcp_code"]))
            self.tbl_lcp.setItem(i, 1, _item(f"{int(r['cost'] or 0):,}원", True))
            self.tbl_lcp.setItem(i, 2, _item(f"{int(r['clk'] or 0):,}", True))
            self.tbl_lcp.setItem(i, 3, _item(f"{int(r['imp'] or 0):,}", True))
            amt = int(r.get("conv_amt") or 0)
            cost = int(r.get("cost") or 0)
            self.tbl_lcp.setItem(i, 4, _item(f"{amt:,}원", True))
            # **ROAS 가 낮으면 돈만 먹는 상품이다.** 색으로 갈라 둔다.
            cell = _item(f"{amt * 100 // cost:,}%" if cost else "-", True)
            if cost:
                from PySide6.QtGui import QColor
                r_ = amt * 100 // cost
                cell.setForeground(QColor("#2e7d32" if r_ >= 300
                                          else "#c62828" if r_ == 0
                                          else "#ef6c00"))
            self.tbl_lcp.setItem(i, 5, cell)
            self.tbl_lcp.setItem(i, 6, _item(r["days"], True))

    # ------------------------------------------------------ 광고 시작일
    def _fill_start(self):
        cu = (ad_account.main_account() or {}).get("customer_id") or ""
        st = ad_account.start_of(cu)
        auto = ad_account.detect_start(cu)
        self.dt_start.setDate(QDate.fromString(st or auto
                                               or datetime.date.today()
                                               .isoformat(), "yyyy-MM-dd"))
        if st:
            self.lbl_start.setText(
                f"{st} 이후만 통계에 넣습니다. 그 전 매출은 광고 성과가"
                " 아닙니다.")
        else:
            self.lbl_start.setText(
                "미지정 — 지금은 기간 전체를 봅니다."
                + (f"  지출이 처음 잡힌 날은 {auto} 입니다."
                   if auto else ""))

    def _save_start(self):
        cu = (ad_account.main_account() or {}).get("customer_id") or ""
        if not cu:
            QMessageBox.warning(self, "광고 시작일", "메인 계정을 정하십시오.")
            return
        ad_account.set_start(cu, self.dt_start.date().toString("yyyy-MM-dd"))
        self._fill_start()
        self._fill_spend()
        if getattr(self, "_report", None) is not None:
            self._report.refresh()
        QMessageBox.information(
            self, "광고 시작일",
            f"{self.dt_start.date().toString('yyyy-MM-dd')} 로 저장했습니다."
            + NL + "이 날 이전은 통계에서 빠집니다.")

    def _detect_start(self):
        cu = (ad_account.main_account() or {}).get("customer_id") or ""
        d = ad_account.detect_start(cu)
        if not d:
            QMessageBox.information(self, "광고 시작일",
                                    "아직 지출 기록이 없습니다.")
            return
        self.dt_start.setDate(QDate.fromString(d, "yyyy-MM-dd"))
        self.lbl_start.setText(f"지출이 처음 잡힌 날 {d} — [저장] 을 누르면"
                               " 적용됩니다.")

    # ---------------------------------------------------------- 동작
    def on_report(self):
        from .ad_report_dialog import AdReportDialog
        if getattr(self, "_report", None) is None:
            self._report = AdReportDialog(self)
        else:
            self._report.refresh()
        self._report.show()
        self._report.raise_()

    def on_add_account(self):
        """
        계정 번호를 손으로 더한다.

        **계정 목록을 주는 API 가 없다.** `/customer-links` 는 대행사
        계정에서만 동작하고 직접 광고주 계정에서는 404 다(2026-09-12 실측).
        번호는 광고주센터 화면에서 읽어와야 한다.
        """
        from PySide6.QtWidgets import QInputDialog

        num, ok = QInputDialog.getText(
            self, "계정 추가",
            "광고주센터의 Customer ID (숫자)" + NL
            + "광고주센터 오른쪽 위 계정 이름 옆에 나옵니다.")
        num = (num or "").strip()
        if not ok or not num.isdigit():
            return
        name, ok = QInputDialog.getText(
            self, "계정 추가", "이 계정을 뭐라고 부를까요? (예: 비트테크노-1)")
        if not ok:
            return
        ad_account.add(num, (name or "").strip())
        # 바로 접속해 보고 캠페인까지 받아온다
        try:
            ad_account.collect(log=lambda *_: None)
        except Exception as e:
            QMessageBox.warning(self, "계정 추가", str(e)[:200])
        self.refresh()
        got = [a for a in ad_account.accounts() if a["customer_id"] == num]
        if got and got[0]["ok"]:
            QMessageBox.information(
                self, "계정 추가",
                f"{num} 접속 확인" + NL
                + f"비즈머니 {int(got[0]['bizmoney'] or 0):,}원 · "
                  f"캠페인 {got[0]['campaigns']}개")
        else:
            QMessageBox.warning(
                self, "계정 추가",
                f"{num} 에 접속하지 못했습니다." + NL
                + "이 API 키가 그 계정을 못 보는 것일 수 있습니다 —" + NL
                + "그 계정으로 로그인해 API 키를 따로 발급받아야 합니다.")

    def on_collect_base(self):
        if not searchad.available():
            QMessageBox.warning(self, "광고",
                                ".env 에 네이버 검색광고 API 키가 없습니다.")
            return
        self.btn_base.setEnabled(False)
        self.btn_base.setText("받는 중…")
        try:
            r = ad_account.collect(log=lambda *_: None)
        except Exception as e:
            QMessageBox.critical(self, "광고", str(e)[:300])
            return
        finally:
            self.btn_base.setEnabled(True)
            self.btn_base.setText("① 기본정보 수집")
        self.refresh()
        msg = (f"계정 {r['accounts']}개 · 캠페인 {r['campaigns']}개 · "
               f"광고그룹 {r['groups']}개")
        if r["fail"]:
            msg += NL + "접속 실패 : " + ", ".join(r["fail"])
        QMessageBox.information(self, "기본정보 수집", msg)

    def on_collect_spend(self):
        picks = ad_account.selection()
        if not picks:
            QMessageBox.warning(self, "광고", "쓸 계정을 켜십시오.")
            return
        days = self.spin_days.value()
        n_c = sum(len(p["campaigns"]) for p in picks)
        msg = [f"최근 {days}일 지출을 받아옵니다.", ""]
        for p in picks:
            msg.append(f"  {p['customer']}  캠페인 "
                       + (f"{len(p['campaigns'])}개" if p["campaigns"]
                          else "전부"))
        if not n_c:
            msg += ["", "캠페인을 고르지 않아 전부 받아옵니다. 오래 걸립니다."]
        msg += ["", "읽기 전용입니다 — 광고를 바꾸지 않습니다.", "", "시작할까요?"]
        if QMessageBox.question(
                self, "지출 수집", NL.join(msg),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes) != QMessageBox.Yes:
            return
        self.btn_spend.setEnabled(False)
        self.btn_spend.setText("받는 중…")
        try:
            r = ad_spend.collect_all(days=days, log=lambda *_: None)
        except Exception as e:
            QMessageBox.critical(self, "광고", str(e)[:300])
            return
        finally:
            self.btn_spend.setEnabled(True)
            self.btn_spend.setText("② 지출 수집")
        self._fill_spend()
        QMessageBox.information(
            self, "지출 수집",
            f"{r['rows']:,}행 저장 · 기간 지출 {r['cost']:,}원")
