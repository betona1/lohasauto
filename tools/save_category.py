"""
카테고리 저장 — 기본은 드라이런(실제 저장 안 함).

  python tools/save_category.py                 앞 10종 미리보기
  python tools/save_category.py --all           전량 미리보기 + 리포트
  python tools/save_category.py --all --basis 형제 --apply    근거 등급으로 저장
  python tools/save_category.py --lcp LCP_...  --apply        한 LCP 만

등급과 그 정확도는 app/lohas/category_plan.py 에 정의돼 있다. 화면
(카테고리 검토 탭)과 같은 판정을 쓰므로 둘의 결과가 어긋나지 않는다.

--all 드라이런은 logs/category_plan.json 에 계획을 남긴다.
"""
import io
import json
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app import db                         # noqa: E402
from app.lohas import attr_detail          # noqa: E402
from app.lohas import category_plan as cp  # noqa: E402
from app.lohas.session import get_client   # noqa: E402

ARGS = sys.argv[1:]
LIMIT = next((int(a) for a in ARGS if a.isdigit()),
             10 ** 9 if "--all" in ARGS else 10)
APPLY = "--apply" in ARGS
ONLY = next((ARGS[i + 1] for i, a in enumerate(ARGS) if a == "--lcp"), "")
BASIS = [x for x in next(
    (ARGS[i + 1] for i, a in enumerate(ARGS) if a == "--basis"), ""
).split(",") if x]


def log(m):
    print(m, flush=True)


def main():
    db.init_db()
    folder = db.get_job_folder()
    client = get_client(log=log)
    s = client.session

    log(f"모드 : {'★ 실제 저장' if APPLY else '드라이런 (저장 안 함)'}")
    t0 = time.time()
    plan = cp.build(s, db, folder, only=ONLY, log=log)[:LIMIT]
    log(f"대상 : LCP {len(plan):,}종 / L코드 "
        f"{sum(len(p['rows']) for p in plan):,}건  ({time.time() - t0:.0f}초)")
    log("-" * 74)

    if not APPLY:
        preview(plan)
        report(plan)
        return 0

    todo = [p for p in plan if p.get("auto") or BASIS]
    todo = [p for p in todo if not BASIS or p.get("tier") in BASIS]
    if not todo:
        log("저장 대상이 없습니다. --basis 로 등급을 지정하세요 "
            f"(가능: {', '.join(cp.TIER_NOTE)})")
        return 1

    ok = fail = 0
    saved = []
    for i, p in enumerate(todo, 1):
        res = cp.save_group(s, p, p["code"], capacity=p.get("capacity") or "",
                            unit=p.get("unit") or "", log=log)
        ok += res["ok"]
        fail += res["fail"]
        saved.extend(res["saved"])
        log(f"[{i}/{len(todo)}] {p['lcp_code']}  {p['name'][:44]}"
            f"  저장 {res['ok']}건"
            + (f" / 실패 {res['fail']}건" if res["fail"] else ""))

    log("-" * 74)
    log(f"저장 {ok:,} / 실패 {fail:,}")
    if saved:
        sync(s, saved, folder)
    return 0


def sync(session, rows, folder):
    """저장한 건을 사이트에서 다시 읽어 로컬 DB(+미러)에 반영한다.
    화면 값을 그대로 믿지 않고 실제 상태를 재확인하는 쪽이 안전하다."""
    log("")
    log(f"저장분 {len(rows):,}건 재조회 중...")
    out, bad, t0 = [], 0, time.time()
    for i, r in enumerate(rows, 1):
        try:
            d = attr_detail.fetch_detail(session, r["product_no"])
            d["lcp_code"] = r["lcp_code"]
            d["l_code"] = r["l_code"]
            out.append(d)
        except Exception:
            bad += 1
        if i % 50 == 0:
            log(f"  {i:,}/{len(rows):,} ({time.time() - t0:.0f}초)")
    if out:
        res = db.save_lcode_attr(folder, out)
        log(f"로컬 DB {res['rows']:,}행   {res.get('mirror', '')}")
    still = [d for d in out if not d["cat_saved"]]
    log(f"재조회 실패 {bad}건 / 저장 반영 안 된 건 {len(still)}건")
    for d in still[:5]:
        log(f"   ! {d['l_code']} 여전히 카테고리 없음")


def preview(plan):
    """--all 이 아닐 때만 한 줄씩 보여준다. 전량은 리포트로 충분하다."""
    if "--all" in ARGS:
        return
    for i, p in enumerate(plan, 1):
        r0 = p["rows"][0]
        log(f"[{i}] {p['lcp_code']}  L코드 {len(p['rows'])}건  "
            f"후보 {len(p.get('candidates') or [])}개  [{p.get('tier')}]")
        log(f"     -> {p.get('code')}  {p.get('name')}  ({p.get('cnt')}개)")
        log(f"     {p.get('note', '')}")
        if p.get("auto"):
            log(f"     보낼 값: no={r0['product_no']} leaf={p.get('code')}")


def report(plan):
    nl = lambda g: sum(len(p["rows"]) for p in g)          # noqa: E731
    auto = [p for p in plan if p.get("auto")]
    manual = [p for p in plan if not p.get("auto")]

    log("")
    log(f"  자동 가능   LCP {len(auto):>4,}종   L코드 {nl(auto):>5,}건")
    log(f"  수동 필요   LCP {len(manual):>4,}종   L코드 {nl(manual):>5,}건")

    log("")
    log("등급별:")
    by = {}
    for p in plan:
        by.setdefault(p.get("tier", "?"), []).append(p)
    for t, g in sorted(by.items(), key=lambda x: -len(x[1])):
        log(f"   {t:>6}  LCP {len(g):>4,}종 / L코드 {nl(g):>5,}건"
            f"   {cp.TIER_NOTE.get(t, '')}")

    log("")
    log("자동 대상 상위 카테고리:")
    cnt = {}
    for p in auto:
        cnt[p["name"]] = cnt.get(p["name"], 0) + len(p["rows"])
    for n, c in sorted(cnt.items(), key=lambda x: -x[1])[:12]:
        log(f"   {c:>4,}건  {n[:56]}")

    out = Path(__file__).resolve().parent.parent / "logs" / "category_plan.json"
    slim = [{k: v for k, v in p.items() if k != "candidates"} for p in plan]
    for p, s in zip(plan, slim):
        s["lcodes"] = len(p["rows"])
        s["l_codes"] = [r["l_code"] for r in p["rows"]]
        s["product_nos"] = [r["product_no"] for r in p["rows"]]
        s.pop("rows", None)
    out.write_text(json.dumps(slim, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    log("")
    log(f"계획 저장 -> {out}")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log(traceback.format_exc())
        sys.exit(1)
