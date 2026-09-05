"""
ALL 상품분석 (일괄 실행).

화면 조작 순서는 [상품정보 수정] -> 팝업 -> [상품분석] 클릭이지만,
팝업 JS 를 뜯어보면 실제로는 내부 분석서버에 AJAX 두 번을 던지는 게 전부다.
그래서 팝업창을 20개씩 띄우는 대신 HTTP 로 요청만 걸고 바로 다음 상품으로 넘어간다.

동작
  1) 작업폴더에서 '대표이미지 승인완료 + 상품정보 미작업' 검색
  2) 같은 LCP 가 최대 20행까지 있으므로 LCP 당 1건만 남긴다
  3) 이미 분석한 LCP 는 DB 기록으로 스킵
  4) batch_size 건씩:
       - 팝업 조회 -> analysis_date 가 있으면 이미 완료 -> 기록만
       - 아니면 분석 요청을 걸고 곧바로 다음 상품으로
       - 배치를 다 건 뒤 완료될 때까지 폴링
  5) 완료 즉시 on_record 로 DB/로컬에 최신화 -> 다음 실행 때 다시 안 누른다
"""
import time

from . import constants as C
from .analysis import check_analysis, fetch_popup, start_analysis


def collect_targets(client, folder_name: str, done_lcps: set, log=print,
                    queue: list = None) -> dict:
    """
    작업폴더의 분석 대상(LCP 단위, 기존 완료분 제외)을 만든다.

    queue 가 주어지면(자동점검이 저장해둔 미분석 LCP 목록) 검색을 생략하고
    그 목록을 그대로 쓴다 - ALL 상품분석이 곧바로 시작된다.
    """
    if queue:
        todo = [{"lcp_code": q["lcp_code"], "l_code": q.get("l_code"),
                 "product_no": q.get("product_no")}
                for q in queue
                if q.get("lcp_code") and q["lcp_code"] not in done_lcps
                and q.get("product_no")]
        log(f"[분석] 저장된 미분석 대기열 사용 → 대상 {len(todo):,}종 (검색 생략)")
        return {"rows": 0, "lcps": len(queue), "skipped": 0, "todo": todo}

    res = client.search_full(folder_name, C.TARGET_IMAGE_VALUE, C.TARGET_INFO_VALUE)
    rows = res["rows"]

    by_lcp = {}
    for lcp, lcode, no in rows:
        if lcp and lcp not in by_lcp:
            by_lcp[lcp] = {"lcp_code": lcp, "l_code": lcode, "product_no": no}

    todo = [v for k, v in by_lcp.items() if k not in done_lcps]
    skipped = len(by_lcp) - len(todo)

    log(f"[분석] 검색 {len(rows):,}행 → LCP {len(by_lcp):,}종 "
        f"(기존 완료 {skipped:,}종 제외) → 대상 {len(todo):,}종 ({res['elapsed']}초)")
    return {"rows": len(rows), "lcps": len(by_lcp),
            "skipped": skipped, "todo": todo}


def _record(on_record, base: dict, folder_name: str, **kw) -> None:
    if not on_record:
        return
    rec = {k: v for k, v in base.items() if k != "popup"}
    rec["folder_name"] = folder_name
    rec.update(kw)
    on_record(rec)


def run_all_analysis(client, folder_name: str, done_lcps: set,
                     batch_size: int = 20, poll_interval: int = 10,
                     batch_timeout: int = 300, log=print, progress=None,
                     should_stop=None, on_record=None, limit: int = 0,
                     on_stat=None, queue: list = None) -> dict:
    """ALL 상품분석 실행. limit>0 이면 앞에서 그만큼만(시험용). 반환: 통계 dict"""
    started = time.time()
    info = collect_targets(client, folder_name, done_lcps, log=log,
                           queue=queue)
    todo = info["todo"]
    if limit and limit > 0:
        todo = todo[:limit]
        log(f"[분석] 시험 실행 : 앞 {len(todo)}건만 처리합니다.")

    stats = {"total": len(todo), "done": 0, "already": 0, "error": 0,
             "timeout": 0, "skipped": info["skipped"],
             "lcps": info["lcps"], "rows": info["rows"], "elapsed": 0.0}
    if not todo:
        stats["elapsed"] = round(time.time() - started, 1)
        log("[분석] 새로 분석할 대상이 없습니다.")
        return stats

    processed = 0
    total_batches = (len(todo) + batch_size - 1) // batch_size

    def step():
        nonlocal processed
        processed += 1
        if progress:
            progress(processed, len(todo))
        if on_stat:
            on_stat(dict(stats, processed=processed, remain=len(todo) - processed))

    for start in range(0, len(todo), batch_size):
        if should_stop and should_stop():
            log("[분석] 사용자 중단")
            break

        batch = todo[start:start + batch_size]
        bno = start // batch_size + 1
        log(f"[분석] ── 배치 {bno}/{total_batches} ({len(batch)}건) 요청 ──")

        pending = []
        for item in batch:
            if should_stop and should_stop():
                break
            lcp = item["lcp_code"]

            try:
                pop = fetch_popup(client.session, item["product_no"])
            except Exception as e:
                log(f"   [{lcp}] 팝업 조회 실패: {e}")
                stats["error"] += 1
                _record(on_record, item, folder_name,
                        status="error", state_msg=str(e)[:180])
                step()
                continue

            if pop.get("already_done"):
                log(f"   [{lcp}] 이미 분석완료 ({pop['analysis_date']}) → 기록만")
                stats["already"] += 1
                _record(on_record, item, folder_name,
                        product_id=pop.get("product_id"), status="done",
                        state_msg="이미완료", analyzed_at=pop.get("analysis_date"))
                step()
                continue

            r = start_analysis(pop)
            if not r["ok"]:
                log(f"   [{lcp}] 분석요청 실패: {r['msg']}")
                stats["error"] += 1
                _record(on_record, item, folder_name,
                        product_id=pop.get("product_id"),
                        status="error", state_msg=r["msg"])
                step()
                continue

            log(f"   [{lcp}] 분석요청 OK (no={r['analysis_no']})")
            pending.append({**item, "popup": pop, "analysis_no": r["analysis_no"]})
            _record(on_record, item, folder_name,
                    product_id=pop.get("product_id"),
                    analysis_no=r["analysis_no"],
                    status="pending", state_msg="요청됨")

        if not pending:
            continue

        log(f"[분석] 배치 {bno} 완료 대기 ({len(pending)}건)")
        deadline = time.time() + batch_timeout
        while pending and time.time() < deadline:
            if should_stop and should_stop():
                log("[분석] 사용자 중단 (대기 중)")
                break
            still = []
            for it in pending:
                st = check_analysis(it["popup"], it["analysis_no"])
                if st["done"]:
                    stats["done"] += 1
                    log(f"   [{it['lcp_code']}] 분석완료")
                    _record(on_record, it, folder_name,
                            product_id=it["popup"].get("product_id"),
                            status="done", state_msg=st["msg"],
                            analyzed_at=time.strftime("%Y-%m-%d %H:%M:%S"))
                    step()
                elif st["state"] == "P":
                    still.append(it)
                else:
                    stats["error"] += 1
                    log(f"   [{it['lcp_code']}] 오류: {st['msg']}")
                    _record(on_record, it, folder_name,
                            status="error", state_msg=st["msg"])
                    step()
            pending = still
            if pending:
                time.sleep(poll_interval)

        for it in pending:
            stats["timeout"] += 1
            log(f"   [{it['lcp_code']}] 시간초과 (서버에서는 계속 진행중일 수 있음)")
            _record(on_record, it, folder_name,
                    status="pending", state_msg="시간초과")
            step()

    stats["elapsed"] = round(time.time() - started, 1)
    log(f"[분석] 종료 : 완료 {stats['done']} / 이미완료 {stats['already']} / "
        f"오류 {stats['error']} / 시간초과 {stats['timeout']} ({stats['elapsed']}초)")
    return stats
