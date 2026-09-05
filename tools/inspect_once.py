"""폴더스캔 -> 작업폴더 지정 -> 수량점검 -> DB저장 을 CLI로 전부 실행."""
import io, sys, traceback
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app import config, db
from app.lohas import folders as folder_api
from app.lohas import ss_image
from app.lohas.browser import open_logged_in_browser

def log(m): print(m, flush=True)

KEYWORD = sys.argv[1] if len(sys.argv) > 1 else "비트마인드"

def main():
    db.init_db()
    driver = None
    try:
        log("[1/4] 로그인")
        driver = open_logged_in_browser(headless=False, log=log)

        log("[2/4] 마스터 폴더 스캔")
        found = folder_api.scan_master_folders(driver, log=log)
        r = db.save_folders(found, source="ss_image")
        log(f"      폴더 {r['total']}개 저장 (신규 {r['new']})")

        cand = sorted([f for f in found if KEYWORD in f["name"]],
                      key=lambda f: len(f["name"]))
        if not cand:
            log(f"!! '{KEYWORD}' 폴더 없음"); return 1
        target = cand[0]["name"]
        db.set_work_folder(target)
        log(f"[3/4] 작업폴더 = {target}")

        log("[4/4] 수량 점검 (12칸 매트릭스)")
        res = ss_image.inspect_folder(
            driver, target, page_size=config.PAGE_SIZE,
            log=log,
            progress=lambda d, t: log(f"      진행 {d}/{t}"),
        )
        saved = db.save_scan(res["summary"], res["cells"], res["items"])
        s = res["summary"]

        log("=" * 62)
        log(f"폴더            : {s['folder_name']}")
        log(f"전체            : {s['total_rows']:,}행 / LCP {s['total_lcps']:,}종")
        log(f"대표이미지      : 미작업 {s['img_todo_rows']:,} / "
            f"이미지작업 {s['img_work_rows']:,} / 승인완료 {s['img_done_rows']:,}")
        log(f"상품정보        : 미작업 {s['info_todo_rows']:,} / "
            f"저장완료 {s['info_save_rows']:,} / 제외 {s['info_exclude_rows']:,} / "
            f"보류 {s['info_hold_rows']:,}")
        log(f"★ 작업대상     : {s['target_rows']:,}행 / LCP {s['target_lcps']:,}종")
        log(f"조회상한 걸림   : {'예' if s['capped'] else '아니오'}")
        log(f"소요            : {s['elapsed_sec']}초")
        log(f"DB              : scan_id={saved['scan_id']} "
            f"(셀 {saved['cells']} / 상세 {saved['items']})")
        log("=" * 62)
        log("[매트릭스] 대표이미지 x 상품정보  = 행수(LCP종수)")
        for c in res["cells"]:
            mark = " ★" if c["is_target"] else ""
            cap = " ⚠상한" if c["capped"] else ""
            log(f"   {c['image_status']:<8} x {c['info_status']:<6} "
                f"= {c['row_count']:>6,} ({c['lcp_count']:>4,}){cap}{mark}")
        return 0
    except Exception:
        log("!! 오류"); log(traceback.format_exc()); return 1
    finally:
        if driver is not None:
            try: driver.quit()
            except Exception: pass

if __name__ == "__main__":
    sys.exit(main())
