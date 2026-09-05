"""
L코드(상품)별 작업 상세 수집 — 카테고리 / 속성 / 상품명·태그 저장 상태.

사용:
  python tools/collect_attr.py         전체
  python tools/collect_attr.py 20      앞 20건만
"""
import io
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app import db                        # noqa: E402
from app.lohas import attr_detail         # noqa: E402
from app.lohas.session import get_client  # noqa: E402

LIMIT = next((int(a) for a in sys.argv[1:] if a.isdigit()), 0)


def log(m):
    print(m, flush=True)


def main():
    db.init_db()
    folder = db.get_job_folder()
    if not folder:
        log("!! 작업폴더 미지정")
        return 1

    rows = db.lcode_rows(folder)
    if LIMIT:
        rows = rows[:LIMIT]
    log(f"작업폴더 : {folder}")
    log(f"대상 L코드 {len(rows):,}건")
    log("-" * 62)

    client = get_client(log=log)
    res = attr_detail.collect_folder(client, rows, log=log)
    saved = db.save_lcode_attr(folder, res["rows"])

    log("=" * 62)
    log(f"수집 {res['ok']:,}건 / 실패 {res['fail']:,}건 / {res['elapsed_sec']}초")
    log(f"DB 저장 {saved['rows']:,}행   {saved.get('mirror', '')}")

    summ = db.attr_summary(folder)
    t = summ["total"]
    log("-" * 62)
    log(f"전체 {t.get('n', 0):,}건 중")
    log(f"   상품분석 완료 {t.get('ana', 0) or 0:,}")
    log(f"   카테고리 저장 {t.get('cat', 0) or 0:,}")
    log(f"   속성 저장     {t.get('attr', 0) or 0:,}")
    log(f"   상품명/태그   {t.get('title', 0) or 0:,}")
    log("다음 작업 단계별:")
    for r in summ["steps"]:
        log(f"   {r['next_step'] or '-':10} {r['n']:>7,}건")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log(traceback.format_exc())
        sys.exit(1)
