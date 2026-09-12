"""백그라운드 작업 스레드. UI 가 멈추지 않도록 Selenium 작업을 전부 여기서 돌린다."""
import traceback

from PySide6.QtCore import QObject, Signal

from .. import db
from ..lohas import folders as folder_api
from ..lohas import ss_image
from ..lohas.browser import open_logged_in_browser
from ..lohas.session import get_client
from ..lohas.analysis_batch import run_all_analysis
from ..lohas import preview as preview_mod
from ..lohas import collect as collect_mod
from ..lohas import lcode_status
from ..lohas import category_plan
from ..lohas import fix10


class BaseWorker(QObject):
    log = Signal(str)
    progress = Signal(int, int)     # (done, total) - total<=0 이면 불확정
    failed = Signal(str)
    finished = Signal(dict)
    stat = Signal(dict)            # 진행 중 통계 (작업수량 표시용)

    def __init__(self, headless: bool = False, monitor: int = 0,
                 use_http: bool = True):
        super().__init__()
        self.headless = headless
        self.monitor = monitor
        self.use_http = use_http      # 쿠키 기반 HTTP 조회 (브라우저 없이)
        self._stop = False

    def stop(self):
        self._stop = True

    def should_stop(self) -> bool:
        return self._stop

    def _log(self, msg: str):
        self.log.emit(str(msg))


class FolderScanWorker(BaseWorker):
    """마스터(작업폴더) 목록 스캔 → DB 저장."""

    def run(self):
        driver = None
        try:
            if self.use_http:
                client = get_client(self.headless, self.monitor, log=self._log)
                self._log("폴더 목록 조회 중...")
                found = client.fetch_folders()
            else:
                self._log("브라우저 실행 중...")
                driver = open_logged_in_browser(
                    self.headless, log=self._log, monitor=self.monitor)
                self._log("상품정보관리 페이지에서 마스터 폴더 목록 스캔 중...")
                found = folder_api.scan_master_folders(driver, log=self._log)

            result = db.save_folders(found, source="ss_image")
            self._log(
                f"DB 저장 : 총 {result['total']}개 "
                f"(신규 {result['new']} / 갱신 {result['updated']} / "
                f"비활성 {result['deactivated']})"
            )
            if result.get("mirror"):
                self._log(result["mirror"])

            self.finished.emit(result)
        except Exception as e:
            self._log(traceback.format_exc())
            self.failed.emit(str(e))
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass


class InspectWorker(BaseWorker):
    """작업폴더 수량 점검 → DB 저장."""

    def __init__(self, folder_name: str, page_size: str, max_pages: int,
                 headless: bool = False, monitor: int = 0, quick: bool = False,
                 use_http: bool = True):
        super().__init__(headless, monitor, use_http)
        self.folder_name = folder_name
        self.page_size = page_size
        self.max_pages = max_pages
        self.quick = quick

    def run(self):
        driver = None
        try:
            kind = "빠른 점검" if self.quick else "전체 점검"

            if self.use_http:
                client = get_client(self.headless, self.monitor, log=self._log)
                self._log(f"작업폴더 '{self.folder_name}' {kind} 시작 (HTTP)")
                res = ss_image.inspect_folder_http(
                    client, self.folder_name, log=self._log,
                    progress=lambda d, t: self.progress.emit(d, t),
                    should_stop=self.should_stop, quick=self.quick,
                )
            else:
                self._log("브라우저 실행 중...")
                driver = open_logged_in_browser(
                    self.headless, log=self._log, monitor=self.monitor)
                self._log(f"작업폴더 '{self.folder_name}' {kind} 시작 (브라우저)")
                res = ss_image.inspect_folder(
                    driver, self.folder_name,
                    page_size=self.page_size, max_pages=self.max_pages,
                    log=self._log,
                    progress=lambda d, t: self.progress.emit(d, t or 0),
                    should_stop=self.should_stop, quick=self.quick,
                )

            saved = db.save_scan(res["summary"], res["cells"], res["items"])
            self._log(
                f"DB 저장 : scan_id={saved['scan_id']}, "
                f"매트릭스 {saved['cells']}칸 / 작업대상 {saved['items']}행"
            )
            if saved.get("mirror"):
                self._log(saved["mirror"])

            self.finished.emit({
                "summary": res["summary"],
                "cells": res["cells"],
                "items": res["items"],
                "scan_id": saved["scan_id"],
            })
        except Exception as e:
            self._log(traceback.format_exc())
            self.failed.emit(str(e))
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass


class DumpWorker(BaseWorker):
    """페이지 구조 덤프 (상태 판정 규칙 보정용 진단)."""

    def __init__(self, folder_name: str, page_size: str,
                 headless: bool = False, monitor: int = 0):
        super().__init__(headless, monitor)
        self.folder_name = folder_name
        self.page_size = page_size

    def run(self):
        driver = None
        try:
            self._log("브라우저 실행 중...")
            driver = open_logged_in_browser(
                self.headless, log=self._log, monitor=self.monitor)
            path = ss_image.dump_page_structure(
                driver, self.folder_name, self.page_size, log=self._log
            )
            self.finished.emit({"path": path})
        except Exception as e:
            self._log(traceback.format_exc())
            self.failed.emit(str(e))
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass


class AiImageWorker(BaseWorker):
    """
    AI 이미지 일괄수정 — 시작 페이지부터 끝 페이지까지 **한 번에 하나씩** 건다.

    로하스는 작업을 하나만 돌린다. 그래서 한 페이지를 걸고 완료를 기다렸다가
    다음 페이지를 건다(2026-09-07 사용자: "3페이지 선택하면 거기부터 끝까지").

    **폴링은 여기 한 곳에서만 한다.** 진행 상태를 `db.ai_job_set()` 으로
    적어두고, 자동점검은 그 기록만 읽는다 — 같은 화면을 두 곳에서 긁지
    않기 위해서다(사용자: "불필요한 로하스 크롤링은 자제하자").
    """

    POLL_SEC = 120             # 1,000건에 32분. 2분마다면 진행이 보인다

    def __init__(self, folder_name: str, page_from: int, page_to: int,
                 title: str, query: str, headless: bool = False,
                 monitor: int = 0):
        super().__init__(headless, monitor, use_http=True)
        self.folder_name = folder_name
        self.page_from = int(page_from)
        self.page_to = max(int(page_to), int(page_from))
        self.title = title
        self.query = query

    def run(self):
        import time

        from ..lohas import ai_image

        done, t0 = [], time.time()
        try:
            client = get_client(self.headless, self.monitor, log=self._log)
            pages = range(self.page_from, self.page_to + 1)
            self._log(f"'{self.folder_name}' {self.page_from}~{self.page_to}"
                      f"페이지 AI 이미지 일괄수정 ({len(pages)}회) — "
                      f"질의어 '{self.query}'")

            for n, page in enumerate(pages, 1):
                if self.should_stop():
                    self._log("중지 요청 — 남은 페이지는 걸지 않습니다")
                    break
                title = ai_image.title_for(self.title, page)
                res = ai_image.run_page(client, self.folder_name, page,
                                        title, query=self.query, dry=False)
                if not res.get("ok"):
                    self._log(f"  !! {page}페이지 {res.get('reason', '')}")
                    if res.get("reason", "").startswith("그 페이지"):
                        break          # 마지막 페이지를 지났다
                    continue
                self._log(f"  [{n}/{len(pages)}] {page}페이지 "
                          f"{res['count']:,}건 접수 — {title}")

                job = ai_image.find_job(client.session, title)
                no = job.get("No", "")
                rec = {"no": no, "title": title, "folder": self.folder_name,
                       "page": page, "count": res["count"],
                       "query": self.query, "first": res.get("first"),
                       "last": res.get("last"), "state": job.get("상태"),
                       "started": job.get("시작일시", ""), "ended": ""}
                db.ai_job_set(rec)
                db.save_ai_job(rec)

                last = None
                while no and not self.should_stop():
                    j = next((x for x in ai_image.jobs(client.session)
                              if x.get("No") == no), None)
                    if not j:
                        break
                    if j.get("상태") != last:
                        last = j.get("상태")
                        self._log(f"      No.{no} {last}")
                        rec.update(state=last,
                                   started=j.get("시작일시", ""),
                                   ended=j.get("완료일시", ""))
                        db.ai_job_set(rec)
                        # 화면에 그대로 보여줄 상태를 함께 보낸다
                        self.stat.emit({
                            "processed": n, "total": len(pages),
                            "state": last, "page": page, "no": no,
                            "done_pages": n - 1, "title": title,
                        })
                    if last in ("완료", "취소완료", "실패", "오류"):
                        res["job"] = j
                        break
                    time.sleep(self.POLL_SEC)
                # 끝난 페이지는 **로컬 DB + 서버**에 남긴다 - 다음에 같은
                # 페이지를 또 걸지 않기 위해서다(2026-09-07 사용자 요청).
                # 마지막 상태를 못 본 채 다음 작업으로 넘어가는 일이 있어,
                # 결과 화면에서 그 작업을 한 번 더 확인해 확정한다.
                fin = next((x for x in ai_image.jobs(client.session)
                            if x.get("No") == no), None)
                if fin:
                    rec.update(state=fin.get("상태"),
                               started=fin.get("시작일시", ""),
                               ended=fin.get("완료일시", ""))
                db.save_ai_job(rec)
                self.stat.emit({"processed": n, "total": len(pages),
                                "state": rec.get("state") or "", "page": page,
                                "no": no, "done_pages": n, "title": title})
                done.append(res)
                if (res.get("job") or {}).get("상태") not in (None, "완료"):
                    self._log("      완료가 아니어서 다음 페이지로 넘어가지 않습니다")
                    break

            db.ai_job_clear()
            self.finished.emit({
                "folder": self.folder_name, "pages": len(done),
                "from": self.page_from, "to": self.page_to,
                "count": sum(x.get("count", 0) for x in done),
                "query": self.query, "title": self.title,
                "results": done, "seconds": round(time.time() - t0, 1),
            })
        except Exception as e:
            db.ai_job_clear()
            self._log(traceback.format_exc())
            self.failed.emit(str(e))


class AnalysisWorker(BaseWorker):
    """
    ALL 상품분석 : 미분석 LCP 를 찾아 분석 요청 → 완료 기록.

    `folder_name` 은 폴더 하나여도 되고 **여러 폴더의 목록**이어도 된다.
    폴더가 넷으로 늘어난 뒤 작업폴더 하나만 분석되던 것을 고쳤다
    (2026-09-07 사용자 요청). 여럿이면 순서대로 돌고 합계를 돌려준다.
    """

    def __init__(self, folder_name, batch_size: int, poll_interval: int,
                 batch_timeout: int, headless: bool = False, monitor: int = 0,
                 limit: int = 0, use_queue: bool = True):
        super().__init__(headless, monitor, use_http=True)
        self.folders = ([folder_name] if isinstance(folder_name, str)
                        else [f for f in (folder_name or []) if f])
        self.folder_name = self.folders[0] if self.folders else ""
        self.batch_size = batch_size
        self.poll_interval = poll_interval
        self.batch_timeout = batch_timeout
        self.limit = limit
        self.use_queue = use_queue

    def run(self):
        try:
            client = get_client(self.headless, self.monitor, log=self._log)
            total = {}
            for i, folder in enumerate(self.folders, 1):
                if self.should_stop():
                    break
                done = db.done_lcp_set()
                queue = db.list_queue(folder) if self.use_queue else None
                if queue:
                    self._log(f"저장된 미분석 대기열 {len(queue):,}종 발견 → 검색 생략")
                head = (f"[{i}/{len(self.folders)}] "
                        if len(self.folders) > 1 else "")
                self._log(f"{head}작업폴더 '{folder}' ALL 상품분석 시작 "
                          f"(기록된 완료 LCP {len(done):,}종)")

                stats = run_all_analysis(
                    client, folder, done,
                    batch_size=self.batch_size,
                    poll_interval=self.poll_interval,
                    batch_timeout=self.batch_timeout,
                    log=self._log,
                    progress=lambda d, t: self.progress.emit(d, t),
                    should_stop=self.should_stop,
                    on_record=self._record,
                    limit=self.limit,
                    on_stat=lambda st: self.stat.emit(st),
                    queue=queue,
                )
                for k, v in (stats or {}).items():
                    if isinstance(v, (int, float)):
                        total[k] = total.get(k, 0) + v
                    else:
                        total.setdefault(k, v)
            total["folders"] = len(self.folders)
            self.finished.emit(total)
        except Exception as e:
            self._log(traceback.format_exc())
            self.failed.emit(str(e))

    def _record(self, rec: dict):
        """분석 기록 + 완료건은 대기열에서 제거."""
        db.save_analysis(rec)
        if rec.get("status") == "done" and rec.get("lcp_code"):
            db.remove_from_queue(rec["lcp_code"])


class SampleWorker(BaseWorker):
    """샘플 1건 분석 (미리보기). 저장은 하지 않는다."""

    def __init__(self, folder_name: str, lcp_code: str = None,
                 headless: bool = False, monitor: int = 0):
        super().__init__(headless, monitor, use_http=True)
        self.folder_name = folder_name
        self.lcp_code = lcp_code

    def run(self):
        try:
            client = get_client(self.headless, self.monitor, log=self._log)
            res = preview_mod.run_sample(
                client, self.folder_name, self.lcp_code, log=self._log)
            self.finished.emit(res)
        except Exception as e:
            self._log(traceback.format_exc())
            self.failed.emit(str(e))


class LcodeStatusWorker(BaseWorker):
    """폴더의 L코드 상태(대표이미지/상품정보) 수집."""

    def __init__(self, folder_name: str, headless=False, monitor=0):
        super().__init__(headless, monitor, use_http=True)
        self.folder_name = folder_name

    def run(self):
        try:
            client = get_client(self.headless, self.monitor, log=self._log)
            res = lcode_status.collect_folder(
                client, self.folder_name, log=self._log,
                progress=lambda d, t: self.progress.emit(d, t),
                should_stop=self.should_stop)
            saved = db.save_lcode_status(self.folder_name, res["rows"])
            self._log(f"DB 저장 : {saved['rows']:,}행 (정리 {saved['removed']}행)")
            if saved.get("mirror"):
                self._log(saved["mirror"])
            self.finished.emit({**res, **saved})
        except Exception as e:
            self._log(traceback.format_exc())
            self.failed.emit(str(e))


class BasicCollectWorker(BaseWorker):
    """LCP 기본정보(포함상품·키워드·카테고리) 수집."""

    def __init__(self, folder_name: str, limit: int = 0, redo: bool = False,
                 headless=False, monitor=0):
        super().__init__(headless, monitor, use_http=True)
        self.folder_name = folder_name
        self.limit = limit
        self.redo = redo

    def run(self):
        try:
            client = get_client(self.headless, self.monitor, log=self._log)
            # 폴더의 모든 LCP (상태 테이블 우선, 없으면 전체 검색)
            seen, targets = set(), []
            for r in db.lcode_rows(self.folder_name):
                if r["lcp_code"] not in seen:
                    seen.add(r["lcp_code"])
                    targets.append((r["lcp_code"], r["product_no"]))
            if not targets:
                res = client.search_full(self.folder_name, "all", "all")
                for lcp, lcode, no in res["rows"]:
                    if lcp and lcp not in seen:
                        seen.add(lcp)
                        targets.append((lcp, no))

            done = set() if self.redo else db.collected_lcps()
            todo = [t for t in targets if t[0] not in done]
            if self.limit:
                todo = todo[:self.limit]
            self._log(f"대상 {len(targets):,}종 중 미수집 {len(todo):,}종 처리")

            ok = fail = 0
            agg = {"used": 0, "recommend": 0, "tokens": 0,
                   "wish": 0, "categories": 0, "options": 0}
            for i, (lcp, no) in enumerate(todo, 1):
                if self.should_stop():
                    self._log("사용자 중단")
                    break
                try:
                    d = collect_mod.collect_one(
                        client, lcp, no, log=lambda *a, **k: None)
                    r = db.save_lcp_collect(d, self.folder_name)
                    ok += 1
                    for k in agg:
                        agg[k] += r.get(k, 0)
                    if i % 10 == 0 or i == len(todo):
                        self._log(f"  {i}/{len(todo)} 수집 (키워드 누적 "
                                  f"{agg['used'] + agg['recommend'] + agg['tokens']:,})")
                except Exception as e:
                    fail += 1
                    self._log(f"  ! {lcp} 실패: {str(e)[:60]}")
                self.progress.emit(i, len(todo))

            self._log(f"완료 {ok}건 / 실패 {fail}건")
            self.finished.emit({"ok": ok, "fail": fail, "total": len(todo), **agg})
        except Exception as e:
            self._log(traceback.format_exc())
            self.failed.emit(str(e))


class CategoryPlanWorker(BaseWorker):
    """카테고리 미저장 LCP 의 후보를 모아 검토 목록을 만든다(읽기 전용)."""

    def __init__(self, folder_name: str = None, tiers=(), headless=False,
                 monitor=0):
        super().__init__(headless, monitor, use_http=True)
        self.folder_name = folder_name
        self.tiers = tuple(tiers)

    def run(self):
        try:
            client = get_client(self.headless, self.monitor, log=self._log)
            rows = category_plan.build(
                client.session, db, self.folder_name, tiers=self.tiers,
                log=self._log,
                progress=lambda d, t: self.progress.emit(d, t),
                should_stop=self.should_stop)
            self._log(f"검토 대상 {len(rows):,}종")
            self.finished.emit({"rows": rows})
        except Exception as e:
            self._log(traceback.format_exc())
            self.failed.emit(str(e))


class CategorySaveWorker(BaseWorker):
    """
    고른 카테고리를 저장한다.
    저장 뒤에는 사이트에서 다시 읽어 실제로 들어갔는지 확인하고 DB 를 맞춘다.
    """

    def __init__(self, jobs: list, folder_name: str = None, headless=False,
                 monitor=0):
        super().__init__(headless, monitor, use_http=True)
        self.jobs = jobs          # [{item, code, capacity, unit, total_capacity}]
        self.folder_name = folder_name

    def run(self):
        try:
            from ..lohas import attr_detail

            client = get_client(self.headless, self.monitor, log=self._log)
            s = client.session
            ok = fail = 0
            saved = []
            for i, j in enumerate(self.jobs, 1):
                if self.should_stop():
                    self._log("사용자 중단")
                    break
                item = j["item"]
                res = category_plan.save_group(
                    s, item, j["code"], capacity=j.get("capacity", ""),
                    unit=j.get("unit", ""),
                    total_capacity=j.get("total_capacity", ""),
                    log=self._log)
                ok += res["ok"]
                fail += res["fail"]
                saved.extend(res["saved"])
                self._log(f"[{i}/{len(self.jobs)}] {item['lcp_code']} "
                          f"-> {j['code']}  저장 {res['ok']}건"
                          + (f" / 실패 {res['fail']}건" if res["fail"] else ""))
                self.progress.emit(i, len(self.jobs))

            done = []
            if saved:
                self._log(f"저장분 {len(saved):,}건 재조회 중...")
                for r in saved:
                    try:
                        d = attr_detail.fetch_detail(s, r["product_no"])
                        d["lcp_code"] = r["lcp_code"]
                        d["l_code"] = r["l_code"]
                        done.append(d)
                    except Exception:
                        pass
                if done:
                    st = db.save_lcode_attr(
                        self.folder_name or db.get_job_folder(), done)
                    self._log(f"로컬 DB {st['rows']:,}행  {st.get('mirror', '')}")
                bad = [d for d in done if not d["cat_saved"]]
                if bad:
                    self._log(f"!! 반영 안 된 건 {len(bad)}건")

            self.finished.emit({"ok": ok, "fail": fail,
                                "verified": len(done)})
        except Exception as e:
            self._log(traceback.format_exc())
            self.failed.emit(str(e))


class CategoryAutoWorker(BaseWorker):
    """
    **ALL 카테고리** — 폴더 하나의 카테고리를 끝까지 채운다.

    대상은 **상품정보가 '미작업' 인 것만**이고, 대표이미지 상태는 가리지
    않는다. 카테고리는 이미지 작업과 상관없는 선행 단계이기 때문이다
    (2026-09-09 사용자 지시).

    순서
      1) 필요하면 attr 팝업을 다시 읽어 `next_step` 을 최신으로 만든다.
         분석을 막 끝낸 폴더는 이게 없으면 대상이 0건으로 나온다.
      2) LCP 마다 후보를 조회해 등급을 매기고(`category_plan.build`)
      3) 등급별로 한 코드를 정해(`auto_choice`) 저장한다
      4) 저장분을 되읽어 실제로 들어갔는지 확인한다

    **상품정보 상태는 바뀌지 않는다** — 카테고리 저장은 태그·상품명 저장과
    달리 '저장완료' 로 넘기지 않는다.
    """

    def __init__(self, folder_name: str, refresh: bool = True,
                 tiers=None, headless=False, monitor=0):
        super().__init__(headless, monitor, use_http=True)
        self.folder_name = folder_name
        self.refresh = refresh
        self.tiers = tuple(tiers) if tiers else ()

    def run(self):
        try:
            from ..lohas import attr_detail

            client = get_client(self.headless, self.monitor, log=self._log)
            s = client.session
            f = self.folder_name

            if self.refresh:
                rows = db.lcode_rows(f)
                self._log(f"[1/3] 작업상태 다시 읽기 — L코드 {len(rows):,}건")
                self.stat.emit({"phase": "상태읽기", "processed": 0,
                                "total": len(rows)})
                res = attr_detail.collect_folder(
                    client, rows, log=self._log,
                    progress=lambda d, t: (
                        self.progress.emit(d, t),
                        self.stat.emit({"phase": "상태읽기", "processed": d,
                                        "total": t})),
                    should_stop=self.should_stop)
                if res["rows"]:
                    st = db.save_lcode_attr(f, res["rows"])
                    self._log(f"      DB {st['rows']:,}행 {st.get('mirror','')}")
            if self.should_stop():
                self.finished.emit({"stopped": True}); return

            self._log("[2/3] 카테고리 후보 조회 · 등급 매기기")
            plan = category_plan.build(
                s, db, f, tiers=self.tiers, log=self._log,
                todo_only=True, should_stop=self.should_stop,
                progress=lambda d, t: (
                    self.progress.emit(d, t),
                    self.stat.emit({"phase": "후보조회", "processed": d,
                                    "total": t})))
            plan = [p for p in plan if p.get("candidates")]
            by_tier = {}
            for p in plan:
                by_tier[p.get("tier")] = by_tier.get(p.get("tier"), 0) + 1
            self._log(f"      대상 {len(plan):,}종  " + " · ".join(
                f"{k} {v}" for k, v in sorted(by_tier.items())))
            if self.should_stop():
                self.finished.emit({"stopped": True}); return

            self._log(f"[3/3] 저장 — {len(plan):,}종")
            ok = fail = 0
            saved = []
            for i, item in enumerate(plan, 1):
                if self.should_stop():
                    self._log("사용자 중단"); break
                ch = category_plan.auto_choice(item, use_ai=False)
                if not ch:
                    continue
                r = category_plan.save_group(
                    s, item, ch["code"], capacity=ch.get("capacity", ""),
                    unit=ch.get("unit", ""),
                    total_capacity=ch.get("total_capacity", ""),
                    log=self._log)
                ok += r["ok"]; fail += r["fail"]; saved.extend(r["saved"])
                self._log(f"  [{i}/{len(plan)}] {item['lcp_code']} "
                          f"{ch['code']} {ch['name'][:22]} "
                          f"({ch['tier']}/{ch['source']}) 저장 {r['ok']}건"
                          + (f" 실패 {r['fail']}" if r["fail"] else ""))
                self.progress.emit(i, len(plan))
                self.stat.emit({"phase": "저장", "processed": i,
                                "total": len(plan), "ok": ok, "fail": fail})

            done = []
            if saved:
                self._log(f"저장분 {len(saved):,}건 재조회 중...")
                for r in saved:
                    try:
                        d = attr_detail.fetch_detail(s, r["product_no"])
                        d["lcp_code"] = r["lcp_code"]; d["l_code"] = r["l_code"]
                        done.append(d)
                    except Exception:
                        pass
                if done:
                    st = db.save_lcode_attr(f, done)
                    self._log(f"로컬 DB {st['rows']:,}행 {st.get('mirror','')}")
                bad = [d for d in done if not d["cat_saved"]]
                if bad:
                    self._log(f"!! 반영 안 된 건 {len(bad)}건")
            self.finished.emit({"ok": ok, "fail": fail, "lcps": len(plan),
                                "verified": len(done), "by_tier": by_tier})
        except Exception as e:
            self._log(traceback.format_exc())
            self.failed.emit(str(e))


class TagAllWorker(BaseWorker):
    """
    **ALL 태그** — 폴더 하나의 태그를 끝까지 채운다.

    대상은 **카테고리가 저장된 · 상품정보 미작업**이고, 대표이미지 상태는
    가리지 않는다. 규칙은 `tag_batch` 에 있다.

    태그 저장은 상품정보 상태를 바꾸지 않는다.
    """

    def __init__(self, folder_name: str, want: int = 10, any_image: bool = True,
                 headless=False, monitor=0):
        super().__init__(headless, monitor, use_http=True)
        self.folder_name = folder_name
        self.want = want
        self.any_image = any_image

    def run(self):
        try:
            from ..lohas import tag_batch

            client = get_client(self.headless, self.monitor, log=self._log)
            res = tag_batch.run(
                client.session, db, self.folder_name, want=self.want,
                any_image=self.any_image, apply_=True, log=self._log,
                progress=lambda d, t: self.progress.emit(d, t),
                should_stop=self.should_stop,
                stat=lambda st: self.stat.emit(st))
            if res.get("empty"):
                self._log("후보가 아예 없던 LCP "
                          f"{len(res['empty'])}종: "
                          + ", ".join(res["empty"][:8]))
            self.finished.emit(res)
        except Exception as e:
            self._log(traceback.format_exc())
            self.failed.emit(str(e))


class DatalabWorker(BaseWorker):
    """네이버 데이터랩 카테고리별 인기키워드(최대 500) 수집. 로하스와 무관한 외부 API 다."""

    def __init__(self, cids: list, days: int = 30, redo: bool = False):
        super().__init__(False, 0, use_http=True)
        self.cids = cids
        self.days = days
        self.redo = redo

    def run(self):
        try:
            from ..lohas import datalab

            if not datalab.ping():
                self.failed.emit("데이터랩 서버(100번)에 연결할 수 없습니다.")
                return
            res = datalab.collect(
                db, self.cids, days=self.days, redo=self.redo, log=self._log,
                progress=lambda d, t: self.progress.emit(d, t),
                should_stop=self.should_stop)
            self.finished.emit(res)
        except Exception as e:
            self._log(traceback.format_exc())
            self.failed.emit(str(e))


class CatKeywordWorker(BaseWorker):
    """로하스 태그/상품명 탭 키워드 수집. 카테고리가 저장된 LCP 만 대상이다."""

    def __init__(self, folder_name: str = None, titles: int = 1,
                 redo: bool = False, limit: int = 0, only: str = "",
                 headless=False, monitor=0):
        super().__init__(headless, monitor, use_http=True)
        self.folder_name = folder_name
        self.titles = titles
        self.redo = redo
        self.limit = limit
        self.only = only

    def run(self):
        try:
            from ..lohas import cat_keyword

            client = get_client(self.headless, self.monitor, log=self._log)
            rows = cat_keyword.targets(db, self.folder_name, self.redo,
                                       self.only)
            if self.limit:
                rows = rows[:self.limit]
            self._log(f"수집 대상 LCP {len(rows):,}종")
            res = cat_keyword.collect_folder(
                db, client.session, rows, titles=self.titles, log=self._log,
                progress=lambda d, t: self.progress.emit(d, t),
                should_stop=self.should_stop)
            self.finished.emit(res)
        except Exception as e:
            self._log(traceback.format_exc())
            self.failed.emit(str(e))


class AiCategoryWorker(BaseWorker):
    """
    카테고리를 AI 가 고른다. 고르기만 하고 저장은 하지 않는다 —
    사람이 화면에서 확인한 뒤 저장 버튼을 누르게 한다.
    """

    def __init__(self, items: list):
        super().__init__(False, 0, use_http=True)
        self.items = items          # category_plan.build() 결과

    def run(self):
        try:
            import json as _json

            from ..lohas import gemini

            if not gemini.available():
                self.failed.emit("Gemini API 키가 없습니다 (.env 확인)")
                return

            out = []
            for i, p in enumerate(self.items, 1):
                if self.should_stop():
                    self._log("사용자 중단")
                    break
                with db.sqlite_conn() as c:
                    r = c.execute(
                        "SELECT product_name, wish_keywords, markets "
                        "FROM lcp_product WHERE lcp_code=?",
                        (p["lcp_code"],)).fetchone()
                name = (r["product_name"] if r else "") or ""
                wish = (r["wish_keywords"] if r else "") or ""
                mk = ""
                try:
                    m = _json.loads((r["markets"] if r else "") or "{}")
                    mk = " | ".join(f"{k}: {v}" for k, v in m.items() if v)
                except Exception:
                    pass

                cands = [{"code": str(x.get("code")), "name": x.get("name"),
                          "cnt": x.get("cnt")}
                         for x in (p.get("candidates") or [])[:25]]
                ai = gemini.pick_category(name, cands, wish, mk, log=self._log)
                same = ai.get("code") == p.get("code")
                out.append({"lcp_code": p["lcp_code"], "ai": ai, "same": same})
                self._log(
                    f"[{i}/{len(self.items)}] {p['lcp_code']} {name[:22]}"
                    + (f"  AI: {ai['name'][:38]}" if ai else "  AI 실패")
                    + ("  (규칙과 같음)" if same else "  ★ 규칙과 다름"))
                self.progress.emit(i, len(self.items))

            n_ok = sum(1 for x in out if x["ai"])
            n_diff = sum(1 for x in out if x["ai"] and not x["same"])
            self._log(f"AI 판단 {n_ok:,}건 / 규칙과 다른 것 {n_diff:,}건")
            self.finished.emit({"rows": out, "ok": n_ok, "diff": n_diff})
        except Exception as e:
            self._log(traceback.format_exc())
            self.failed.emit(str(e))

class CategoryFixWorker(BaseWorker):
    """
    카테고리 수정 화면 전용. 두 가지 일을 한다.

      rows 가 비어 있으면  후보 목록만 가져온다 (읽기 전용)
      rows 가 있으면       그 L코드들의 카테고리를 code 로 바꾼다

    이미 저장된 값을 바꾸는 것이므로 allow_change=True 로 부른다. 저장 뒤에는
    사이트에서 다시 읽어 실제로 들어갔는지 확인하고 DB 를 맞춘다.
    """

    def __init__(self, lcp_code: str, rows: list, code: str = "",
                 capacity: str = "", unit: str = "", total_capacity: str = "",
                 folder_name: str = None):
        super().__init__(False, 0, use_http=True)
        self.lcp_code = lcp_code
        self.rows = rows or []
        self.code = code
        self.capacity = capacity
        self.unit = unit
        self.total_capacity = total_capacity
        self.folder_name = folder_name

    def run(self):
        try:
            from ..lohas import attr_detail, category

            client = get_client(self.headless, self.monitor, log=self._log)
            s = client.session

            if not self.rows:
                cands = category.fetch_candidates(s, self.lcp_code)
                self.finished.emit({"candidates": cands})
                return

            ok = fail = 0
            saved = []
            for i, r in enumerate(self.rows, 1):
                if self.should_stop():
                    self._log("사용자 중단")
                    break
                try:
                    res = category.save_category(
                        s, r["product_no"], r["l_code"], self.code,
                        capacity=self.capacity, unit=self.unit,
                        total_capacity=self.total_capacity,
                        current=r.get("etc_category") or "",
                        allow_change=True)
                    if res["ok"]:
                        ok += 1
                        saved.append(r)
                    else:
                        fail += 1
                        self._log(f"  !! {r['l_code']} {res['message'][:50]}")
                except Exception as e:
                    fail += 1
                    self._log(f"  !! {r['l_code']} {str(e)[:70]}")
                self.progress.emit(i, len(self.rows))

            verified = 0
            if saved:
                self._log(f"저장분 {len(saved)}건 재조회 중...")
                out = []
                for r in saved:
                    try:
                        d = attr_detail.fetch_detail(s, r["product_no"])
                        d["lcp_code"] = self.lcp_code
                        d["l_code"] = r["l_code"]
                        out.append(d)
                        if str(d["etc_category"]) == str(self.code):
                            verified += 1
                    except Exception:
                        pass
                if out:
                    db.save_lcode_attr(self.folder_name or
                                       db.get_job_folder(), out)
            self.finished.emit({"ok": ok, "fail": fail, "verified": verified})
        except Exception as e:
            self.failed.emit(f"{e}\n{traceback.format_exc()[:600]}")

class TagCopyWorker(BaseWorker):
    """
    같은 LCP 안에서 태그를 복사한다.

    한 LCP 의 L코드들은 색·크기만 다른 같은 상품이라 태그가 거의 같다.
    그래서 하나에 사람이 달아둔 태그를 나머지에 그대로 넣어주면 된다.

    태그의 code 는 사이트가 tag_search 로 받아둔 검색코드다. 이미 저장된
    태그를 옮기는 것이므로 code 가 이미 있고, 검증을 다시 부를 필요가 없다
    (2026-09-05 LCP_LHA_B914589 8건 / B914609 4건으로 실측).

    rows 가 비어 있으면 그 LCP 의 L코드별 태그 현황만 읽어온다.
    """

    def __init__(self, lcp_code: str, rows: list, src_no: str = "",
                 tags: list = None, overwrite: bool = False,
                 folder_name: str = None):
        super().__init__(False, 0, use_http=True)
        self.lcp_code = lcp_code
        self.rows = rows or []
        self.src_no = str(src_no or "")
        self.tags = tags or []
        self.overwrite = overwrite
        self.folder_name = folder_name

    def run(self):
        try:
            from ..lohas import attr_detail, tabs

            client = get_client(self.headless, self.monitor, log=self._log)
            s = client.session

            # 현황 조회만
            if not self.rows:
                rows = db.lcode_rows_of(self.lcp_code)
                out = []
                for i, r in enumerate(rows, 1):
                    if self.should_stop():
                        break
                    try:
                        t = tabs.fetch_saved_tags(s, r["product_no"])
                    except Exception as e:
                        t = []
                        self._log(f"  ! {r['l_code']} {str(e)[:50]}")
                    out.append({**r, "tags": t})
                    self.progress.emit(i, len(rows))
                self.finished.emit({"rows": out})
                return

            tags = self.tags
            if not tags and self.src_no:
                tags = tabs.fetch_saved_tags(s, self.src_no)
            if not tags:
                self.failed.emit("복사할 태그가 없습니다.")
                return

            ok = fail = skip = 0
            saved = []
            for i, r in enumerate(self.rows, 1):
                if self.should_stop():
                    self._log("사용자 중단")
                    break
                if str(r["product_no"]) == self.src_no:
                    skip += 1
                    self.progress.emit(i, len(self.rows))
                    continue
                try:
                    cur = tabs.fetch_saved_tags(s, r["product_no"])
                    if cur and not self.overwrite:
                        skip += 1
                        self._log(f"  = {r['l_code']} 이미 {len(cur)}개 - 건너뜀")
                        self.progress.emit(i, len(self.rows))
                        continue
                    tabs.save_tags(s, r["product_no"], tags)
                    got = tabs.fetch_saved_tags(s, r["product_no"])
                    if len(got) == len(tags):
                        ok += 1
                        saved.append(r)
                    else:
                        fail += 1
                        self._log(f"  !! {r['l_code']} 저장 {len(got)}/{len(tags)}개")
                except Exception as e:
                    fail += 1
                    self._log(f"  !! {r['l_code']} {str(e)[:70]}")
                self.progress.emit(i, len(self.rows))

            if saved:
                out = []
                for r in saved:
                    try:
                        d = attr_detail.fetch_detail(s, r["product_no"])
                        d["lcp_code"] = self.lcp_code
                        d["l_code"] = r["l_code"]
                        out.append(d)
                    except Exception:
                        pass
                if out:
                    db.save_lcode_attr(self.folder_name or db.get_job_folder(),
                                       out)
            self.finished.emit({"ok": ok, "fail": fail, "skip": skip,
                                "tags": tags})
        except Exception as e:
            self.failed.emit(f"{e}\n{traceback.format_exc()[:600]}")

class TagAutoWorker(BaseWorker):
    """
    키워드 자동추가 — 두 가지를 한다.

      1) 데이터랩에서 그 카테고리(cid)의 인기키워드 + 조회수를 받아
         datalab_keyword 에 쌓는다 (키워드 풀)
      2) 로하스 **기본 태그 후보**에서 태그를 골라 L코드에 넣는다

    태그 출처는 데이터랩이 아니라 로하스 태그 탭 후보다. 그 표는 저장된
    카테고리를 기준으로 사이트가 만들어 준 것이라 상품과 맞다. 태그 후보가
    하나도 없을 때만 상품명 후보에서 1개를 가져온다 (tag_auto 참고).

    카테고리가 틀리면 후보도 틀리므로 카테고리를 먼저 바로잡아야 한다.
    2026-09-05 모형 CCTV 상품이 'CCTV' 로 잡혀 후보가 전부 진짜 CCTV 였다.
    """

    def __init__(self, lcps: list, overwrite: bool = False, top: int = 200,
                 folder_name: str = None):
        super().__init__(False, 0, use_http=True)
        self.lcps = lcps or []      # [{lcp_code, cat, product_name}, ...]
        self.overwrite = overwrite
        self.top = top
        self.folder_name = folder_name

    def run(self):
        try:
            from ..lohas import attr_detail, datalab, tag_auto

            client = get_client(self.headless, self.monitor, log=self._log)
            s = client.session
            n_ok = n_fail = n_skip = 0
            done_lcp = 0

            for i, g in enumerate(self.lcps, 1):
                if self.should_stop():
                    self._log("사용자 중단")
                    break
                cid = str(g.get("cat") or "")
                if not cid:
                    self._log(f"  - {g['lcp_code']} 카테고리 미저장 - 건너뜀")
                    n_skip += 1
                    self.progress.emit(i, len(self.lcps))
                    continue

                rows = db.lcode_rows_of(g["lcp_code"])
                target = [r for r in rows if r.get("etc_category")]
                if not target:
                    self.progress.emit(i, len(self.lcps))
                    continue
                try:
                    # (1) 키워드 풀 - 데이터랩 인기키워드 + 조회수
                    try:
                        ranks = datalab.category_keywords_with_views(
                            cid, top=self.top, log=self._log)
                        if ranks:
                            db.save_datalab_keywords(
                                cid, ranks, g.get("cat_name") or "", 30)
                    except Exception as e:
                        self._log(f"  ! 데이터랩 {cid}: {str(e)[:60]}")

                    # (2) 태그 - 로하스 기본 태그 후보에서 고른다
                    ap = tag_auto.apply_to_rows(
                        s, target, overwrite=self.overwrite,
                        log=self._log, should_stop=self.should_stop)
                    n_ok += ap["ok"]
                    n_fail += ap["fail"]
                    n_skip += ap["skip"]
                    if ap["ok"]:
                        done_lcp += 1
                    self._log(f"[{i}/{len(self.lcps)}] {g['lcp_code']} "
                              f"저장 {ap['ok']}건")

                    out = []
                    for r in ap["saved"]:
                        try:
                            d = attr_detail.fetch_detail(s, r["product_no"])
                            d["lcp_code"] = g["lcp_code"]
                            d["l_code"] = r["l_code"]
                            out.append(d)
                        except Exception:
                            pass
                    if out:
                        db.save_lcode_attr(
                            self.folder_name or db.get_job_folder(), out)
                except Exception as e:
                    n_fail += 1
                    self._log(f"  !! {g['lcp_code']} {str(e)[:80]}")
                self.progress.emit(i, len(self.lcps))

            self.finished.emit({"ok": n_ok, "fail": n_fail, "skip": n_skip,
                                "lcps": done_lcp})
        except Exception as e:
            self.failed.emit(f"{e}\n{traceback.format_exc()[:600]}")

class TagPlanWorker(BaseWorker):
    """
    태그 제안만 만든다. **저장하지 않는다.**

    화면에서 사람이 눈으로 보고 고친 뒤 저장 버튼을 누르게 하려고 계획과
    저장을 갈랐다. use_ai=True 면 타사 브랜드·안 맞는 기능을 AI 가 거른다.
    """

    def __init__(self, rows: list, overwrite: bool = False,
                 use_ai: bool = False):
        super().__init__(False, 0, use_http=True)
        self.rows = rows or []
        self.overwrite = overwrite
        self.use_ai = use_ai

    def run(self):
        try:
            from ..lohas import tag_auto

            client = get_client(self.headless, self.monitor, log=self._log)
            res = tag_auto.plan_rows(
                client.session, self.rows, overwrite=self.overwrite,
                use_ai=self.use_ai, log=self._log,
                should_stop=self.should_stop)
            self.finished.emit(res)
        except Exception as e:
            self.failed.emit(f"{e}\n{traceback.format_exc()[:600]}")


class TagSaveWorker(BaseWorker):
    """화면에서 확인·수정한 태그 제안을 저장한다."""

    def __init__(self, plan: list, lcp_code: str = "",
                 folder_name: str = None):
        super().__init__(False, 0, use_http=True)
        self.plan = plan or []
        self.lcp_code = lcp_code
        self.folder_name = folder_name

    def run(self):
        try:
            from ..lohas import attr_detail, tag_auto

            client = get_client(self.headless, self.monitor, log=self._log)
            s = client.session
            res = tag_auto.save_plan(s, self.plan, log=self._log,
                                     should_stop=self.should_stop,
                                     progress=lambda a, b: self.progress.emit(a, b))
            out = []
            for r in res["saved"]:
                try:
                    d = attr_detail.fetch_detail(s, r["product_no"])
                    d["lcp_code"] = self.lcp_code
                    d["l_code"] = r["l_code"]
                    out.append(d)
                except Exception:
                    pass
            if out:
                db.save_lcode_attr(self.folder_name or db.get_job_folder(), out)
            self.finished.emit(res)
        except Exception as e:
            self.failed.emit(f"{e}\n{traceback.format_exc()[:600]}")

class Fix10Worker(BaseWorker):
    """
    수정사항 1.0 일괄수정. **정방향·역방향을 동시에** 돌린다.

    사이트 마법사를 HTTP 로 그대로 재현하므로 브라우저가 뜨지 않는다.
    목록 앞뒤에서 서로 마주 보고 좁혀 오기 때문에 시간이 절반으로 준다.
    묶인 상품이 전부 품절인 LCP 는 시도하지 않고 건너뛴다.
    """

    def __init__(self, folder_name: str = None, both: bool = True,
                 limit: int = 0):
        super().__init__(False, 0, use_http=True)
        self.folder_name = folder_name
        self.both = both
        self.limit = limit

    def run(self):
        try:
            client = get_client(self.headless, self.monitor, log=self._log)
            folders = self.folder_name or db.get_job_folder()
            if isinstance(folders, str):
                folders = [folders]
            tot = {"total": 0, "ok": 0, "soldout": 0, "fail": 0, "seconds": 0}
            for f in [x for x in folders if x]:
                res = fix10.bulk(
                    lambda: fix10.clone_session(client.session), f,
                    both=self.both, limit=self.limit, log=self._log,
                    stop=self.should_stop,
                    progress=lambda d, t: self.progress.emit(d, t))
                for k in tot:
                    tot[k] += (res.get(k) or 0)
            self.finished.emit(tot)
        except Exception as e:
            self.failed.emit(str(e))


class SoldoutSweepWorker(BaseWorker):
    """
    수정사항이 있으면서 **품절**인 광고상품을 찾아 지정한 폴더로 넘긴다.

    화면에서 하던 것과 같다 — 임의분류 고르고 '수정사항유' + 상태 '품절' 로
    검색해 전체 선택한 뒤 [분류변경](2026-09-06 사용자 설명).
    """

    def __init__(self, folders: list, target: str):
        super().__init__(False, 0, use_http=True)
        self.folders = folders or []
        self.target = target

    def run(self):
        try:
            client = get_client(self.headless, self.monitor, log=self._log)
            found = moved = 0
            for f in self.folders:
                res = fix10.sweep_soldout(client.session, f, self.target,
                                          log=self._log)
                found += res["found"]
                moved += res["moved"]
            self.finished.emit({"found": found, "moved": moved,
                                "target": self.target})
        except Exception as e:
            self.failed.emit(str(e))


class StatusSyncWorker(BaseWorker):
    """
    상품정보 상태를 사이트 현재값으로 맞춘다.

    미작업목록은 점검 당시의 스냅샷이라, 그 사이에 끝낸 것이 계속 미작업으로
    남는다. 상태별로 한 번씩 검색해(각 1~2초) 실제 상태를 받아 DB 를 고친다.
    검색 4번이면 되므로 12칸 전체 점검보다 훨씬 가볍다.
    """

    STATUSES = [("save", "저장완료"), ("none", "미작업"),
                ("exclude", "제외"), ("hold", "보류")]

    def __init__(self, folder_name: str = None):
        super().__init__(False, 0, use_http=True)
        self.folder_name = folder_name

    def run(self):
        try:
            client = get_client(self.headless, self.monitor, log=self._log)
            folder = self.folder_name or db.get_job_folder()
            by_status = {}
            for i, (val, name) in enumerate(self.STATUSES, 1):
                if self.should_stop():
                    self._log("사용자 중단")
                    break
                res = client.search(folder, dest_list="allow", dest_attr=val)
                codes = {r[1] for r in res["rows"] if len(r) > 1}
                by_status[name] = codes
                self._log(f"  {name} {len(codes):,}행 ({res['elapsed']}초)")
                self.progress.emit(i, len(self.STATUSES))

            changed = db.sync_info_status(folder, by_status)
            total = sum(changed.values())
            if total:
                self._log("상태 갱신 — " + " / ".join(
                    f"{k} {v:,}건" for k, v in changed.items() if v))
            else:
                self._log("바뀐 것 없음 (이미 최신)")
            self.finished.emit({"changed": changed, "total": total,
                                "counts": {k: len(v) for k, v in by_status.items()}})
        except Exception as e:
            self.failed.emit(f"{e}\n{traceback.format_exc()[:600]}")


class AdSpendWorker(BaseWorker):
    """
    광고비 새로고침 — 켜 둔 계정·캠페인의 지출과 비즈머니 잔액.

    로하스가 아니라 **네이버 검색광고 API** 를 부른다. 쿠키도 브라우저도
    쓰지 않아 다른 작업과 겹쳐 돌아도 된다(2026-09-12).
    """

    def __init__(self, days: int = 1, base: bool = False):
        super().__init__(False, 0, use_http=True)
        self.days = days
        self.base = base          # 계정·캠페인 기본정보도 다시 받을지

    def run(self):
        try:
            from ..lohas import ad_account, ad_spend
            if self.base:
                self._log("계정·캠페인 기본정보를 받는 중…")
                ad_account.collect(log=self._log)
            else:
                # 잔액만 갱신한다 - 캠페인 목록은 그대로 둔다
                from ..lohas import searchad as sa
                for a in ad_account.accounts(only_on=True):
                    biz = sa.bizmoney(a["customer_id"])
                    with db.sqlite_conn() as c:
                        c.execute("UPDATE ad_account SET bizmoney=?,"
                                  " updated_at=? WHERE customer_id=?",
                                  (int(biz.get("bizmoney") or 0),
                                   db.now_str(), a["customer_id"]))
            res = ad_spend.collect_all(days=self.days, log=self._log)
            # **오늘 매출은 커머스 API 로.** 주문 DB 는 당일이 비어 있다
            # (2026-09-12 사용자).
            try:
                from ..lohas import commerce
                if commerce.available():
                    import datetime as _dt
                    r = commerce.sales(_dt.date.today(), log=self._log)
                    if r["orders"]:
                        commerce.save(r, log=self._log)
                    res["sales_today"] = r["amount"]
            except Exception as e:
                self._log(f"[커머스] {str(e)[:80]}")
            self.finished.emit(res)
        except Exception as e:
            self._log(traceback.format_exc())
            self.failed.emit(str(e))


class OrderWatchWorker(BaseWorker):
    """
    새 주문 감시 — 커머스 API 로 오늘 주문을 훑어 **늘어난 것만** 알린다.

    광고 상세는 어제까지지만 **주문은 실시간**이라 바로 잡힌다
    (2026-09-12 사용자). 알림은 화면이 띄우고, 여기서는 숫자만 넘긴다.
    """

    def __init__(self, known: set = None):
        super().__init__(False, 0, use_http=True)
        self.known = set(known or ())

    def run(self):
        try:
            import datetime as _dt
            from ..lohas import ad_sales, commerce
            if not commerce.available():
                self.finished.emit({"skip": True})
                return
            day = _dt.date.today()
            tok = commerce.token()
            start = _dt.datetime.combine(day, _dt.time.min)
            ids = commerce.changed_ids(start, _dt.datetime.now(), tok)
            new_ids = [i for i in ids if i not in self.known]
            fresh = []
            if new_ids:
                cmap = ad_sales._code_map()
                for r in commerce.details(new_ids, tok):
                    po = r.get("productOrder") or {}
                    o = r.get("order") or {}
                    if (po.get("productOrderStatus") or "") in commerce.DEAD:
                        continue
                    if ((o.get("orderDate") or "")[:10]) != str(day):
                        continue          # 옛 주문의 상태 변경일 뿐이다
                    code = str(po.get("productId") or "")
                    fresh.append({
                        "id": po.get("productOrderId"),
                        "name": po.get("productName") or "",
                        "qty": int(po.get("quantity") or 0),
                        "amount": int(po.get("totalPaymentAmount") or 0),
                        "lcp": (cmap.get(code) or ("", ""))[0],
                        "ad": code in cmap,
                    })
            self.finished.emit({"ids": ids, "new": fresh, "day": str(day)})
        except Exception as e:
            self.failed.emit(str(e))
