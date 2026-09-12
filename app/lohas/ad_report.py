"""
네이버 검색광고 보고서 — 만들고 · 기다리고 · 받아서 저장한다.

두 종류다.
  마스터 보고서  `/master-reports`  캠페인·광고그룹·키워드·쇼핑상품 같은
                                    **기준 정보** 목록
  대용량 보고서  `/stat-reports`    날짜별 **성과**(노출·클릭·비용·전환)

둘 다 "요청 → 서버가 만듦 → 다 되면 받기" 구조다. 바로 안 나온다.
  POST 로 만들고 → `status` 가 `BUILT`(또는 `NONE` 이 아닌 것)가 될 때까지
  기다렸다가 → `downloadUrl` 로 받는다.

⚠️ **다운로드 서명은 쿼리를 뺀 경로로 만든다.** `downloadUrl` 전체나
쿼리까지 넣어 서명하면 403 `Invalid Signature` 가 온다. 호스트도
`api.searchad.naver.com` 으로 다르다(2026-09-12 실측에서 한 번 걸렸다).

    서명 대상 URI = "/report-download"
"""
import datetime
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .. import config
from . import searchad as sa

DOWNLOAD_URI = "/report-download"

# 마스터 보고서 항목 — 기준 정보
MASTER_ITEMS = ["Campaign", "Adgroup", "Keyword", "Ad", "ShoppingProduct",
                "BusinessChannel", "AdExtension", "Qi", "KeywordHistory"]

# 대용량 보고서 — 날짜별 성과
STAT_TYPES = ["AD", "AD_DETAIL", "EXPKEYWORD", "AD_CONVERSION",
              "SHOPPINGKEYWORD_DETAIL", "SHOPPINGKEYWORD_CONVERSION_DETAIL",
              "SHOPPINGBRANDPRODUCT", "SHOPPINGBRANDPRODUCT_CONVERSION",
              "CRITERION"]


def out_dir() -> Path:
    d = config.ROOT / "data" / "ad_report"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---- 마스터 보고서 ---------------------------------------------------
def master_list(customer) -> list:
    return sa.get("/master-reports", customer) or []


def master_create(customer, item: str):
    return sa.call("/master-reports", customer, method="POST",
                   body={"item": item})


def master_delete_all(customer):
    return sa.call("/master-reports", customer, method="DELETE")


# ---- 대용량 보고서 ---------------------------------------------------
def stat_list(customer) -> list:
    d = sa.get("/stat-reports", customer)
    return d if isinstance(d, list) else []


def stat_create(customer, report_tp: str, stat_dt):
    """`stat_dt` 는 그 하루. ISO8601(Z) 로 보낸다."""
    day = str(stat_dt)
    return sa.call("/stat-reports", customer, method="POST",
                   body={"reportTp": report_tp,
                         "statDt": f"{day}T00:00:00.000Z"})


def stat_get(customer, report_id: str):
    return sa.get(f"/stat-reports/{report_id}", customer) or {}


# ---- 받기 -----------------------------------------------------------
def download(customer, url: str, timeout: int = 60) -> bytes:
    """`downloadUrl` 을 받아 온다. 서명은 쿼리를 뺀 경로로 만든다."""
    req = urllib.request.Request(
        url, headers=sa._headers("GET", DOWNLOAD_URI, customer))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def save(customer, name: str, data: bytes) -> Path:
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    p = out_dir() / f"{customer}_{name}_{stamp}.tsv"
    p.write_bytes(data)
    return p


def wait_and_fetch(customer, pick, tries: int = 20, wait: int = 6,
                   log=print):
    """
    `pick()` 이 돌려주는 보고서가 받을 수 있게 될 때까지 기다렸다 받는다.

    `pick` 은 매번 목록을 다시 읽어 그 보고서 dict 를 돌려주는 함수다 —
    상태는 서버가 바꾸므로 캐시를 믿으면 안 된다.
    """
    for i in range(tries):
        r = pick() or {}
        st = str(r.get("status") or "")
        url = r.get("downloadUrl") or ""
        if url and st not in ("NONE", "REGIST", "RUNNING"):
            try:
                return r, download(customer, url)
            except urllib.error.HTTPError as e:
                log(f"      받기 실패 HTTP {e.code}")
                return r, b""
        log(f"      기다리는 중… ({i + 1}/{tries}) 상태={st or '-'}")
        time.sleep(wait)
    return (pick() or {}), b""
