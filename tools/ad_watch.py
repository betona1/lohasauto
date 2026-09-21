"""
**3시간마다 도는 가벼운 감시.**

`ad_daily.py` 는 대용량 보고서까지 받느라 몇 분씩 걸려서 하루 한 번이
맞다. 그런데 그러면 **낮에 바꾼 입찰가를 다음 날 새벽에야 잡는다** —
9/18 에 200원을 150원으로 내리신 것이 `2026-09-18 11:18` 로, 실제 바꾼
시각이 아니라 수집한 시각으로 남았다(2026-09-18 사용자: 수집 주기를 3시간
마다로 늘려줘).

그래서 **빨리 끝나는 것만** 따로 뽑아 3시간마다 돌린다.

    입찰가·잔액   ad_account.collect   전 값과 달라지면 ad_bid_history 에 남는다
    오늘 광고비   ad_spend.collect_all /stats 는 당일치가 실시간으로 나온다
    오늘 매출     ad_sales.collect     커머스 API
    오늘 이익     ad_profit.collect    주문 DB 의 정산·원가

대용량 보고서(매체·검색어·시간대)는 **건드리지 않는다.** 그건 어제까지
것만 만들어지므로 새벽에 한 번이면 된다.

    python -X utf8 tools/ad_watch.py              3일치
    python -X utf8 tools/ad_watch.py --days 7     7일치
    python -X utf8 tools/ad_watch.py --quiet      바뀐 것이 있을 때만 적는다
"""
import argparse
import datetime
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db                                              # noqa: E402
from app.lohas import (ad_account, ad_profit, ad_sales,         # noqa: E402
                       ad_spend, searchad as sa)

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace")


def log(msg):
    print(f"{db.now_str()}  {msg}", flush=True)


def bids_since(when: str) -> list:
    """그 시각 이후에 남은 입찰가 변경."""
    with db.sqlite_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT h.customer_id, a.label, h.old_bid, h.new_bid, COUNT(*) n"
            " FROM ad_bid_history h"
            " LEFT JOIN ad_account a ON a.customer_id=h.customer_id"
            " WHERE h.changed_at>? GROUP BY h.customer_id, h.old_bid,"
            " h.new_bid ORDER BY n DESC", (when,))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3,
                    help="지출·매출을 며칠치 다시 받을지")
    ap.add_argument("--quiet", action="store_true",
                    help="바뀐 것이 없으면 한 줄만 적는다")
    args = ap.parse_args()

    db.init_db()
    if not sa.available():
        log("!! .env 에 네이버 검색광고 API 키가 없습니다")
        return 1

    mark = db.now_str()
    changed = []

    # 1) 계정·입찰가 — **이게 이 스크립트의 본 목적이다**
    try:
        r = ad_account.collect(log=lambda *_: None)
        hits = bids_since(mark)
        for h in hits:
            o, n = int(h["old_bid"] or 0), int(h["new_bid"] or 0)
            changed.append(
                f"입찰가 {h['label'] or h['customer_id']} "
                f"{o:,}원 → {n:,}원  {h['n']}개 그룹")
        if not args.quiet or hits:
            log(f"계정 {r['accounts']}개 · 그룹 {r['groups']:,}개 확인"
                + (f"  ※ 입찰가 변경 {sum(h['n'] for h in hits)}건"
                   if hits else ""))
        for cu in ad_account.accounts():
            if not args.quiet:
                log(f"  {cu['label']:16} 잔액 "
                    f"{int(cu.get('bizmoney') or 0):>9,}원")
            if int(cu.get("bizmoney") or 0) < 30000 and cu.get("is_main"):
                changed.append(
                    f"⚠ {cu['label']} 잔액 "
                    f"{int(cu['bizmoney']):,}원 — 충전이 필요합니다")
    except Exception as e:
        log(f"계정 실패: {str(e)[:110]}")

    # 2) 오늘 광고비 — /stats 는 당일치가 실시간으로 나온다
    try:
        r = ad_spend.collect_all(days=args.days, log=lambda *_: None)
        today = datetime.date.today().isoformat()
        with db.sqlite_conn() as c:
            row = c.execute(
                "SELECT SUM(cost) c, SUM(clk) k, SUM(imp) i FROM ad_spend"
                " WHERE level='campaign' AND day=?", (today,)).fetchone()
        cost, clk = int(row[0] or 0), int(row[1] or 0)
        log(f"오늘 광고비 {cost:,}원 · 클릭 {clk:,} · 노출 "
            f"{int(row[2] or 0):,}"
            + (f"  ·  CPC {cost // clk:,}원" if clk else ""))
    except Exception as e:
        log(f"광고비 실패: {str(e)[:110]}")

    # 3) 오늘 매출·이익
    try:
        ad_sales.collect(days=args.days, log=lambda *_: None)
        s = ad_sales.today()
        log(f"오늘 실매출 {int(s.get('amount') or 0):,}원 "
            f"({int(s.get('orders') or 0)}건)")
    except Exception as e:
        log(f"매출 실패: {str(e)[:110]}")
    try:
        ad_profit.collect(days=max(args.days, 7), log=lambda *_: None)
        t = datetime.date.today().isoformat()
        p = ad_profit.by_day(t, t)
        p0 = p[0] if p else {}
        log(f"오늘 이익 {int(p0.get('profit') or 0):,}원 − 광고비 "
            f"{int(p0.get('ad') or 0):,}원 = 실수입 "
            f"{int(p0.get('net') or 0):,}원")
    except Exception as e:
        log(f"이익 실패: {str(e)[:110]}")

    # 4) **웹 DB(joachamproduct)로 보내기.** 웹이 읽는 곳은 거기다.
    #    SQLite 가 원본이고 이쪽은 늘 덮어쓴다(2026-09-21 사용자: DB 통합).
    try:
        import jp_sync
        r = jp_sync.run(quiet=True)
        log(f"웹 DB 반영 {r['rows']:,}행 ({r['since']} 이후)")
    except Exception as e:
        log(f"웹 DB 반영 실패: {str(e)[:110]}")

    for c in changed:
        log(f"  ★ {c}")
    db.set_setting("ad_watch_last", db.now_str())
    return 0


if __name__ == "__main__":
    sys.exit(main())
