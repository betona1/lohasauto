"""
**웹 프로젝트(joachamproduct) DB 로 보내기.**

웹서버가 DB 를 하나로 모으는 중이다(2026-09-21 사용자). 목적지는
`192.168.219.200 / joachamproduct` 의 `jp_*` 표다.

실측으로 확인한 지금 상태 —

    jp_naver_ad_group   8,139행   ★ 이미 우리 ad_spend 와 **숫자가 똑같다**
    jp_naver_ad_daily      32행   ★ 이미 맞다
    jp_ad_daily             0행   ← 비었다. 우리 ad_detail 207,825행이 여기 간다
    jp_sales_stat           0행   ← 비었다. 우리 매출·원가·이익이 여기 간다
    jp_keyword              0행   ← 비었다

광고비(캠페인·광고그룹 일자별)는 웹이 **자기 수집기로 이미 받고 있다.**
같은 네이버 API 라 숫자가 같다. 그러니 **거기는 건드리지 않는다** — 둘이
같은 표에 쓰면 어느 쪽이 맞는지 알 수 없게 된다.

우리만 가진 것을 채운다.

    ad_detail    → jp_ad_daily      매체·검색어·시간대·지역 (웹에 없음)
    order_profit → jp_sales_stat    정산·원가·이익 (웹에 없음)
    ad_sales     → jp_sales_stat    LCP 별 실매출
    그 밖        → jp_lohas_*       소재·입찰가이력·노출시간·계정 (둘 곳이 없어 새로 만든다)

**읽기는 하지 않는다. 쓰기만 한다.** 이 도구는 우리 SQLite 를 원본으로
보고 웹 DB 를 맞춘다. 되돌릴 일이 생기면 `backup/` 의 백업을 쓴다.

    python -X utf8 tools/jp_sync.py --dry            무엇이 갈지만 보여준다(기본)
    python -X utf8 tools/jp_sync.py --apply          실제로 보낸다
    python -X utf8 tools/jp_sync.py --apply --since 2026-09-10
    python -X utf8 tools/jp_sync.py --verify         양쪽 숫자를 대본다
"""
import argparse
import datetime
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config, db                                      # noqa: E402

def _utf8_stdout():
    """**import 될 때는 절대 부르지 않는다.**

    `sys.stdout` 을 새 TextIOWrapper 로 감싸면, 그 임시 객체가 사라질 때
    밑에 있는 buffer 까지 닫아 버린다. 이 파일을 다른 스크립트가 import
    하는 순간 그쪽 print 가 `I/O operation on closed file` 로 죽었다
    (2026-09-21 ad_watch 에서 실측).
    """
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace")


QUIET = False

JP_DB = "joachamproduct"
MARKET = "naver"            # jp_* 는 마켓을 섞어 쓰는 구조다
CHUNK = 2000

# 우리만 가진 것 — jp_ 에 둘 곳이 없어 새로 만든다. 이름은 jp_ 규칙을 따른다.
NEW_DDL = {
    "jp_lohas_creative": """CREATE TABLE IF NOT EXISTS `jp_lohas_creative` (
        `customer_id` VARCHAR(20) NOT NULL,
        `ad_id` VARCHAR(48) NOT NULL,
        `adgroup_id` VARCHAR(48), `campaign_id` VARCHAR(48),
        `lcp_code` VARCHAR(40), `l_code` VARCHAR(20),
        `status` VARCHAR(24), `status_ko` VARCHAR(24),
        `enabled` TINYINT(1) DEFAULT 1,
        `product_name` VARCHAR(512),
        `mall_product_no` VARCHAR(64), `product_url` VARCHAR(512),
        `bid` INT DEFAULT 0,
        `updated_at` DATETIME,
        PRIMARY KEY (`customer_id`,`ad_id`),
        KEY `ix_lcp` (`lcp_code`), KEY `ix_status` (`status`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
      COMMENT='로하스 광고소재 + L코드 매칭'""",

    "jp_lohas_bid_log": """CREATE TABLE IF NOT EXISTS `jp_lohas_bid_log` (
        `id` BIGINT NOT NULL AUTO_INCREMENT,
        `customer_id` VARCHAR(20) NOT NULL,
        `adgroup_id` VARCHAR(48), `lcp_code` VARCHAR(40),
        `name` VARCHAR(200),
        `old_bid` INT, `new_bid` INT,
        `changed_at` DATETIME NOT NULL,
        PRIMARY KEY (`id`),
        UNIQUE KEY `uq_bid` (`customer_id`,`adgroup_id`,`changed_at`),
        KEY `ix_at` (`changed_at`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
      COMMENT='입찰가 변경 이력. 시각은 바꾼 때가 아니라 확인한 때'""",

    "jp_lohas_schedule": """CREATE TABLE IF NOT EXISTS `jp_lohas_schedule` (
        `id` BIGINT NOT NULL AUTO_INCREMENT,
        `customer_id` VARCHAR(20) NOT NULL,
        `from_day` DATE NOT NULL,
        `start_hour` INT, `end_hour` INT,
        `days` VARCHAR(40), `memo` VARCHAR(255),
        `created_at` DATETIME,
        PRIMARY KEY (`id`),
        UNIQUE KEY `uq_sc` (`customer_id`,`from_day`,`created_at`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
      COMMENT='광고 노출시간 변경 이력'""",

    "jp_lohas_account": """CREATE TABLE IF NOT EXISTS `jp_lohas_account` (
        `customer_id` VARCHAR(20) NOT NULL,
        `label` VARCHAR(80), `enabled` TINYINT(1) DEFAULT 0,
        `is_main` TINYINT(1) DEFAULT 0,
        `bizmoney` BIGINT DEFAULT 0,
        `campaigns` INT DEFAULT 0, `adgroups` INT DEFAULT 0,
        `start_date` DATE NULL,
        `updated_at` DATETIME,
        PRIMARY KEY (`customer_id`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
      COMMENT='네이버 광고계정 + 잔액'""",
}


def log(m):
    if not QUIET:
        print(f"{datetime.datetime.now():%H:%M:%S}  {m}", flush=True)


def jp_conn():
    import pymysql
    return pymysql.connect(
        host=config._str("JP_DB_HOST") or config._str("DB_HOST"),
        port=int(config._str("JP_DB_PORT")
                 or config._str("DB_PORT", "3306")),
        user=config._str("JP_DB_USER") or config._str("DB_USER"),
        password=(config._str("JP_DB_PASSWORD")
                  or config._str("DB_PASSWORD")).strip("'\""),
        database=config._str("JP_DB_NAME") or JP_DB,
        charset="utf8mb4", autocommit=False, connect_timeout=15)


def _push(cn, sql, rows, what, dry):
    """같은 INSERT 를 덩어리로 나눠 보낸다."""
    if not rows:
        log(f"  {what:<28} 보낼 것 없음")
        return 0
    if dry:
        log(f"  {what:<28} {len(rows):>9,}행  (연습 — 보내지 않음)")
        return len(rows)
    n = 0
    with cn.cursor() as c:
        for i in range(0, len(rows), CHUNK):
            c.executemany(sql, rows[i:i + CHUNK])
            n += len(rows[i:i + CHUNK])
    cn.commit()
    log(f"  {what:<28} {n:>9,}행  보냄")
    return n


# ------------------------------------------------------------ 각 표
def sync_detail(cn, since, dry):
    """ad_detail → jp_ad_daily. 매체·검색어·시간대·지역."""
    # **반드시 먼저 합쳐서 보낸다.**
    #
    # 우리 `ad_detail` 은 같은 검색어를 **매체 x 시간대 x 지역**으로 쪼개
    # 갖고 있다. '학생용거울' 하루치가 204행이다. 그런데 `jp_ad_daily` 의
    # 유일키는 (일자, 마켓, 계정, 캠페인, 그룹, 검색어, 상품번호) 라
    # 그 쪼갬이 키에 없다. 그대로 보내면 둘 중 하나가 된다.
    #
    #   덮어쓰기 → 204행 중 마지막 하나만 남아 숫자가 1/204 로 줄고
    #   중복쌓임 → MySQL 은 유일키 안의 NULL 을 서로 다른 값으로 본다.
    #              상품번호를 NULL 로 두면 **다시 돌릴 때마다 두 배**가 된다
    #
    # 그래서 (일자+캠페인+그룹+검색어) 로 **합계를 내서** 보낸다.
    # 207,825행 → 73,936행. 시간대·지역·매체는 우리 화면이 쓴다.
    # 상품번호 자리에는 LCP 코드를 넣어 NULL 을 없앤다.
    with db.sqlite_conn() as c:
        rows = [(
            r["day"], MARKET, int(r["customer_id"] or 0), "쇼핑검색",
            r["campaign_id"], r["adgroup_id"], r["lcp_code"] or "",
            r["lcp_code"] or "-", r["query"],
            int(r["imp"] or 0), int(r["clk"] or 0),
            round(float(r["clk"] or 0) * 100 / float(r["imp"]), 4)
            if r["imp"] else None,
            round(float(r["cost"] or 0) / float(r["clk"]), 2)
            if r["clk"] else None,
            float(r["cost"] or 0), int(r["conv"] or 0),
        ) for r in c.execute(
            "SELECT day, customer_id, campaign_id, adgroup_id,"
            " MAX(lcp_code) lcp_code, query, SUM(imp) imp, SUM(clk) clk,"
            " SUM(cost) cost, SUM(conv) conv FROM ad_detail"
            " WHERE day>=? AND COALESCE(query,'')<>''"
            " GROUP BY day, customer_id, campaign_id, adgroup_id, query",
            (since,))]
    sql = ("INSERT INTO jp_ad_daily (report_date, market, account_id,"
           " ad_type, campaign_id, group_id, product_code,"
           " market_product_no, keyword,"
           " impressions, clicks, ctr, avg_cpc, cost, conversions)"
           " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
           " ON DUPLICATE KEY UPDATE impressions=VALUES(impressions),"
           " clicks=VALUES(clicks), ctr=VALUES(ctr),"
           " avg_cpc=VALUES(avg_cpc), cost=VALUES(cost),"
           " conversions=VALUES(conversions), collected_at=NOW()")
    return _push(cn, sql, rows, "ad_detail → jp_ad_daily", dry)


def sync_sales(cn, since, dry):
    """order_profit → jp_sales_stat. 매출·원가·광고비·이익을 한 줄로."""
    from app.lohas import ad_account as _acc
    # 유일키 안의 NULL 은 서로 다른 값으로 취급된다 — 계정을 비우면
    # 다시 돌릴 때마다 행이 쌓인다. 반드시 채운다.
    cu = int((_acc.main_account() or {}).get("customer_id") or 0)
    with db.sqlite_conn() as c:
        ad = {(r["day"], r["lcp_code"]): int(r["c"] or 0) for r in c.execute(
            "SELECT day, lcp_code, SUM(cost) c FROM ad_spend"
            " WHERE level='adgroup' AND lcp_code<>'' AND day>=?"
            " GROUP BY day, lcp_code", (since,))}
        rows = []
        for r in c.execute(
                "SELECT * FROM order_profit WHERE day>=?", (since,)):
            cost_ad = ad.get((r["day"], r["lcp_code"] or ""), 0)
            paid = int(r["paid"] or 0)
            gross = int(r["profit"] or 0) - cost_ad
            rows.append((
                r["day"], MARKET, cu, r["product_code"],
                int(r["orders"] or 0), int(r["qty"] or 0), paid,
                int(r["cost"] or 0), float(cost_ad), gross,
                round(paid * 100 / cost_ad, 2) if cost_ad else None))
    sql = ("INSERT INTO jp_sales_stat (stat_date, market, account_id,"
           " product_code, order_cnt, qty, sales_amount, supply_amount,"
           " ad_cost, gross_margin, roas)"
           " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
           " ON DUPLICATE KEY UPDATE order_cnt=VALUES(order_cnt),"
           " qty=VALUES(qty), sales_amount=VALUES(sales_amount),"
           " supply_amount=VALUES(supply_amount), ad_cost=VALUES(ad_cost),"
           " gross_margin=VALUES(gross_margin), roas=VALUES(roas)")
    return _push(cn, sql, rows, "order_profit → jp_sales_stat", dry)


def sync_creative(cn, dry):
    with db.sqlite_conn() as c:
        rows = [(r["customer_id"], r["ad_id"], r["adgroup_id"],
                 r["campaign_id"], r["lcp_code"], r["l_code"], r["status"],
                 r["status_ko"], int(r["enabled"] or 0), r["product_name"],
                 r["mall_product_id"], r["product_url"], int(r["bid"] or 0),
                 r["updated_at"]) for r in c.execute(
                     "SELECT * FROM ad_creative")]
    sql = ("INSERT INTO jp_lohas_creative (customer_id, ad_id, adgroup_id,"
           " campaign_id, lcp_code, l_code, status, status_ko, enabled,"
           " product_name, mall_product_no, product_url, bid, updated_at)"
           " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
           " ON DUPLICATE KEY UPDATE lcp_code=VALUES(lcp_code),"
           " l_code=VALUES(l_code), status=VALUES(status),"
           " status_ko=VALUES(status_ko), enabled=VALUES(enabled),"
           " product_name=VALUES(product_name), bid=VALUES(bid),"
           " updated_at=VALUES(updated_at)")
    return _push(cn, sql, rows, "ad_creative → jp_lohas_creative", dry)


def sync_bid(cn, dry):
    with db.sqlite_conn() as c:
        rows = [(r["customer_id"], r["adgroup_id"], r["lcp_code"], r["name"],
                 int(r["old_bid"] or 0), int(r["new_bid"] or 0),
                 r["changed_at"]) for r in c.execute(
                     "SELECT * FROM ad_bid_history")]
    sql = ("INSERT INTO jp_lohas_bid_log (customer_id, adgroup_id, lcp_code,"
           " name, old_bid, new_bid, changed_at)"
           " VALUES (%s,%s,%s,%s,%s,%s,%s)"
           " ON DUPLICATE KEY UPDATE new_bid=VALUES(new_bid)")
    return _push(cn, sql, rows, "ad_bid_history → jp_lohas_bid_log", dry)


def sync_misc(cn, dry):
    n = 0
    with db.sqlite_conn() as c:
        rows = [(r["customer_id"], r["from_day"], r["start_hour"],
                 r["end_hour"], r["days"], r["memo"], r["created_at"])
                for r in c.execute("SELECT * FROM ad_schedule")]
    n += _push(cn, "INSERT INTO jp_lohas_schedule (customer_id, from_day,"
               " start_hour, end_hour, days, memo, created_at)"
               " VALUES (%s,%s,%s,%s,%s,%s,%s)"
               " ON DUPLICATE KEY UPDATE memo=VALUES(memo)",
               rows, "ad_schedule → jp_lohas_schedule", dry)
    with db.sqlite_conn() as c:
        rows = [(r["customer_id"], r["label"], int(r["enabled"] or 0),
                 int(r["is_main"] or 0), int(r["bizmoney"] or 0),
                 int(r["campaigns"] or 0), int(r["adgroups"] or 0),
                 r["start_date"], r["updated_at"])
                for r in c.execute("SELECT * FROM ad_account")]
    n += _push(cn, "INSERT INTO jp_lohas_account (customer_id, label,"
               " enabled, is_main, bizmoney, campaigns, adgroups,"
               " start_date, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)"
               " ON DUPLICATE KEY UPDATE label=VALUES(label),"
               " bizmoney=VALUES(bizmoney), campaigns=VALUES(campaigns),"
               " adgroups=VALUES(adgroups), start_date=VALUES(start_date),"
               " updated_at=VALUES(updated_at)",
               rows, "ad_account → jp_lohas_account", dry)
    return n


def verify(cn, since):
    """양쪽 숫자를 대본다. 안 맞으면 옮긴 것이 아니다."""
    print()
    log("=== 대조 ===")
    # **행 수가 아니라 합계를 댄다.** 검색어는 합쳐서 보내므로 행 수는
    # 당연히 다르다(207,825 → 73,936). 돈과 클릭이 맞는지가 기준이다.
    with db.sqlite_conn() as c:
        loc_sp = {str(r[0]): int(r[1] or 0) for r in c.execute(
            "SELECT day, SUM(cost) FROM ad_spend WHERE level='adgroup'"
            " AND day>=? GROUP BY day", (since,))}
        r = c.execute(
            "SELECT COUNT(*), SUM(cost), SUM(clk) FROM ("
            " SELECT day, campaign_id, adgroup_id, query, SUM(cost) cost,"
            " SUM(clk) clk FROM ad_detail WHERE day>=?"
            " AND COALESCE(query,'')<>''"
            " GROUP BY day, campaign_id, adgroup_id, query)",
            (since,)).fetchone()
        loc_kw, loc_kwc, loc_kwk = int(r[0]), int(r[1] or 0), int(r[2] or 0)
        loc_ps = c.execute(
            "SELECT COUNT(*) FROM order_profit WHERE day>=?",
            (since,)).fetchone()[0]
    with cn.cursor() as c:
        # 웹의 캠페인별 표(jp_naver_ad_daily)는 지난 날짜를 다시 받지 않아
        # 뒤에 네이버가 고친 값이 반영되지 않는다(9/17 이 275원 높게 남아
        # 있었다). 광고그룹별 표가 최신이라 그쪽과 댄다.
        c.execute("SELECT stat_date, SUM(cost) FROM jp_naver_ad_group"
                  " WHERE stat_date>=%s GROUP BY stat_date", (since,))
        web_sp = {str(r[0]): int(r[1] or 0) for r in c.fetchall()}
        c.execute("SELECT COUNT(*), SUM(cost), SUM(clicks) FROM jp_ad_daily"
                  " WHERE report_date>=%s", (since,))
        r = c.fetchone()
        web_kw, web_kwc, web_kwk = int(r[0]), int(r[1] or 0), int(r[2] or 0)
        c.execute("SELECT COUNT(*) FROM jp_sales_stat WHERE stat_date>=%s",
                  (since,))
        web_ps = c.fetchone()[0]
    bad = 0
    for d in sorted(set(loc_sp) | set(web_sp)):
        a, b = loc_sp.get(d, 0), web_sp.get(d, 0)
        if a != b:
            bad += 1
            log(f"  광고비 {d}  우리 {a:,}원  웹 {b:,}원  ← 다름")
    ok_kw = (loc_kw == web_kw and loc_kwc == web_kwc and loc_kwk == web_kwk)
    log(f"  광고비    날짜 {len(loc_sp)}일 중 어긋남 {bad}일"
        + ("  ✅" if not bad else "  ⚠"))
    log(f"  검색어    우리 {loc_kw:,}행 {loc_kwc:,}원 {loc_kwk:,}클릭")
    log(f"            웹   {web_kw:,}행 {web_kwc:,}원 {web_kwk:,}클릭"
        + ("  ✅" if ok_kw else "  ⚠ 다름"))
    log(f"  매출행    우리 {loc_ps:,}  웹 {web_ps:,}"
        + ("  ✅" if web_ps >= loc_ps else "  ⚠ 모자람"))
    return bad == 0 and ok_kw and web_ps >= loc_ps


def run(since: str = "", quiet: bool = True) -> dict:
    """다른 스크립트에서 부르는 입구. 최근 것만 밀어 넣는다."""
    global QUIET
    QUIET = quiet
    since = since or (datetime.date.today()
                      - datetime.timedelta(days=14)).isoformat()
    cn = jp_conn()
    try:
        with cn.cursor() as c:
            for q in NEW_DDL.values():
                c.execute(q)
        cn.commit()
        n = (sync_detail(cn, since, False) + sync_sales(cn, since, False)
             + sync_creative(cn, False) + sync_bid(cn, False)
             + sync_misc(cn, False))
        return {"rows": n, "since": since}
    finally:
        cn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제로 보낸다")
    ap.add_argument("--dry", action="store_true", help="연습 (기본)")
    ap.add_argument("--since", default="2026-08-01")
    ap.add_argument("--verify", action="store_true", help="대조만 한다")
    args = ap.parse_args()
    dry = not args.apply

    cn = jp_conn()
    with cn.cursor() as c:
        c.execute("SELECT DATABASE()")
        log(f"목적지 {config._str('DB_HOST')} / {c.fetchone()[0]}")
    if args.verify:
        ok = verify(cn, args.since)
        cn.close()
        return 0 if ok else 1

    if not dry:
        with cn.cursor() as c:
            for t, q in NEW_DDL.items():
                c.execute(q)
        cn.commit()
        log(f"새 표 준비 {len(NEW_DDL)}개 — " + ", ".join(NEW_DDL))
    else:
        log("**연습입니다.** 실제로 보내려면 --apply 를 주십시오.")
        log(f"새로 만들 표: {', '.join(NEW_DDL)}")

    log(f"기준일 {args.since} 이후")
    tot = 0
    tot += sync_detail(cn, args.since, dry)
    tot += sync_sales(cn, args.since, dry)
    tot += sync_creative(cn, dry)
    tot += sync_bid(cn, dry)
    tot += sync_misc(cn, dry)
    log(f"합계 {tot:,}행")
    if not dry:
        verify(cn, args.since)
    cn.close()
    return 0


if __name__ == "__main__":
    _utf8_stdout()
    sys.exit(main())
