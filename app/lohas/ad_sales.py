"""
**실제 매출**과 광고비를 붙인다 — 네이버 추정치가 아니라 우리 주문 DB 다.

네이버가 주는 `convAmt`(총 전환매출)에는 **장바구니 담기가 섞여 있다.**
2026-09-11 을 전환 유형별로 뜯어보니 그대로 드러났다.

    add_to_cart  4건  57,430원   ← 담기만 하고 안 샀다
    purchase     1건   8,770원   ← 실제로 산 것
    ─────────────────────────
    총 전환매출      66,200원    ← 이 숫자가 대시보드에 뜨던 값이다

그래서 매출은 **주문 DB(`joacham.orders_order`)** 에서 가져온다.
(2026-09-12 사용자: "오늘전환매출 이건 허수잔어")

붙이는 열쇠는 **스마트스토어 상품번호**다.
    주문       `orders_order.product_code`
    광고 소재  `ad_creative.mall_product_id`  →  lcp_code / l_code

주문 DB 는 192.168.219.200 의 `joacham` 스키마다. 주문관리 서버는
192.168.219.210 에서 돌지만 DB 는 200 을 본다(order 프로젝트의
`mysite/settings.py` 가 `DB_HOST` 를 읽고, 실제 표는 200 에 있다).
"""
import datetime

from .. import config, db

SCHEMA = "joacham"
TABLE = "orders_order"
SITE = "스마트"                 # site_name LIKE '%스마트%'
# **가게를 가려야 한다.** 주문 DB 에는 조아참·비트윙·나인조이 등 여러
# 스토어가 같이 들어 있다. 광고 계정(비트테크노-1)의 비즈채널이
# smartstore.naver.com/bitmind 라 **비트마인드 매출만** 봐야 한다
# (2026-09-12 사용자).
STORE = "비트마인드"             # seller_alias LIKE '%비트마인드%'

# 매출로 치지 않는 상태. 취소·반품·품절은 돈이 들어온 게 아니다.
DEAD = ("주문취소", "취소", "반품", "반품요청", "반품요청(접수)", "반품진행",
        "반품완료", "교환", "품절", "역마진품절", "옵션품절", "주문후품절",
        "변경안내", "보류")


def order_db():
    import pymysql
    return pymysql.connect(
        host=config._str("ORDER_DB_HOST") or config._str("DB_HOST"),
        port=int(config._str("ORDER_DB_PORT") or config._str("DB_PORT", "3306")),
        user=config._str("ORDER_DB_USER") or config._str("DB_USER"),
        password=(config._str("ORDER_DB_PASSWORD")
                  or config._str("DB_PASSWORD")).strip("'\""),
        database=config._str("ORDER_DB_NAME") or SCHEMA,
        charset="utf8mb4", connect_timeout=10,
        cursorclass=__import__("pymysql").cursors.DictCursor)


def store_filter() -> str:
    return config._str("ORDER_STORE") or STORE


def _ddl(c):
    c.execute("""CREATE TABLE IF NOT EXISTS ad_sales (
        day        TEXT NOT NULL,
        lcp_code   TEXT NOT NULL,
        l_code     TEXT,
        orders     INTEGER DEFAULT 0,   -- 주문 건수
        qty        INTEGER DEFAULT 0,   -- 수량
        amount     INTEGER DEFAULT 0,   -- 실결제금액 합
        updated_at TEXT,
        PRIMARY KEY (day, lcp_code, l_code)
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_asale_day ON ad_sales(day)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_asale_lcp ON ad_sales(lcp_code)")


def mgmt_codes(codes: list) -> dict:
    """
    스마트스토어 상품번호 → **판매자 관리코드**.

    관리코드로 그 상품이 어디 것인지 갈린다(2026-09-12 사용자).
        LCE_SX_L9613331   로하스 상품 (광고 대상)
        W06BBB9           내가 따로 올린 상품 — 광고와 무관
    스토어 74(bitmind)에는 L 6,537개 · W 203개가 있다.
    """
    if not codes:
        return {}
    try:
        import pymysql
        cn = pymysql.connect(
            host=config._str("DB_HOST"), port=3306,
            user=config._str("DB_USER"),
            password=config._str("DB_PASSWORD").strip("'\""),
            database="myproduct", charset="utf8mb4", connect_timeout=8,
            cursorclass=pymysql.cursors.DictCursor)
    except Exception:
        return {}
    try:
        q = ",".join(["%s"] * len(codes))
        with cn.cursor() as c:
            c.execute(f"SELECT channel_product_no, seller_management_code"
                      f" FROM smartstore_product"
                      f" WHERE channel_product_no IN ({q})",
                      [str(x) for x in codes])
            return {str(r["channel_product_no"]):
                    (r["seller_management_code"] or "")
                    for r in c.fetchall()}
    except Exception:
        return {}
    finally:
        cn.close()


def kind_of(mgmt: str, is_ad: bool) -> str:
    """매출을 세 갈래로 나눈다."""
    m = (mgmt or "").upper()
    if is_ad:
        return "광고상품"
    if m.startswith("L"):
        return "로하스-비광고"
    if m.startswith("W"):
        return "W코드"
    return "기타"


KINDS = ("광고상품", "로하스-비광고", "W코드", "기타")


def _ddl_split(c):
    c.execute("""CREATE TABLE IF NOT EXISTS sales_split (
        day    TEXT NOT NULL,
        kind   TEXT NOT NULL,
        orders INTEGER DEFAULT 0,
        qty    INTEGER DEFAULT 0,
        amount INTEGER DEFAULT 0,
        PRIMARY KEY (day, kind)
    )""")


def split(days: int = 14) -> list:
    since = (datetime.date.today()
             - datetime.timedelta(days=days - 1)).isoformat()
    with db.sqlite_conn() as c:
        _ddl_split(c)
        return [dict(r) for r in c.execute(
            "SELECT kind, SUM(orders) orders, SUM(qty) qty,"
            " SUM(amount) amount FROM sales_split WHERE day>=?"
            " GROUP BY kind ORDER BY amount DESC", (since,))]


def _code_map() -> dict:
    """스마트스토어 상품번호 → (lcp_code, l_code)."""
    with db.sqlite_conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS ad_creative ("
                  "customer_id TEXT, ad_id TEXT, adgroup_id TEXT,"
                  " campaign_id TEXT, lcp_code TEXT, l_code TEXT,"
                  " match_score REAL, status TEXT, status_ko TEXT,"
                  " enabled INTEGER, product_name TEXT, mall_product_id TEXT,"
                  " product_url TEXT, bid INTEGER, updated_at TEXT)")
        return {str(r["mall_product_id"]): (r["lcp_code"] or "",
                                            r["l_code"] or "")
                for r in c.execute(
                    "SELECT mall_product_id, lcp_code, l_code FROM ad_creative"
                    " WHERE mall_product_id<>''")}


def collect(days: int = 14, log=print, source: str = "") -> dict:
    """
    매출을 받아 LCP 별로 묶어 저장한다.

    **기준은 커머스 API 다**(2026-09-12 사용자). 주문 DB(`orders_order`)는
    이미 들어온 주문을 처리·배송하는 곳이라 배송비가 섞여 있고 당일치가
    비어 있다. 매출 숫자는 스토어에서 받은 실결제금액으로 통일한다.

    `source="orderdb"` 를 주면 예전 방식(주문 DB)으로 돈다 — 커머스 키가
    없거나 옛 기간을 메울 때만 쓴다.
    """
    src = source or config._str("SALES_SOURCE") or "commerce"
    if src == "commerce":
        from . import commerce
        if commerce.available():
            return collect_commerce(days, log=log)
        log("커머스 키가 없어 주문 DB 로 받습니다")
    return collect_orderdb(days, log=log)


def collect_commerce(days: int = 14, log=print) -> dict:
    """커머스 API 로 날짜마다 받아 저장한다. (하루에 한 번씩 호출)"""
    from . import commerce
    cmap = _code_map()
    now = db.now_str()
    since = datetime.date.today() - datetime.timedelta(days=days - 1)
    per_day = commerce.sales_range(since, log=log)
    tot_n = sum(v["orders"] for v in per_day.values())
    tot_amt = sum(v["amount"] for v in per_day.values())
    rows_all = []
    with db.sqlite_conn() as c2:
        _ddl(c2)
        c2.execute("DELETE FROM ad_sales WHERE day>=?", (since.isoformat(),))
    # 관리코드로 광고상품 / 로하스-비광고 / W코드 를 가른다
    all_codes = sorted({c for r in per_day.values()
                        for c in (r["by_product"] or {})})
    mg = mgmt_codes(all_codes)
    sp = {}
    for d, r in per_day.items():
        for c, v in (r["by_product"] or {}).items():
            k = kind_of(mg.get(c, ""), c in cmap)
            a = sp.setdefault((d, k), [0, 0, 0])
            a[0] += v["orders"]
            a[1] += v["qty"]
            a[2] += v["amount"]
    with db.sqlite_conn() as c2:
        _ddl_split(c2)
        c2.execute("DELETE FROM sales_split WHERE day>=?",
                   (since.isoformat(),))
        c2.executemany(
            "INSERT OR REPLACE INTO sales_split (day, kind, orders, qty,"
            " amount) VALUES (?,?,?,?,?)",
            [(d, k, v[0], v[1], v[2]) for (d, k), v in sp.items()])

    for d, r in sorted(per_day.items()):
        rows = [(d, cmap[c][0], cmap[c][1], v["orders"], v["qty"],
                 v["amount"], now)
                for c, v in (r["by_product"] or {}).items() if c in cmap]
        with db.sqlite_conn() as c2:
            c2.executemany(
                "INSERT OR REPLACE INTO ad_sales (day, lcp_code, l_code,"
                " orders, qty, amount, updated_at) VALUES (?,?,?,?,?,?,?)",
                rows)
        rows_all += rows
    amt = sum(r[5] for r in rows_all)
    log(f"커머스 {days}일 — 가게 전체 {tot_n:,}건 · {tot_amt:,}원")
    for r in split(days):
        log(f"    {r['kind']:14} {int(r['amount'] or 0):>9,}원 "
            f"({int(r['orders'] or 0)}건)")
    return {"rows": len(rows_all), "amount": amt,
            "matched": sum(r[3] for r in rows_all),
            "orders": tot_n, "amount_all": tot_amt, "source": "commerce"}


def collect_orderdb(days: int = 14, log=print) -> dict:
    """예비 경로 — 주문 DB 에서 받아온다 (배송비 포함이라 값이 다르다)."""
    since = (datetime.date.today() - datetime.timedelta(days=days))
    cmap = _code_map()
    if not cmap:
        log("광고 소재가 없습니다 — tools/ad_creative.py --collect 먼저")
        return {"rows": 0, "amount": 0, "matched": 0, "orders": 0}
    dead = ",".join(["%s"] * len(DEAD))
    sql = (f"SELECT order_date, product_code, COUNT(*) n,"
           f" SUM(quantity) qty, SUM(total_payment_price) amt"
           f" FROM {TABLE} WHERE order_date>=%s AND site_name LIKE %s"
           f" AND seller_alias LIKE %s"
           f" AND (order_status IS NULL OR order_status NOT IN ({dead}))"
           f" GROUP BY order_date, product_code")
    cn = order_db()
    try:
        with cn.cursor() as c:
            c.execute(sql, [since, f"%{SITE}%",
                            f"%{store_filter()}%", *DEAD])
            rows = c.fetchall()
    finally:
        cn.close()

    agg, n_ord, amt_all, matched = {}, 0, 0, 0
    for r in rows:
        n_ord += int(r["n"] or 0)
        amt_all += int(r["amt"] or 0)
        key = cmap.get(str(r["product_code"] or ""))
        if not key:
            continue
        matched += int(r["n"] or 0)
        k = (str(r["order_date"]), key[0], key[1])
        a = agg.setdefault(k, [0, 0, 0])
        a[0] += int(r["n"] or 0)
        a[1] += int(r["qty"] or 0)
        a[2] += int(r["amt"] or 0)

    now = db.now_str()
    with db.sqlite_conn() as c:
        _ddl(c)
        c.execute("DELETE FROM ad_sales WHERE day>=?", (since.isoformat(),))
        c.executemany(
            "INSERT OR REPLACE INTO ad_sales (day, lcp_code, l_code, orders,"
            " qty, amount, updated_at) VALUES (?,?,?,?,?,?,?)",
            [(d, lcp, lc, v[0], v[1], v[2], now)
             for (d, lcp, lc), v in agg.items()])
    amt = sum(v[2] for v in agg.values())
    log(f"주문 {n_ord:,}건 · 매출 {amt_all:,}원 중 "
        f"광고상품 {matched:,}건 · {amt:,}원 ({len(agg):,}행)")
    return {"rows": len(agg), "amount": amt, "matched": matched,
            "orders": n_ord, "amount_all": amt_all, "source": "orderdb"}


# ---- 읽기 -----------------------------------------------------------
def _q(sql, args=()):
    with db.sqlite_conn() as c:
        _ddl(c)
        return [dict(r) for r in c.execute(sql, args)]


def today(day: str = None) -> dict:
    day = day or datetime.date.today().isoformat()
    r = _q("SELECT COALESCE(SUM(amount),0) amount, COALESCE(SUM(orders),0)"
           " orders, COALESCE(SUM(qty),0) qty, MAX(updated_at) upd"
           " FROM ad_sales WHERE day=?", (day,))
    d = r[0] if r else {"amount": 0, "orders": 0, "qty": 0, "upd": ""}
    d["day"] = day
    return d


def by_day(days: int = 14) -> list:
    since = (datetime.date.today()
             - datetime.timedelta(days=days - 1)).isoformat()
    return _q("SELECT day, SUM(orders) orders, SUM(qty) qty,"
              " SUM(amount) amount FROM ad_sales WHERE day>=?"
              " GROUP BY day ORDER BY day DESC", (since,))


def by_lcp(days: int = 14, limit: int = 100) -> list:
    """LCP 별 **실매출 + 광고비 + 진짜 ROAS**."""
    since = (datetime.date.today()
             - datetime.timedelta(days=days - 1)).isoformat()
    rows = _q(
        "SELECT s.lcp_code,"
        " COALESCE(SUM(s.amount),0) amount, COALESCE(SUM(s.orders),0) orders,"
        " COALESCE(SUM(s.qty),0) qty FROM ad_sales s"
        " WHERE s.day>=? GROUP BY s.lcp_code", (since,))
    cost = {r["lcp_code"]: r for r in _q(
        "SELECT lcp_code, SUM(cost) cost, SUM(clk) clk FROM ad_spend"
        " WHERE level='adgroup' AND day>=? AND lcp_code<>''"
        " GROUP BY lcp_code", (since,))}
    seen = set()
    out = []
    for r in rows:
        c = cost.get(r["lcp_code"], {})
        r["cost"] = int(c.get("cost") or 0)
        r["clk"] = int(c.get("clk") or 0)
        seen.add(r["lcp_code"])
        out.append(r)
    # **광고비만 쓰고 매출이 0 인 것도 보여야 한다.** 그게 잘라낼 후보다.
    for k, c in cost.items():
        if k in seen:
            continue
        out.append({"lcp_code": k, "amount": 0, "orders": 0, "qty": 0,
                    "cost": int(c.get("cost") or 0),
                    "clk": int(c.get("clk") or 0)})
    for r in out:
        r["roas"] = (r["amount"] * 100 // r["cost"]) if r["cost"] else 0
    out.sort(key=lambda r: (-r["amount"], -r["cost"]))
    return out[:limit]
