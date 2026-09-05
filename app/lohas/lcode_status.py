"""
L코드 단위 상태 수집.

목록의 상태는 버튼 클래스가 2진값이라 행만 봐서는 세부 상태를 알 수 없다.
그래서 대표이미지 3상태 × 상품정보 4상태로 검색을 돌려, 각 검색결과에 들어있는
행(LCP, L코드, 팝업no)에 그 조합의 상태를 붙인다.

  대표이미지 : 미작업 / 이미지작업 / 이미지승인완료
  상품정보   : 미작업 / 저장완료 / 제외 / 보류

12회 검색이면 폴더의 모든 L코드에 상태가 매겨진다(HTTP 라 폴더당 수 초).
"""
import time
from datetime import datetime

from . import constants as C


def collect_folder(client, folder_name: str, log=print,
                   progress=None, should_stop=None) -> dict:
    """
    폴더 전체의 L코드 상태를 수집.
    반환: {'rows': [{lcp_code, l_code, product_no, img_status, info_status}], ...}
    """
    started = time.time()
    scanned_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    rows = {}            # (lcp, lcode) -> dict
    total = len(C.IMAGE_FILTERS) * len(C.INFO_FILTERS)
    done = 0

    for img_label, img_val in C.IMAGE_FILTERS:
        for info_label, info_val in C.INFO_FILTERS:
            if should_stop and should_stop():
                log("[L코드] 사용자 중단")
                break
            res = client.search_full(folder_name, img_val, info_val)
            for lcp, lcode, no in res["rows"]:
                if not lcp:
                    continue
                rows[(lcp, lcode)] = {
                    "lcp_code": lcp, "l_code": lcode, "product_no": no,
                    "img_status": img_label, "info_status": info_label,
                }
            done += 1
            if progress:
                progress(done, total)
            log(f"[L코드] {img_label} / {info_label} → {len(res['rows']):,}행 "
                f"(누적 {len(rows):,})")

    out = list(rows.values())
    lcps = {r["lcp_code"] for r in out}
    log(f"[L코드] 완료 : {len(out):,}행 / LCP {len(lcps):,}종 "
        f"({time.time() - started:.1f}초)")
    return {"folder_name": folder_name, "scanned_at": scanned_at,
            "rows": out, "lcp_count": len(lcps),
            "elapsed_sec": round(time.time() - started, 1)}


def summarize(rows: list) -> dict:
    """LCP 별 상태 요약 — 화면의 한 줄 표시에 쓴다."""
    from collections import defaultdict

    by_lcp = defaultdict(lambda: {"total": 0, "img": defaultdict(int),
                                  "info": defaultdict(int)})
    for r in rows:
        d = by_lcp[r["lcp_code"]]
        d["total"] += 1
        d["img"][r["img_status"]] += 1
        d["info"][r["info_status"]] += 1

    out = {}
    for lcp, d in by_lcp.items():
        out[lcp] = {
            "total": d["total"],
            "img_done": d["img"].get(C.TARGET_IMAGE, 0),
            "img_work": d["img"].get("이미지작업", 0),
            "img_todo": d["img"].get("미작업", 0),
            "info_save": d["info"].get("저장완료", 0),
            "info_todo": d["info"].get("미작업", 0),
            "info_exclude": d["info"].get("제외", 0),
            "info_hold": d["info"].get("보류", 0),
        }
        s = out[lcp]
        s["target"] = sum(1 for r in rows
                          if r["lcp_code"] == lcp
                          and r["img_status"] == C.TARGET_IMAGE
                          and r["info_status"] == C.TARGET_INFO)
    return out
