"""
로하스 오토 - 상품정보관리(ss_image) 폴더 수량 점검

실행: python main.py
"""
import os
import sys


def _safe_streams():
    """
    pythonw.exe 로 실행하면 sys.stdout/stderr 가 None 이라 print() 가 터진다.
    로그 파일로 돌려서 창 없이 실행해도 안전하게 한다.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        from app.config import LOG_DIR
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        f = open(LOG_DIR / "gui.log", "a", encoding="utf-8", buffering=1)
    except Exception:
        f = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = f
    if sys.stderr is None:
        sys.stderr = f


def suppress_qt_warnings():
    os.environ["QT_DEVICE-PIXEL-RATIO"] = "0"
    os.environ["QT_AUTOSCREEN_SCALE_FACTOR"] = "1"
    os.environ["QT_SCREEN_SCALE_FACTOR"] = "1"
    os.environ["QT_SCALE_FACTOR"] = "1"


def _place_window(win) -> None:
    """.env 의 GUI_MONITOR 에 지정된 모니터 가운데로 창을 옮긴다."""
    try:
        from app import config
        from app.lohas.monitors import monitor_of, place_window

        msg = place_window(win, config.GUI_MONITOR)
        print(f"[창] GUI_MONITOR={config.GUI_MONITOR} → {msg}"
              f"  (지금 {monitor_of(win)}번)")
    except Exception as e:
        print(f"[창] 배치 실패(무시): {e}")


def _force_visible(win) -> None:
    """
    **창이 안 보이면 억지로라도 보이게 한다.**

    윈도우는 프로그램을 띄운 쪽이 STARTUPINFO 에 넣어 준 `nCmdShow` 를
    **첫 `show()` 에 그대로 적용**한다. 작업 스케줄러나 `Start-Process
    -WindowStyle Hidden` 으로 띄우면 창이 만들어지고 자리까지 잡은 채로
    숨어 있다 — 프로그램은 살아 있는데 화면에 아무것도 없다
    (2026-09-16: 창이 (1935,0) 에 1910x1040 으로 놓였는데 vis=False 였다).
    """
    try:
        if win.isVisible():
            return
        win.show()
        win.raise_()
        win.activateWindow()
        if sys.platform == "win32":
            import ctypes
            h = int(win.winId())
            ctypes.windll.user32.ShowWindow(h, 5)      # SW_SHOW
            ctypes.windll.user32.SetForegroundWindow(h)
        print("[창] 숨어 있어 강제로 띄웠습니다")
    except Exception as e:
        print(f"[창] 강제 표시 실패(무시): {e}")


def _open_tunnel() -> None:
    """
    외부망이면 SSH 터널부터 연다. DB 를 처음 건드리기 전에 열려 있어야
    미러가 붙는다. 실패해도 그냥 진행한다 — 미러·데이터랩만 꺼진다.
    """
    try:
        from app import config
        from app.lohas import tunnel

        print(f"[접속] 위치 : {config.net_profile()}")
        if tunnel.wanted():
            tunnel.start()
        print(f"[접속] {tunnel.status()}")
    except Exception as e:
        print(f"[접속] 터널 준비 실패(무시): {e}")


def main() -> int:
    _safe_streams()
    suppress_qt_warnings()
    _open_tunnel()

    from PySide6.QtWidgets import QApplication

    from app.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("lohasauto")
    # 앱 아이콘 — 네이버 검색광고 느낌(초록 바탕 + 상승 막대그래프).
    # 작업표시줄에도 뜨게 하려면 윈도우에 AppUserModelID 를 알려줘야 한다
    # (2026-09-16 사용자).
    try:
        from PySide6.QtGui import QIcon
        from app.config import ROOT
        ico = ROOT / "assets" / "app_icon.ico"
        if ico.exists():
            app.setWindowIcon(QIcon(str(ico)))
            if sys.platform == "win32":
                import ctypes
                ctypes.windll.shell32.                    SetCurrentProcessExplicitAppUserModelID("betona.lohasauto")
    except Exception:
        pass

    win = MainWindow()
    _place_window(win)
    win.show()
    win.raise_()              # 다른 창에 가려지지 않게 맨 앞으로
    win.activateWindow()
    # **띄운 뒤에 한 번 더.** 윈도우가 창을 띄우면서 마지막 위치나 커서가
    # 있는 화면으로 되돌리는 수가 있다 — 그래서 show() 전 한 번만 옮기면
    # 엉뚱한 모니터에 떴다(2026-09-16 사용자).
    from PySide6.QtCore import QTimer
    QTimer.singleShot(0, lambda: (_force_visible(win), _place_window(win)))
    QTimer.singleShot(400, lambda: (_force_visible(win), _place_window(win)))
    try:
        return app.exec()
    finally:
        try:
            from app.lohas import tunnel
            tunnel.stop()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
