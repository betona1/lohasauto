"""
전체 파이프라인 — 마스터 폴더를 순서대로 끝까지 민다.

    상태수집 → AI 이미지 일괄 → 상품분석 → 카테고리(+총용량) → 태그 → 상품명

**저장완료는 절대 누르지 않는다.** 값만 채운다 — 확인은 사람 몫이다
(사용자 지침, 2026-09-08 이후 계속).

    python -X utf8 tools/pipeline.py --plan            # 무엇을 할지만 보기
    python -X utf8 tools/pipeline.py --run
    python -X utf8 tools/pipeline.py --run --step 태그
    python -X utf8 tools/pipeline.py --run --skip "594. 광고진행-비트마인드"

`--skip` 한 폴더는 건드리지 않는다. 비트마인드는 이미 끝난 폴더라
기본으로 제외한다(2026-09-12 사용자: "비트마인드는 건들지말것").
"""
import argparse
import datetime
import io
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

from app import db                                          # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = [sys.executable, "-X", "utf8", "-u"]

# 기본 제외 — 이미 끝난 폴더
SKIP_DEFAULT = ["594. 광고진행-비트마인드"]

STEPS = ["상태수집", "이미지", "상품분석", "카테고리", "태그", "상품명"]


def log(msg=""):
    print(f"{datetime.datetime.now():%m-%d %H:%M:%S}  {msg}", flush=True)


def run(args_, timeout=None) -> int:
    """하위 도구를 돌린다. 출력은 그대로 흘려보낸다."""
    p = subprocess.Popen(PY + args_, cwd=ROOT, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace")
    for line in p.stdout:
        print("    " + line.rstrip(), flush=True)
    return p.wait(timeout=timeout)


def busy_job():
    """지금 돌고 있는 AI 이미지 작업. 없으면 None."""
    from app.lohas import ai_image, session as ses
    try:
        s = ses.get_client(allow_login=False).session
        for j in ai_image.jobs(s):
            st = str(j.get("상태") or "")
            if "완료" not in st and "취소" not in st and "실패" not in st:
                return j
    except Exception as e:
        log(f"     작업결과 조회 실패: {str(e)[:70]}")
    return None


def wait_free(log=print, every: int = 120, max_min: int = 90) -> bool:
    """
    **AI 이미지는 한 번에 하나만 걸린다.** 대기열에 쌓이지 않고
    '동작중인 작업이 있습니다' 로 거절당한다(2026-09-12 실측: 17페이지를
    한꺼번에 걸었더니 첫 장만 들어갔다). 앞 작업이 끝날 때까지 기다린다.
    """
    t0 = time.time()
    while True:
        j = busy_job()
        if not j:
            return True
        if (time.time() - t0) / 60 > max_min:
            return False
        log(f"     앞 작업 진행중 — No.{j.get('No')} {j.get('상태')} "
            f"{j.get('작업명/분류', '')[:22]}")
        time.sleep(every)


def folders(skip: list) -> list:
    out = [f for f in db.list_master_folders() if f and f not in skip]
    return out


def status(folder: str) -> dict:
    with db.sqlite_conn() as c:
        r = c.execute("""
            SELECT COUNT(*) n,
              SUM(a.analysis_done=0) ana0,
              SUM(a.cat_saved=0) cat0,
              SUM(a.tag_count=0 OR a.tag_count IS NULL) tag0,
              SUM(a.title1='' OR a.title1 IS NULL) tit0,
              SUM(l.img_status='미작업') img0,
              SUM(l.img_status='이미지승인완료') imgok,
              SUM(l.info_status='미작업') todo
            FROM lcode_attr a LEFT JOIN lcp_lcode l
                 ON l.product_no=a.product_no
            WHERE a.folder_name=?""", (folder,)).fetchone()
        return {k: (r[k] or 0) for k in r.keys()} if r else {}


def plan(skip: list):
    log("=" * 62)
    log("파이프라인 계획")
    for f in folders(skip):
        s = status(f)
        if not s or not s["n"]:
            log(f"  {f[:24]:26} 자료 없음 — 상태수집부터")
            continue
        log(f"  {f[:24]:26} 총 {s['n']:>6,} | 이미지미{s['img0']:>6,} "
            f"분석미{s['ana0']:>6,} 카테미{s['cat0']:>6,} "
            f"태그0 {s['tag0']:>6,} 상품명0 {s['tit0']:>6,}")
    log("제외: " + (", ".join(skip) or "없음"))


def do_folder(f: str, steps: list, apply_: bool):
    s = status(f)
    log("-" * 62)
    log(f"[{f}]  총 {s.get('n', 0):,}건")

    if "상태수집" in steps:
        log("  ① 상태 수집")
        run(["tools/collect_status.py", "--folder", f])

    if "이미지" in steps:
        s = status(f)
        n = s.get("img0") or 0
        if n:
            # **페이지 단위(1000건)로 건다.** 전상품선택은 사이트가 막는다.
            # 작업은 대기열에 쌓이고 앞 것이 끝나야 다음이 돈다.
            pages = (n + 999) // 1000
            log(f"  ② AI 이미지 일괄 — 미작업 {n:,}건 · {pages}페이지")
            if apply_:
                for pg in range(1, pages + 1):
                    if not wait_free(log):
                        log("     대기 한도를 넘겨 중단합니다")
                        break
                    title = (f"{f.split('.')[0]}_{datetime.date.today():%m%d}"
                             f"_p{pg}")
                    rc = run(["tools/ai_image.py", "--folder", f,
                              "--page", str(pg), "--title", title, "--apply"])
                    if rc != 0:
                        log(f"     페이지 {pg} 실패(rc={rc}) — 중단")
                        break
                    log(f"     {pg}/{pages} 걸었습니다 — 끝날 때까지 기다립니다")
            else:
                log("     (드라이런 — --run 이어야 겁니다)")
        else:
            log("  ② AI 이미지 — 미작업 없음")

    if "상품분석" in steps:
        log("  ③ ALL 상품분석")
        if apply_:
            run(["tools/run_analysis.py", "--folder", f])

    if "카테고리" in steps:
        log("  ④ 카테고리 + 총용량")
        if apply_:
            run(["tools/category_all.py", "--folder", f, "--apply",
                 "--any-status"])

    if "태그" in steps:
        log("  ⑤ ALL 태그")
        if apply_:
            run(["tools/tag_all.py", "--folder", f, "--apply"])

    if "상품명" in steps:
        log("  ⑥ 상품명 (클릭 방식 · 저장완료 안 누름)")
        if apply_:
            run(["tools/fill_titles.py", "--apply", "--folder", f,
                 "--any-image"])

    s2 = status(f)
    log(f"  => 이미지미{s2.get('img0', 0):,} 분석미{s2.get('ana0', 0):,} "
        f"카테미{s2.get('cat0', 0):,} 태그0 {s2.get('tag0', 0):,} "
        f"상품명0 {s2.get('tit0', 0):,}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--step", default="", help="이 단계만 (쉼표로 여럿)")
    ap.add_argument("--folder", default="", help="이 폴더만")
    ap.add_argument("--skip", action="append", default=None)
    args = ap.parse_args()

    db.init_db()
    skip = args.skip if args.skip is not None else list(SKIP_DEFAULT)
    steps = [x.strip() for x in args.step.split(",") if x.strip()] or STEPS
    fs = [args.folder] if args.folder else folders(skip)

    if args.plan or not args.run:
        plan(skip)
        log("")
        log("[계획만 보기] --run 을 주면 실제로 돕니다.")
        return 0

    t0 = time.time()
    log(f"파이프라인 시작 — 폴더 {len(fs)}개 · 단계 {', '.join(steps)}")
    log(f"제외: {', '.join(skip) or '없음'}")
    log("**저장완료는 누르지 않습니다.**")
    for f in fs:
        try:
            do_folder(f, steps, apply_=True)
        except KeyboardInterrupt:
            log("사용자 중단")
            break
        except Exception as e:
            log(f"  !! {f} {str(e)[:120]}")
    log("=" * 62)
    log(f"끝 — {(time.time() - t0) / 60:.0f}분")
    return 0


if __name__ == "__main__":
    sys.exit(main())
