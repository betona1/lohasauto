"""
남은 카테고리 미저장분을 등급 가리지 않고 전부 저장한다.

`tools/save_category.py` 는 근거가 강한 등급(형제·압도적)만 저장했다.
이건 그 뒤에 남은 것 — 접전·용량·갈림 — 까지 저장한다. 등급마다 근거의
세기가 다르므로 고르는 방법이 다르다(category_plan.auto_choice 참고).

    접전   AI 판단이 있으면 그것, 없으면 규칙 (규칙만 쓰면 실측 74.1%)
    갈림   형제 L코드가 가장 많이 쓰는 카테고리
    용량   규칙 + 후보의 capacity/unit, 총 용량은 비운다

드라이런이 기본이다. `--apply` 를 줘야 실제로 저장하고, 저장 뒤에는
전건을 다시 읽어 실제 반영을 확인한다.

    python -X utf8 tools/save_category_all.py                 # 계획만
    python -X utf8 tools/save_category_all.py --apply         # 저장
    python -X utf8 tools/save_category_all.py --tier 접전 --apply
"""
import argparse
import collections
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db                                    # noqa: E402
from app.lohas import (attr_detail, category_plan as cp,   # noqa: E402
                       session as ses)

PLAN_PATH = "logs/category_plan_all.json"


def load_plan(session, use_cache: bool) -> list:
    if use_cache and os.path.exists(PLAN_PATH):
        plan = json.load(open(PLAN_PATH, encoding="utf-8"))
        print(f"[계획] 캐시 사용 {PLAN_PATH} — {len(plan)}종")
        return plan
    t0 = time.time()
    plan = cp.build(session, db, log=lambda m: None)
    print(f"[계획] 새로 조회 {len(plan)}종 ({time.time() - t0:.0f}초)")
    return plan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제로 저장한다")
    ap.add_argument("--tier", default="", help="이 등급만 (접전/용량/갈림)")
    ap.add_argument("--limit", type=int, default=0, help="앞에서 N종만")
    ap.add_argument("--no-cache", action="store_true", help="계획을 새로 조회")
    ap.add_argument("--no-ai", action="store_true", help="접전도 규칙값으로")
    args = ap.parse_args()

    cli = ses.get_client()
    plan = load_plan(cli.session, not args.no_cache)
    if args.tier:
        plan = [p for p in plan if p.get("tier") == args.tier]
    if args.limit:
        plan = plan[:args.limit]

    jobs = []
    for p in plan:
        if p.get("done"):
            continue
        ch = cp.auto_choice(p, use_ai=not args.no_ai)
        if ch.get("code"):
            jobs.append({"item": p, **ch})

    n_rows = sum(len(j["item"].get("rows") or []) for j in jobs)
    by_src = collections.Counter(j["source"] for j in jobs)
    by_tier = collections.Counter(j["tier"] for j in jobs)
    print(f"\n대상 {len(jobs):,}종 / L코드 {n_rows:,}건")
    print("  등급 :", dict(by_tier))
    print("  근거 :", dict(by_src))

    if not args.apply:
        print("\n[드라이런] --apply 를 주면 저장합니다. 앞 10종 미리보기:")
        for j in jobs[:10]:
            it = j["item"]
            print(f"   {it['lcp_code']}  {len(it['rows']):>2}건  "
                  f"[{j['tier']}/{j['source']}]  {j['code']}  {j['name'][:48]}")
        return

    ok = fail = 0
    saved = []
    t0 = time.time()
    for i, j in enumerate(jobs, 1):
        it = j["item"]
        res = cp.save_group(cli.session, it, j["code"],
                            capacity=j["capacity"], unit=j["unit"],
                            total_capacity=j["total_capacity"])
        ok += res["ok"]
        fail += res["fail"]
        saved.extend(res["saved"])
        print(f"[{i}/{len(jobs)}] {it['lcp_code']} -> {j['code']} "
              f"({j['source']}) 저장 {res['ok']}건"
              + (f" / 실패 {res['fail']}건" if res["fail"] else ""))

    print(f"\n저장 {ok:,}건 / 실패 {fail:,}건 ({time.time() - t0:.0f}초)")

    # 저장한 것을 사이트에서 다시 읽어 실제로 들어갔는지 본다.
    print(f"[검증] {len(saved):,}건 재조회...")
    rows, bad = [], 0
    for r in saved:
        try:
            d = attr_detail.fetch_detail(cli.session, r["product_no"])
            d["lcp_code"] = r["lcp_code"]
            d["l_code"] = r["l_code"]
            rows.append(d)
            if not d["cat_saved"]:
                bad += 1
        except Exception:
            bad += 1
    if rows:
        db.save_lcode_attr(db.get_job_folder(), rows)
    print(f"[검증] 반영확인 {len(rows) - bad:,}건 / 미반영 {bad:,}건")


if __name__ == "__main__":
    main()
