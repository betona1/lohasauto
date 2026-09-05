"""
태그 후보를 AI 가 검수했을 때 무엇이 빠지는지 본다. **저장하지 않는다.**

규칙(1000 미만 우선 / 태그사전+추천 순 / 상품별 분포)까지는 코드가 하지만,
'타사 브랜드' 와 '이 상품에 없는 기능' 은 데이터로 판정이 안 된다.
그 부분만 AI 에 물어 결과를 눈으로 보려고 만든 것이다.

    python -X utf8 tools/tag_ai_test.py --limit 5
    python -X utf8 tools/tag_ai_test.py --lcp LCP_LHA_B914616
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db                                        # noqa: E402
from app.lohas import (gemini, session as ses, tabs,      # noqa: E402
                       tag_auto)


def pick_lcps(limit: int, lcp: str = ""):
    """태그가 비어 있는 L코드를 가진 LCP. 후보가 많은 것부터 본다."""
    sql = ("SELECT lcp_code, COUNT(*) n FROM lcode_attr "
           "WHERE cat_saved = 1 AND tag_count = 0")
    args = []
    if lcp:
        sql += " AND lcp_code = ?"
        args.append(lcp)
    sql += " GROUP BY lcp_code ORDER BY n DESC"
    with db.sqlite_conn() as c:
        rows = [dict(r) for r in c.execute(sql, args)]
    return rows[:limit] if limit else rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--lcp", default="")
    args = ap.parse_args()

    if not gemini.available():
        print("Gemini API 키가 없습니다 (.env)")
        return

    cli = ses.get_client()
    vocab = tag_auto.brand_vocab()
    targets = pick_lcps(args.limit, args.lcp)
    print(f"시험 대상 {len(targets)}종 (저장하지 않습니다)\n")

    n_drop = 0
    for i, g in enumerate(targets, 1):
        lcp = g["lcp_code"]
        with db.sqlite_conn() as c:
            p = c.execute("SELECT product_name, brand, maker FROM lcp_product "
                          "WHERE lcp_code = ?", (lcp,)).fetchone()
            rows = [dict(r) for r in c.execute(
                "SELECT l_code, product_no FROM lcode_attr "
                "WHERE lcp_code = ? AND tag_count = 0 ORDER BY l_code", (lcp,))]
        name = (p["product_name"] if p else "") or ""
        brand = (p["brand"] if p else "") or ""
        maker = (p["maker"] if p else "") or ""

        own = tag_auto.own_words(lcp)
        pool = tag_auto._pool(
            tabs.fetch_tag_rows(cli.session, rows[0]["product_no"]),
            set(), own, vocab, None)
        if not pool:
            print(f"[{i}/{len(targets)}] {lcp}  {name[:40]}  후보 없음\n")
            continue

        ordered = tag_auto._order(pool)
        names = [c["name"] for c in ordered]
        res = gemini.filter_tags(name, names, brand, maker,
                                 log=lambda *_: None)
        drop = set(res["drop"])
        n_drop += len(drop)

        print(f"[{i}/{len(targets)}] {lcp}  {name[:44]}")
        print(f"    브랜드 {brand or '-'} / 제조사 {maker or '-'}"
              f" / L코드 {len(rows)}건 / 후보 {len(names)}개"
              f" / AI 응답 {'성공' if res['ok'] else '실패(규칙대로 진행)'}")
        if drop:
            print(f"    AI 가 뺀 것 {len(drop)}개: " + ", ".join(sorted(drop)))
        else:
            print("    AI 가 뺀 것 없음")

        keep = [c for c in ordered if c["name"] not in drop]
        shares = tag_auto.distribute(keep, len(rows))
        for r, share in zip(rows, shares):
            print(f"      {r['l_code']:10} "
                  + ", ".join(c["name"] for c in share))
        print()

    print(f"합계 — AI 가 뺀 태그 {n_drop}개")
    print("stats:", dict(gemini.stats))


if __name__ == "__main__":
    main()
