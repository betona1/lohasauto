"""
접속 환경 진단 — 지금 이 자리에서 무엇이 되고 무엇이 안 되는지.

사내망과 외부망(집)에서 각각 돌려보고 비교하면 된다.

  python tools/check_env.py
"""
import io
import socket
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
def _console_stdout():
    """
    콘솔 코드페이지에 맞춰 출력한다.

    .bat 은 한글이 깨지지 않게 CP949 로 저장돼 있는데, 파이썬이 UTF-8 로
    쏘면 진단 결과만 깨져 보인다. 콘솔이 쓰는 코드페이지를 그대로 따른다.
    """
    enc = "utf-8"
    if sys.platform == "win32":
        try:
            import ctypes

            enc = f"cp{ctypes.windll.kernel32.GetConsoleOutputCP()}"
        except Exception:
            enc = sys.stdout.encoding or "utf-8"
    return io.TextIOWrapper(sys.stdout.buffer, encoding=enc, errors="replace")


sys.stdout = _console_stdout()

from app import config                    # noqa: E402
from app.lohas import constants as C      # noqa: E402
from app.lohas import tunnel              # noqa: E402

OK, NG, SKIP = "[ 정상 ]", "[ 막힘 ]", "[ 생략 ]"


def log(m=""):
    print(m, flush=True)


def tcp(host: str, port: int, timeout: float = 4.0):
    t0 = time.time()
    try:
        s = socket.create_connection((host, int(port)), timeout=timeout)
        s.close()
        return True, f"{(time.time() - t0) * 1000:.0f}ms"
    except Exception as e:
        return False, type(e).__name__


def http(url: str, timeout: float = 6.0):
    t0 = time.time()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return True, f"HTTP {r.status} · {(time.time() - t0) * 1000:.0f}ms"
    except Exception as e:
        return False, str(e)[:60]


def line(label: str, ok: bool, detail: str, need: str = ""):
    log(f"  {OK if ok else NG}  {label:24} {detail}"
        + (f"   <- {need}" if not ok and need else ""))
    return ok


def main():
    log("=" * 68)
    log(f"접속 위치 : {config.net_profile()}"
        f"   (NET_PROFILE={config.NET_PROFILE})")
    log("=" * 68)

    log()
    log("1. 어디서든 되어야 하는 것 (공인 IP)")
    a = line("로하스 사이트", *http(C.BASE + "/member/"), need="인터넷 연결 확인")
    ip = "110.15.202.109"  # 로하스 분석 API (화면이 알려주는 값)
    b = line(f"분석·태그 API :3403", *tcp(ip, 3403),
             need="회사 방화벽에서 이 포트를 막았는지 확인")
    c = line("Gemini API", *tcp("generativelanguage.googleapis.com", 443))
    d = line("네이버 광고 API", *tcp("api.naver.com", 443))

    log()
    if tunnel.wanted():
        log("2. SSH 터널")
        started = tunnel.start(log=lambda m: log(f"      {m}"))
        line(f"터널 {config.SSH_USER}@{config.SSH_HOST}:{config.SSH_PORT}",
             started, tunnel.status().split(": ", 1)[-1],
             need=".env 의 SSH_HOST / SSH_USER / SSH_PASSWORD 확인")
        log()
        log("3. 사내 서비스 (터널 경유)")
    else:
        log("2. 사내망에서만 되는 것")
    m = config.mysql_settings()
    ok_my = False
    if config.MYSQL_ENABLED and m.get("host"):
        ok_my = line(f"MySQL {m['host']}:{m['port']}",
                     *tcp(m["host"], m["port"]),
                     need="외부라면 .env DB_HOST_EXTERNAL 설정 또는 포트포워딩")
        if ok_my:
            # 포트가 열린 것과 '맞는 서버' 인 것은 다르다. 공인 IP 포워딩이
            # 엉뚱한 장비를 가리키면 미러가 통째로 다른 DB 에 쌓인다.
            ok, detail = _db_identity(m)
            line("  └ 미러 DB 확인", ok, detail,
                 need="이 주소가 사내 미러 서버로 포워딩되는지 확인")
        elif _looks_public(m["host"]):
            # 사내에서 공인 IP 로 되돌아가는 접속은 대부분의 공유기가 막는다
            # (헤어핀 NAT 미지원). 집에서 돌리면 정상일 수 있다.
            log("          ※ 사내에서 공인 IP 로는 원래 잘 안 됩니다"
                "(헤어핀 NAT). 집에서 다시 확인하세요.")
    else:
        log(f"  {SKIP}  MySQL 미러             "
            + ("MYSQL_ENABLED=0" if not config.MYSQL_ENABLED
               else "호스트 미설정 (외부 프로파일)"))

    base = config.datalab_base()
    ok_dl = False
    if base:
        host = base.split("//", 1)[-1].split("/")[0]
        h, _, prt = host.partition(":")
        ok_dl = line(f"데이터랩 {host}", *tcp(h, int(prt or 80)),
                     need="외부라면 .env DATALAB_HOST_EXTERNAL 설정")
    else:
        log(f"  {SKIP}  데이터랩 서버          주소 미설정 → '데이터랩 500' 기능만 꺼짐")

    log()
    log("4. 로컬 저장소" if tunnel.wanted() else "3. 로컬 저장소")
    p = config.SQLITE_PATH
    exists = p.exists()
    size = f"{p.stat().st_size / 1024 / 1024:.0f}MB" if exists else "없음"
    unc = str(p).startswith("\\\\") or str(p)[1:3] == ":\\" and _is_net_drive(p)
    line(f"SQLite", exists, f"{size}  {p}")
    if unc:
        log("          ※ 네트워크 드라이브 위에 있습니다. 외부망에서는 "
            "로컬 경로로 바꾸세요 (.env SQLITE_PATH)")

    ck = Path(config.ROOT) / "data"
    line("data 폴더 쓰기", _writable(ck), str(ck))

    log()
    log("=" * 68)
    core = a and b
    if core:
        log("  로하스 작업(조회·카테고리·키워드·저장)은 여기서 됩니다.")
    else:
        log("  ★ 로하스 사이트나 분석 API 가 막혀 있어 작업을 할 수 없습니다.")
    if not ok_my:
        log("  · MySQL 미러 없음 → 로컬 SQLite 에만 쌓입니다 (작업 자체는 정상).")
    if not ok_dl:
        log("  · 데이터랩 없음 → '데이터랩 500' 탭만 비활성. 나머지는 정상.")
    log("=" * 68)
    tunnel.stop(log=lambda *_: None)
    return 0 if core else 1


def _db_identity(m: dict):
    """정말 우리 미러 DB 인지 — LOHASAUTO_* 테이블이 있는지로 판단한다."""
    try:
        import pymysql
    except ImportError:
        return False, "pymysql 없음"
    try:
        c = pymysql.connect(host=m["host"], port=int(m["port"]),
                            user=m["user"], password=m["password"],
                            connect_timeout=8)
        with c:
            cur = c.cursor()
            cur.execute("SELECT @@hostname")
            hostname = cur.fetchone()[0]
            cur.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema=%s AND table_name LIKE 'LOHASAUTO%%'",
                (m["db"],))
            n = cur.fetchone()[0]
        if n:
            return True, f"{hostname} · {m['db']} 안에 LOHASAUTO_* {n}개"
        return False, f"{hostname} · {m['db']} 에 LOHASAUTO_* 테이블이 없음"
    except Exception as e:
        return False, str(e)[:70]


def _looks_public(host: str) -> bool:
    return bool(host) and not host.startswith(("192.168.", "10.", "127.",
                                               "172.16.", "172.17."))


def _writable(d: Path) -> bool:
    try:
        d.mkdir(parents=True, exist_ok=True)
        t = d / ".write_test"
        t.write_text("x", encoding="utf-8")
        t.unlink()
        return True
    except Exception:
        return False


def _is_net_drive(p: Path) -> bool:
    try:
        import ctypes

        return ctypes.windll.kernel32.GetDriveTypeW(
            str(p)[:3]) == 4          # DRIVE_REMOTE
    except Exception:
        return False


if __name__ == "__main__":
    sys.exit(main())
