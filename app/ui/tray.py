"""
트레이 모드.

창을 띄워두지 않고 **트레이 아이콘 숫자만 보는** 방식이다. 오늘 작업량이
늘면 풍선 알림으로 알려준다 — 화면을 차지하지 않고 숫자만 확인하면 된다
(2026-09-06 사용자 요청).

숫자는 로컬 DB 만 본다. 사이트를 부르지 않으므로 부담이 없다.
"""
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from .. import db

POLL_SEC = 30


def _num_icon(n, color: str = "#c62828") -> QIcon:
    """숫자(또는 '?')를 그려 넣은 아이콘. 트레이에서 값이 바로 보이게."""
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(color))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(0, 0, 64, 64, 14, 14)
    p.setPen(QColor("#ffffff"))
    txt = (str(n) if not isinstance(n, int)
           else (str(n) if n < 1000 else f"{n // 1000}k"))
    f = QFont()
    f.setBold(True)
    f.setPixelSize(40 if len(txt) <= 2 else (32 if len(txt) == 3 else 26))
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignCenter, txt)
    p.end()
    return QIcon(pm)


class Tray(QSystemTrayIcon):
    """
    트레이 아이콘. 오늘 작업량을 아이콘 숫자로 보여주고, 늘면 알림을 띄운다.

    `main` 은 메인 창이다 — 메뉴에서 열고 닫는다.
    """

    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self._last = None          # 마지막으로 본 오늘 작업량
        self._done = None
        self._day = None           # 그 숫자가 **어느 날짜의 것인지**
        self._stale = False        # 마지막 읽기가 실패했나

        menu = QMenu()
        self.act_open = QAction("창 열기", menu)
        self.act_open.triggered.connect(self.show_main)
        menu.addAction(self.act_open)
        self.act_mini = QAction("미니 화면", menu)
        self.act_mini.triggered.connect(self._to_mini)
        menu.addAction(self.act_mini)
        menu.addSeparator()
        self.act_now = QAction("지금 수치 보기", menu)
        self.act_now.triggered.connect(lambda: self._notify(force=True))
        menu.addAction(self.act_now)
        menu.addSeparator()
        act_quit = QAction("종료", menu)
        act_quit.triggered.connect(self._quit)
        menu.addAction(act_quit)
        self.setContextMenu(menu)
        self._menu = menu

        self.activated.connect(self._clicked)
        self.setIcon(_num_icon(0, "#9e9e9e"))
        self.setToolTip("로하스 — 오늘 작업량")

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(POLL_SEC * 1000)
        self.refresh(first=True)

    # ------------------------------------------------------------ 동작
    def _clicked(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_main()

    def show_main(self):
        self.main.showNormal()
        self.main.raise_()
        self.main.activateWindow()

    def _to_mini(self):
        self.show_main()
        if hasattr(self.main, "to_mini"):
            self.main.to_mini()

    def _quit(self):
        from PySide6.QtWidgets import QApplication
        self.hide()
        QApplication.quit()

    # ------------------------------------------------------------ 숫자
    def _read(self) -> tuple:
        """(오늘 작업량, 저장완료 누적, 폴더, 날짜). 못 읽으면 오늘=None."""
        from datetime import datetime

        day = datetime.now().strftime("%Y-%m-%d")
        try:
            folder = db.get_job_folder()
        except Exception:
            return None, None, "", day      # DB 잠김 등 - 폴더도 못 읽는다
        if not folder:
            return None, None, "", day
        try:
            t = db.today_totals(folder)
            today = int(t.get("info") or 0)
            day = t.get("date") or day      # 셈에 쓴 날짜를 그대로 쓴다
        except Exception:
            today = None
        try:
            done = int((db.latest_scan(folder) or {}).get("info_save_rows") or 0)
        except Exception:
            done = None
        return today, done, folder, day

    def refresh(self, first: bool = False):
        """
        30초마다 다시 읽는다.

        **날짜가 바뀌면 반드시 0 부터 다시 센다.** 자정을 넘겨도 어제 숫자를
        그대로 들고 있어서, 아이콘에 어제 값이 남고 오늘 몇 건을 해도
        `오늘 > 어제총합` 이 아니면 알림이 아예 안 떴다(2026-09-07 사용자 지적).

        읽기에 실패하면 **옛 숫자를 그대로 두지 않는다.** 회색 물음표로
        바꿔 '지금 값이 아님' 을 눈에 보이게 한다 - DB 가 잠기면 조용히
        멈춰서 어제 값이 하루 종일 남아 있었다.
        """
        today, done, folder, day = self._read()

        if day != self._day:              # 날짜가 바뀌었다 - 어제 값 버린다
            self._day = day
            self._last = None
            first = True                  # 새 날의 첫 읽기는 알리지 않는다

        if today is None:
            self._stale = True
            self.setIcon(_num_icon("?", "#9e9e9e"))
            self.setToolTip("수치를 읽지 못했습니다 (DB 사용 중)"
                            if folder else "작업폴더가 지정되지 않았습니다")
            return

        self._stale = False
        self.setIcon(_num_icon(today, "#9e9e9e" if today == 0 else "#c62828"))
        head = f"{folder}" + chr(10) + f"오늘({day[5:]}) 작업량 {today:,}건"
        self.setToolTip(head + (f" / 저장완료 {done:,}"
                                if done is not None else ""))
        if not first and self._last is not None and today > self._last:
            d = today - self._last
            self.showMessage(
                f"오늘({day[5:]}) 작업량 {today:,}건",
                f"+{d}건 늘었습니다 · 저장완료 {done:,}건"
                if done is not None else f"+{d}건",
                QSystemTrayIcon.Information, 4000)
        self._last, self._done = today, done

    def _notify(self, force: bool = False):
        today, done, folder, day = self._read()
        if today is None:
            self.showMessage(
                "로하스",
                "수치를 읽지 못했습니다 (DB 사용 중)" if folder
                else "작업폴더가 지정되지 않았습니다",
                QSystemTrayIcon.Warning, 3000)
            return
        self.showMessage(
            f"오늘({day[5:]}) 작업량 {today:,}건",
            f"저장완료 {done:,}건 · {folder}" if done is not None else folder,
            QSystemTrayIcon.Information, 4000)
