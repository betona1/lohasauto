"""
분석이 빠진 L코드를 **강제로** 다시 분석한다.

`run_analysis.py` 는 `db.done_lcp_set()` 에 든 LCP 를 건너뛴다. 그런데 그
기록이 있어도 **사이트에는 분석일이 없는 LCP** 가 있다 — 그런 건 영영
다시 안 돌아 카테고리·태그·상품명이 줄줄이 막힌다(2026-09-13 실측:
LCP_LHA_B905703 아이패드 강화유리 19건).

여기서는 `lcode_attr.analysis_done=0` 인 L코드를 모아 그 LCP 를
**완료 기록과 무관하게** 다시 건다. 먼저 사이트를 확인해서
  · 이미 분석돼 있으면  → DB 만 고친다 (사이트를 건드리지 않는다)
  · 정말 없으면        → 분석을 건다

    python -X utf8 tools/reanalyze.py                 # 무엇을 할지만
    python -X utf8 tools/reanalyze.py --apply
    python -X utf8 tools/reanalyze.py --apply --folder "596. 광고진행-조아참"
"""
import argparse
import collections
import io
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

from app import config, db                                  # noqa: E402
from app.lohas import analysis, tabs                        # noqa: E402
from app.lohas.session import get_client                    # noqa: E402


def log(msg=""):
    print(msg, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--folder", default="")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    db.init_db()
    sql = ("SELECT folder_name, lcp_code, l_code, product_no FROM lcode_attr"
           " WHERE analysis_done=0")
    a = []
    if args.folder:
        sql += " AND folder_name=?"
        a.append(args.folder)
    sql += " ORDER BY folder_name, lcp_code, l_code"
    with db.sqlite_conn() as c:
        rows = [dict(r) for r in c.execute(sql, a)]
    if not rows:
        log("분석 빠진 L코드가 없습니다"); return 0

    by_lcp = collections.OrderedDict()
    for r in rows:
        by_lcp.setdefault(r["lcp_code"], []).append(r)
    log(f"분석 빠진 L코드 {len(rows):,}건 / LCP {len(by_lcp):,}종")
    if args.limit:
        by_lcp = collections.OrderedDict(
            list(by_lcp.items())[:args.limit])

    client = get_client(log=lambda *_: None)
    s = client.session
    fixed = ran = fail = 0
    for i, (lcp, rs) in enumerate(by_lcp.items(), 1):
        head = rs[0]
        try:
            at = tabs.fetch_attr(s, head["product_no"])
        except Exception as e:
            log(f"[{i}/{len(by_lcp)}] {lcp} 조회 실패 {str(e)[:50]}")
            fail += 1
            continue
        date = str(at.get("analysis_date") or "")
        if date.startswith("20"):
            # 사이트에는 있다 — 우리 기록만 낡았다
            log(f"[{i}/{len(by_lcp)}] {lcp} 이미 분석됨({date[:10]}) "
                f"— DB 만 고칩니다 {len(rs)}건")
            if args.apply:
                with db.sqlite_conn() as c:
                    c.executemany(
                        "UPDATE lcode_attr SET analysis_done=1,"
                        " analysis_date=? WHERE l_code=?",
                        [(date, r["l_code"]) for r in rs])
            fixed += len(rs)
            continue
        log(f"[{i}/{len(by_lcp)}] {lcp} 분석 없음 — 겁니다 ({len(rs)}건)")
        if not args.apply:
            continue
        try:
            info = analysis.fetch_popup(s, head["product_no"])
            st = analysis.start_analysis(info)
            if not st.get("ok"):
                log(f"      시작 실패 {st.get('msg', '')[:70]}")
                fail += 1
                continue
            no = st["analysis_no"]
            t0 = time.time()
            ok = False
            while time.time() - t0 < config.ANALYSIS_TIMEOUT:
                ck = analysis.check_analysis(info, no)
                if ck.get("done"):
                    ok = True
                    break
                if ck.get("state") not in ("P", "?"):
                    break
                time.sleep(config.ANALYSIS_POLL)
            log(f"      {'완료' if ok else '시간초과/실패'} "
                f"({time.time() - t0:.0f}초)")
            ran += ok
            fail += (not ok)
        except Exception as e:
            log(f"      !! {str(e)[:70]}")
            fail += 1
        time.sleep(1)

    log("=" * 58)
    log(f"DB만 고침 {fixed:,}건 · 새로 분석 {ran:,}종 · 실패 {fail}")
    if not args.apply:
        log("[드라이런] --apply 를 주면 실제로 합니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
