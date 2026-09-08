"""
AI 이미지 일괄수정 — 폴더의 페이지 단위(1000건)로 걸고 결과를 지켜본다.

    # 무엇을 보낼지만 본다 (기본)
    python -X utf8 tools/ai_image.py --folder "595. 광고진행-엑사" --page 2 --title 잘어울리는배경2

    # 실제로 건다
    python -X utf8 tools/ai_image.py --folder "595. 광고진행-엑사" --page 2 --title 잘어울리는배경2 --apply

    # 작업결과만 본다 / 끝날 때까지 지켜본다
    python -X utf8 tools/ai_image.py --jobs
    python -X utf8 tools/ai_image.py --watch 30

질의어는 화면 기본값 '잘어울리는 배경' 을 그대로 쓴다(`--query` 로 바꿀 수 있다).
**앞 작업이 완료돼야 다음 작업이 돈다** — 대기중으로 쌓인다.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.lohas import ai_image, session as ses            # noqa: E402


def show_jobs(session, n: int = 10):
    for j in ai_image.jobs(session)[:n]:
        print(f"  No.{j.get('No', ''):>3}  {j.get('상태', ''):<6} "
              f"{j.get('작업명/분류', '')[:30]:<32} "
              f"등록 {j.get('등록일시', '-')}  "
              f"시작 {j.get('시작일시') or '-'}  "
              f"완료 {j.get('완료일시') or '-'}", flush=True)


def watch(session, no: str, every: int = 60):
    last = None
    while True:
        j = next((x for x in ai_image.jobs(session) if x.get("No") == no), None)
        if not j:
            print(f"No.{no} 를 찾지 못했습니다"); return
        cur = (j["상태"], j["시작일시"], j["완료일시"])
        if cur != last:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {j['상태']}  "
                  f"시작 {j['시작일시'] or '-'}  완료 {j['완료일시'] or '-'}",
                  flush=True)
            last = cur
        if j["상태"] in ("완료", "취소완료", "실패", "오류"):
            return j
        time.sleep(every)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", default="")
    ap.add_argument("--page", type=int, default=1)
    ap.add_argument("--title", default="")
    ap.add_argument("--query", default=ai_image.DEFAULT_QUERY)
    ap.add_argument("--viewnum", default="1000")
    ap.add_argument("--apply", action="store_true", help="실제로 건다")
    ap.add_argument("--jobs", action="store_true", help="작업결과만 본다")
    ap.add_argument("--watch", default="", help="이 No 가 끝날 때까지 본다")
    args = ap.parse_args()

    cli = ses.get_client()
    if args.jobs:
        show_jobs(cli.session); return
    if args.watch:
        watch(cli.session, args.watch); return
    if not args.folder or not args.title:
        print("--folder 와 --title 이 필요합니다"); return

    r = ai_image.run_page(cli, args.folder, args.page, args.title,
                          query=args.query, viewnum=args.viewnum,
                          dry=not args.apply)
    head = "걸었습니다" if args.apply else "[드라이런] 보낼 내용"
    print(f"{head} — {args.folder} {args.page}페이지 / {r.get('count', 0):,}건")
    print(f"  작업명 {r.get('title')} / 질의어 {r.get('query')}")
    print(f"  L코드 {r.get('first')} ~ {r.get('last')}")
    if r.get("message"):
        print(f"  응답: {r['message']}")
    if not r.get("ok"):
        print(f"  !! {r.get('reason', '')}")
        return
    if args.apply:
        print("\n작업결과:")
        show_jobs(cli.session, 3)


if __name__ == "__main__":
    main()
