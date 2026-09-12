"""
광고계정 · 캠페인 기본정보 수집과 **사용할 것 고르기**.

계정이 여러 개고, 캠페인 10개 중 실제로 쓰는 것은 하나뿐이다
(2026-09-11 사용자). 그래서 전부 긁어와 두고 **화면에서 켜고 끄게** 한다.
켠 것만 지출 수집이 돈다.

**계정 목록을 주는 API 는 없다.** `/customer-links?type=MYCLIENTS` 는
대행사 계정에서만 동작하고, 직접 광고주 계정에서는 404 다(2026-09-11
두 계정 모두 실측). 그래서 계정 번호는 `.env` 의 `NAVER_AD_CUSTOMERS` 가
출처이고, 여기서는 그 번호로 접속되는지 확인하고 이름을 붙여 둔다.

표기 이름은 사람이 바꿀 수 있다 — 사이트가 계정 이름을 안 주기 때문이다.
"""
from .. import db
from . import searchad as sa

_DDL = [
    """CREATE TABLE IF NOT EXISTS ad_account (
        customer_id TEXT PRIMARY KEY,
        label       TEXT,               -- 사람이 붙인 이름
        enabled     INTEGER DEFAULT 1,
        bizmoney    INTEGER,
        campaigns   INTEGER,
        adgroups    INTEGER,
        ok          INTEGER DEFAULT 0,  -- 접속 확인됨
        updated_at  TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS ad_campaign (
        customer_id TEXT NOT NULL,
        campaign_id TEXT NOT NULL,
        name        TEXT,
        tp          TEXT,
        status      TEXT,
        adgroups    INTEGER DEFAULT 0,
        enabled     INTEGER DEFAULT 0,  -- **기본 꺼짐** - 쓰는 것만 켠다
        updated_at  TEXT,
        PRIMARY KEY (customer_id, campaign_id)
    )""",
    """CREATE TABLE IF NOT EXISTS ad_group (
        customer_id TEXT NOT NULL,
        adgroup_id  TEXT NOT NULL,
        campaign_id TEXT,
        name        TEXT,
        lcp_code    TEXT,
        status      TEXT,
        bid         INTEGER,
        updated_at  TEXT,
        PRIMARY KEY (customer_id, adgroup_id)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_adgrp_lcp ON ad_group(lcp_code)""",
]


def ddl(c):
    for q in _DDL:
        c.execute(q)
    # 메인 계정 — 대시보드가 보여줄 계정 하나. 여러 계정을 합산하면
    # "충전 내역인가?" 하고 헷갈린다(2026-09-12 사용자: 300,412원은
    # 두 계정 잔액을 더한 값이었다). 실제로 보는 것은 하나뿐이다.
    cols = {r[1] for r in c.execute("PRAGMA table_info(ad_account)")}
    if "is_main" not in cols:
        c.execute("ALTER TABLE ad_account ADD COLUMN is_main INTEGER DEFAULT 0")
    # 광고 시작일 — **이 날 이전은 통계에서 뺀다.** 시작 전 매출까지
    # 광고 성과로 잡히면 ROAS 가 부풀려진다 (2026-09-12 사용자:
    # 비트테크노-1 은 9월 10일부터 집행).
    if "start_date" not in cols:
        c.execute("ALTER TABLE ad_account ADD COLUMN start_date TEXT")


def collect(log=print) -> dict:
    """
    계정 · 캠페인 · 광고그룹을 받아 저장한다.

    **켜고 끈 것은 건드리지 않는다.** 다시 수집해도 사람이 고른 선택이
    그대로 남아야 한다 - `enabled` 는 INSERT 할 때만 넣는다.
    """
    now = db.now_str()
    out = {"accounts": 0, "campaigns": 0, "groups": 0, "fail": []}
    with db.sqlite_conn() as c:
        ddl(c)
    for cu in sa.customers():
        camps = sa.campaigns(cu)
        biz = sa.bizmoney(cu)
        ok = bool(camps) or bool(biz)
        if not ok:
            out["fail"].append(cu)
            log(f"  [{cu}] 접속 실패 - 키 또는 계정번호를 확인하십시오")
        grps = sa.adgroups(cu) if ok else []
        n_by_camp = {}
        for g in grps:
            n_by_camp[g.get("nccCampaignId")] = \
                n_by_camp.get(g.get("nccCampaignId"), 0) + 1
        with db.sqlite_conn() as c:
            ddl(c)
            c.execute(
                "INSERT INTO ad_account (customer_id, label, enabled,"
                " bizmoney, campaigns, adgroups, ok, updated_at)"
                " VALUES (?,?,1,?,?,?,?,?)"
                " ON CONFLICT(customer_id) DO UPDATE SET"
                " bizmoney=excluded.bizmoney, campaigns=excluded.campaigns,"
                " adgroups=excluded.adgroups, ok=excluded.ok,"
                " updated_at=excluded.updated_at",
                (str(cu), str(cu), int(biz.get("bizmoney") or 0), len(camps),
                 len(grps), 1 if ok else 0, now))
            for x in camps:
                c.execute(
                    "INSERT INTO ad_campaign (customer_id, campaign_id, name,"
                    " tp, status, adgroups, enabled, updated_at)"
                    " VALUES (?,?,?,?,?,?,0,?)"
                    " ON CONFLICT(customer_id, campaign_id) DO UPDATE SET"
                    " name=excluded.name, tp=excluded.tp,"
                    " status=excluded.status, adgroups=excluded.adgroups,"
                    " updated_at=excluded.updated_at",
                    (str(cu), x["nccCampaignId"], x.get("name"),
                     x.get("campaignTp"), x.get("status"),
                     n_by_camp.get(x["nccCampaignId"], 0), now))
            for g in grps:
                nm = (g.get("name") or "").strip()
                c.execute(
                    "INSERT OR REPLACE INTO ad_group (customer_id, adgroup_id,"
                    " campaign_id, name, lcp_code, status, bid, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (str(cu), g["nccAdgroupId"], g.get("nccCampaignId"), nm,
                     nm if nm.startswith("LCP_") else "", g.get("status"),
                     int(g.get("bidAmt") or 0), now))
        out["accounts"] += 1
        out["campaigns"] += len(camps)
        out["groups"] += len(grps)
        log(f"  [{cu}] 캠페인 {len(camps)} · 광고그룹 {len(grps)} · "
            f"비즈머니 {int(biz.get('bizmoney') or 0):,}원")
    return out


# ---- 읽기 · 고르기 ---------------------------------------------------
def accounts(only_on: bool = False) -> list:
    sql = "SELECT * FROM ad_account"
    if only_on:
        sql += " WHERE enabled=1"
    sql += " ORDER BY customer_id"
    with db.sqlite_conn() as c:
        ddl(c)
        return [dict(r) for r in c.execute(sql)]


def campaigns(customer: str = "", only_on: bool = False) -> list:
    sql, args = "SELECT * FROM ad_campaign WHERE 1=1", []
    if customer:
        sql += " AND customer_id=?"
        args.append(str(customer))
    if only_on:
        sql += " AND enabled=1"
    sql += " ORDER BY customer_id, name"
    with db.sqlite_conn() as c:
        ddl(c)
        return [dict(r) for r in c.execute(sql, args)]


def group_ids(customer: str, campaign_ids: list = None) -> list:
    """지출을 받아올 광고그룹. 캠페인을 고르면 그 캠페인 것만."""
    sql = "SELECT adgroup_id FROM ad_group WHERE customer_id=?"
    args = [str(customer)]
    if campaign_ids:
        sql += " AND campaign_id IN (%s)" % ",".join("?" * len(campaign_ids))
        args += list(campaign_ids)
    with db.sqlite_conn() as c:
        ddl(c)
        return [r["adgroup_id"] for r in c.execute(sql, args)]


def set_account(customer: str, enabled: bool = None, label: str = None):
    with db.sqlite_conn() as c:
        ddl(c)
        if enabled is not None:
            c.execute("UPDATE ad_account SET enabled=? WHERE customer_id=?",
                      (1 if enabled else 0, str(customer)))
        if label is not None:
            c.execute("UPDATE ad_account SET label=? WHERE customer_id=?",
                      (label, str(customer)))


def set_main(customer: str):
    """대시보드가 보여줄 계정을 하나 고른다. 나머지는 해제된다."""
    with db.sqlite_conn() as c:
        ddl(c)
        c.execute("UPDATE ad_account SET is_main=0")
        c.execute("UPDATE ad_account SET is_main=1, enabled=1"
                  " WHERE customer_id=?", (str(customer),))


def set_start(customer: str, day: str):
    with db.sqlite_conn() as c:
        ddl(c)
        c.execute("UPDATE ad_account SET start_date=? WHERE customer_id=?",
                  (day or None, str(customer)))


def detect_start(customer: str) -> str:
    """지출이 처음 잡힌 날. 설정이 비었을 때 제안값으로 쓴다."""
    with db.sqlite_conn() as c:
        r = c.execute(
            "SELECT MIN(day) d FROM ad_spend WHERE customer_id=? AND cost>0",
            (str(customer),)).fetchone()
        return (r["d"] if r and r["d"] else "") or ""


def start_of(customer: str = "") -> str:
    """그 계정의 광고 시작일. 없으면 빈 문자열."""
    with db.sqlite_conn() as c:
        ddl(c)
        if customer:
            r = c.execute("SELECT start_date FROM ad_account"
                          " WHERE customer_id=?", (str(customer),)).fetchone()
        else:
            r = c.execute("SELECT start_date FROM ad_account"
                          " WHERE is_main=1").fetchone()
        return (r["start_date"] if r and r["start_date"] else "") or ""


def main_account() -> dict:
    """메인 계정. 정해둔 것이 없으면 켜 둔 것 중 첫 번째."""
    with db.sqlite_conn() as c:
        ddl(c)
        r = c.execute("SELECT * FROM ad_account WHERE is_main=1").fetchone()
        if r:
            return dict(r)
        r = c.execute("SELECT * FROM ad_account WHERE enabled=1"
                      " ORDER BY customer_id").fetchone()
        return dict(r) if r else {}


def add(customer: str, label: str = ""):
    """계정 번호를 손으로 더한다 — 계정 목록을 주는 API 가 없다."""
    with db.sqlite_conn() as c:
        ddl(c)
        c.execute("INSERT OR IGNORE INTO ad_account (customer_id, label,"
                  " enabled, updated_at) VALUES (?,?,1,?)",
                  (str(customer), label or str(customer), db.now_str()))
        if label:
            c.execute("UPDATE ad_account SET label=? WHERE customer_id=?",
                      (label, str(customer)))


def set_campaign(customer: str, campaign_id: str, enabled: bool):
    with db.sqlite_conn() as c:
        ddl(c)
        c.execute("UPDATE ad_campaign SET enabled=? "
                  "WHERE customer_id=? AND campaign_id=?",
                  (1 if enabled else 0, str(customer), campaign_id))


def selection() -> list:
    """[{customer, campaigns:[id...]}] — 지출 수집이 볼 대상."""
    out = []
    for a in accounts(only_on=True):
        cs = [x["campaign_id"] for x in campaigns(a["customer_id"],
                                                  only_on=True)]
        out.append({"customer": a["customer_id"], "label": a["label"],
                    "campaigns": cs})
    return out
