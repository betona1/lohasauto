"""
광고 소재(쇼핑 상품형) — **검토 상태와 로하스 L코드 연결**.

소재 하나가 스마트스토어 상품 하나다. 소재 응답에 그 상품의 번호가
그대로 들어 있어서(2026-09-12 실측) 로하스 L코드와 맞출 수 있다.

    nccAdId        nad-a001-02-000000580138716
    inspectStatus  APPROVED / UNDER_REVIEW / REJECTED  ← 보류는 여기
    ad.productName 상품명
    referenceKey   91286147901        (쇼핑 상품 ID)
    referenceData  mallProductId      13741636537   ← 스마트스토어 상품번호
                   mallProductUrl     .../products/13741636537
                   prodStatusCd       P02015 같은 상태 코드

광고그룹 이름이 곧 LCP 코드라 **그룹 → LCP** 는 바로 붙고, 그 LCP 의
L코드 중에서 **상품명이 가장 비슷한 것**을 골라 소재와 짝지운다.

소재 목록은 광고그룹마다 따로 불러야 한다 — 한 번에 주는 API 가 없다.
`Ad` 마스터 보고서도 만들어 봤지만 계속 `status=NONE` 이었다.
"""
import collections
import re

from .. import db
from . import searchad as sa

# 화면에 뜨는 말로 옮긴다
STATUS = {
    "APPROVED": "승인",
    "ELIGIBLE": "노출가능",
    # **PENDING 이 화면의 「소재 보류」다.** 사용자가 알려준 보류 알림
    # 10건(nad-...580163355 등)이 전부 PENDING 이었다(2026-09-12).
    "PENDING": "보류",
    "UNDER_REVIEW": "검토중",
    "REJECTED": "거절",
    "NOT_YET_RECEIVED": "미접수",
}
BAD = ("PENDING", "REJECTED", "UNDER_REVIEW", "NOT_YET_RECEIVED")

_WORD = re.compile(r"[0-9A-Za-z가-힣]+")


def _ddl(c):
    c.execute("""CREATE TABLE IF NOT EXISTS ad_creative (
        customer_id TEXT NOT NULL,
        ad_id       TEXT NOT NULL,
        adgroup_id  TEXT, campaign_id TEXT,
        lcp_code    TEXT,
        l_code      TEXT,               -- 상품명으로 맞춘 로하스 L코드
        match_score REAL,
        status      TEXT,               -- inspectStatus 원문
        status_ko   TEXT,
        enabled     INTEGER,
        product_name TEXT,
        mall_product_id TEXT,           -- 스마트스토어 상품번호
        product_url TEXT,
        bid         INTEGER,
        updated_at  TEXT,
        PRIMARY KEY (customer_id, ad_id)
    )""")
    for q in ("CREATE INDEX IF NOT EXISTS idx_adc_st ON ad_creative(status)",
              "CREATE INDEX IF NOT EXISTS idx_adc_lcp ON ad_creative(lcp_code)",
              "CREATE INDEX IF NOT EXISTS idx_adc_l ON ad_creative(l_code)"):
        c.execute(q)


def _words(s: str) -> set:
    return set(_WORD.findall((s or "").upper()))


def _match(name: str, cands: list) -> tuple:
    """상품명이 가장 비슷한 L코드. 반환 (l_code, 점수 0~1)."""
    w = _words(name)
    if not w:
        return "", 0.0
    best, score = "", 0.0
    for r in cands:
        o = _words(r.get("product_name") or r.get("title1") or "")
        if not o:
            continue
        s = len(w & o) / len(w | o)
        if s > score:
            best, score = r["l_code"], s
    return best, round(score, 3)


def _lcode_pool(lcp: str) -> list:
    with db.sqlite_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT l_code, title1 FROM lcode_attr WHERE lcp_code=?", (lcp,))]


def collect(customer, log=print, only_lcp: str = "",
            progress=None) -> dict:
    """광고그룹을 돌며 소재를 받아 저장한다. 읽기 전용이다."""
    groups = sa.adgroups(customer)
    if only_lcp:
        groups = [g for g in groups if (g.get("name") or "") == only_lcp]
    log(f"광고그룹 {len(groups):,}개")
    now = db.now_str()
    pools, rows, cnt = {}, [], collections.Counter()
    for i, g in enumerate(groups, 1):
        lcp = (g.get("name") or "").strip()
        try:
            ads = sa.ads(customer, g["nccAdgroupId"])
        except Exception as e:
            log(f"  {lcp} !! {str(e)[:50]}")
            continue
        if lcp and lcp not in pools:
            pools[lcp] = _lcode_pool(lcp) if lcp.startswith("LCP_") else []
        for a in ads:
            ref = a.get("referenceData") or {}
            nm = (a.get("ad") or {}).get("productName") or \
                ref.get("productTitle") or ""
            st = a.get("inspectStatus") or ""
            cnt[st] += 1
            lc, sc = _match(nm, pools.get(lcp, []))
            rows.append((str(customer), a["nccAdId"], a.get("nccAdgroupId"),
                         g.get("nccCampaignId"), lcp, lc, sc, st,
                         STATUS.get(st, st), 1 if a.get("enable") else 0,
                         nm, str(ref.get("mallProductId") or ""),
                         ref.get("mallProductUrl") or "",
                         int((a.get("adAttr") or {}).get("bidAmt") or 0), now))
        if progress:
            progress(i, len(groups))
        if i % 100 == 0:
            log(f"  {i}/{len(groups)} 그룹 · 소재 {len(rows):,}개")
    if rows:
        with db.sqlite_conn() as c:
            _ddl(c)
            c.executemany(
                "INSERT OR REPLACE INTO ad_creative (customer_id, ad_id,"
                " adgroup_id, campaign_id, lcp_code, l_code, match_score,"
                " status, status_ko, enabled, product_name, mall_product_id,"
                " product_url, bid, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    log("상태별 — " + " · ".join(f"{STATUS.get(k, k)} {v:,}"
                               for k, v in cnt.most_common()))
    return {"groups": len(groups), "ads": len(rows), "by_status": dict(cnt)}


# ---- 읽기 -----------------------------------------------------------
def _q(sql, args=()):
    with db.sqlite_conn() as c:
        _ddl(c)
        return [dict(r) for r in c.execute(sql, args)]


def problems(customer="") -> list:
    """승인 안 된 소재 — 보류·검토중·대기."""
    sql = ("SELECT * FROM ad_creative WHERE status IN (%s)"
           % ",".join("?" * len(BAD)))
    args = list(BAD)
    if customer:
        sql += " AND customer_id=?"
        args.append(str(customer))
    sql += " ORDER BY status, lcp_code"
    return _q(sql, args)


def summary(customer="", days: int = 7) -> dict:
    """
    대시보드용 광고 규모 — **등록 기준과 실제 돈 것을 나눠서** 센다.

    등록만 많고 노출이 안 되면 의미가 없다. 보류 소재는 광고가 아예
    안 나간다(2026-09-12 사용자: 광고되는 총 수량이 보여야 한다).
    """
    import datetime
    since = (datetime.date.today()
             - datetime.timedelta(days=days)).isoformat()
    cu = str(customer or "")
    w, a = ("", []) if not cu else (" AND customer_id=?", [cu])
    with db.sqlite_conn() as c:
        _ddl(c)

        def one(sql, args=()):
            try:
                return c.execute(sql, args).fetchone()[0] or 0
            except Exception:
                return 0

        return {
            "lcp": one("SELECT COUNT(*) FROM ad_group WHERE lcp_code<>''"
                       + w, a),
            "ads": one("SELECT COUNT(*) FROM ad_creative WHERE 1=1" + w, a),
            "ads_ok": one("SELECT COUNT(*) FROM ad_creative WHERE status IN"
                          " ('APPROVED','ELIGIBLE')" + w, a),
            "ads_hold": one("SELECT COUNT(*) FROM ad_creative WHERE status IN"
                            " ('PENDING','REJECTED','UNDER_REVIEW',"
                            "'NOT_YET_RECEIVED')" + w, a),
            "live_lcp": one("SELECT COUNT(DISTINCT lcp_code) FROM ad_detail"
                            " WHERE day>=? AND lcp_code<>''", [since]),
            "paid_lcp": one("SELECT COUNT(DISTINCT lcp_code) FROM ad_detail"
                            " WHERE day>=? AND lcp_code<>'' AND cost>0",
                            [since]),
            "live_ads": one("SELECT COUNT(DISTINCT ad_id) FROM ad_detail"
                            " WHERE day>=?", [since]),
            "queries": one("SELECT COUNT(DISTINCT query) FROM ad_detail"
                           " WHERE day>=? AND query<>''", [since]),
            "days": days,
        }


def counts(customer="") -> list:
    sql = "SELECT status_ko, COUNT(*) n FROM ad_creative"
    args = []
    if customer:
        sql += " WHERE customer_id=?"
        args.append(str(customer))
    sql += " GROUP BY status_ko ORDER BY n DESC"
    return _q(sql, args)
