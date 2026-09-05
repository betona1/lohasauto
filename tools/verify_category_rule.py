"""
'건수 1위' 규칙의 정확도 실측 (읽기 전용).

이미 사람이 카테고리를 저장해둔 LCP 를 정답지로 삼아, 규칙이 같은 값을
골랐는지 센다. 자동 저장을 어디까지 믿고 맡길지 판단하는 근거다.
"""
import io
import json
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app import db                        # noqa: E402
from app.lohas import category            # noqa: E402
from app.lohas.session import get_client  # noqa: E402

LIMIT = next((int(a) for a in sys.argv[1:] if a.isdigit()), 0)


def log(m):
    print(m, flush=True)


def answers():
    """카테고리가 전부 저장됐고 한 가지로 통일된 LCP -> 정답 코드."""
    out = {}
    with db.sqlite_conn() as c:
        for r in c.execute(
                "select lcp_code, count(*) n, sum(cat_saved) saved, "
                "count(distinct etc_category) k, "
                "min(etc_category) code from lcode_attr "
                "group by lcp_code having saved=n and k=1"):
            out[r["lcp_code"]] = {"code": str(r["code"]), "lcodes": r["n"]}
    return out


def main():
    db.init_db()
    gt = answers()
    names = list(gt)[:LIMIT] if LIMIT else list(gt)
    log(f"정답지 : LCP {len(names):,}종 / L코드 "
        f"{sum(gt[n]['lcodes'] for n in names):,}건")
    log("-" * 70)

    s = get_client(log=log).session
    hit = miss = err = 0
    hit_l = miss_l = 0
    bands = {}          # 격차대 -> [맞음, 전체]
    wrong = []
    t0 = time.time()

    for i, lcp in enumerate(names, 1):
        try:
            cands = category.fetch_candidates(s, lcp)
        except Exception:
            err += 1
            continue
        if not cands:
            err += 1
            continue
        top = str(cands[0].get("code") or "")
        c1 = int(str(cands[0].get("cnt") or 0) or 0)
        c2 = int(str(cands[1].get("cnt") or 0) or 0) if len(cands) > 1 else 0
        band = ("압도적" if c1 >= 3 * max(1, c2)
                else "우세" if c1 >= 1.5 * max(1, c2) else "접전")
        b = bands.setdefault(band, [0, 0])
        b[1] += 1

        real = gt[lcp]["code"]
        if top == real:
            hit += 1
            hit_l += gt[lcp]["lcodes"]
            b[0] += 1
        else:
            miss += 1
            miss_l += gt[lcp]["lcodes"]
            names_by = {str(x.get("code")): x.get("name") for x in cands}
            wrong.append({
                "lcp_code": lcp, "lcodes": gt[lcp]["lcodes"], "band": band,
                "rule": top, "rule_name": names_by.get(top, ""),
                "rule_cnt": c1,
                "real": real, "real_name": names_by.get(real, "(후보에 없음)"),
                "real_cnt": next((int(str(x.get("cnt") or 0) or 0)
                                  for x in cands
                                  if str(x.get("code")) == real), 0),
            })
        if i % 50 == 0:
            log(f"  {i:,}/{len(names):,} ({time.time() - t0:.0f}초)")

    tot = hit + miss
    log("-" * 70)
    log(f"검증 {tot:,}종 (조회실패 {err})   {time.time() - t0:.0f}초")
    log(f"  일치 {hit:,}종 ({hit / max(1, tot) * 100:.1f}%)   "
        f"L코드 {hit_l:,}건")
    log(f"  불일치 {miss:,}종 ({miss / max(1, tot) * 100:.1f}%)   "
        f"L코드 {miss_l:,}건")
    log("")
    log("1·2위 격차대별 정확도:")
    for b in ("압도적", "우세", "접전"):
        if b in bands:
            h, n = bands[b]
            log(f"   {b:>4}  {h:>3,}/{n:>3,}  {h / max(1, n) * 100:5.1f}%")

    if wrong:
        log("")
        log("불일치 예시 (규칙 -> 실제):")
        for w in sorted(wrong, key=lambda x: -x["lcodes"])[:12]:
            log(f"   {w['lcp_code']} L{w['lcodes']:>2}건 [{w['band']}]")
            log(f"      규칙 {w['rule_cnt']:>4}개  {w['rule_name'][:52]}")
            log(f"      실제 {w['real_cnt']:>4}개  {w['real_name'][:52]}")

    out = Path(__file__).resolve().parent.parent / "logs" / "category_rule_eval.json"
    out.write_text(json.dumps(
        {"hit": hit, "miss": miss, "bands": bands, "wrong": wrong},
        ensure_ascii=False, indent=1), encoding="utf-8")
    log("")
    log(f"상세 저장 -> {out}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log(traceback.format_exc())
        sys.exit(1)
