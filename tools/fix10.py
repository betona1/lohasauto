"""
수정사항 1.0 일괄수정 — 명령줄용.

작업폴더에서 '정보수정' 이 붙은 광고상품을 찾아 1.0 수정 마법사를 끝까지
눌러 준다. **정방향·역방향을 동시에** 돌아 목록 앞뒤에서 좁혀 온다.
브라우저를 띄우지 않으므로 백그라운드로 돌려도 된다.

    python -X utf8 tools/fix10.py                     # 대상만 세어본다
    python -X utf8 tools/fix10.py --apply
    python -X utf8 tools/fix10.py --apply --limit 10
    python -X utf8 tools/fix10.py --apply --one-way   # 정방향만
    python -X utf8 tools/fix10.py --apply --weekly    # 주 1회만 (스케줄러용)

`--weekly` 는 마지막 실행이 7일 안이면 아무것도 하지 않고 끝난다. 윈도우
작업 스케줄러에 매일 걸어두어도 실제로는 주 1회만 돈다.
"""
import argparse
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db                                        # noqa: E402
from app.lohas import fix10, session as ses               # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제로 저장한다")
    ap.add_argument("--folder", default="", help="작업폴더 (기본: 지정된 폴더)")
    ap.add_argument("--all-folders", action="store_true",
                    help="작업대상으로 지정한 폴더 전부. 마스터폴더를 여럿"
                         " 쓰면 작업폴더 하나만 봐서는 빠진다")
    ap.add_argument("--limit", type=int, default=0, help="앞에서 N건만")
    ap.add_argument("--one-way", action="store_true",
                    help="정방향만 (기본은 정·역 동시)")
    ap.add_argument("--weekly", type=int, nargs="?", const=7, default=0,
                    help="마지막 실행이 N일(기본 7) 안이면 그냥 끝낸다")
    args = ap.parse_args()

    if args.all_folders:
        folders = db.list_master_folders()
    else:
        folders = [args.folder or db.get_job_folder()]
    folders = [f for f in folders if f]
    if not folders:
        print("작업폴더가 지정되지 않았습니다.", flush=True)
        return 1
    now = dt.datetime.now()
    print(f"[{now:%Y-%m-%d %H:%M}] 폴더 {len(folders)}개 — "
          + " / ".join(folders), flush=True)
    folder = folders[0]

    if args.weekly:
        last = db.fix10_last_run(folder).get("at") or ""
        if last:
            try:
                t = dt.datetime.strptime(last[:19], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                t = None
            if t and (now - t).days < args.weekly:
                print(f"마지막 실행 {last} — {args.weekly}일이 안 지나 건너뜁니다.",
                      flush=True)
                return 0

    cli = ses.get_client()
    if not args.apply:
        for f in folders:
            rows = fix10.search(cli.session, f)
            print(f"[{f}] 수정사항 {len(rows):,}건", flush=True)
            for r in rows[:10]:
                print(f"   {r['product_code']}  {r['state']}  {r['title'][:40]}",
                      flush=True)
        print("(드라이런 — --apply 를 주면 진행)", flush=True)
        return 0

    tot = {"total": 0, "ok": 0, "soldout": 0, "fail": 0}
    for f in folders:
        res = fix10.bulk(lambda: fix10.clone_session(cli.session), f,
                         both=not args.one_way, limit=args.limit)
        for k in tot:
            tot[k] += int(res.get(k) or 0)
    if len(folders) > 1:
        print("")
        print(f"전체 — 대상 {tot['total']:,} / 완료 {tot['ok']:,} / "
              f"품절 {tot['soldout']:,} / 실패 {tot['fail']:,}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
