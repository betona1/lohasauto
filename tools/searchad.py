"""
네이버 검색광고 광고주센터 조회 — 캠페인·그룹·키워드·비용·비즈머니.

크롤링이 아니라 공개 API 다. 로그인도 2단계 인증도 필요 없다.
읽기 전용이라 광고를 건드리지 않는다.

    python -X utf8 tools/searchad.py                 # 요약 (모든 광고계정)
    python -X utf8 tools/searchad.py --days 30       # 최근 30일 비용
    python -X utf8 tools/searchad.py --detail        # 그룹·키워드까지
    python -X utf8 tools/searchad.py --customer 4464788
"""
import argparse
import datetime
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

from app.lohas import searchad as sa                 # noqa: E402


def won(v) -> str:
    return f"{int(v or 0):,}원"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--detail", action="store_true")
    ap.add_argument("--customer", default="")
    args = ap.parse_args()

    if not sa.available():
        print("!! .env 에 네이버 검색광고 API 키가 없습니다")
        return 1
    until = datetime.date.today()
    since = until - datetime.timedelta(days=args.days)
    custs = [args.customer] if args.customer else sa.customers()
    print(f"조회 기간 {since} ~ {until}   광고계정 {', '.join(custs)}")

    grand = 0
    for cu in custs:
        print("=" * 62)
        biz = sa.bizmoney(cu)
        bal = biz.get("bizmoney")
        warn = "  ← 잔액 없음! 광고가 멈춥니다" if (bal or 0) <= 0 else ""
        print(f"[{cu}] 비즈머니 {won(bal)}{warn}"
              + ("  (예산잠금)" if biz.get("budgetLock") else ""))
        for h in sa.charge_history(cu, since, until):
            t = datetime.datetime.fromtimestamp(
                (h.get("statDt") or 0) / 1000).strftime("%m-%d %H:%M")
            print(f"     충전 {t}  {h.get('displayName','')}  "
                  f"{won(h.get('newRefundableAmt'))}")

        camps = sa.campaigns(cu)
        if not camps:
            print("     캠페인 없음")
            continue
        name = {c["nccCampaignId"]: c for c in camps}
        rows = sa.stats(cu, list(name), since, until)
        cost = {r.get("id"): r for r in rows}
        sub = 0
        for cid, c in name.items():
            r = cost.get(cid, {})
            amt = int(r.get("salesAmt") or 0)
            sub += amt
            print(f"     {c['name'][:22]:24} {c.get('status',''):10}"
                  f" 노출 {int(r.get('impCnt') or 0):>8,}"
                  f" 클릭 {int(r.get('clkCnt') or 0):>6,}"
                  f" 비용 {amt:>9,}")
            if not args.detail:
                continue
            for g in sa.adgroups(cu, cid):
                kw = sa.keywords(cu, g["nccAdgroupId"])
                print(f"        └ {g.get('name','')[:20]:22}"
                      f" 키워드 {len(kw):>3}개")
        print(f"     {args.days}일 지출 합계 {won(sub)}")
        grand += sub
    print("=" * 62)
    print(f"전체 {args.days}일 지출 {won(grand)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
