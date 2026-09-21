"""
환경설정 — 흩어져 있던 설정을 한곳에 모은다.

지금까지 설정이 상단바·광고 탭·`.env` 로 흩어져 있어 어디서 바꾸는지
찾기 어려웠다(2026-09-16 사용자).

    화면    프로그램을 띄울 모니터 · 브라우저 모니터 · 창 크기
    실행    헤드리스 · 페이지당 조회수 · 자동점검 주기
    광고    메인 계정 · 광고 시작일 · 노출시간
    연결    로하스 계정 · 네트워크 프로파일 · 메일함 (읽기 전용 확인)
    예약    매일 도는 작업 (예약 창 열기)

**모니터가 여러 대면 어느 것이 몇 번인지 모른다.** [모니터 보기] 를 누르면
각 화면 한가운데에 큰 숫자를 잠깐 띄워 준다.

값은 `.env` 에 쓴다 — 다음 실행부터 그 모니터에서 뜬다.
"""
import re

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout,
                               QHBoxLayout, QLabel, QMessageBox, QPushButton,
                               QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from .. import config, db
from ..lohas.monitors import list_monitors

NL = chr(10)


def env_path():
    return config.ROOT / ".env"


def set_env(**kv):
    """
    `.env` 의 값을 바꾼다. **다른 줄과 주석은 그대로 둔다.**

    편집기가 잠깐 파일을 잡고 있으면 쓰기가 막힌다(2026-09-16 사용자:
    "쓰고 있다네"). 그래서 세 번까지 다시 해 보고, 그래도 안 되면
    **임시파일에 쓴 뒤 바꿔치기**한다 — 대개 이 방법은 통한다.

    반환 (성공여부, 사유)
    """
    import os
    import tempfile
    import time

    p = env_path()
    try:
        txt = p.read_text(encoding="utf-8")
    except Exception as e:
        return False, f"읽지 못했습니다: {str(e)[:80]}"
    for k, v in kv.items():
        pat = re.compile(rf"^{re.escape(k)}\s*=.*$", re.M)
        line = f"{k}={v}"
        if pat.search(txt):
            txt = pat.sub(line, txt, count=1)
        else:
            txt = txt.rstrip(NL) + NL + line + NL

    last = ""
    for i in range(3):
        try:
            p.write_text(txt, encoding="utf-8")
            return True, ""
        except Exception as e:
            last = str(e)[:90]
            time.sleep(0.4)
    # 임시파일에 쓰고 바꿔치기 — 열려 있는 편집기와 덜 부딪힌다
    tmp = ""
    try:
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(txt)
        os.replace(tmp, str(p))
        return True, ""
    except Exception as e:
        try:
            if tmp and os.path.exists(tmp):
                os.unlink(tmp)
        except Exception:
            pass
        return False, f"{last} / 교체도 실패: {str(e)[:80]}"


class MonitorFlash(QWidget):
    """모니터 한가운데에 번호를 크게 띄우는 창."""

    def __init__(self, mon: dict):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.Tool)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        lay = QVBoxLayout(self)
        lab = QLabel(str(mon["index"]))
        lab.setAlignment(Qt.AlignCenter)
        lab.setStyleSheet(
            "background:rgba(3,199,90,235); color:white; border-radius:28px;"
            " font-size:150px; font-weight:bold; padding:30px 70px;")
        sub = QLabel(mon["label"])
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet(
            "background:rgba(0,0,0,190); color:white; border-radius:10px;"
            " font-size:17px; padding:8px 14px;")
        lay.addWidget(lab)
        lay.addWidget(sub)
        self.adjustSize()
        self.move(mon["work_x"] + (mon["work_width"] - self.width()) // 2,
                  mon["work_y"] + (mon["work_height"] - self.height()) // 2)


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("환경설정")
        self.setWindowFlag(Qt.Window, True)
        self.setMinimumSize(720, 560)
        self._flash = []
        v = QVBoxLayout(self)

        t = QLabel("환경설정")
        t.setStyleSheet("font-size:16px; font-weight:bold;")
        v.addWidget(t)
        note = QLabel(f"값은 {env_path()} 에 저장됩니다."
                      "  화면·모니터 설정은 **다음 실행부터** 적용됩니다.")
        note.setWordWrap(True)
        note.setStyleSheet(
            "color:#555; background:#f3f6f9; border-radius:6px; padding:8px;")
        v.addWidget(note)

        tabs = QTabWidget()
        tabs.addTab(self._tab_screen(), "🖥 화면")
        tabs.addTab(self._tab_run(), "⚙ 실행")
        tabs.addTab(self._tab_ad(), "📣 광고")
        tabs.addTab(self._tab_conn(), "🔌 연결")
        v.addWidget(tabs, 1)

        row = QHBoxLayout()
        self.lbl = QLabel("")
        self.lbl.setStyleSheet("color:#1565c0; font-weight:bold;")
        row.addWidget(self.lbl, 1)
        b = QPushButton("저장")
        b.setStyleSheet("font-weight:bold;")
        b.clicked.connect(self.save)
        row.addWidget(b)
        c = QPushButton("닫기")
        c.clicked.connect(self.close)
        row.addWidget(c)
        v.addLayout(row)

    # ------------------------------------------------------------ 화면
    def _tab_screen(self):
        w = QWidget()
        f = QFormLayout(w)
        mons = list_monitors()
        self.mons = mons

        self.cmb_gui = QComboBox()
        self.cmb_gui.addItem("기본(주 모니터)", 0)
        for m in mons:
            self.cmb_gui.addItem(m["label"], m["index"])
        i = self.cmb_gui.findData(config.GUI_MONITOR)
        self.cmb_gui.setCurrentIndex(i if i >= 0 else 0)
        self.cmb_gui.setStyleSheet("font-weight:bold;")
        self.cmb_gui.setToolTip("이 프로그램(로하스 오토) 창이 뜰 화면입니다.")
        f.addRow("① 이 프로그램을 띄울 모니터", self.cmb_gui)

        self.cmb_br = QComboBox()
        self.cmb_br.addItem("기본(자동)", 0)
        for m in mons:
            self.cmb_br.addItem(m["label"], m["index"])
        i = self.cmb_br.findData(config.BROWSER_MONITOR)
        self.cmb_br.setCurrentIndex(i if i >= 0 else 0)
        self.cmb_br.setToolTip(
            "크롬 등 자동화 브라우저가 뜰 화면입니다."
            + NL + "이 프로그램 창과는 상관없습니다.")
        f.addRow("② 브라우저(크롬)를 띄울 모니터", self.cmb_br)

        row = QHBoxLayout()
        b = QPushButton("🔢 모니터 보기")
        b.setToolTip("각 화면 한가운데에 번호를 3초간 띄웁니다."
                     + NL + "보이는 번호를 위에서 고르십시오.")
        b.clicked.connect(self.flash)
        row.addWidget(b)
        b2 = QPushButton("지금 이 창을 그 모니터로")
        b2.clicked.connect(self.move_now)
        row.addWidget(b2)
        row.addStretch(1)
        holder = QWidget()
        holder.setLayout(row)
        f.addRow("", holder)

        self.lbl_mon = QLabel(
            f"모니터 {len(mons)}대" + NL
            + NL.join("   " + m["label"] for m in mons))
        self.lbl_mon.setStyleSheet(
            "color:#555; background:#fafafa; border:1px solid #e0e0e0;"
            " border-radius:6px; padding:8px;")
        f.addRow("", self.lbl_mon)
        return w

    def flash(self):
        """각 모니터에 번호를 띄운다."""
        self._flash = []
        for m in list_monitors():
            fw = MonitorFlash(m)
            fw.show()
            self._flash.append(fw)
        QTimer.singleShot(3000, self._flash_off)

    def _flash_off(self):
        for w in self._flash:
            w.close()
        self._flash = []

    def _main_window(self):
        """옮길 대상은 설정창이 아니라 **본 프로그램 창** 이다."""
        try:
            top = self.parent().window() if self.parent() else None
        except Exception:
            top = None
        return top or self

    def move_now(self):
        from ..lohas.monitors import monitor_of, place_window
        idx = self.cmb_gui.currentData() or 0
        w = self._main_window()
        msg = place_window(w, idx)
        w.raise_()
        w.activateWindow()
        self.lbl.setText(f"{msg}  ·  지금 {monitor_of(w)}번 화면")

    # ------------------------------------------------------------ 실행
    def _tab_run(self):
        w = QWidget()
        f = QFormLayout(w)
        self.chk_head = QCheckBox("브라우저를 창 없이 실행(헤드리스)")
        self.chk_head.setChecked(bool(config.HEADLESS))
        f.addRow("브라우저", self.chk_head)

        self.cmb_page = QComboBox()
        self.cmb_page.addItems(["1000", "500", "200", "100", "50"])
        self.cmb_page.setCurrentText(str(config.PAGE_SIZE))
        f.addRow("페이지당 조회 개수", self.cmb_page)

        self.sp_max = QSpinBox()
        self.sp_max.setRange(1, 999)
        self.sp_max.setValue(int(config.MAX_PAGES))
        f.addRow("최대 페이지 수", self.sp_max)

        self.sp_tags = QSpinBox()
        self.sp_tags.setRange(1, 10)
        self.sp_tags.setValue(int(config.MAX_TAGS))
        f.addRow("태그 최대 개수", self.sp_tags)

        b = QPushButton("⏰ 예약 작업 열기")
        b.clicked.connect(self._open_sched)
        f.addRow("예약", b)
        return w

    def _open_sched(self):
        from .schedule_dialog import ScheduleDialog
        ScheduleDialog(self).show()

    # ------------------------------------------------------------ 광고
    def _tab_ad(self):
        from ..lohas import ad_account, ad_schedule
        w = QWidget()
        f = QFormLayout(w)
        acc = ad_account.main_account() or {}
        self.cu = acc.get("customer_id") or ""
        f.addRow("메인 광고계정",
                 QLabel(f"{acc.get('label') or '(없음)'}  ({self.cu})"))
        f.addRow("광고 시작일",
                 QLabel(ad_account.start_of(self.cu) or "(미지정)"))
        f.addRow("노출시간",
                 QLabel(ad_schedule.label(ad_schedule.current(self.cu))))
        f.addRow("매출 기준",
                 QLabel(f"{config._str('SALES_SOURCE') or 'commerce'}"
                        f"  ·  스토어 {config._str('COMMERCE_STORE_NAME')}"
                        f" ({config._str('COMMERCE_STORE_URL')})"))
        b = QPushButton("「설정」 탭에서 계정·캠페인 바꾸기")
        b.setEnabled(False)
        f.addRow("", b)
        f.addRow("", QLabel(
            "계정·캠페인·광고 시작일은 「설정」 탭에서,"
            + NL + "노출시간은 「광고상황판 → ⏱ 변경이력」 과"
                   " 「성과 보고서 → ⏱ 광고시간대」 에서 바꿉니다."))
        return w

    # ------------------------------------------------------------ 연결
    def _tab_conn(self):
        w = QWidget()
        f = QFormLayout(w)
        f.addRow("로하스 계정", QLabel(config.masked_id()))
        f.addRow("네트워크", QLabel(config._str("NET_PROFILE") or "auto"))
        f.addRow("SQLite", QLabel(str(config.SQLITE_PATH)))
        f.addRow("MySQL 미러", QLabel(db.mysql_status()))
        mails = config.mailboxes()
        f.addRow("메일함", QLabel(
            NL.join(f"{m['name']} · {m['user']} · {m['proto']}://"
                    f"{m['host']}:{m['port']}" for m in mails)
            or "(설정 없음)"))
        f.addRow("", QLabel(
            "비밀번호·API 키는 여기서 보여주지 않습니다."
            + NL + f"바꾸려면 {env_path()} 를 직접 여십시오."))
        return w

    # ------------------------------------------------------------ 저장
    def save(self):
        ok, why = set_env(
            GUI_MONITOR=self.cmb_gui.currentData() or 0,
            BROWSER_MONITOR=self.cmb_br.currentData() or 0,
            HEADLESS=1 if self.chk_head.isChecked() else 0,
            PAGE_SIZE=self.cmb_page.currentText(),
            MAX_PAGES=self.sp_max.value(),
            MAX_TAGS=self.sp_tags.value(),
        )
        if not ok:
            QMessageBox.warning(
                self, "환경설정",
                ".env 를 쓰지 못했습니다." + NL + NL + str(why) + NL * 2
                + "편집기(VS Code 등)에서 그 파일을 닫고 다시 눌러 주십시오.")
            return
        # **저장하면 그 자리에서 옮겨 준다.** '다음 실행부터' 라고만
        # 하면 제대로 저장됐는지 확인할 길이 없다(2026-09-16 사용자).
        from ..lohas.monitors import monitor_of, place_window
        w = self._main_window()
        msg = place_window(w, self.cmb_gui.currentData() or 0)
        w.raise_()
        w.activateWindow()
        self.lbl.setText(f"저장했습니다 — {msg}")
        QMessageBox.information(
            self, "환경설정",
            "저장했습니다." + NL * 2
            + f"① 프로그램 : {self.cmb_gui.currentText()}" + NL
            + f"② 브라우저 : {self.cmb_br.currentText()}" + NL * 2
            + f"본 창을 지금 {monitor_of(w)}번 화면으로 옮겼습니다."
            + NL + "다음에 켤 때도 거기서 뜹니다.")
