"""
대표이미지 URL 수집 — 원본(images3) / 현재(images_ai) 와 AI 이미지 유무.

화면의 [대표이미지] 팝업이 띄우는 이미지 서버 API 를 그대로 부른다
(`app/lohas/prod_image.py`). 읽기 전용이라 사이트를 바꾸지 않는다.

    python -X utf8 tools/collect_images.py                        # 작업폴더
    python -X utf8 tools/collect_images.py --folder "595. 광고진행-엑사"
    python -X utf8 tools/collect_images.py --all                  # 폴더 전부
    ... --only-new     이미 받아둔 것은 건너뛴다 (이어서 돌릴 때)
    ... --limit 100
"""
import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

from app import db                                        # noqa: E402
from app.lohas import prod_image as pi                    # noqa: E402
from app.lohas.session import get_client                  # noqa: E402


def arg(name, default=""):
    for i, a in enumerate(sys.argv):
        if a == name and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def main():
    db.init_db()
    limit = int(arg("--limit", "0") or 0)
    only_new = "--only-new" in sys.argv
    sql = "SELECT l_code, product_no, folder_name FROM lcode_attr"
    args = []
    if "--all" not in sys.argv:
        folder = arg("--folder") or db.get_job_folder()
        if not folder:
            print("!! 작업폴더 미지정"); return 1
        sql += " WHERE folder_name = ?"
        args.append(folder)
        print(f"작업폴더 : {folder}")
    else:
        print("폴더 전부")
    if only_new:
        sql += (" AND " if args else " WHERE ") + \
               "l_code NOT IN (SELECT l_code FROM lcode_image)"
    sql += " ORDER BY folder_name, l_code"
    with db.sqlite_conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS lcode_image (
            l_code TEXT PRIMARY KEY, product_no TEXT, uid TEXT, ai INTEGER,
            org_main TEXT, edit_main TEXT, org_n INTEGER, edit_n INTEGER,
            updated_at TEXT)""")
        rows = [dict(r) for r in c.execute(sql, args)]
    if limit:
        rows = rows[:limit]
    print(f"대상 {len(rows):,}건")
    if not rows:
        return 0

    client = get_client()
    s = client.session
    t0 = time.time()
    ok = fail = ai = 0
    uid = ""
    for i, r in enumerate(rows, 1):
        try:
            d = pi.fetch_images(s, r["product_no"], uid=uid)
            uid = uid or d.get("uid") or ""      # 한 번 알아내면 재사용
            if not d.get("org", {}).get("main1") and not d.get("uid"):
                fail += 1
            else:
                db.save_lcode_image(r["l_code"], r["product_no"], d)
                ok += 1
                ai += bool(d.get("ai"))
        except Exception as e:
            fail += 1
            if fail <= 5:
                print(f"  ! {r['l_code']} {str(e)[:60]}")
        if i % 200 == 0:
            el = time.time() - t0
            print(f"  {i:,}/{len(rows):,}  성공 {ok:,} · AI {ai:,} · 실패 {fail}"
                  f"   ({el / 60:.1f}분, 남은 약 "
                  f"{(len(rows) - i) * el / i / 60:.0f}분)")
    print("=" * 58)
    print(f"수집 {ok:,}건 · AI 이미지 {ai:,}건 · 실패 {fail:,}건 "
          f"({(time.time() - t0) / 60:.1f}분)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
