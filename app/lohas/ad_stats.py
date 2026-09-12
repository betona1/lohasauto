"""
광고 성과 집계 — 보고서 창이 쓰는 숫자를 한곳에서 만든다.

세 곳에서 온다.
    ad_spend    광고비·클릭·노출  (`/stats`, **오늘 포함**)
    ad_detail   매체·검색어·소재별 (대용량 보고서, **어제까지**)
    ad_sales    실매출            (커머스 API, 실결제금액)

`ad_detail` 이 어제까지뿐이라 **매체별 합계는 총 광고비보다 작다.** 그건
자료가 늦는 것이지 빠진 게 아니다 — 화면에 그렇게 적어 둔다.
"""
import datetime

from .. import db
from . import ad_account, ad_detail, ad_sales, ad_spend


def _q(sql, args=()):
    with db.sqlite_conn() as c:
        return [dict(r) for r in c.execute(sql, args)]


def _since(days: int, customer: str = "") -> str:
    """
    시작 날짜. **광고 시작일보다 앞으로 가지 않는다.**

    시작 전에는 광고를 안 했으니 그때 난 매출은 광고 성과가 아니다.
    같이 세면 ROAS 가 부풀려진다 — 14일로 보면 101%, 집행일만 보면 75%
    였다(2026-09-12 사용자: 광고 시작일은 9월 10일).
    """
    s = (datetime.date.today() - datetime.timedelta(days=days - 1)).isoformat()
    st = ad_account.start_of(customer)
    return max(s, st) if st else s


def _days_of(days: int, customer: str = "") -> int:
    """시작일로 잘린 뒤 실제 며칠인지."""
    s = datetime.date.fromisoformat(_since(days, customer))
    return (datetime.date.today() - s).days + 1


def summary(days: int = 7, customer: str = "") -> dict:
    """기간 전체 요약."""
    s = _since(days, customer)
    a = _q("SELECT COALESCE(SUM(cost),0) cost, COALESCE(SUM(clk),0) clk,"
           " COALESCE(SUM(imp),0) imp, COALESCE(SUM(conv),0) conv,"
           " COALESCE(SUM(conv_amt),0) conv_amt, COUNT(DISTINCT day) days"
           " FROM ad_spend WHERE level='campaign' AND day>=?", (s,))[0]
    b = _q("SELECT COALESCE(SUM(amount),0) amount, COALESCE(SUM(orders),0)"
           " orders, COALESCE(SUM(qty),0) qty FROM ad_sales WHERE day>=?",
           (s,))[0]
    cost = int(a["cost"] or 0)
    amt = int(b["amount"] or 0)
    clk = int(a["clk"] or 0)
    return {
        "days": days, "since": s, "eff_days": _days_of(days, customer),
        "start": ad_account.start_of(customer), "cost": cost, "clk": clk,
        "imp": int(a["imp"] or 0), "conv_amt": int(a["conv_amt"] or 0),
        "amount": amt, "orders": int(b["orders"] or 0),
        "qty": int(b["qty"] or 0),
        "roas": (amt * 100 // cost) if cost else 0,
        "cpc": (cost // clk) if clk else 0,
        "ctr": round(clk * 100 / int(a["imp"] or 1), 2),
        "spend_days": int(a["days"] or 0),
    }


def by_day(days: int = 7, customer: str = "") -> list:
    """날짜별 광고비 + 실매출. 광고 시작일 이후만."""
    s = _since(days, customer)
    cost = {r["day"]: r for r in ad_spend.by_day(days)}
    sale = {r["day"]: r for r in ad_sales.by_day(days)}
    out = []
    d = datetime.date.fromisoformat(s)
    while d <= datetime.date.today():
        k = d.isoformat()
        c = cost.get(k, {})
        v = sale.get(k, {})
        cst = int(c.get("cost") or 0)
        amt = int(v.get("amount") or 0)
        out.append({"day": k, "cost": cst, "clk": int(c.get("clk") or 0),
                    "imp": int(c.get("imp") or 0), "amount": amt,
                    "orders": int(v.get("orders") or 0),
                    "roas": (amt * 100 // cst) if cst else 0})
        d += datetime.timedelta(days=1)
    return out


def by_media(days: int = 7, customer: str = "", daily: bool = False) -> list:
    """매체별 (원하면 날짜별로도)."""
    rows = (ad_detail.by_day("media", customer, days=days, limit=2000)
            if daily else ad_detail.by("media", customer, days=days, limit=20))
    for r in rows:
        r["name"] = ad_detail.media_name(r["k"])
    return rows


def top(days: int = 7, by: str = "lcp", limit: int = 10,
        customer: str = "") -> list:
    """
    광고비 TOP N. `by` 는 'lcp' 또는 'ad'(소재).

    실매출을 함께 붙인다 — 돈만 쓰고 안 팔리는 것을 가려내는 게 목적이다.
    """
    s = _since(days, customer)
    if by == "ad":
        rows = _q("SELECT ad_id k, SUM(cost) cost, SUM(clk) clk, SUM(imp) imp"
                  " FROM ad_detail WHERE day>=? AND ad_id<>''"
                  " GROUP BY ad_id ORDER BY cost DESC LIMIT ?", (s, limit))
        meta = {r["ad_id"]: r for r in _q(
            "SELECT ad_id, lcp_code, l_code, product_name, status_ko"
            " FROM ad_creative")}
        for r in rows:
            m = meta.get(r["k"], {})
            r["name"] = (m.get("product_name") or "")[:40]
            r["lcp_code"] = m.get("lcp_code") or ""
            r["l_code"] = m.get("l_code") or ""
            r["status"] = m.get("status_ko") or ""
    else:
        rows = _q("SELECT lcp_code k, SUM(cost) cost, SUM(clk) clk,"
                  " SUM(imp) imp FROM ad_spend WHERE level='adgroup'"
                  " AND day>=? AND lcp_code<>'' GROUP BY lcp_code"
                  " ORDER BY cost DESC LIMIT ?", (s, limit))
        nm = lcp_names()
        for r in rows:
            r["lcp_code"] = r["k"]
            r["name"] = nm.get(r["k"], r["k"])
    sale = {r["lcp_code"]: r for r in _q(
        "SELECT lcp_code, SUM(amount) amount, SUM(orders) orders"
        " FROM ad_sales WHERE day>=? GROUP BY lcp_code", (s,))}
    for r in rows:
        v = sale.get(r.get("lcp_code") or "", {})
        r["amount"] = int(v.get("amount") or 0)
        r["orders"] = int(v.get("orders") or 0)
        r["roas"] = (r["amount"] * 100 // r["cost"]) if r["cost"] else 0
    return rows


def lcp_names() -> dict:
    """
    LCP 코드 → 대표 상품명.

    광고 소재(`ad_creative`)의 상품명을 쓴다. 한 LCP 에 여러 소재가 있으면
    **가장 짧은 것**을 고른다 — 옵션이 덜 붙어 품목이 잘 드러난다
    (2026-09-12 사용자: 코드만 보면 뭔지 모른다).
    """
    out = {}
    for r in _q("SELECT lcp_code, product_name FROM ad_creative"
                " WHERE lcp_code<>'' AND product_name<>''"):
        k = r["lcp_code"]
        n = r["product_name"]
        if k not in out or len(n) < len(out[k]):
            out[k] = n
    # 소재가 없는 LCP 는 로하스 상품명으로 메운다
    for r in _q("SELECT lcp_code, MIN(title1) nm FROM lcode_attr"
                " WHERE lcp_code<>'' AND title1<>'' GROUP BY lcp_code"):
        out.setdefault(r["lcp_code"], r["nm"])
    return out


def by_lcp(days: int = 7, limit: int = 300, customer: str = "") -> list:
    """LCP별 광고비 + 실매출 + ROAS (매출 0원인 것도 포함). 상품명 포함."""
    st = ad_account.start_of(customer)
    if st:
        eff = (datetime.date.today()
               - datetime.date.fromisoformat(st)).days + 1
        days = min(days, eff)
    rows = ad_sales.by_lcp(days, limit)
    nm = lcp_names()
    for r in rows:
        r["name"] = nm.get(r["lcp_code"], "")
    return rows


def by_dim(dim: str, days: int = 7, customer: str = "",
           limit: int = 20) -> list:
    rows = ad_detail.by(dim, customer, days=days, limit=limit)
    for r in rows:
        k = r["k"]
        r["name"] = (ad_detail.DEVICE.get(k, k) if dim == "device"
                     else ad_detail.media_name(k) if dim == "media"
                     else f"{k}시" if dim == "hour" else k)
    return rows
