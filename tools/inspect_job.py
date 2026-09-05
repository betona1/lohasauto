"""DB에 지정된 작업폴더(is_job)로 수량 점검을 실행한다. (폴더 재스캔 없음)"""
import io, sys, traceback
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app import config, db
from app.lohas import ss_image
from app.lohas.browser import open_logged_in_browser

def log(m): print(m, flush=True)

def main():
    db.init_db()
    job = db.get_job_folder()
    if not job:
        log("!! 작업폴더가 지정되어 있지 않습니다."); return 1

    log(f"작업폴더 : {job}")
    log(f"마스터폴더 {len(db.list_master_folders())}개 / 전체 {len(db.list_folders())}개")
    log("-" * 62)

    driver = None
    try:
        driver = open_logged_in_browser(headless=False, log=log)
        res = ss_image.inspect_folder(
            driver, job, page_size=config.PAGE_SIZE, log=log,
            progress=lambda d, t: log(f"      진행 {d}/{t}"),
        )
        saved = db.save_scan(res["summary"], res["cells"], res["items"])
        s = res["summary"]

        log("=" * 62)
        log(f"폴더           : {s['folder_name']}")
        log(f"전체           : {s['total_rows']:,}행 / LCP {s['total_lcps']:,}종")
        log(f"대표이미지     : 미작업 {s['img_todo_rows']:,} / "
            f"이미지작업 {s['img_work_rows']:,} / 승인완료 {s['img_done_rows']:,}")
        log(f"상품정보       : 미작업 {s['info_todo_rows']:,} / "
            f"저장완료 {s['info_save_rows']:,} / 제외 {s['info_exclude_rows']:,} / "
            f"보류 {s['info_hold_rows']:,}")
        log(f"★ 작업대상    : {s['target_rows']:,}행 / LCP {s['target_lcps']:,}종")
        log(f"상한 걸림      : {'예' if s['capped'] else '아니오'}")
        log(f"소요           : {s['elapsed_sec']}초")
        log(f"DB             : scan_id={saved['scan_id']} "
            f"(셀 {saved['cells']} / 상세 {saved['items']})")
        log("=" * 62)
        log("[매트릭스] 행=대표이미지, 열=상품정보  → 행수(LCP종수)")
        for c in res["cells"]:
            mark = " ★작업대상" if c["is_target"] else ""
            cap = " ⚠상한" if c["capped"] else ""
            log(f"   {c['image_status']:<8} x {c['info_status']:<6} "
                f"= {c['row_count']:>6,} ({c['lcp_count']:>4,}){cap}{mark}")

        prev = db.list_scans(job, limit=2)
        if len(prev) >= 2:
            a, b = prev[0], prev[1]
            log("-" * 62)
            log(f"[직전 점검 대비] {b['scanned_at']} → {a['scanned_at']}")
            log(f"   작업대상 : {b['target_rows']:,} → {a['target_rows']:,} "
                f"({a['target_rows'] - b['target_rows']:+,}행)")
            log(f"   LCP종수  : {b['target_lcps']:,} → {a['target_lcps']:,} "
                f"({a['target_lcps'] - b['target_lcps']:+,}종)")
        return 0
    except Exception:
        log("!! 오류"); log(traceback.format_exc()); return 1
    finally:
        if driver is not None:
            try: driver.quit()
            except Exception: pass

if __name__ == "__main__":
    sys.exit(main())
