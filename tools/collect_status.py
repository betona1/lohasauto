"""
폴더의 **L코드 목록과 상태**를 수집한다. 다른 수집의 출발점이다.

    lcp_lcode   LCP - L코드 - 대표이미지상태 - 상품정보상태
       |
       +-> collect_attr.py   L코드별 카테고리/태그/상품명 저장 여부
       +-> collect_lcp.py    LCP 기본정보(카테고리 후보·키워드)

이게 없으면 태그·상품명 도구가 대상 0건으로 나온다. 폴더를 새로 늘렸으면
**이것부터** 돌린다(2026-09-06 595. 광고진행-엑사 에서 겪었다).

    python -X utf8 tools/collect_status.py
    python -X utf8 tools/collect_status.py --folder "595. 광고진행-엑사"
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db                                        # noqa: E402
from app.lohas import lcode_status                        # noqa: E402
from app.lohas.session import get_client                  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", default="", help="이 폴더 (기본: 작업폴더)")
    args = ap.parse_args()

    db.init_db()
    folder = args.folder or db.get_job_folder()
    if not folder:
        print("작업폴더가 지정되지 않았습니다.", flush=True)
        return 1
    print(f"폴더 : {folder}", flush=True)

    t0 = time.time()
    client = get_client(log=lambda m: print(m, flush=True))
    res = lcode_status.collect_folder(client, folder,
                                      log=lambda m: print(m, flush=True))
    saved = db.save_lcode_status(folder, res["rows"])
    print(f"\nL코드 {saved['rows']:,}행 저장 (정리 {saved['removed']}행) "
          f"({(time.time() - t0) / 60:.1f}분)", flush=True)
    if saved.get("mirror"):
        print(saved["mirror"], flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
