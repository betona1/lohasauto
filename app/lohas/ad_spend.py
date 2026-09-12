"""
네이버 검색광고 지출 집계 — **LCP별 / 날짜별**.

이 계정은 광고그룹 이름이 곧 LCP 코드다(2026-09-11 실측: `쇼핑검색광고01`
아래 740개가 전부 `LCP_LHA_*`). 그래서 광고그룹 지출이 바로 LCP별 광고비가
된다. 따로 매핑표를 만들 필요가 없다.

**날짜별은 `breakdown` 을 쓰지 않고 하루씩 끊어 부른다.** `/stats` 에
`breakdown=day` 를 넣어도 200 은 오지만, 집행 내역이 0원이라 응답 생김새를
확인할 수가 없었다. 확인하지 못한 모양에 기대느니, 이미 확인된 호출
(`timeRange` 하루)을 날짜 수만큼 반복하는 편이 안전하다. 나중에 집행이
시작되어 `breakdown` 응답을 눈으로 보면 그때 줄이면 된다.

호출 수를 줄이려고 두 번에 나눠 훑는다.
  1) 기간 전체를 광고그룹 단위로 한 번 (740개 = 8콜) → **쓴 그룹만 추린다**
  2) 그 그룹들만 하루씩 (대개 몇 개뿐이다)
캠페인은 개수가 적어 늘 하루씩 받는다.
"""
import datetime

from .. import db
from . import searchad as sa

LEVEL_CAMP = "campaign"
LEVEL_GROUP = "adgroup"


def _ddl(c):
    c.execute("""CREATE TABLE IF NOT EXISTS ad_spend (
        customer_id TEXT NOT NULL,
        entity_id   TEXT NOT NULL,
        level       TEXT NOT NULL,       -- campaign | adgroup
        day         TEXT NOT NULL,       -- YYYY-MM-DD
        lcp_code    TEXT,                -- 광고그룹 이름이 LCP 코드면 그것
        name        TEXT,
        campaign_id TEXT,
        imp         INTEGER DEFAULT 0,
        clk         INTEGER DEFAULT 0,
        cost        INTEGER DEFAULT 0,   -- salesAmt (원)
        conv        INTEGER DEFAULT 0,   -- ccnt (전환수)
        conv_amt    INTEGER DEFAULT 0,   -- convAmt (전환매출)
        updated_at  TEXT,
        PRIMARY KEY (customer_id, entity_id, day)
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_ad_day ON ad_spend(day)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_ad_lcp ON ad_spend(lcp_code)")


def _lcp_of(name: str) -> str:
    n = (name or "").strip()
    return n if n.startswith("LCP_") else ""


def _save(rows: list):
    if not rows:
        return
    now = db.now_str()
    with db.sqlite_conn() as c:
        _ddl(c)
        c.executemany(
            "INSERT OR REPLACE INTO ad_spend (customer_id, entity_id, level,"
            " day, lcp_code, name, campaign_id, imp, clk, cost, conv,"
            " conv_amt, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(r["customer_id"], r["entity_id"], r["level"], r["day"],
              r["lcp_code"], r["name"], r["campaign_id"], r["imp"], r["clk"],
              r["cost"], r["conv"], r["conv_amt"], now) for r in rows])


def _rows(cu, meta: dict, level: str, day, stats: list) -> list:
    out = []
    for s in stats or []:
        eid = s.get("id")
        m = meta.get(eid, {})
        out.append({
            "customer_id": str(cu), "entity_id": eid, "level": level,
            "day": str(day), "lcp_code": _lcp_of(m.get("name")),
            "name": m.get("name") or "", "campaign_id": m.get("campaign") or "",
            "imp": int(s.get("impCnt") or 0), "clk": int(s.get("clkCnt") or 0),
            "cost": int(s.get("salesAmt") or 0), "conv": int(s.get("ccnt") or 0),
            "conv_amt": int(float(s.get("convAmt") or 0)),
        })
    return out


def collect(customer, since, until, log=print, groups_daily: bool = True,
            campaign_ids: list = None):
    """
    한 광고계정의 지출을 날짜별로 받아 저장한다. 반환 {days, rows, cost}.

    `campaign_ids` 를 주면 **그 캠페인과 그 아래 광고그룹만** 본다.
    캠페인 10개 중 실제로 쓰는 것은 하나뿐이라, 다 훑으면 헛돈다
    (2026-09-11 사용자).
    """
    camps = sa.campaigns(customer)
    if campaign_ids:
        want = set(campaign_ids)
        camps = [c for c in camps if c["nccCampaignId"] in want]
    cmeta = {c["nccCampaignId"]: {"name": c.get("name"), "campaign": ""}
             for c in camps}
    grps = sa.adgroups(customer)
    if campaign_ids:
        grps = [g for g in grps if g.get("nccCampaignId") in set(campaign_ids)]
    gmeta = {g["nccAdgroupId"]: {"name": g.get("name"),
                                 "campaign": g.get("nccCampaignId")}
             for g in grps}
    log(f"  [{customer}] 캠페인 {len(camps)} · 광고그룹 {len(grps)}")

    # 1) 기간 전체로 훑어 **쓴 광고그룹만** 추린다
    live = []
    if groups_daily and gmeta:
        agg = sa.stats(customer, list(gmeta), since, until)
        live = [s["id"] for s in agg
                if int(s.get("impCnt") or 0) or int(s.get("salesAmt") or 0)]
        log(f"  [{customer}] 기간 중 노출/지출이 있던 광고그룹 {len(live)}개")

    total = 0
    saved = 0
    day = since
    while day <= until:
        rows = _rows(customer, cmeta, LEVEL_CAMP, day,
                     sa.stats(customer, list(cmeta), day, day))
        if live:
            rows += _rows(customer, gmeta, LEVEL_GROUP, day,
                          sa.stats(customer, live, day, day))
        rows = [r for r in rows
                if r["imp"] or r["clk"] or r["cost"] or r["conv"]
                or r["conv_amt"]]
        _save(rows)
        saved += len(rows)
        total += sum(r["cost"] for r in rows if r["level"] == LEVEL_CAMP)
        day += datetime.timedelta(days=1)
    return {"days": (until - since).days + 1, "rows": saved, "cost": total}


def collect_all(days: int = 14, log=print, use_selection: bool = True) -> dict:
    """
    **화면에서 켠 계정·캠페인만** 돈다(`ad_account.selection`).
    아직 아무것도 고르지 않았으면 `.env` 의 계정 전부를 본다.
    """
    from . import ad_account

    until = datetime.date.today()
    since = until - datetime.timedelta(days=days - 1)
    picks = ad_account.selection() if use_selection else []
    if not picks:
        picks = [{"customer": cu, "label": cu, "campaigns": []}
                 for cu in sa.customers()]
    out = {"rows": 0, "cost": 0, "customers": []}
    for p in picks:
        cu = p["customer"]
        r = collect(cu, since, until, log=log,
                    campaign_ids=p.get("campaigns") or None)
        out["rows"] += r["rows"]
        out["cost"] += r["cost"]
        out["customers"].append({"customer": cu, **r})
        log(f"  [{cu}] {since}~{until}  기록 {r['rows']}행 · 지출 "
            f"{r['cost']:,}원")
    return out


# ---- 읽기 (화면용) ---------------------------------------------------
def _q(sql, args=()):
    with db.sqlite_conn() as c:
        _ddl(c)
        return [dict(r) for r in c.execute(sql, args)]


def today_cost(day: str = None) -> dict:
    """오늘(또는 지정일) 광고비. 캠페인 단위 합계가 계정 지출이다."""
    day = day or datetime.date.today().isoformat()
    r = _q("SELECT COALESCE(SUM(cost),0) cost, COALESCE(SUM(clk),0) clk,"
           " COALESCE(SUM(imp),0) imp, COALESCE(SUM(conv),0) conv,"
           " COALESCE(SUM(conv_amt),0) conv_amt,"
           " MAX(updated_at) upd FROM ad_spend"
           " WHERE day=? AND level=?", (day, LEVEL_CAMP))
    d = r[0] if r else {"cost": 0, "clk": 0, "imp": 0, "conv": 0,
                        "conv_amt": 0, "upd": ""}
    d["day"] = day
    return d


def by_day(days: int = 14) -> list:
    since = (datetime.date.today()
             - datetime.timedelta(days=days - 1)).isoformat()
    return _q("SELECT day, SUM(cost) cost, SUM(clk) clk, SUM(imp) imp,"
              " SUM(conv) conv, SUM(conv_amt) conv_amt"
              " FROM ad_spend WHERE level=? AND day>=?"
              " GROUP BY day ORDER BY day DESC", (LEVEL_CAMP, since))


def by_lcp(days: int = 14, limit: int = 50) -> list:
    since = (datetime.date.today()
             - datetime.timedelta(days=days - 1)).isoformat()
    return _q("SELECT lcp_code, SUM(cost) cost, SUM(clk) clk, SUM(imp) imp,"
              " SUM(conv) conv, SUM(conv_amt) conv_amt,"
              " COUNT(DISTINCT day) days FROM ad_spend"
              " WHERE level=? AND day>=? AND lcp_code<>''"
              " GROUP BY lcp_code ORDER BY cost DESC LIMIT ?",
              (LEVEL_GROUP, since, limit))
