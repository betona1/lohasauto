"""
**돈은 나갔는데 태그에 없는 검색어** 찾기.

광고 대용량 보고서의 검색어와, 그 LCP 에 지금 저장된 태그를 맞춰 본다.
네트워크를 쓰지 않는다 — 둘 다 이미 DB 에 있다.

    python -X utf8 tools/ad_gap.py                 # 클릭 난 것만
    python -X utf8 tools/ad_gap.py --days 30 --top 40
    python -X utf8 tools/ad_gap.py --lcp LCP_LHA_B913916

여기 나온 말을 **그대로 태그로 넣으면 안 된다.** 태그는 로하스 태그 후보
표에서만 고르는 것이 절대규칙이다. 이 목록은 '이 말로 팔리고 있으니
후보에 있으면 챙기고, 없으면 사람이 판단하라' 는 뜻이다.
"""
import argparse
import datetime
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

from app import db                                      # noqa: E402
from app.lohas.ad_keyword import _norm                  # noqa: E402


def saved_tags(lcp: str) -> set:
    """그 LCP 의 L코드들에 저장된 태그를 다 모은다."""
    out = set()
    with db.sqlite_conn() as c:
        for r in c.execute("SELECT tags FROM lcode_attr WHERE lcp_code=?",
                           (lcp,)):
            for t in (r["tags"] or "").replace("|", ",").split(","):
                t = t.strip()
                if t:
                    out.add(_norm(t))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--lcp", default="")
    ap.add_argument("--min-click", type=int, default=1)
    args = ap.parse_args()

    db.init_db()
    since = (datetime.date.today()
             - datetime.timedelta(days=args.days)).isoformat()
    sql = ("SELECT lcp_code, query, SUM(cost) cost, SUM(clk) clk,"
           " SUM(imp) imp FROM ad_detail"
           " WHERE day>=? AND lcp_code<>'' AND query<>''")
    a = [since]
    if args.lcp:
        sql += " AND lcp_code=?"
        a.append(args.lcp)
    sql += " GROUP BY lcp_code, query HAVING SUM(clk)>=? ORDER BY cost DESC"
    a.append(args.min_click)
    with db.sqlite_conn() as c:
        rows = [dict(r) for r in c.execute(sql, a)]
    if not rows:
        print("광고 상세 기록이 없습니다 — tools/ad_detail.py --collect 먼저")
        return 0

    cache, gaps, hit = {}, [], 0
    for r in rows:
        tags = cache.get(r["lcp_code"])
        if tags is None:
            tags = cache[r["lcp_code"]] = saved_tags(r["lcp_code"])
        q = _norm(r["query"])
        covered = q in tags or any(
            len(t) >= 3 and (t in q or q in t) for t in tags)
        if covered:
            hit += 1
        else:
            gaps.append(r)

    tot = len(rows)
    print(f"검색어 {tot:,}개 (클릭 {args.min_click}회 이상, 최근 {args.days}일)")
    print(f"  태그로 덮인 것   {hit:,}개 ({hit * 100 // max(tot, 1)}%)")
    print(f"  안 덮인 것       {len(gaps):,}개 "
          f"· 광고비 {sum(int(g['cost'] or 0) for g in gaps):,}원")
    print("=" * 62)
    print("돈은 나갔는데 태그에 없는 검색어")
    for g in gaps[:args.top]:
        print(f"  {g['query'][:24]:26} {int(g['cost'] or 0):>7,}원 "
              f"클릭 {int(g['clk'] or 0):>3}  {g['lcp_code']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
