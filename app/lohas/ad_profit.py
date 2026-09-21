"""
**실수입** — 매출에서 원가·배송비·광고비를 뺀 진짜 남는 돈.

매출만 보면 잘 판 것처럼 보인다. 원가가 90%인 상품은 많이 팔아도 남는
게 없다. 주문 DB 에 원가와 정산금액이 다 있으니 그걸로 센다
(2026-09-16 사용자).

    정산금액   settlement_price      네이버가 실제로 주는 돈 (수수료 뺀 뒤)
    원가       owner_supply_price    공급가. 비어 있으면 supply_price
    배송비     real_shipping_fee     우리가 실제로 낸 배송비
    광고비     ad_spend              그 LCP·그 날 쓴 광고비

    이익   = 정산금액 − 원가 − 실배송비
    실수입 = 이익 − 광고비

정산금액이 비면 결제금액에서 네이버 수수료를 어림잡지 않고 **결제금액을
그대로** 쓴다 — 없는 숫자를 지어내지 않는다. 그런 건 표에 표시한다.
"""
import datetime

from .. import config, db
from . import ad_sales

FEE_NOTE = "정산금액 없음(결제금액으로 셈)"


def _conn():
    import pymysql
    return pymysql.connect(
        host=config._str("ORDER_DB_HOST") or config._str("DB_HOST"),
        port=int(config._str("ORDER_DB_PORT")
                 or config._str("DB_PORT", "3306")),
        user=config._str("ORDER_DB_USER") or config._str("DB_USER"),
        password=(config._str("ORDER_DB_PASSWORD")
                  or config._str("DB_PASSWORD")).strip("'\""),
        database=config._str("ORDER_DB_NAME") or "joacham",
        charset="utf8mb4", connect_timeout=10,
        cursorclass=__import__("pymysql").cursors.DictCursor)


def _ddl(c):
    c.execute("""CREATE TABLE IF NOT EXISTS order_profit (
        day          TEXT NOT NULL,
        product_code TEXT NOT NULL,
        name         TEXT,
        lcp_code     TEXT,
        kind         TEXT,              -- 광고상품 / 로하스-비광고 / W코드
        orders       INTEGER DEFAULT 0,
        qty          INTEGER DEFAULT 0,
        paid         INTEGER DEFAULT 0,   -- 결제금액
        settle       INTEGER DEFAULT 0,   -- 정산금액
        cost         INTEGER DEFAULT 0,   -- 원가
        ship         INTEGER DEFAULT 0,   -- 실배송비
        profit       INTEGER DEFAULT 0,   -- 정산 − 원가 − 배송
        no_settle    INTEGER DEFAULT 0,   -- 정산금액이 없던 건수
        updated_at   TEXT,
        PRIMARY KEY (day, product_code)
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_prof_day ON order_profit(day)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_prof_lcp"
              " ON order_profit(lcp_code)")
    # 예전 표에는 kind 가 없다. 있으면 넘어간다
    try:
        c.execute("ALTER TABLE order_profit ADD COLUMN kind TEXT")
    except Exception:
        pass


def collect(days: int = 30, log=print) -> dict:
    """주문 DB 에서 원가·정산을 읽어 상품·날짜별 이익을 쌓는다."""
    since = datetime.date.today() - datetime.timedelta(days=days - 1)
    dead = ",".join(["%s"] * len(ad_sales.DEAD))
    sql = (
        "SELECT order_date d, product_code, MAX(product_name) nm,"
        " COUNT(*) n, SUM(quantity) qty,"
        " SUM(COALESCE(total_payment_price,0)) paid,"
        " SUM(COALESCE(NULLIF(settlement_price,0),"
        "              COALESCE(total_payment_price,0))) settle,"
        " SUM(COALESCE(NULLIF(owner_supply_price,0),"
        "              COALESCE(supply_price,0))) cost,"
        " SUM(COALESCE(real_shipping_fee,0)) ship,"
        " SUM(CASE WHEN COALESCE(settlement_price,0)=0 THEN 1 ELSE 0 END) ns"
        f" FROM {ad_sales.TABLE}"
        " WHERE order_date>=%s AND site_name LIKE %s AND seller_alias LIKE %s"
        f" AND (order_status IS NULL OR order_status NOT IN ({dead}))"
        " GROUP BY order_date, product_code")
    cn = _conn()
    try:
        with cn.cursor() as c:
            c.execute(sql, [since, f"%{ad_sales.SITE}%",
                            ad_sales.store_like(), *ad_sales.DEAD])
            rows = c.fetchall()
    finally:
        cn.close()

    cmap = ad_sales._code_map()
    now = db.now_str()
    # **광고상품인지 가른다.** 광고비는 광고상품에만 썼는데 이익은 가게
    # 전체를 세면 성과가 부풀거나 깎인다. 9/11 은 가게 전체 이익이 −74원,
    # 광고상품만 보면 +4,820원이었다(2026-09-16 실측).
    mg = ad_sales.mgmt_codes(sorted({str(r["product_code"] or "")
                                     for r in rows}))
    out = []
    for r in rows:
        code = str(r["product_code"] or "")
        settle = int(r["settle"] or 0)
        cost = int(r["cost"] or 0)
        ship = int(r["ship"] or 0)
        out.append((str(r["d"]), code, r["nm"] or "",
                    (cmap.get(code) or ("", ""))[0],
                    ad_sales.kind_of(mg.get(code, ""), code in cmap),
                    int(r["n"] or 0),
                    int(r["qty"] or 0), int(r["paid"] or 0), settle, cost,
                    ship, settle - cost - ship, int(r["ns"] or 0), now))
    with db.sqlite_conn() as c:
        _ddl(c)
        c.execute("DELETE FROM order_profit WHERE day>=?",
                  (since.isoformat(),))
        c.executemany(
            "INSERT OR REPLACE INTO order_profit (day, product_code, name,"
            " lcp_code, kind, orders, qty, paid, settle, cost, ship, profit,"
            " no_settle, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            out)
    tot = sum(x[11] for x in out)
    adp = sum(x[11] for x in out if x[4] == "광고상품")
    log(f"이익 {len(out):,}행 · 정산 {sum(x[8] for x in out):,}원 · "
        f"원가 {sum(x[9] for x in out):,}원 · 이익 {tot:,}원"
        f" (광고상품 {adp:,}원)")
    return {"rows": len(out), "profit": tot, "profit_ad": adp,
            "settle": sum(x[8] for x in out),
            "cost": sum(x[9] for x in out)}


# ---- 읽기 -----------------------------------------------------------
def _q(sql, a=()):
    with db.sqlite_conn() as c:
        _ddl(c)
        return [dict(r) for r in c.execute(sql, a)]


# **광고상품만 볼 것인가.** 광고비는 광고상품에만 나간다. 그래서 실수입은
# 광고상품 이익에서 광고비를 빼는 게 맞다. 가게 전체를 보고 싶으면 끈다.
AD_ONLY = " AND kind='광고상품'"


def last_collected() -> str:
    r = _q("SELECT MAX(updated_at) u FROM order_profit")
    return (r[0]["u"] or "") if r else ""


def covered_until() -> str:
    r = _q("SELECT MAX(day) d FROM order_profit")
    return (r[0]["d"] or "") if r else ""


def by_day(since: str, until: str, ad_only: bool = True) -> list:
    """날짜별 이익 + 그날 광고비 → 실수입."""
    w = AD_ONLY if ad_only else ""
    prof = {r["day"]: r for r in _q(
        "SELECT day, SUM(orders) orders, SUM(qty) qty, SUM(paid) paid,"
        " SUM(settle) settle, SUM(cost) cost, SUM(ship) ship,"
        " SUM(profit) profit, SUM(no_settle) ns FROM order_profit"
        " WHERE day>=? AND day<=?" + w + " GROUP BY day", (since, until))}
    cost = {r["day"]: int(r["cost"] or 0) for r in _q(
        "SELECT day, SUM(cost) cost FROM ad_spend"
        " WHERE level='campaign' AND day>=? AND day<=? GROUP BY day",
        (since, until))}
    out = []
    d = datetime.date.fromisoformat(since)
    end = datetime.date.fromisoformat(until)
    while d <= end:
        k = d.isoformat()
        p = prof.get(k, {})
        ad = cost.get(k, 0)
        pr = int(p.get("profit") or 0)
        out.append({"day": k, "orders": int(p.get("orders") or 0),
                    "qty": int(p.get("qty") or 0),
                    "paid": int(p.get("paid") or 0),
                    "settle": int(p.get("settle") or 0),
                    "cost": int(p.get("cost") or 0),
                    "ship": int(p.get("ship") or 0),
                    "profit": pr, "ad": ad, "net": pr - ad,
                    "ns": int(p.get("ns") or 0)})
        d += datetime.timedelta(days=1)
    return out


def by_item(since: str, until: str, limit: int = 300,
            ad_only: bool = False) -> list:
    """상품별 이익 (광고비는 LCP 단위라 여기선 참고로만 붙인다)."""
    rows = _q(
        "SELECT product_code, MAX(name) name, MAX(lcp_code) lcp_code,"
        " MAX(kind) kind,"
        " SUM(orders) orders, SUM(qty) qty, SUM(paid) paid,"
        " SUM(settle) settle, SUM(cost) cost, SUM(ship) ship,"
        " SUM(profit) profit FROM order_profit"
        " WHERE day>=? AND day<=?" + (AD_ONLY if ad_only else "")
        + " GROUP BY product_code"
        " ORDER BY profit DESC LIMIT ?", (since, until, limit))
    ad = {r["lcp_code"]: int(r["c"] or 0) for r in _q(
        "SELECT lcp_code, SUM(cost) c FROM ad_spend WHERE level='adgroup'"
        " AND day>=? AND day<=? AND lcp_code<>'' GROUP BY lcp_code",
        (since, until))}
    for r in rows:
        r["ad"] = ad.get(r["lcp_code"] or "", 0)
        r["net"] = r["profit"] - r["ad"]
        r["margin"] = (r["profit"] * 100 // r["settle"]) if r["settle"] else 0
    return rows


def split(since: str, until: str) -> list:
    """구분(광고상품/비광고/W코드)별 이익 — 어디서 남았는지."""
    return _q(
        "SELECT COALESCE(NULLIF(kind,''),'기타') kind, SUM(orders) orders,"
        " SUM(qty) qty, SUM(settle) settle, SUM(cost) cost,"
        " SUM(profit) profit FROM order_profit WHERE day>=? AND day<=?"
        " GROUP BY 1 ORDER BY profit DESC", (since, until))


def summary(since: str, until: str, ad_only: bool = True) -> dict:
    d = by_day(since, until, ad_only)
    s = {k: sum(x[k] for x in d) for k in
         ("orders", "qty", "paid", "settle", "cost", "ship", "profit",
          "ad", "net", "ns")}
    s["margin"] = (s["profit"] * 100 // s["settle"]) if s["settle"] else 0
    s["net_margin"] = (s["net"] * 100 // s["settle"]) if s["settle"] else 0
    return s


def breakeven(since: str, until: str, ad_only: bool = True) -> dict:
    """
    **수익이 나려면 ROAS 가 얼마여야 하는가.**

    실수입 = 이익 − 광고비 이고, 이익 = 정산 × 마진율 이다.
    실수입이 0 이 되는 지점은

        정산 × 마진율 = 광고비
        정산 ÷ 광고비 = 1 ÷ 마진율          ← 이게 손익분기 ROAS

    마진율 21% 면 ROAS 476% 가 되어야 본전이다. ROAS 144% 는 좋아 보여도
    적자다(2026-09-16 실측).

    ROAS 를 결제금액으로 재는지 정산금액으로 재는지에 따라 기준이 달라져
    둘 다 돌려준다.
    """
    s = summary(since, until, ad_only)
    settle, paid, ad = s["settle"], s["paid"], s["ad"]
    margin = (s["profit"] / settle) if settle else 0.0
    be_settle = (100 / margin) if margin > 0 else 0
    # 결제금액 기준으로 환산 — 정산은 수수료를 뺀 값이라 결제보다 작다
    ratio = (paid / settle) if settle else 1.0
    return {
        "margin": round(margin * 100, 1),
        "be_roas": int(round(be_settle)) if be_settle else 0,
        "be_roas_paid": int(round(be_settle * ratio)) if be_settle else 0,
        "roas": int(paid * 100 // ad) if ad else 0,
        "roas_settle": int(settle * 100 // ad) if ad else 0,
        "settle": settle, "paid": paid, "ad": ad,
        "profit": s["profit"], "net": s["net"],
        "fee_rate": round((1 - (settle / paid)) * 100, 1) if paid else 0.0,
        # 지금 ROAS 로 본전 내려면 광고비를 얼마까지 줄여야 하나
        "ad_budget": int(s["profit"]),
    }


def tip(since: str, until: str, ad_only: bool = True) -> str:
    """ROAS 카드에 붙일 설명 (마우스 올렸을 때)."""
    b = breakeven(since, until, ad_only)
    NL = chr(10)
    if not b["ad"]:
        return "광고비가 없어 계산할 수 없습니다."
    ok = b["roas_settle"] >= b["be_roas"]
    return (
        f"평균 마진율 {b['margin']}%  (이익 {b['profit']:,}원 ÷ 정산 "
        f"{b['settle']:,}원)" + NL
        + f"네이버 수수료 등 {b['fee_rate']}%  (결제 {b['paid']:,}원 → 정산 "
          f"{b['settle']:,}원)" + NL * 2
        + f"손익분기 ROAS = 1 ÷ 마진율 = {b['be_roas']:,}%"
          f"  (결제금액 기준 {b['be_roas_paid']:,}%)" + NL
        + f"지금 ROAS {b['roas']:,}%  (정산기준 {b['roas_settle']:,}%)" + NL
        + ("→ 본전을 넘겼습니다." if ok else
           f"→ {b['be_roas'] - b['roas_settle']:,}%p 모자랍니다.") + NL * 2
        + f"같은 매출이면 광고비를 {b['ad_budget']:,}원 아래로 써야 본전입니다."
        + NL + f"지금 광고비 {b['ad']:,}원 → "
        + ("여유 " if b["net"] > 0 else "초과 ")
        + f"{abs(b['net']):,}원")
