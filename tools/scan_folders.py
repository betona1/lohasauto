"""
폴더별 **전체 점검**(12칸 상태 매트릭스)을 CLI 로 돌린다.

화면의 「④ 전체 점검」과 같은 것이다. HTTP 로 조회하므로 브라우저가 없다.
작업폴더 하나만 되던 것을 여러 폴더로 넓혔다(2026-09-07 사용자 요청 —
594 만 점검되고 592·595·596 은 한 번도 안 돌아 있었다).

    python -X utf8 tools/scan_folders.py --folder "595. 광고진행-엑사"
    python -X utf8 tools/scan_folders.py --all-59      # 59x 광고진행 폴더 전부
    python -X utf8 tools/scan_folders.py --quick ...   # ★작업대상 1칸만
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db                                        # noqa: E402
from app.lohas import session as ses, ss_image            # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", action="append", default=[],
                    help="점검할 폴더 (여러 번 줄 수 있다)")
    ap.add_argument("--all-59", action="store_true",
                    help="'59x. 광고진행-' 폴더 전부")
    ap.add_argument("--quick", action="store_true",
                    help="★작업대상 1칸만 (빠른 점검)")
    args = ap.parse_args()

    names = list(args.folder)
    if args.all_59:
        with db.sqlite_conn() as c:
            names += [r["name"] for r in c.execute(
                "SELECT name FROM folder WHERE name LIKE '59%광고진행%' "
                "ORDER BY name") if r["name"] not in names]
    if not names:
        names = [db.get_job_folder()]
    names = [n for n in names if n]
    if not names:
        print("점검할 폴더가 없습니다"); return

    cli = ses.get_client()
    kind = "빠른 점검" if args.quick else "전체 점검"
    print(f"{kind} — 폴더 {len(names)}개", flush=True)
    t0 = time.time()
    for i, f in enumerate(names, 1):
        s = time.time()
        try:
            res = ss_image.inspect_folder_http(
                cli, f, log=lambda *_: None, quick=args.quick)
            saved = db.save_scan(res["summary"], res["cells"], res["items"])
            m = res["summary"]
            print(f"[{i}/{len(names)}] {f}", flush=True)
            print(f"    전체 {m.get('total_rows', 0):,} / "
                  f"★작업대상 {m.get('target_rows', 0):,} / "
                  f"저장완료 {m.get('info_save_rows', 0):,} / "
                  f"이미지승인 {m.get('img_done_rows', 0):,}"
                  f"   ({time.time() - s:.1f}초, scan_id={saved['scan_id']})",
                  flush=True)
        except Exception as e:
            print(f"[{i}/{len(names)}] !! {f} {str(e)[:90]}", flush=True)
    print(f"\n끝 — {len(names)}개 ({(time.time() - t0) / 60:.1f}분)", flush=True)


if __name__ == "__main__":
    main()
