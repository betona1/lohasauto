"""
Windows 다중 모니터 열거 / 브라우저 창 배치.

ctypes(user32) 만 사용하므로 추가 의존성이 없고,
QApplication 없이 워커 스레드에서도 안전하게 호출할 수 있다.
"""
import ctypes
from ctypes import wintypes

MONITORINFOF_PRIMARY = 1


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long), ("top", ctypes.c_long),
        ("right", ctypes.c_long), ("bottom", ctypes.c_long),
    ]


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", _RECT),
        ("rcWork", _RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


_ENUMPROC = ctypes.WINFUNCTYPE(
    ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
    ctypes.POINTER(_RECT), ctypes.c_double,
)


def list_monitors() -> list:
    """
    연결된 모니터 목록.
    반환: [{index, device, primary, x, y, width, height,
            work_x, work_y, work_width, work_height, label}, ...]
    index 는 1부터. 화면 배치 순서(위->아래, 왼쪽->오른쪽)로 정렬한다.
    """
    try:
        user32 = ctypes.windll.user32
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass
    except Exception:
        return []

    found = []

    def _cb(hmon, hdc, lprc, data):
        mi = _MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(_MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r, w = mi.rcMonitor, mi.rcWork
            found.append({
                "device": mi.szDevice,
                "primary": bool(mi.dwFlags & MONITORINFOF_PRIMARY),
                "x": r.left, "y": r.top,
                "width": r.right - r.left, "height": r.bottom - r.top,
                "work_x": w.left, "work_y": w.top,
                "work_width": w.right - w.left,
                "work_height": w.bottom - w.top,
            })
        return 1

    try:
        user32.EnumDisplayMonitors(0, 0, _ENUMPROC(_cb), 0)
    except Exception:
        return []

    # 화면상 배치 순서로 정렬 (위쪽 줄 먼저, 같은 줄에서는 왼쪽 먼저)
    found.sort(key=lambda m: (m["y"], m["x"]))
    for i, m in enumerate(found, 1):
        m["index"] = i
        pos = _position_name(m, found)
        m["label"] = (
            f"[{i}] {pos} {m['width']}x{m['height']}"
            f"{' ★주모니터' if m['primary'] else ''}"
        )
    return found


def _cluster(values, tol: int):
    """좌표가 tol 이내면 같은 줄/열로 묶는다. 반환: {원본값: 그룹번호}"""
    out, groups = {}, []
    for v in sorted(set(values)):
        if groups and v - groups[-1][-1] <= tol:
            groups[-1].append(v)
        else:
            groups.append([v])
    for gi, g in enumerate(groups):
        for v in g:
            out[v] = gi
    return out, len(groups)


def _position_name(m: dict, all_mons: list) -> str:
    """'좌상단' 같은 사람이 읽는 위치 이름."""
    if len(all_mons) < 2:
        return "메인"

    # 모니터 폭/높이의 절반을 허용오차로 써서 (0,0) 과 (5,0) 을 같은 열로 본다
    tol_x = max(mm["width"] for mm in all_mons) // 2
    tol_y = max(mm["height"] for mm in all_mons) // 2
    col_map, ncols = _cluster([mm["x"] for mm in all_mons], tol_x)
    row_map, nrows = _cluster([mm["y"] for mm in all_mons], tol_y)
    col, row = col_map[m["x"]], row_map[m["y"]]

    if ncols == 2 and nrows == 2:
        return f"{('상', '하')[row]}{('좌', '우')[col]}단"
    if nrows == 1:
        return ("맨왼쪽", "왼쪽", "가운데", "오른쪽", "맨오른쪽")[col] if ncols <= 5 else f"{col+1}번째"
    if ncols == 1:
        return f"위에서 {row + 1}번째"
    return f"{row + 1}행 {col + 1}열"


def get_monitor(index: int):
    """1-based 인덱스로 모니터 조회. 없으면 None."""
    if not index or index < 1:
        return None
    mons = list_monitors()
    if index > len(mons):
        return None
    return mons[index - 1]


def window_geometry(index: int, margin: int = 0):
    """
    지정 모니터의 작업영역 기준 창 위치/크기.
    반환: (x, y, width, height) 또는 None
    """
    m = get_monitor(index)
    if not m:
        return None
    return (
        m["work_x"] + margin,
        m["work_y"] + margin,
        max(m["work_width"] - margin * 2, 800),
        max(m["work_height"] - margin * 2, 600),
    )


def place_window(win, index: int) -> str:
    """
    Qt 창을 그 모니터 가운데로 옮긴다. 반환: 사람이 읽는 결과 한 줄.

    **`show()` 전에 한 번 옮기는 것만으로는 부족하다.** 윈도우는 창을
    띄우면서 마지막 위치나 커서가 있는 화면으로 되돌리는 수가 있어서,
    띄운 뒤에 한 번 더 옮겨야 한다(2026-09-16: 4번으로 저장했는데 다른
    화면에 떴다).

    창이 그 모니터보다 크면 작업영역에 맞춰 줄인다 — 안 그러면 가운데로
    옮겨도 절반이 옆 화면으로 삐져 나간다.
    """
    m = get_monitor(index)
    if not m:
        mm = [x for x in list_monitors() if x["primary"]]
        m = mm[0] if mm else None
    if not m:
        return "모니터를 찾지 못해 그대로 둡니다"
    w = min(win.width(), m["work_width"])
    h = min(win.height(), m["work_height"])
    if (w, h) != (win.width(), win.height()):
        win.resize(w, h)
    x = m["work_x"] + max((m["work_width"] - w) // 2, 0)
    y = m["work_y"] + max((m["work_height"] - h) // 2, 0)
    win.move(x, y)
    return f"{m['label']} 에 배치 ({x}, {y}) {w}x{h}"


def monitor_of(win):
    """그 창이 지금 어느 모니터에 있나. 1-based, 못 찾으면 0."""
    try:
        c = win.frameGeometry().center()
        cx, cy = c.x(), c.y()
    except Exception:
        return 0
    for m in list_monitors():
        if (m["x"] <= cx < m["x"] + m["width"]
                and m["y"] <= cy < m["y"] + m["height"]):
            return m["index"]
    return 0


def describe_all() -> str:
    mons = list_monitors()
    if not mons:
        return "모니터 정보를 읽을 수 없습니다."
    return "\n".join(
        f"{m['label']} @ ({m['x']},{m['y']})" for m in mons
    )
