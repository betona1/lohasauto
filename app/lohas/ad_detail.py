"""
대용량 보고서(StatReport)를 읽어 **차원별 광고비**로 쌓는다.

`/stats` 는 `breakdown` 을 줘도 **무시한다** — day·pcMobile·hourly·region·
gender·age 를 다 넣어봤지만 전부 같은 한 줄이 왔다(2026-09-12 실측).
쪼개려면 대용량 보고서를 받아 직접 집계하는 수밖에 없다.

`SHOPPINGKEYWORD_DETAIL` 의 16열을 실측으로 해독했다. 합계가 `/stats` 와
정확히 맞는 것으로 확인했다(2026-09-10: 노출 9,923 · 클릭 74 · 비용 14,782).

    1 날짜(YYYYMMDD)   2 고객ID        3 캠페인ID     4 광고그룹ID
    5 **검색어**        6 소재ID        7 **비즈채널ID**
    8 **시간대**(00~23)  9 지역코드(00~18,99)  10 **매체코드**
    11 **PC/모바일**(P/M)
    12 노출수          13 클릭수        14 **비용**     15 순위합  16 전환수

⚠️ 8·9·10 을 처음에 한 칸씩 밀려 읽었다(시간대로 지역을 세고 있었다).
값의 범위로 잡아냈다 — 8열은 00~23 이 딱 24개(시간), 9열은 00~18 에
99(기타)가 섞인 지역, 10열은 값이 4개뿐인 매체다(2026-09-12).

보고서는 **이틀 전(D-2)뿐 아니라 어제(D-1)도** 만들어진다. 당일은
`20007 해당 일자 지표 준비중입니다` 가 온다 — 오늘치는 `/stats` 로 본다.
"""
import datetime

from .. import db
from . import ad_report as ar

TP = "SHOPPINGKEYWORD_DETAIL"

C_DAY, C_CUST, C_CAMP, C_GROUP = 0, 1, 2, 3
C_QUERY, C_AD, C_CHANNEL = 4, 5, 6
C_HOUR, C_REGION, C_MEDIA, C_DEVICE = 7, 8, 9, 10
C_IMP, C_CLK, C_COST, C_RANK, C_CONV = 11, 12, 13, 14, 15


def _ddl(c):
    c.execute("""CREATE TABLE IF NOT EXISTS ad_detail (
        customer_id TEXT NOT NULL,
        day         TEXT NOT NULL,
        campaign_id TEXT, adgroup_id TEXT, lcp_code TEXT,
        query       TEXT,               -- 검색어
        ad_id       TEXT,               -- 소재(상품)
        channel_id  TEXT,               -- 비즈채널
        device      TEXT,               -- P / M
        hour        TEXT, region TEXT, media TEXT,
        imp INTEGER, clk INTEGER, cost INTEGER, conv INTEGER,
        PRIMARY KEY (customer_id, day, adgroup_id, query, ad_id,
                     device, hour, region, media)
    )""")
    for q in ("CREATE INDEX IF NOT EXISTS idx_adt_day ON ad_detail(day)",
              "CREATE INDEX IF NOT EXISTS idx_adt_q ON ad_detail(query)",
              "CREATE INDEX IF NOT EXISTS idx_adt_ch ON ad_detail(channel_id)",
              "CREATE INDEX IF NOT EXISTS idx_adt_lcp ON ad_detail(lcp_code)"):
        c.execute(q)


def _lcp_map(customer) -> dict:
    with db.sqlite_conn() as c:
        return {r["adgroup_id"]: (r["lcp_code"] or "") for r in c.execute(
            "SELECT adgroup_id, lcp_code FROM ad_group WHERE customer_id=?",
            (str(customer),))}


def ingest_file(customer, path, log=print) -> int:
    """받아둔 보고서 파일 하나를 DB 에 넣는다. 같은 날은 덮어쓴다."""
    lcp = _lcp_map(customer)
    rows, day = [], ""
    with open(path, encoding="utf-8") as f:
        for line in f:
            t = line.rstrip("\n").split("\t")
            if len(t) < 16:
                continue
            raw = t[C_DAY]
            day = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}" if len(raw) == 8 else raw

            def n(i):
                try:
                    return int(float(t[i] or 0))
                except ValueError:
                    return 0

            rows.append((str(customer), day, t[C_CAMP], t[C_GROUP],
                         lcp.get(t[C_GROUP], ""), t[C_QUERY], t[C_AD],
                         t[C_CHANNEL], t[C_DEVICE], t[C_HOUR], t[C_REGION],
                         t[C_MEDIA], n(C_IMP), n(C_CLK), n(C_COST),
                         n(C_CONV)))
    if not rows:
        return 0
    with db.sqlite_conn() as c:
        _ddl(c)
        c.execute("DELETE FROM ad_detail WHERE customer_id=? AND day=?",
                  (str(customer), day))
        c.executemany(
            "INSERT OR REPLACE INTO ad_detail (customer_id, day, campaign_id,"
            " adgroup_id, lcp_code, query, ad_id, channel_id, device, hour,"
            " region, media, imp, clk, cost, conv)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    log(f"  {day}  {len(rows):,}행 저장")
    return len(rows)


def spent_on(customer, day) -> int:
    """그날 실제로 쓴 돈. 보고서를 요청하기 전에 확인한다."""
    from . import searchad as sa
    ids = [c["nccCampaignId"] for c in sa.campaigns(customer)]
    if not ids:
        return 0
    return sum(int(r.get("salesAmt") or 0)
               for r in sa.stats(customer, ids, day, day))


def collect(customer, day, log=print, check_spend: bool = True) -> int:
    """
    그 날짜 보고서를 만들어 받아서 넣는다.

    **쓴 돈이 0원인 날은 건너뛴다.** 그런 날은 보고서가 영영
    `status=NONE` 에 머물러(만들 내용이 없다) 기다리기만 2분을 버린다
    (2026-09-12 첫 자동수집에서 09-05~09-09 다섯 날이 그랬다).
    """
    if check_spend and not spent_on(customer, day):
        log(f"  {day} 지출 0원 — 건너뜁니다")
        return 0
    st, d = ar.stat_create(customer, TP, day)
    if not (isinstance(d, dict) and d.get("reportJobId")):
        log(f"  {day} 보고서 요청 실패 {st} {str(d)[:80]}")
        return 0
    job = str(d["reportJobId"])
    r, data = ar.wait_and_fetch(customer, lambda: ar.stat_get(customer, job),
                                tries=20, wait=6, log=lambda *_: None)
    if not data:
        log(f"  {day} 아직 준비 안 됨 (상태 {r.get('status')})")
        return 0
    p = ar.save(customer, f"stat_{TP}_{day}", data)
    return ingest_file(customer, p, log=log)


def collect_days(customer, days: int = 2, log=print) -> int:
    """어제부터 거슬러 N일치. 당일은 보고서가 안 나오므로 뺀다."""
    n = 0
    for back in range(1, days + 1):
        d = (datetime.date.today() - datetime.timedelta(days=back)).isoformat()
        n += collect(customer, d, log=log)
    return n


# ---- 집계 -----------------------------------------------------------
def _q(sql, args=()):
    with db.sqlite_conn() as c:
        _ddl(c)
        return [dict(r) for r in c.execute(sql, args)]


DEVICE = {"P": "PC", "M": "모바일"}

# 매체 코드 — 4개뿐이고 기기와 짝을 이룬다(2026-09-12 실측).
#   11068 / 684927  = PC     · 33421 / 684926 = 모바일
# 이름은 API 가 안 준다. 광고주센터 보고서의 '매체' 와 대조하면 채울 수 있다.
# 이름은 사용자가 받아준 광고주센터 「매체 보고서」와 금액을 맞춰 확정했다
# (2026-09-12). 09-10~09-11 합계가 원 단위까지 일치한다.
MEDIA = {
    "11068": "네이버 쇼핑 - PC",            # 12,301원
    "33421": "네이버 쇼핑 - 모바일",         # 20,240원
    "684927": "네이버플러스 스토어 - PC",     # 2,568원
    "684926": "네이버플러스 스토어 - 모바일",  # 12,749원
}


def media_name(code) -> str:
    return MEDIA.get(str(code), str(code))


def by(dim: str, customer="", days: int = 7, limit: int = 60) -> list:
    """
    차원별 광고비. `dim` 은 ad_detail 의 열 이름
    (`query` 검색어 / `channel_id` 비즈채널 / `device` PC·모바일 /
     `hour` 시간대 / `lcp_code` LCP / `ad_id` 소재).
    """
    since = (datetime.date.today()
             - datetime.timedelta(days=days)).isoformat()
    sql = (f"SELECT {dim} AS k, SUM(cost) cost, SUM(clk) clk, SUM(imp) imp,"
           " SUM(conv) conv FROM ad_detail WHERE day>=?")
    args = [since]
    if customer:
        sql += " AND customer_id=?"
        args.append(str(customer))
    sql += f" GROUP BY {dim} ORDER BY cost DESC LIMIT ?"
    args.append(limit)
    return _q(sql, args)


def by_day(dim: str, customer="", days: int = 7, limit: int = 500) -> list:
    """
    **날짜 × 차원** 지출. 채널별로 하루하루 얼마 썼는지 볼 때 쓴다.

    반환 [{day, k, cost, clk, imp, conv}, ...] — 날짜 내림차순.
    """
    since = (datetime.date.today()
             - datetime.timedelta(days=days)).isoformat()
    sql = (f"SELECT day, {dim} AS k, SUM(cost) cost, SUM(clk) clk,"
           " SUM(imp) imp, SUM(conv) conv FROM ad_detail WHERE day>=?")
    args = [since]
    if customer:
        sql += " AND customer_id=?"
        args.append(str(customer))
    sql += f" GROUP BY day, {dim} ORDER BY day DESC, cost DESC LIMIT ?"
    args.append(limit)
    return _q(sql, args)


def channel_names(customer="") -> dict:
    """비즈채널 ID → 이름. 없으면 ID 그대로."""
    from . import searchad as sa
    out = {}
    for cu in ([customer] if customer else sa.customers()):
        try:
            for ch in (sa.get("/ncc/channels", cu) or []):
                out[ch.get("nccBusinessChannelId") or ch.get("id") or ""] = \
                    ch.get("name") or ""
        except Exception:
            pass
    return {k: v for k, v in out.items() if k}


def days_in_db(customer="") -> list:
    sql = "SELECT day, SUM(cost) cost, COUNT(*) rows FROM ad_detail"
    args = []
    if customer:
        sql += " WHERE customer_id=?"
        args.append(str(customer))
    sql += " GROUP BY day ORDER BY day DESC"
    return _q(sql, args)
