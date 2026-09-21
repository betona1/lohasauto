"""
ALL 카테고리 — 폴더 하나의 카테고리를 끝까지 채운다. (화면의 「🗂 ALL 카테고리」)

대상은 **상품정보가 '미작업' 인 것만**이고, 대표이미지 상태는 가리지 않는다.
카테고리는 이미지 작업과 상관없는 선행 단계다(2026-09-09 사용자 지시).

    python -X utf8 tools/category_all.py --folder "595. 광고진행-엑사"
    ... --apply            실제로 저장 (기본은 드라이런)
    ... --no-refresh       작업상태 다시 읽기를 건너뛴다 (이미 최신일 때)
    ... --tier 형제,압도적  이 등급만

카테고리 저장은 **상품정보 상태를 바꾸지 않는다.** 태그·상품명 저장과 다르다.
"""
import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

from app import db                                        # noqa: E402
from app.lohas import attr_detail, category_plan as cp    # noqa: E402
from app.lohas.session import get_client                  # noqa: E402


def arg(name, default=""):
    for i, a in enumerate(sys.argv):
        if a == name and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def log(m):
    print(m, flush=True)


def main():
    db.init_db()
    folder = arg("--folder") or db.get_job_folder()
    apply_ = "--apply" in sys.argv
    tiers = tuple(x for x in arg("--tier").split(",") if x)
    if not folder:
        log("!! 작업폴더 미지정"); return 1

    client = get_client(log=log)
    s = client.session
    t0 = time.time()
    log(f"작업폴더 : {folder}")

    if "--no-refresh" not in sys.argv:
        rows = db.lcode_rows(folder)
        log(f"[1/3] 작업상태 다시 읽기 — L코드 {len(rows):,}건")
        res = attr_detail.collect_folder(client, rows, log=log)
        if res["rows"]:
            st = db.save_lcode_attr(folder, res["rows"])
            log(f"      DB {st['rows']:,}행  {st.get('mirror', '')}")

    log("[2/3] 카테고리 후보 조회 · 등급 매기기")
    # **`lcp_lcode` 에 없는 L코드가 있다.** 점검(12칸)이 그 상품을 못 담으면
    # info_status 가 비고, todo_only 가 통째로 걸러낸다 — 158건이 그렇게
    # 영영 안 보였다(2026-09-15). `--any-status` 로 그 필터를 푼다.
    todo = "--any-status" not in sys.argv
    plan = cp.build(s, db, folder, tiers=tiers, log=log, todo_only=todo)
    plan = [p for p in plan if p.get("candidates")]
    by_tier = {}
    for p in plan:
        by_tier[p.get("tier")] = by_tier.get(p.get("tier"), 0) + 1
    n_rows = sum(len(p["rows"]) for p in plan)
    log(f"      대상 {len(plan):,}종 / {n_rows:,}건  "
        + " · ".join(f"{k} {v}종" for k, v in sorted(by_tier.items())))

    if not apply_:
        log("\n[드라이런] --apply 를 주면 저장합니다. 앞 15종:")
        for p in plan[:15]:
            ch = cp.auto_choice(p, use_ai=False)
            log(f"   {p['lcp_code']}  {len(p['rows']):>3}건  "
                f"{ch.get('code')} {ch.get('name', '')[:28]}  "
                f"({p.get('tier')}/{ch.get('source')})")
        return 0

    log(f"[3/3] 저장 — {len(plan):,}종")
    ok = fail = 0
    saved = []
    for i, item in enumerate(plan, 1):
        ch = cp.auto_choice(item, use_ai=False)
        if not ch:
            continue
        r = cp.save_group(s, item, ch["code"], capacity=ch.get("capacity", ""),
                          unit=ch.get("unit", ""),
                          total_capacity=ch.get("total_capacity", ""), log=log)
        ok += r["ok"]; fail += r["fail"]; saved.extend(r["saved"])
        log(f"  [{i}/{len(plan)}] {item['lcp_code']} {ch['code']} "
            f"{ch['name'][:24]} ({ch['tier']}/{ch['source']}) 저장 {r['ok']}건"
            + (f" 실패 {r['fail']}" if r["fail"] else ""))

    done = []
    if saved:
        log(f"저장분 {len(saved):,}건 재조회 중...")
        for r in saved:
            try:
                d = attr_detail.fetch_detail(s, r["product_no"])
                d["lcp_code"] = r["lcp_code"]; d["l_code"] = r["l_code"]
                done.append(d)
            except Exception:
                pass
        if done:
            st = db.save_lcode_attr(folder, done)
            log(f"로컬 DB {st['rows']:,}행  {st.get('mirror', '')}")
        bad = [d for d in done if not d["cat_saved"]]
        if bad:
            log(f"!! 반영 안 된 건 {len(bad)}건")
    log("=" * 60)
    log(f"LCP {len(plan):,}종 / 저장 {ok:,}건 · 실패 {fail:,}건 · "
        f"확인 {len(done):,}건 ({(time.time() - t0) / 60:.1f}분)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
