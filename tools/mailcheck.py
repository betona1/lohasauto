"""
메일함에서 스마트스토어 경고 · 광고비 알림을 건져온다.

    python -X utf8 tools/mailcheck.py --test      # 접속만 확인
    python -X utf8 tools/mailcheck.py             # 최근 14일 해당 메일
    python -X utf8 tools/mailcheck.py --days 30 --all   # 전부 (분류 무관)
    python -X utf8 tools/mailcheck.py --kind 스토어경고

설정은 `.env` 의 MAIL_1_* ~ MAIL_9_*.
네이트는 POP3 가 없어졌으니 imap.nate.com:993 을 쓴다.
"""
import argparse
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

from app import config                               # noqa: E402
from app.lohas import mailbox, secret_store          # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--all", action="store_true", help="분류 안 된 것도")
    ap.add_argument("--kind", default="", help="이 갈래만")
    ap.add_argument("--test", action="store_true", help="접속만 확인")
    ap.add_argument("--body", action="store_true", help="본문 앞부분도")
    args = ap.parse_args()

    boxes = config.mailboxes()
    if not boxes:
        print("!! .env 에 MAIL_1_HOST / MAIL_1_USER 가 없습니다")
        return 1

    if args.test:
        if secret_store.available():
            print("  출처 : 사내 100번 서버 (.env 에 적지 않음)")
        for mb in boxes:
            mb = secret_store.fill(mb)
            if not mb["password"]:
                print(f"  {mb['name']:8} {mb['user']:26} 비밀번호 미설정")
                continue
            r = mailbox.check(mb)
            print(f"  {mb['name']:8} {mb['user']:26} "
                  f"{mb['proto']}://{mb['host']}:{mb['port']}  "
                  + (f"OK  {r['count']}통" if r["ok"]
                     else f"실패 {r.get('error','')}"))
        return 0

    rows = mailbox.fetch_all(days=args.days, only_hit=not args.all)
    if args.kind:
        rows = [r for r in rows if r["kind"] == args.kind]
    print("=" * 66)
    print(f"{len(rows)}통")
    for r in rows:
        print(f"[{r['kind'] or '-':6}] {r['date']}  {r['box']}")
        print(f"    {r['subject'][:70]}")
        print(f"    from {r['from'][:60]}")
        if args.body and r["body"]:
            print(f"    {r['body'][:200]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
