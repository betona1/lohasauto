"""
광고 자료 매일 받아오기 — 지출(실시간) + 대용량 보고서(검색어·소재별).

윈도우 작업 스케줄러에 **매일** 걸어두면 된다(`광고수집_매일.bat`).
`--once-a-day` 가 마지막 실행을 보고 오늘 이미 돌았으면 그냥 끝낸다.

    python -X utf8 tools/ad_daily.py --once-a-day
    python -X utf8 tools/ad_daily.py --days 3        # 지금 바로, 3일치
    python -X utf8 tools/ad_daily.py --catchup       # 빠진 날짜만 채우기

받는 것은 두 가지다.
  1) `/stats` 로 **오늘 포함** 날짜별 지출 (실시간, 캠페인·광고그룹 단위)
  2) 대용량 보고서로 **어제까지** 검색어·소재·기기·시간대별 상세

당일치 보고서는 네이버가 만들어 주지 않는다(`20007 준비중`). 그래서
오늘 숫자는 1)로, 상세는 2)로 본다.
"""
import argparse
import datetime
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

from app import db                                              # noqa: E402
from app.lohas import (ad_account, ad_creative, ad_detail,       # noqa: E402
                       ad_sales, ad_spend, commerce, searchad as sa)

KEY = "ad_daily_last"
MAX_BACKFILL = 14          # 너무 옛날까지 거슬러 올라가지 않는다


def log(msg=""):
    print(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}", flush=True)


def missing_days(customer, upto_back: int = MAX_BACKFILL) -> list:
    """어제부터 거슬러 올라가며 **DB 에 없는 날짜**만 고른다."""
    have = {r["day"] for r in ad_detail.days_in_db(customer)}
    out = []
    for back in range(1, upto_back + 1):
        d = (datetime.date.today() - datetime.timedelta(days=back)).isoformat()
        if d not in have:
            out.append(d)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once-a-day", action="store_true",
                    help="오늘 이미 돌았으면 아무것도 하지 않는다")
    ap.add_argument("--days", type=int, default=2,
                    help="상세 보고서를 어제부터 며칠치 받을지")
    ap.add_argument("--spend-days", type=int, default=7,
                    help="지출(/stats)을 며칠치 받을지")
    ap.add_argument("--catchup", action="store_true",
                    help="DB 에 빠진 날짜만 채운다 (최대 14일)")
    ap.add_argument("--customer", default="")
    args = ap.parse_args()

    db.init_db()
    if not sa.available():
        log("!! .env 에 네이버 검색광고 API 키가 없습니다")
        return 1

    today = datetime.date.today().isoformat()
    if args.once_a_day and db.get_setting(KEY, "") == today:
        log("오늘 이미 받았습니다 — 건너뜁니다.")
        return 0

    cu = args.customer or (ad_account.main_account() or {}).get("customer_id")
    if not cu:
        log("!! 메인 광고계정이 없습니다 (「광고」 탭에서 지정)")
        return 1
    log(f"광고계정 {cu}")

    # 1) 계정·캠페인 기본정보 (광고그룹이 늘거나 이름이 바뀔 수 있다)
    try:
        r = ad_account.collect(log=lambda m: log(f"  {m}"))
        log(f"기본정보 — 캠페인 {r['campaigns']} · 광고그룹 {r['groups']}")
    except Exception as e:
        log(f"기본정보 실패: {str(e)[:90]}")

    # 2) 지출 (오늘 포함)
    try:
        r = ad_spend.collect_all(days=args.spend_days, log=lambda *_: None)
        t = ad_spend.today_cost()
        log(f"지출 — {r['rows']:,}행 · 오늘 {int(t['cost'] or 0):,}원 "
            f"(클릭 {int(t['clk'] or 0):,})")
    except Exception as e:
        log(f"지출 실패: {str(e)[:90]}")

    # 3) 상세 보고서 (어제까지)
    days = missing_days(cu) if args.catchup else [
        (datetime.date.today() - datetime.timedelta(days=b)).isoformat()
        for b in range(1, args.days + 1)]
    if not days:
        log("상세 — 빠진 날짜가 없습니다")
    n = 0
    for d in days:
        try:
            n += ad_detail.collect(cu, d, log=lambda m: log(f"  {m}"))
        except Exception as e:
            log(f"  {d} 실패: {str(e)[:80]}")
    log(f"상세 — {n:,}행")

    # 4) 소재(쇼핑 상품형) — **보류가 새로 생기면 아침에 바로 보이게**
    #    한다. 보류면 그 상품 광고가 아예 안 나간다(2026-09-12 사용자).
    try:
        before = {r["ad_id"] for r in ad_creative.problems(cu)}
        r = ad_creative.collect(cu, log=lambda m: None)
        now_bad = ad_creative.problems(cu)
        new = [x for x in now_bad if x["ad_id"] not in before]
        log(f"소재 — {r['ads']:,}개 · 보류 {len(now_bad)}개"
            + (f" (새로 {len(new)}개)" if new else ""))
        for x in new[:10]:
            log(f"  ↳ 새 보류 {x['lcp_code']} {x['l_code']} "
                f"{x['product_name'][:28]}")
    except Exception as e:
        log(f"소재 실패: {str(e)[:90]}")

    # 5) **실매출** — 주문 DB 에서. 네이버 전환매출은 장바구니가 섞여
    #    있어 숫자가 부풀려진다(2026-09-12 사용자).
    try:
        r = ad_sales.collect(days=args.spend_days, log=lambda *_: None)
        log(f"실매출 — 광고상품 {r['matched']:,}건 · {r['amount']:,}원"
            f"  (가게 전체 {r['orders']:,}건 · {r['amount_all']:,}원)")
    except Exception as e:
        log(f"실매출 실패: {str(e)[:90]}")

    # 5-2) **오늘치는 커머스 API 로 덮는다.** 주문 DB 는 수집 주기가 있어
    #      당일이 비어 있다(2026-09-12 사용자).
    #      ad_sales.collect 가 그 날짜를 지우므로 **반드시 그 뒤에** 부른다.
    if commerce.available():
        try:
            for back in (1, 0):
                r = commerce.sales(
                    datetime.date.today() - datetime.timedelta(days=back),
                    log=lambda *_: None)
                if r["orders"]:
                    commerce.save(r, log=lambda *_: None)
                log(f"커머스 {r['day']} — 주문 {r['orders']:,}건 · "
                    f"{r['amount']:,}원 (가게 전체)")
        except Exception as e:
            log(f"커머스 실패: {str(e)[:90]}")
    try:
        s = ad_sales.today()
        log(f"  오늘 광고상품 실매출 {int(s['amount'] or 0):,}원 "
            f"({int(s['orders'] or 0):,}건)")
        # 관리코드로 가른 매출 — W코드는 광고와 무관하다
        for r in ad_sales.split(args.spend_days):
            log(f"    {r['kind']:14} {int(r['amount'] or 0):>9,}원 "
                f"({int(r['orders'] or 0)}건)")
    except Exception:
        pass

    # 6) 놓친 검색어 요약 (태그 작업에 쓸 거리)
    try:
        import subprocess
        out = subprocess.run(
            [sys.executable, "-X", "utf8",
             os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "ad_gap.py"), "--top", "10"],
            capture_output=True, text=True, encoding="utf-8", timeout=120)
        for line in (out.stdout or "").splitlines()[:16]:
            log("  " + line)
    except Exception:
        pass

    # **어제치를 확실히 챙겼을 때만** '오늘 받음' 으로 표시한다.
    # 새벽 3시에는 네이버가 전날 보고서를 아직 안 만들어 두는 수가 있다.
    # 그냥 표시해 버리면 --once-a-day 가 재시도를 막아 하루를 통째로
    # 놓친다 (2026-09-12 사용자: 새벽 3시 수집, 4시 업무 시작).
    y = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    have = {r["day"] for r in ad_detail.days_in_db(cu)}
    try:
        zero = not ad_detail.spent_on(cu, y)
    except Exception:
        zero = False
    if y in have or zero:
        db.set_setting(KEY, today)
        log("끝 — 어제치까지 받았습니다"
            + (" (어제 지출 0원)" if zero and y not in have else ""))
    else:
        log("끝 — 어제치가 아직 준비 전입니다. 다음 실행 때 다시 받습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
