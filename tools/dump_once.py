"""
GUI의 [마스터 폴더 스캔] → [작업폴더 지정] → [페이지 구조 덤프] 를 한 번에 실행.
사용: python tools/dump_once.py [폴더검색어]
"""
import io
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from app import config, db                       # noqa: E402
from app.lohas import folders as folder_api      # noqa: E402
from app.lohas import ss_image                   # noqa: E402
from app.lohas.browser import open_logged_in_browser  # noqa: E402


def log(msg):
    print(msg, flush=True)


def main():
    keyword = sys.argv[1] if len(sys.argv) > 1 else "비트마인드"
    db.init_db()

    log(f"계정      : {config.masked_id()}")
    log(f"SQLite    : {config.SQLITE_PATH}")
    log(f"폴더검색어: {keyword}")
    log("-" * 60)

    driver = None
    try:
        log("[1/4] 브라우저 실행 + 로그인 ...")
        driver = open_logged_in_browser(headless=False, log=log)

        log("[2/4] 마스터 폴더 스캔 ...")
        found = folder_api.scan_master_folders(driver, log=log)
        res = db.save_folders(found, source="ss_image")
        log(f"      DB 저장: 총 {res['total']} / 신규 {res['new']}")

        log(f"      --- 수집된 폴더 (앞 20개) ---")
        for f in found[:20]:
            log(f"      · {f['raw_label']}")
        if len(found) > 20:
            log(f"      ... 외 {len(found) - 20}개")

        cand = [f for f in found if keyword in f["name"]]
        log(f"[3/4] '{keyword}' 매칭 폴더 {len(cand)}개")
        for c in cand:
            log(f"      · {c['raw_label']}")

        if not cand:
            log("      !! 매칭 폴더 없음 → 폴더 미선택 상태로 덤프합니다.")
            target = ""
        else:
            # '삭제/재입찰/새상품' 같은 보조폴더 말고 가장 짧은 기본 폴더 우선
            cand.sort(key=lambda f: len(f["name"]))
            target = cand[0]["name"]
            db.set_work_folder(target)
            log(f"      작업폴더 지정 => {target}")

        log("[4/4] 페이지 구조 덤프 ...")
        path = ss_image.dump_page_structure(
            driver, target, config.PAGE_SIZE, rows_limit=15, log=log
        )
        log("-" * 60)
        log(f"덤프 파일: {path}")
        return 0

    except Exception:
        log("!! 오류 발생")
        log(traceback.format_exc())
        return 1
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
