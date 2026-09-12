"""
네이버 검색광고 API — 광고주센터 데이터를 통째로 읽어온다.

**로그인·2단계 인증·크롤링이 필요 없다.** 광고주센터가 보여주는 것은
거의 다 공개 API 로 나온다(2026-09-11 실측). 브라우저를 띄우면 2단계 인증
때문에 사람이 붙어 있어야 하지만, API 키는 한 번 발급받으면 무인으로 돈다.

    인증  HMAC-SHA256 서명 3종 헤더 (`naver_ad._signature` 와 같다)
          X-API-KEY   발급받은 액세스 라이선스
          X-Customer  **광고계정 번호** - 이것만 바꾸면 다른 계정이 읽힌다
          X-Signature {타임스탬프}.{메서드}.{URI} 를 시크릿키로 서명

키 발급  https://searchad.naver.com > 도구 > API 사용관리

실측 (2026-09-11, 지금 .env 의 키)
    X-Customer=2718735   캠페인 3건
    X-Customer=4464788   캠페인 10건   ← 키 하나로 두 계정 다 열린다
    /billing/bizmoney                    잔액 301,000원
    /billing/bizmoney/histories/charge   충전 이력
    /stats                               노출·클릭·비용 (salesAmt = 지출액)

`/stats` 주의 — `ids` 는 **JSON 배열이 아니라 같은 이름으로 여러 번** 보낸다.
JSON 으로 보내면 `11001 유효하지 않은 ID 형식입니다` 가 온다.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .. import config
from .naver_ad import _signature

BASE = "https://api.naver.com"
# **쓰기(POST/PUT/DELETE)는 호스트가 다르다.** api.naver.com 으로 보내면
# 308 Permanent Redirect 만 오고, urllib 은 POST 를 따라가지 않아 빈 응답처럼
# 보인다. Location 헤더를 열어보고 알았다(2026-09-12 보고서 생성).
BASE_WRITE = "https://api.searchad.naver.com"


def customers() -> list:
    """읽을 광고계정 번호들. `.env` 의 NAVER_AD_CUSTOMERS."""
    return config.naver_ad_customers()


def available() -> bool:
    return config.naver_ready()


def _headers(method: str, uri: str, customer) -> dict:
    # 키는 **광고계정마다 다르다.** 그 계정용 키가 .env 에 있으면 그것을,
    # 없으면 기본 키를 쓴다 (2026-09-12).
    access, secret = config.naver_ad_key(customer)
    ts = str(round(time.time() * 1000))
    return {
        "Content-Type": "application/json; charset=UTF-8",
        "X-Timestamp": ts,
        "X-API-KEY": access,
        "X-Customer": str(customer),
        "X-Signature": _signature(ts, method, uri, secret),
    }


def call(uri: str, customer, params: dict = None, method: str = "GET",
         body: dict = None, timeout: int = 30):
    """서명해서 한 번 부른다. 반환 (status, 파싱된 값).

    `params` 의 값이 list 면 **같은 이름으로 여러 번** 보낸다(`/stats` 의 ids).
    """
    q = ("?" + urllib.parse.urlencode(params, doseq=True)) if params else ""
    data = json.dumps(body).encode() if body is not None else None
    base = BASE if method == "GET" else BASE_WRITE
    req = urllib.request.Request(base + uri + q, data=data,
                                 headers=_headers(method, uri, customer),
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(raw)
            except ValueError:
                return r.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]


def get(uri: str, customer, params: dict = None, timeout: int = 30):
    """200 이 아니면 빈 값. 한 계정이 막혀도 나머지는 계속 돈다."""
    st, d = call(uri, customer, params, timeout=timeout)
    return d if st == 200 else None


# ---- 광고 구조 -------------------------------------------------------
def campaigns(customer) -> list:
    return get("/ncc/campaigns", customer) or []


def adgroups(customer, campaign_id: str = "") -> list:
    p = {"nccCampaignId": campaign_id} if campaign_id else None
    return get("/ncc/adgroups", customer, p) or []


def keywords(customer, adgroup_id: str) -> list:
    return get("/ncc/keywords", customer,
               {"nccAdgroupId": adgroup_id}) or []


def ads(customer, adgroup_id: str) -> list:
    return get("/ncc/ads", customer, {"nccAdgroupId": adgroup_id}) or []


# ---- 성과 · 비용 -----------------------------------------------------
# 전환까지 받는다. `convAmt`(전환매출)·`ror`(ROAS) 가 있어야 그 상품이
# 돈만 먹는지 실제로 파는지 갈린다 — 광고주센터 보고서와 값이 같다
# (2026-09-12 실측: 09-10 광고비 14,781 / 전환매출 189,960 / ROAS 1,285%).
FIELDS = ["impCnt", "clkCnt", "salesAmt", "ctr", "cpc", "avgRnk",
          "ccnt", "convAmt", "crto", "ror", "cpConv"]


def stats(customer, ids: list, since, until, fields: list = None,
          breakdown: str = "") -> list:
    """
    노출·클릭·**비용(salesAmt)**. `ids` 는 캠페인/그룹/키워드 ID 목록.

    `breakdown="day"` 면 날짜별로 쪼개 준다 — 일자별 지출 내역이 이것이다.
    ID 는 한 번에 너무 많이 보내면 414 가 나므로 100개씩 끊는다.
    """
    out = []
    ids = [i for i in (ids or []) if i]
    for i in range(0, len(ids), 100):
        p = {"ids": ids[i:i + 100],
             "fields": json.dumps(fields or FIELDS),
             "timeRange": json.dumps({"since": str(since),
                                      "until": str(until)})}
        if breakdown:
            p["breakdown"] = breakdown
        d = get("/stats", customer, p)
        if isinstance(d, dict):
            out += d.get("data") or []
    return out


def bizmoney(customer) -> dict:
    """비즈머니 잔액. `bizmoney` 가 마이너스면 광고가 멈춘다."""
    return get("/billing/bizmoney", customer) or {}


def charge_history(customer, since, until) -> list:
    """충전 이력. statDt 는 밀리초 단위 epoch 다."""
    return get("/billing/bizmoney/histories/charge", customer,
               {"searchStartDt": str(since), "searchEndDt": str(until)}) or []


def master_reports(customer) -> list:
    return get("/master-reports", customer) or []
