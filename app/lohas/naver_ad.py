"""
네이버 검색광고 API — 연관키워드 조회.

기존 `S:\\python\\searchadapi\\searchAD.py` 의 인증/호출 방식을 그대로 따랐다.
로하스가 주는 키워드(LCP당 추천 71 + 사용 201)만으로는 부족해서, 여기서
연관키워드를 대량으로 끌어와 후보 풀을 넓히는 용도.

  POST 아님 — GET https://api.naver.com/keywordstool?hintKeywords=...&showDetail=1
  헤더에 HMAC-SHA256 서명이 필요하다.

응답 항목(relKeyword 기준)
  relKeyword            연관키워드
  monthlyPcQcCnt        월간 PC 검색수   ('< 10' 같은 문자열로 올 수 있다)
  monthlyMobileQcCnt    월간 모바일 검색수
  compIdx               경쟁정도 (낮음/중간/높음)
  plAvgDepth            평균노출광고수
"""
import base64
import hashlib
import hmac
import random
import time
import urllib.parse
import urllib.request

from .. import config

BASE_URL = "https://api.naver.com"
URI = "/keywordstool"

stats = {"call": 0, "ok": 0, "fail": 0, "keywords": 0}


def available() -> bool:
    return config.naver_ready()


def _signature(timestamp: str, method: str, uri: str, secret: str) -> str:
    msg = f"{timestamp}.{method}.{uri}"
    digest = hmac.new(secret.encode("utf-8"), msg.encode("utf-8"),
                      hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def _headers(method: str, uri: str) -> dict:
    ts = str(round(time.time() * 1000))
    return {
        "Content-Type": "application/json; charset=UTF-8",
        "X-Timestamp": ts,
        "X-API-KEY": config.NAVER_ACCESS_KEY,
        "X-Customer": str(config.NAVER_CUSTOMER_ID),
        "X-Signature": _signature(ts, method, uri, config.NAVER_SECRET_KEY),
    }


def _to_int(v) -> int:
    """'< 10' 처럼 문자열로 오는 값을 숫자로."""
    if isinstance(v, int):
        return v
    s = str(v or "").replace(",", "").replace("<", "").strip()
    try:
        return int(s)
    except ValueError:
        return 0


def related(hint: str, timeout: int = 30, log=print) -> list:
    """
    힌트 키워드 하나로 연관키워드 조회.
    반환: [{keyword, pc, mobile, total, comp, depth}, ...] (검색량 내림차순)
    """
    if not available():
        log("[네이버] API 키가 없습니다 (.env 확인)")
        return []

    hint = (hint or "").strip().replace(" ", "")
    if not hint:
        return []

    params = {"hintKeywords": hint, "showDetail": "1"}
    url = f"{BASE_URL}{URI}?{urllib.parse.urlencode(params)}"
    stats["call"] += 1
    try:
        req = urllib.request.Request(url, headers=_headers("GET", URI))
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            import json
            data = json.load(resp)
    except Exception as e:
        stats["fail"] += 1
        log(f"[네이버] 조회 실패({hint}): {str(e)[:80]}")
        return []

    out = []
    for it in data.get("keywordList", []) or []:
        kw = (it.get("relKeyword") or "").strip()
        if not kw:
            continue
        pc = _to_int(it.get("monthlyPcQcCnt"))
        mo = _to_int(it.get("monthlyMobileQcCnt"))
        out.append({
            "keyword": kw, "pc": pc, "mobile": mo, "total": pc + mo,
            "comp": it.get("compIdx") or "",
            "depth": _to_int(it.get("plAvgDepth")),
        })
    out.sort(key=lambda x: -x["total"])
    stats["ok"] += 1
    stats["keywords"] += len(out)
    return out


def related_many(hints: list, limit: int = 0, log=print) -> dict:
    """
    여러 힌트로 모아서 조회. 힌트별로 API 를 한 번씩 호출하고 중복은 합친다.
    (네이버 API 는 요청당 힌트 5개까지 받지만, 결과 추적을 위해 하나씩 보낸다)

    반환: {키워드: {keyword, pc, mobile, total, comp, depth, hints:[...]}}
    """
    pool = {}
    for i, h in enumerate(hints, 1):
        rows = related(h, log=log)
        for r in rows:
            k = r["keyword"]
            if k in pool:
                pool[k]["hints"].append(h)
            else:
                pool[k] = dict(r, hints=[h])
        log(f"[네이버] '{h}' → {len(rows)}개 (누적 {len(pool)}개)")
        if limit and len(pool) >= limit:
            log(f"[네이버] 목표 {limit}개 도달 - 중단")
            break
        if i < len(hints):
            time.sleep(random.uniform(config.NAVER_DELAY_MIN,
                                      config.NAVER_DELAY_MAX))
    return pool
