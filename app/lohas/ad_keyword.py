"""
**실제로 돈이 나간 검색어**를 태그 작업에 먹인다.

지금까지 태그는 로하스가 주는 조회수만 보고 골랐다. 조회수는 '얼마나
찾는가' 일 뿐, **그 상품이 그 말로 팔리는가**는 아니다. 광고 대용량
보고서에는 그 답이 들어 있다 — 어떤 검색어로 들어와 클릭했고 얼마를 썼는지.

⚠️ **절대규칙은 그대로다.** 태그는 여전히 **로하스 태그 후보 표에서만**
고른다. 여기서 하는 일은 고르는 게 아니라 **순서를 바꾸는 것**이다.
보고서에 있는 말이라고 해서 후보에 없는 것을 끼워 넣지 않는다.

    실적 있는 검색어  →  같은 말이 후보에 있으면 앞으로 당긴다
    후보에 없으면     →  '놓친 검색어' 로 따로 보여만 준다 (사람이 판단)

집계 단위는 두 가지다.
    LCP      그 상품이 실제로 먹은 검색어 (제일 정확하다)
    카테고리 그 LCP 에 실적이 없을 때 쓰는 보조. 같은 카테고리 상품들의
             검색어를 모은다
"""
import re

from .. import db

MIN_CLICK = 1          # 클릭이 한 번이라도 있어야 의미가 있다
_SPACE = re.compile(r"\s+")


def _norm(s: str) -> str:
    return _SPACE.sub("", (s or "")).upper()


def by_lcp(lcp_code: str, days: int = 30) -> dict:
    """{정규화한 검색어: {'query','cost','clk','imp'}} — 비용 많은 순."""
    import datetime
    since = (datetime.date.today()
             - datetime.timedelta(days=days)).isoformat()
    out = {}
    with db.sqlite_conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS ad_detail (
            customer_id TEXT, day TEXT, campaign_id TEXT, adgroup_id TEXT,
            lcp_code TEXT, query TEXT, ad_id TEXT, channel_id TEXT,
            device TEXT, hour TEXT, region TEXT, place TEXT,
            imp INTEGER, clk INTEGER, cost INTEGER, conv INTEGER)""")
        for r in c.execute(
                "SELECT query, SUM(cost) cost, SUM(clk) clk, SUM(imp) imp"
                " FROM ad_detail WHERE lcp_code=? AND day>=?"
                " GROUP BY query ORDER BY cost DESC", (lcp_code, since)):
            if not r["query"]:
                continue
            out[_norm(r["query"])] = {
                "query": r["query"], "cost": int(r["cost"] or 0),
                "clk": int(r["clk"] or 0), "imp": int(r["imp"] or 0)}
    return out


def by_category(cid: str, days: int = 30, limit: int = 300) -> dict:
    """그 카테고리 LCP 들이 먹은 검색어를 모은다 (LCP 실적이 없을 때)."""
    import datetime
    if not cid:
        return {}
    since = (datetime.date.today()
             - datetime.timedelta(days=days)).isoformat()
    out = {}
    with db.sqlite_conn() as c:
        for r in c.execute(
                "SELECT d.query, SUM(d.cost) cost, SUM(d.clk) clk,"
                " SUM(d.imp) imp FROM ad_detail d"
                " JOIN lcode_attr a ON a.lcp_code = d.lcp_code"
                " WHERE a.etc_category = ? AND d.day >= ? AND d.query <> ''"
                " GROUP BY d.query ORDER BY cost DESC LIMIT ?",
                (str(cid), since, limit)):
            out[_norm(r["query"])] = {
                "query": r["query"], "cost": int(r["cost"] or 0),
                "clk": int(r["clk"] or 0), "imp": int(r["imp"] or 0)}
    return out


def perf_of(name: str, perf: dict) -> dict:
    """
    후보 이름이 실적 검색어와 맞는지. 완전일치 우선, 없으면 포함관계.

    '포토카드홀더' 와 '투명포카홀더' 처럼 표기가 다른 경우가 많아 완전일치만
    보면 대부분 놓친다. 다만 **짧은 말이 긴 말에 우연히 들어가는 것**은
    막는다 - 2글자는 완전일치만 인정한다.
    """
    k = _norm(name)
    if not k:
        return {}
    if k in perf:
        return perf[k]
    if len(k) < 3:
        return {}
    best = {}
    for q, v in perf.items():
        if (k in q or q in k) and v["cost"] > best.get("cost", -1):
            best = v
    return best


def boost(cands: list, perf: dict) -> list:
    """
    후보 목록에 실적을 붙인다. **골라내지 않는다.** 순서만 바꾼다.

    각 후보에 `ad_cost`/`ad_clk` 를 달고, 실적이 있는 것을 앞으로 당긴다.
    실적이 없는 것들끼리의 순서는 원래대로 둔다(로하스 지침이 그대로 산다).
    """
    if not perf:
        return cands
    rank = {id(c): i for i, c in enumerate(cands)}
    for c in cands:
        v = perf_of(c.get("name") or c.get("relKeyword") or "", perf)
        c["ad_cost"] = v.get("cost", 0)
        c["ad_clk"] = v.get("clk", 0)
        c["ad_query"] = v.get("query", "")
    return sorted(cands,
                  key=lambda c: (0 if c.get("ad_clk") else 1,
                                 -(c.get("ad_cost") or 0),
                                 rank[id(c)]))


def missed(cands: list, perf: dict, top: int = 20) -> list:
    """
    **후보에 없는데 돈은 나간 검색어.** 넣지는 않고 보여만 준다.

    후보 표에 없는 말을 태그로 넣는 것은 절대규칙 위반이라, 사람이 보고
    판단할 몫이다. 로하스 태그사전에 없는 말이면 애초에 저장도 안 된다.
    """
    have = {_norm(c.get("name") or c.get("relKeyword") or "") for c in cands}
    out = []
    for q, v in perf.items():
        if v["clk"] < MIN_CLICK:
            continue
        if q in have or any(q in h or h in q for h in have if len(h) >= 3):
            continue
        out.append(v)
    out.sort(key=lambda v: -v["cost"])
    return out[:top]


def summary(lcp_code: str, days: int = 30) -> str:
    p = by_lcp(lcp_code, days)
    if not p:
        return ""
    top = sorted(p.values(), key=lambda v: -v["cost"])[:5]
    return " · ".join(f"{v['query']}({v['clk']}클릭)" for v in top)
