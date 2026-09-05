"""
.env 기반 설정 로더.

LOHASPIC 은 config.txt(콤마구분) 에 계정을 두었지만,
lohasauto 는 요청대로 전부 .env 로 통일한다.
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def app_root() -> Path:
    """소스 실행/PyInstaller exe 실행 모두에서 동작하는 기준 폴더."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


ROOT = app_root()

load_dotenv(ROOT / ".env")


def _bool(key: str, default: bool = False) -> bool:
    raw = (os.getenv(key) or "").strip().strip("'\"").lower()
    if raw == "":
        return default
    return raw in ("1", "true", "y", "yes", "on")


def _int(key: str, default: int) -> int:
    raw = (os.getenv(key) or "").strip().strip("'\"")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _str(key: str, default: str = "") -> str:
    raw = os.getenv(key)
    if raw is None:
        return default
    # DB_PASSWORD='pa55w0rd' 처럼 따옴표로 감싼 값 대응
    return raw.strip().strip("'\"")


# ---- 로하스 계정 ----
LOHAS_ID = _str("LOHAS_ID")
LOHAS_PW = _str("LOHAS_PW")

# ---- 브라우저 ----
HEADLESS = _bool("HEADLESS", False)
CHROME_DRIVER_PATH = _str("CHROME_DRIVER_PATH")
# 브라우저를 띄울 모니터 번호 (1부터, 0이면 기본 동작)
BROWSER_MONITOR = _int("BROWSER_MONITOR", 0)
# 프로그램 창을 띄울 모니터 번호 (1부터, 0이면 주 모니터)
GUI_MONITOR = _int("GUI_MONITOR", 0)
# 로그인 쿠키를 저장해 다음 실행 때 로그인 단계를 건너뛴다
USE_COOKIE_LOGIN = _bool("USE_COOKIE_LOGIN", True)
PAGE_SIZE = _str("PAGE_SIZE", "1000")
MAX_PAGES = _int("MAX_PAGES", 100)

# ---- 상품분석(ALL) ----
ANALYSIS_BATCH = _int("ANALYSIS_BATCH", 20)      # 한 번에 요청할 건수
ANALYSIS_POLL = _int("ANALYSIS_POLL", 10)        # 완료 폴링 간격(초)
ANALYSIS_TIMEOUT = _int("ANALYSIS_TIMEOUT", 300) # 배치당 최대 대기(초)

# ---- Gemini (키워드 선택 AI) ----
GEMINI_API_KEY = _str("GEMINI_API_KEY")
GEMINI_MODEL = _str("GEMINI_MODEL", "gemini-flash-latest")
GEMINI_MIN_INTERVAL = float(_str("GEMINI_MIN_INTERVAL", "4") or 4)  # 호출 최소 간격(초)
GEMINI_MAX_IMAGES = _int("GEMINI_MAX_IMAGES", 1)      # 선택 시 보낼 이미지 장수
GEMINI_VERIFY = _bool("GEMINI_VERIFY", True)          # 검수 2차 호출 사용
GEMINI_VERIFY_IMAGES = _bool("GEMINI_VERIFY_IMAGES", False)  # 검수에도 이미지 첨부
GEMINI_RETRIES = _int("GEMINI_RETRIES", 3)

# ---- 키워드 선택 규칙 ----
BANNED_FILE = ROOT / "data" / "banned_words.txt"
MAX_TAGS = _int("MAX_TAGS", 10)          # 태그 최대 선택 개수
MAX_TITLE_KW = _int("MAX_TITLE_KW", 4)   # 상품명 키워드 선택 개수

# ---- 네이버 검색광고 API (연관키워드) ----
# 키 이름은 기존 프로그램(searchadapi)과 호환되게 둘 다 받는다.
NAVER_CUSTOMER_ID = _str("NAVER_CUSTOMER_ID") or _str("CUSTOMER_ID")
NAVER_ACCESS_KEY = _str("naverapikey") or _str("ACCESS_KEY")
NAVER_SECRET_KEY = _str("naverpass") or _str("SEC_KEY")
NAVER_DELAY_MIN = float(_str("DELAY_MIN", "0.5") or 0.5)
NAVER_DELAY_MAX = float(_str("DELAY_MAX", "1.2") or 1.2)


def naver_ready() -> bool:
    return bool(NAVER_CUSTOMER_ID and NAVER_ACCESS_KEY and NAVER_SECRET_KEY)

# ---- SQLite ----
_sqlite_raw = _str("SQLITE_PATH", "data/lohasauto.db")
SQLITE_PATH = Path(_sqlite_raw)
if not SQLITE_PATH.is_absolute():
    SQLITE_PATH = ROOT / SQLITE_PATH

# ---- 접속 위치 (사내망 / 외부망) ----
# 로하스 사이트(com.exponet.co.kr)와 분석·태그 API(:3403)는 공인 IP라 어디서든
# 되지만, MySQL 미러와 데이터랩 서버는 사내 IP다. 집에서 띄우면 그쪽만 못 쓴다.
#
#   NET_PROFILE=auto      사내 MySQL 에 짧게 접속해보고 되면 internal (기본)
#              =internal  항상 사내 설정
#              =external  항상 외부 설정
NET_PROFILE = (_str("NET_PROFILE", "auto") or "auto").strip().lower()
NET_PROBE_TIMEOUT = float(_str("NET_PROBE_TIMEOUT", "1.2") or 1.2)

_profile_cache = None


def _reachable(host: str, port: int, timeout: float) -> bool:
    import socket

    if not host:
        return False
    try:
        sock = socket.create_connection((host, int(port)), timeout=timeout)
        sock.close()
        return True
    except Exception:
        return False


def net_profile() -> str:
    """'internal' 또는 'external'. auto 면 사내 MySQL 도달 여부로 한 번만 정한다."""
    global _profile_cache
    if NET_PROFILE in ("internal", "external"):
        return NET_PROFILE
    if _profile_cache is None:
        _profile_cache = ("internal"
                          if _reachable(_str("DB_HOST_INTERNAL")
                                        or _str("DB_HOST"),
                                        _int("DB_PORT_INTERNAL",
                                             _int("DB_PORT", 3306)),
                                        NET_PROBE_TIMEOUT)
                          else "external")
    return _profile_cache


def is_external() -> bool:
    return net_profile() == "external"


def _pick(key: str, default: str = "") -> str:
    """
    KEY_INTERNAL / KEY_EXTERNAL 중 현재 프로파일 값.

    프로파일 키가 아예 없을 때만 KEY 로 되돌아간다. **비워둔 것은 '끔'이다** —
    외부망에서 DB_HOST_EXTERNAL 를 비웠는데 사내 주소로 폴백해버리면
    닿지도 않는 곳에 매번 접속을 시도하게 된다.
    """
    name = key + ("_EXTERNAL" if is_external() else "_INTERNAL")
    if os.getenv(name) is not None:
        return _str(name)
    return _str(key, default)


def _pick_int(key: str, default: int) -> int:
    v = _pick(key, "")
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


# ---- MySQL 미러 ----
# 호스트/포트만 내부·외부로 갈린다. 계정·DB명은 같은 서버라 공통이다.
MYSQL_ENABLED = _bool("MYSQL_ENABLED", False)


def mysql_settings() -> dict:
    if TUNNEL_ENABLED and is_external():
        return {
            "host": "127.0.0.1",
            "port": TUNNEL_DB_PORT,
            "user": _str("DB_USER"),
            "password": _str("DB_PASSWORD"),
            "db": _str("DB_NAME"),
        }
    return {
        "host": _pick("DB_HOST"),
        "port": _pick_int("DB_PORT", 3306),
        "user": _str("DB_USER"),
        "password": _str("DB_PASSWORD"),
        "db": _str("DB_NAME"),
    }


class _LazyMySQL(dict):
    """import 시점에 프로파일을 확정하지 않도록 접근할 때 채운다.

    모듈을 읽는 순간 네트워크를 찔러보면 GUI 가 뜨기 전에 멈춘 것처럼 보인다.
    """

    def _load(self):
        if not self:
            self.update(mysql_settings())
        return self

    def __getitem__(self, k):
        return dict.__getitem__(self._load(), k)

    def get(self, k, d=None):
        return dict.get(self._load(), k, d)

    def __iter__(self):
        return iter(self._load().keys())

    def items(self):
        return self._load().items()

    def __repr__(self):
        return repr(dict(self._load()))


MYSQL = _LazyMySQL()


# ---- SSH 터널 (외부망 전용) ----
# 사내 MySQL·데이터랩은 사설 IP다. SSH(22)만 공인 IP 로 열려 있으므로 그리로
# 들어가 두 서비스를 로컬 포트로 끌어온다. MySQL 을 인터넷에 직접 열지 않아도 된다.
TUNNEL_ENABLED = _bool("TUNNEL_ENABLED", False)
SSH_HOST = _str("SSH_HOST")
SSH_PORT = _int("SSH_PORT", 22)
SSH_USER = _str("SSH_USER") or _str("ai100id")
SSH_PASSWORD = _str("SSH_PASSWORD") or _str("ai100pw")
SSH_KEY = _str("SSH_KEY")
SSH_KEY_PASSPHRASE = _str("SSH_KEY_PASSPHRASE")
SSH_TIMEOUT = float(_str("SSH_TIMEOUT", "12") or 12)

# 터널이 끌어올 사내 주소 (SSH 로 들어간 서버에서 보이는 주소)
TUNNEL_DB_TARGET = _str("TUNNEL_DB_TARGET", "192.168.219.200:3306")
TUNNEL_DATALAB_TARGET = _str("TUNNEL_DATALAB_TARGET", "192.168.219.100:8900")

# 내 PC 에서 열 포트. 집 PC 에 MySQL 이 깔려 있어도 안 부딪치게 기본을 비껴 잡았다.
TUNNEL_DB_PORT = _int("TUNNEL_DB_PORT", 13306)
TUNNEL_DATALAB_PORT = _int("TUNNEL_DATALAB_PORT", 18900)


# ---- 데이터랩(100번 서버) ----
def datalab_base() -> str:
    """네이버 데이터랩 백엔드 주소. 외부망에서는 비워두면 기능만 꺼진다."""
    if TUNNEL_ENABLED and is_external():
        return f"http://127.0.0.1:{TUNNEL_DATALAB_PORT}/api/naver"
    explicit = _pick("DATALAB_BASE")
    if explicit:
        return explicit.rstrip("/")
    name = "DATALAB_HOST" + ("_EXTERNAL" if is_external() else "_INTERNAL")
    # 프로파일 키를 비워뒀으면 '끔' 이다. ai100ip 로 되돌아가면 안 된다.
    host = _str(name) if os.getenv(name) is not None else _str("ai100ip")
    port = _pick_int("DATALAB_PORT", 8900)
    if not host:
        return ""
    return f"http://{host.strip()}:{port}/api/naver"

# ---- 기타 경로 ----
LOG_DIR = ROOT / "logs"


def credentials_ok() -> bool:
    return bool(LOHAS_ID and LOHAS_PW)


def masked_id() -> str:
    """UI 표시용 마스킹 (someone@example.com -> som*****@example.com)"""
    if not LOHAS_ID:
        return "(미설정)"
    if "@" not in LOHAS_ID:
        return LOHAS_ID[:2] + "*" * max(len(LOHAS_ID) - 2, 0)
    local, _, domain = LOHAS_ID.partition("@")
    head = local[:3] if len(local) > 3 else local[:1]
    return f"{head}{'*' * max(len(local) - len(head), 1)}@{domain}"
