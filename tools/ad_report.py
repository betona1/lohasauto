"""
네이버 검색광고 보고서 — 만들고 받아서 `data/ad_report/` 에 저장한다.

    python -X utf8 tools/ad_report.py --master          # 기준정보 전부
    python -X utf8 tools/ad_report.py --stat --date 2026-09-10
    python -X utf8 tools/ad_report.py --master --customer 4464788
    python -X utf8 tools/ad_report.py --list            # 지금 있는 것만 보기

보고서는 **요청 → 서버가 만듦 → 받기** 라서 시간이 좀 걸린다.
`--stat` 은 어제 것을 달라 하면 `20007 해당 일자 지표 준비중입니다` 가
온다. 이틀 전부터 된다(2026-09-12 실측).
"""
import argparse
import datetime
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

from app.lohas import ad_report as ar, searchad as sa   # noqa: E402


def do_master(cu, items, log=print):
    got = []
    for item in items:
        st, d = ar.master_create(cu, item)
        if not (isinstance(d, dict) and d.get("id")):
            log(f"  {item:18} 생성 실패 {st} {str(d)[:70]}")
            continue
        rid = d["id"]

        def pick(rid=rid, item=item):
            for m in ar.master_list(cu):
                if m.get("id") == rid:
                    return m
            # id 가 안 맞으면 같은 항목 중 받을 수 있는 것
            for m in ar.master_list(cu):
                if m.get("item") == item and m.get("downloadUrl"):
                    return m
            return {}

        r, data = ar.wait_and_fetch(cu, pick, tries=10, wait=5, log=log)
        if not data:
            log(f"  {item:18} 아직 안 만들어졌습니다 (상태 {r.get('status')})")
            continue
        p = ar.save(cu, f"master_{item}", data)
        lines = data.decode("utf-8", "replace").splitlines()
        log(f"  {item:18} {len(data):,}바이트 · {len(lines):,}행 -> {p.name}")
        got.append((item, p, len(lines)))
    return got


def do_stat(cu, tp, day, log=print):
    st, d = ar.stat_create(cu, tp, day)
    if not (isinstance(d, dict) and d.get("reportJobId")):
        log(f"  {tp:26} 생성 실패 {st} {str(d)[:90]}")
        return None
    job = str(d["reportJobId"])

    def pick():
        return ar.stat_get(cu, job) or {}

    r, data = ar.wait_and_fetch(cu, pick, tries=20, wait=6, log=log)
    if not data:
        log(f"  {tp:26} 못 받았습니다 (상태 {r.get('status')})")
        return None
    p = ar.save(cu, f"stat_{tp}_{day}", data)
    lines = data.decode("utf-8", "replace").splitlines()
    log(f"  {tp:26} {len(data):,}바이트 · {len(lines):,}행 -> {p.name}")
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--master", action="store_true")
    ap.add_argument("--stat", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--customer", default="")
    ap.add_argument("--item", default="", help="마스터 항목 (쉼표로 여럿)")
    ap.add_argument("--tp", default="AD", help="대용량 보고서 종류")
    ap.add_argument("--date", default="",
                    help="대용량 보고서 일자 (기본 이틀 전)")
    args = ap.parse_args()

    if not sa.available():
        print("!! .env 에 네이버 검색광고 API 키가 없습니다")
        return 1
    custs = [args.customer] if args.customer else sa.customers()
    print(f"저장 위치 : {ar.out_dir()}")

    for cu in custs:
        print("=" * 60)
        print(f"[{cu}]")
        if args.list:
            for m in ar.master_list(cu):
                print(f"  마스터 {m.get('item'):18} {m.get('status'):8} "
                      f"{'받을 수 있음' if m.get('downloadUrl') else ''}")
            for s in ar.stat_list(cu):
                print(f"  대용량 {str(s.get('reportTp')):26} "
                      f"{s.get('status')}")
            continue
        if args.master:
            items = ([x.strip() for x in args.item.split(",") if x.strip()]
                     or ["Campaign", "Adgroup", "Keyword", "ShoppingProduct",
                         "BusinessChannel"])
            do_master(cu, items)
        if args.stat:
            day = args.date or (datetime.date.today()
                                - datetime.timedelta(days=2)).isoformat()
            do_stat(cu, args.tp, day)
    return 0


if __name__ == "__main__":
    sys.exit(main())
