"""
작업폴더의 LCP 를 순회하며 키워드·카테고리를 수집한다.

사용:
  python tools/collect_lcp.py            전체
  python tools/collect_lcp.py 10         앞 10건만
  python tools/collect_lcp.py 10 --redo  이미 수집한 것도 다시

이미 수집한 LCP 는 기본으로 건너뛰므로, 중간에 끊겨도 다시 돌리면 이어진다.
"""
import io
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app import db                      # noqa: E402
from app.lohas import collect           # noqa: E402
from app.lohas.session import get_client  # noqa: E402

LIMIT = 0
REDO = "--redo" in sys.argv
for a in sys.argv[1:]:
    if a.isdigit():
        LIMIT = int(a)


def log(m):
    print(m, flush=True)


def main():
    db.init_db()
    folder = db.get_job_folder()
    if not folder:
        log("!! 작업폴더가 지정되어 있지 않습니다.")
        return 1

    client = get_client(log=log)

    # 폴더의 '모든' LCP 를 대상으로 한다.
    # search_full(allow, none) 은 작업대상만 나오므로 상태 수집 결과(lcp_lcode)를
    # 우선 쓰고, 없으면 필터 없이(all/all) 검색한다.
    targets, seen = [], set()
    for r in db.lcode_rows(folder):
        if r["lcp_code"] not in seen:
            seen.add(r["lcp_code"])
            targets.append((r["lcp_code"], r["l_code"], r["product_no"]))
    if targets:
        log(f"L코드 상태 테이블에서 LCP {len(targets):,}종 확보")
    else:
        res = client.search_full(folder, "all", "all")
        for lcp, lcode, no in res["rows"]:
            if lcp and lcp not in seen:
                seen.add(lcp)
                targets.append((lcp, lcode, no))
        log(f"검색으로 LCP {len(targets):,}종 확보")

    done = set() if REDO else db.collected_lcps()
    todo = [t for t in targets if t[0] not in done]
    if LIMIT:
        todo = todo[:LIMIT]

    log(f"작업폴더 : {folder}")
    log(f"대상 LCP {len(targets):,}종 / 기수집 {len(targets) - len([t for t in targets if t[0] not in done]):,}종 "
        f"→ 이번 실행 {len(todo):,}종")
    log("-" * 66)

    t0 = time.time()
    ok = fail = 0
    agg = {"options": 0, "used": 0, "recommend": 0, "wish": 0,
           "tokens": 0, "categories": 0}
    errors = []

    for i, (lcp, lcode, no) in enumerate(todo, 1):
        t1 = time.time()
        try:
            d = collect.collect_one(client, lcp, no, log=lambda *a, **k: None)
            r = db.save_lcp_collect(d, folder)
            ok += 1
            for k in agg:
                agg[k] += r.get(k, 0)
            tie = d["tie"]
            top = (d.get("title_tokens") or [{}])[0]
            log(f"[{i:>4}/{len(todo)}] {lcp}  {(tie.get('product_name') or '')[:20]:22} "
                f"옵션{r['options']:>3} 사용{r['used']:>4} 추천{r['recommend']:>4} "
                f"토큰{r['tokens']:>3} 카테{r['categories']:>3}  "
                f"대표'{top.get('token', '-')}'  ({time.time() - t1:.1f}s)")
        except Exception as e:
            fail += 1
            msg = str(e)[:80]
            errors.append((lcp, msg))
            log(f"[{i:>4}/{len(todo)}] {lcp}  !! 실패: {msg}")

    el = time.time() - t0
    log("=" * 66)
    log(f"완료 {ok}건 / 실패 {fail}건 / {el:.0f}초 (건당 {el / max(ok + fail, 1):.1f}초)")
    log(f"수집   옵션 {agg['options']:,} / 사용 {agg['used']:,} / 추천 {agg['recommend']:,} / "
        f"희망 {agg['wish']:,} / 토큰 {agg['tokens']:,} / 카테고리 {agg['categories']:,}")
    log(f"누적   {db.lcp_collect_stats()}")
    if errors:
        log("-" * 66)
        log("실패 목록:")
        for lcp, m in errors[:20]:
            log(f"   {lcp}  {m}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log(traceback.format_exc())
        sys.exit(1)
