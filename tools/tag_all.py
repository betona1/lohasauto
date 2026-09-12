"""
ALL 태그 — 폴더 하나의 태그를 끝까지 채운다. (화면의 「🏷 ALL 태그」)

    python -X utf8 tools/tag_all.py --folder "595. 광고진행-엑사"
    ... --apply             실제로 저장 (기본은 드라이런)
    ... --want 10           L코드당 목표 개수
    ... --approved-only     대표이미지 승인완료만

태그 저장은 상품정보 상태를 바꾸지 않는다.
"""
import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

from app import db                                        # noqa: E402
from app.lohas import tag_batch                           # noqa: E402
from app.lohas.session import get_client                  # noqa: E402


def arg(name, default=""):
    for i, a in enumerate(sys.argv):
        if a == name and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def main():
    db.init_db()
    folder = arg("--folder") or db.get_job_folder()
    if not folder:
        print("!! 작업폴더 미지정"); return 1
    t0 = time.time()
    client = get_client(log=print)
    res = tag_batch.run(
        client.session, db, folder,
        want=int(arg("--want", "10") or 10),
        any_image="--approved-only" not in sys.argv,
        apply_="--apply" in sys.argv, log=print)
    print("=" * 58)
    print(f"묶음 {res['groups']:,}개 / 대상 {res['rows']:,}건 · "
          f"저장 {res['ok']:,}건 · 실패 {res['fail']:,}건 "
          f"({(time.time() - t0) / 60:.1f}분)")
    if res["empty"]:
        print(f"후보가 아예 없던 LCP {len(res['empty'])}종: "
              + ", ".join(res["empty"][:10]))
    if "--apply" not in sys.argv:
        print("드라이런입니다. --apply 를 주면 저장합니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
