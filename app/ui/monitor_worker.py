"""
작업폴더 1개를 주기적으로 점검해 현황을 갱신하는 백그라운드 워커.

- 대상은 항상 작업폴더 하나뿐이다 (여러 폴더를 돌지 않는다)
- 매 주기마다 12칸 전체점검(HTTP, 약 2~5초)을 돌려 아래 수치를 채운다
      전체 상품수 / 이미지승인완료 / 이미지승인중
      상품정보 완료 / 미완료
      미완료중 아직 상품분석을 안 한 LCP 수량
- 미분석 LCP 는 대기열(analysis_queue)에 저장해두고,
  ALL 상품분석이 검색 없이 그 목록으로 곧바로 처리할 수 있게 한다
"""
import time
import traceback

from PySide6.QtCore import QObject, Signal

from .. import db
from ..lohas import constants as C
from ..lohas import ss_image
from ..lohas.session import get_client


class MonitorWorker(QObject):
    log = Signal(str)
    tick = Signal(dict)        # 주기마다 현황 전달
    failed = Signal(str)
    finished = Signal(dict)

    def __init__(self, folder_name: str, interval: int = 30,
                 headless: bool = False, monitor: int = 0):
        super().__init__()
        self.folder_name = folder_name
        self.interval = max(int(interval), 5)
        self.headless = headless
        self.monitor = monitor
        self._stop = False
        self._cycles = 0
        self._prev = None
        self._last_rate_log = 0.0
        self.rate_log_every = 300      # 속도 로그 기록 간격(초)

    def stop(self):
        self._stop = True

    def should_stop(self) -> bool:
        return self._stop

    def _log(self, msg: str):
        self.log.emit(str(msg))

    # ------------------------------------------------------------------

    # 직전 관측의 이 비율 아래로 떨어지면 정상 결과로 보지 않는다.
    PARTIAL_RATIO = 0.5

    def _is_partial(self, snap) -> bool:
        """검색이 덜 돌아온 결과인지. 첫 점검은 비교 대상이 없어 통과시킨다."""
        prev_total = (self._prev or {}).get("total_rows") or 0
        if prev_total <= 0:
            return False
        return (snap.get("total_rows") or 0) < prev_total * self.PARTIAL_RATIO

    def run(self):
        try:
            client = get_client(self.headless, self.monitor, log=self._log)
            self._log(f"[모니터] '{self.folder_name}' {self.interval}초 주기 점검 시작")

            # 껐다 켠 사이의 작업량이 누락되지 않도록 마지막 기록을 기준점으로
            prev_row = db.last_work_log(self.folder_name)
            if prev_row:
                self._prev = {
                    "total_rows": prev_row["total_rows"] or 0,
                    "img_done_rows": prev_row["img_done_rows"] or 0,
                    "info_save_rows": prev_row["info_save_rows"] or 0,
                    "info_todo_rows": prev_row["info_todo_rows"] or 0,
                    "analyzed_lcps": prev_row["analyzed_lcps"] or 0,
                    "pending_lcps": prev_row["pending_lcps"] or 0,
                }
                self._log(f"[모니터] 직전 기록({prev_row['ts']})을 기준으로 이어서 집계")
            last_key = None

            while not self._stop:
                started = time.time()
                try:
                    snap = self._one_cycle(client)
                except Exception as e:
                    self._log(f"[모니터] 점검 실패: {e}")
                    snap = None

                if snap and self._is_partial(snap):
                    # 세션이 덜 준비된 상태에서 검색하면 결과가 몇 줄만 온다.
                    # 그대로 기록하면 '전량 사라졌다 -> 다시 생겼다' 가 되어
                    # 일별 작업량이 총 수량만큼 부풀려진다(실제로 겪었다).
                    self._log(
                        f"[모니터] 부분응답 무시 : {snap['total_rows']:,}행 "
                        f"(직전 {self._prev.get('total_rows', 0):,}행)")
                    snap = None

                if snap:
                    self._cycles += 1
                    snap["cycle"] = self._cycles

                    # ---- 직전 대비 증감 계산 ----
                    prev = self._prev
                    def d(key):
                        return 0 if prev is None else snap[key] - prev[key]
                    snap["d_img_done"] = d("img_done_rows")
                    snap["d_info_save"] = d("info_save_rows")
                    snap["d_info_todo"] = d("info_todo_rows")
                    snap["d_analyzed"] = d("analyzed_lcps")
                    snap["d_pending"] = d("pending_lcps")
                    snap["first"] = prev is None
                    self._prev = snap

                    # ---- 변동 로그는 매 주기 기록 (그래프/시간당 처리량용) ----
                    db.save_work_log({
                        "ts": snap["scanned_at"],
                        "folder_name": snap["folder_name"],
                        "total_rows": snap["total_rows"],
                        "total_lcps": snap["total_lcps"],
                        "img_done_rows": snap["img_done_rows"],
                        "img_work_rows": snap["img_work_rows"],
                        "info_save_rows": snap["info_save_rows"],
                        "info_todo_rows": snap["info_todo_rows"],
                        "target_lcps": snap["target_lcps"],
                        "analyzed_lcps": snap["analyzed_lcps"],
                        "pending_lcps": snap["pending_lcps"],
                        "d_img_done": snap["d_img_done"],
                        "d_info_save": snap["d_info_save"],
                        "d_info_todo": snap["d_info_todo"],
                        "d_analyzed": snap["d_analyzed"],
                        "d_pending": snap["d_pending"],
                        "elapsed_sec": snap["elapsed_sec"],
                    })

                    # ---- 처리속도 계산 (30분 / 1시간 / 10개당) ----
                    r30 = db.rate_stats(self.folder_name, 30)
                    r60 = db.rate_stats(self.folder_name, 60)
                    snap["r30"] = r30
                    snap["r60"] = r60
                    snap["per10_info"] = db.per10_minutes(r60["info"], r60["span_min"])
                    snap["per10_img"] = db.per10_minutes(r60["img"], r60["span_min"])
                    snap["per10_analyzed"] = db.per10_minutes(
                        r60["analyzed"], r60["span_min"])

                    # 남은 미완료를 다 끝내는 데 걸릴 예상 시간
                    # 표본이 너무 적으면 예상치를 내지 않는다
                    eta = None
                    if r60["info"] > 0 and r60["span_min"] >= 3:
                        rate = r60["info"] / r60["span_min"]          # 건/분
                        eta = round(snap["info_todo_rows"] / rate, 1) if rate else None
                    snap["eta_min"] = eta

                    # ---- 속도 로그는 일정 간격으로만 남긴다 ----
                    now = time.time()
                    if now - self._last_rate_log >= self.rate_log_every:
                        db.save_rate_log({
                            "ts": snap["scanned_at"],
                            "folder_name": self.folder_name,
                            "m30_info": r30["info"], "m30_img": r30["img"],
                            "m30_analyzed": r30["analyzed"],
                            "h1_info": r60["info"], "h1_img": r60["img"],
                            "h1_analyzed": r60["analyzed"],
                            "per10_info": snap["per10_info"],
                            "per10_img": snap["per10_img"],
                            "per10_analyzed": snap["per10_analyzed"],
                            "pending_lcps": snap["pending_lcps"],
                            "eta_min": eta,
                        })
                        self._last_rate_log = now
                        p10 = ("-" if snap["per10_info"] is None
                               else f"{snap['per10_info']}분")
                        self._log(f"[속도] 30분 {r30['info']}개 / 1시간 "
                                  f"{r60['info']}개 / 10개당 {p10}")

                    self.tick.emit(snap)

                    # 수치가 바뀐 경우에만 점검 이력으로 저장
                    key = (snap["total_rows"], snap["img_done_rows"],
                           snap["img_work_rows"], snap["info_save_rows"],
                           snap["info_todo_rows"], snap["pending_lcps"])
                    if key != last_key:
                        db.save_scan(snap["_summary"], snap["_cells"], snap["_items"])
                        last_key = key
                        if prev is not None and any(
                                snap[k] for k in ("d_img_done", "d_info_save",
                                                  "d_analyzed")):
                            self._log(
                                f"[모니터] 변동 : 이미지승인 {snap['d_img_done']:+} / "
                                f"상품정보완료 {snap['d_info_save']:+} / "
                                f"분석 {snap['d_analyzed']:+}")

                # 남은 시간만큼 잘게 나눠 대기 (중단 반응성 확보)
                wait = self.interval - (time.time() - started)
                while wait > 0 and not self._stop:
                    time.sleep(min(0.5, wait))
                    wait -= 0.5

            self._log(f"[모니터] 중지 (총 {self._cycles}회 점검)")
            self.finished.emit({"cycles": self._cycles})
        except Exception as e:
            self._log(traceback.format_exc())
            self.failed.emit(str(e))

    # ------------------------------------------------------------------

    def _one_cycle(self, client) -> dict:
        """1회 점검 → 표시용 현황 dict"""
        res = ss_image.inspect_folder_http(
            client, self.folder_name,
            log=lambda *a, **k: None,       # 주기 실행이라 상세로그는 끈다
            should_stop=self.should_stop, quick=False,
        )
        s = res["summary"]

        # ---- 미완료(상품정보 미작업) 중 아직 상품분석 안 한 LCP ----
        done_lcps = db.done_lcp_set()
        full = client.search_full(self.folder_name,
                                  C.TARGET_IMAGE_VALUE, C.TARGET_INFO_VALUE)
        by_lcp = {}
        for lcp, lcode, no in full["rows"]:
            if lcp and lcp not in by_lcp:
                by_lcp[lcp] = {"lcp_code": lcp, "l_code": lcode, "product_no": no}

        pending = [v for k, v in by_lcp.items() if k not in done_lcps]
        db.save_queue(self.folder_name, pending)

        return {
            "folder_name": self.folder_name,
            "scanned_at": s["scanned_at"],
            "elapsed_sec": s["elapsed_sec"],
            "total_rows": s["total_rows"],
            "total_lcps": s["total_lcps"],
            "img_done_rows": s["img_done_rows"],      # 이미지승인완료
            "img_work_rows": s["img_work_rows"],      # 이미지승인중(승인 전)
            "img_todo_rows": s["img_todo_rows"],
            "info_save_rows": s["info_save_rows"],    # 상품정보 완료
            "info_todo_rows": s["info_todo_rows"],    # 상품정보 미완료
            "target_rows": s["target_rows"],
            "target_lcps": s["target_lcps"],
            "analyzed_lcps": len(by_lcp) - len(pending),
            "pending_lcps": len(pending),             # 미분석 LCP 수량
            "_summary": s, "_cells": res["cells"], "_items": res["items"],
        }
