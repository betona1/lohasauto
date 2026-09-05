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
        from app.lohas.monitors import get_monitor, list_monitors

        mon = get_monitor(config.GUI_MONITOR)
        if mon is None:
            mons = [m for m in list_monitors() if m["primary"]]
            mon = mons[0] if mons else None
        if not mon:
            return
        w, h = win.width(), win.height()
        x = mon["work_x"] + max((mon["work_width"] - w) // 2, 0)
        y = mon["work_y"] + max((mon["work_height"] - h) // 2, 0)
        win.move(x, y)
        print(f"[창] {mon['label']} 에 배치 ({x}, {y})")
    except Exception as e:
        print(f"[창] 배치 실패(무시): {e}")


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

    win = MainWindow()
    _place_window(win)
    win.show()
    win.raise_()              # 다른 창에 가려지지 않게 맨 앞으로
    win.activateWindow()
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
