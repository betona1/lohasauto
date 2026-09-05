"""작업폴더의 남은 상품분석을 전부 실행."""
import io, sys, traceback
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app import config, db
from app.lohas.http_client import LohasHttp
from app.lohas.browser import cookie_path
from app.lohas.session import get_client
from app.lohas.analysis_batch import run_all_analysis

def log(m): print(m, flush=True)

def main():
    db.init_db()
    folder = db.get_job_folder()
    if not folder:
        log("!! 작업폴더 미지정"); return 1
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0

    client = get_client(log=log)
    done = db.done_lcp_set()
    log(f"작업폴더 : {folder}")
    log(f"기록된 완료 LCP : {len(done):,}종")
    log(f"배치 {config.ANALYSIS_BATCH} / 폴링 {config.ANALYSIS_POLL}초 / "
        f"타임아웃 {config.ANALYSIS_TIMEOUT}초")
    log("-" * 62)

    st = run_all_analysis(
        client, folder, done,
        batch_size=config.ANALYSIS_BATCH,
        poll_interval=config.ANALYSIS_POLL,
        batch_timeout=config.ANALYSIS_TIMEOUT,
        log=log,
        progress=lambda d, t: None,
        on_record=db.save_analysis,
        on_stat=lambda s: log(f"      [수량] 진행 {s['processed']}/{s['total']} "
                              f"남음 {s['remain']} · 완료 {s['done']} "
                              f"· 이미완료 {s['already']} · 오류 {s['error']}"),
        limit=limit,
    )
    log("=" * 62)
    log(f"검색 {st['rows']:,}행 → LCP {st['lcps']:,}종 (기존완료 {st['skipped']:,} 스킵)")
    log(f"대상 {st['total']:,}종 → 완료 {st['done']:,} / 이미완료 {st['already']:,} "
        f"/ 오류 {st['error']:,} / 시간초과 {st['timeout']:,}")
    log(f"소요 {st['elapsed']}초")
    log(f"DB 완료 LCP 누계 : {len(db.done_lcp_set()):,}종")
    log(f"상태별 : {db.analysis_stats()}")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log(traceback.format_exc()); sys.exit(1)
