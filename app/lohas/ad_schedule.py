"""
광고 노출시간(요일/시간대) 설정을 **바꾼 이력**으로 남긴다.

노출시간을 바꾸면 그날부터 지출 곡선이 바뀐다. 기록이 없으면 나중에
"이 날 왜 갑자기 줄었지?" 를 알 수 없다 — 그래서 바꿀 때마다 한 줄
남기고, 그래프에 그 날을 표시한다(2026-09-16 사용자: 아침 8시~밤 10시로
바꿨다).

사이트에서 실제로 바꾸는 것은 사람이 한다. 여기는 **기록과 해석**만 맡는다.
"""
import datetime

from .. import db


def _ddl(c):
    c.execute("""CREATE TABLE IF NOT EXISTS ad_schedule (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id TEXT,
        from_day    TEXT NOT NULL,      -- 이 날부터 적용
        start_hour  INTEGER,            -- 08 = 오전 8시
        end_hour    INTEGER,            -- 22 = 밤 10시 (이 시각 전까지)
        days        TEXT,               -- 요일 (비우면 매일)
        memo        TEXT,
        created_at  TEXT
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_sched_day"
              " ON ad_schedule(from_day)")


def add(customer: str, from_day: str, start_hour: int, end_hour: int,
        days: str = "", memo: str = "") -> int:
    with db.sqlite_conn() as c:
        _ddl(c)
        cur = c.execute(
            "INSERT INTO ad_schedule (customer_id, from_day, start_hour,"
            " end_hour, days, memo, created_at) VALUES (?,?,?,?,?,?,?)",
            (str(customer), from_day, int(start_hour), int(end_hour),
             days, memo, db.now_str()))
        return cur.lastrowid


def history(customer: str = "", limit: int = 50) -> list:
    sql = "SELECT * FROM ad_schedule"
    a = []
    if customer:
        sql += " WHERE customer_id=?"
        a.append(str(customer))
    sql += " ORDER BY from_day DESC, id DESC LIMIT ?"
    a.append(limit)
    with db.sqlite_conn() as c:
        _ddl(c)
        return [dict(r) for r in c.execute(sql, a)]


def current(customer: str = "", day: str = None) -> dict:
    """그날 적용되던 노출시간. 없으면 빈 dict."""
    day = day or datetime.date.today().isoformat()
    sql = "SELECT * FROM ad_schedule WHERE from_day<=?"
    a = [day]
    if customer:
        sql += " AND customer_id=?"
        a.append(str(customer))
    sql += " ORDER BY from_day DESC, id DESC LIMIT 1"
    with db.sqlite_conn() as c:
        _ddl(c)
        r = c.execute(sql, a).fetchone()
        return dict(r) if r else {}


def label(row: dict) -> str:
    if not row:
        return "설정 없음 (24시간)"
    s, e = row.get("start_hour"), row.get("end_hour")
    if s is None or e is None:
        return "설정 없음"
    d = row.get("days") or "매일"
    return f"{d} {int(s):02d}:00~{int(e):02d}:00"


def marks(days: int = 30, customer: str = "") -> dict:
    """{날짜: 설명} — 그래프에 세로선을 그을 자리."""
    since = (datetime.date.today()
             - datetime.timedelta(days=days - 1)).isoformat()
    out = {}
    for r in history(customer, 200):
        if r["from_day"] >= since:
            out[r["from_day"]] = label(r) + (
                f" · {r['memo']}" if r.get("memo") else "")
    return out


def outside_spend(customer: str, days: int = 7) -> list:
    """
    **설정한 시간 밖에서 나간 광고비.** 노출시간을 줄였는데도 그 밖에서
    돈이 나가면 설정이 안 먹은 것이다.

    반환 [{day, in_cost, out_cost, out_hours}]
    """
    from . import ad_detail
    rows = ad_detail.by_day("hour", customer, days=days, limit=5000)
    by_day = {}
    for r in rows:
        by_day.setdefault(r["day"], {})[str(r["k"])] = int(r["cost"] or 0)
    out = []
    for day in sorted(by_day, reverse=True):
        sc = current(customer, day)
        s = sc.get("start_hour")
        e = sc.get("end_hour")
        i_c = o_c = 0
        o_h = []
        for h, c in by_day[day].items():
            try:
                hh = int(h)
            except ValueError:
                continue
            inside = True if s is None else (s <= hh < e)
            if inside:
                i_c += c
            else:
                o_c += c
                if c:
                    o_h.append(f"{hh:02d}시 {c:,}원")
        out.append({"day": day, "in_cost": i_c, "out_cost": o_c,
                    "sched": label(sc), "out_hours": ", ".join(sorted(o_h))})
    return out
