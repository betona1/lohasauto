"""
샘플 1건 분석 (미리보기).

작업폴더의 대상 상품 하나를 골라, 실제 저장은 하지 않고
'무엇을 어떻게 고를지'만 계산해서 보여준다. 안전하게 로직을 검증하는 단계.

읽기는 전부 HTTP 라 브라우저가 뜨지 않는다.
  1) 목록 검색 (이미지승인완료 + 상품정보 미작업)
  2) attr 팝업 -> 원상품명 / 이미지 / 분석완료 / 카테고리 저장여부
  3) 셸 -> 탭별 저장 여부(savedTabsStatus)
  4) 태그 탭 / 상품명1 탭 -> 후보 표
  5) 금지어·숫자·색상·갯수 필터 -> Gemini(있으면) 또는 규칙으로 선택
  6) 단계별로 task_log 에 기록 (로컬 DB + 로컬파일 + 서버 DB)
"""
import time

from .. import config, db
from . import constants as C
from . import gemini, keywords, tabs


def _log_step(base: dict, step: str, **kw):
    rec = dict(base)
    rec["step"] = step
    rec.update(kw)
    try:
        db.save_task_log(rec)
    except Exception:
        pass


def pick_target(client, folder_name: str, lcp_code: str = None,
                skip_done: bool = True, log=print) -> dict:
    """
    작업 대상 1건 선택. lcp_code 를 주면 그 상품, 아니면 첫 대상.
    반환: {lcp_code, l_code, product_no} 또는 None
    """
    res = client.search_full(folder_name, C.TARGET_IMAGE_VALUE, C.TARGET_INFO_VALUE)
    rows = res["rows"]
    if not rows:
        log("[샘플] 대상 상품이 없습니다.")
        return None

    by_lcp = {}
    for lcp, lcode, no in rows:
        if lcp and lcp not in by_lcp:
            by_lcp[lcp] = {"lcp_code": lcp, "l_code": lcode, "product_no": no}

    if lcp_code:
        t = by_lcp.get(lcp_code)
        if not t:
            log(f"[샘플] '{lcp_code}' 를 대상 목록에서 찾지 못했습니다.")
        return t

    order = list(by_lcp.values())
    log(f"[샘플] 대상 {len(order)}종 중 첫 상품 선택 ({res['elapsed']}초)")
    return order[0] if order else None


def preview_one(client, folder_name: str, target: dict, log=print) -> dict:
    """상품 1건을 읽고 선택 결과를 계산 (저장 안 함)."""
    started = time.time()
    no = target["product_no"]
    base = {"folder_name": folder_name, "lcp_code": target["lcp_code"],
            "l_code": target.get("l_code"), "product_no": no,
            "action": "pick", "status": "preview"}

    out = {"target": target, "steps": [], "tag": None, "title1": None}

    # ---------- 1) attr ----------
    attr = tabs.fetch_attr(client.session, no)
    out["attr"] = attr
    log(f"[샘플] {target['lcp_code']}  원상품명='{(attr['product_name'] or '')[:40]}'")
    log(f"       분석완료={attr['already_done']}({attr['analysis_date'] or '-'})"
        f"  카테고리저장={attr['category_saved']}  이미지 {len(attr['images'])}장")
    _log_step(base, "attr", action="read", status="ok",
              message=f"분석={attr['already_done']} 카테고리={attr['category_saved']} "
                      f"이미지={len(attr['images'])}")

    # 사이트가 강제하는 순서 : 상품분석 -> 카테고리 -> 상품명/태그
    blocked = []
    if not attr["already_done"]:
        blocked.append("상품분석 미완료")
    if not attr["category_saved"]:
        blocked.append("카테고리 미저장")
    out["blocked"] = blocked
    if blocked:
        log(f"       ⚠ 선행 필요: {' / '.join(blocked)}")

    # ---------- 2) 탭 저장 상태 ----------
    saved = tabs.fetch_saved_tabs(client.session, no)
    out["saved_tabs"] = saved
    log(f"       탭 저장상태: {saved}")

    # ---------- 3) 이미지 파트 (Gemini 용, 상품당 1회) ----------
    img_parts = []
    if gemini.available() and attr["images"]:
        img_parts = gemini.build_image_parts(attr["images"], log=log)

    pname = attr.get("product_name") or ""

    # ---------- 4) 태그 ----------
    if saved.get("tag"):
        log("       [태그] 이미 저장됨 - 스킵")
        _log_step(base, "tag", action="skip", status="skip", message="이미 저장됨")
    else:
        rows = tabs.fetch_tag_rows(client.session, no)
        cands = keywords.filter_candidates(rows, tag_only=True)
        picked = gemini.pick(img_parts, pname, [c["name"] for c in cands],
                             config.MAX_TAGS, log=log) if cands else []
        source = "gemini" if picked else "rule"
        if picked:
            picked = keywords.order_by_rank(cands, picked, config.MAX_TAGS)
        else:
            picked = keywords.rule_pick(cands, config.MAX_TAGS)
        out["tag"] = {"rows": len(rows), "cands": len(cands),
                      "picked": picked, "source": source}
        log(f"       [태그] {len(rows)}행 → 후보 {len(cands)} → "
            f"{len(picked)}개 선택 ({source})")
        log(f"              {picked}")
        _log_step(base, "tag", picked=picked, candidates=len(cands),
                  source=source, message=f"표 {len(rows)}행")

    # ---------- 5) 상품명1 ----------
    if saved.get("product"):
        log("       [상품명1] 이미 저장됨 - 스킵")
        _log_step(base, "title1", action="skip", status="skip", message="이미 저장됨")
    else:
        rows = tabs.fetch_title_rows(client.session, no, 1)
        cands = keywords.filter_candidates(rows, tag_only=False)
        picked = gemini.pick(img_parts, pname, [c["name"] for c in cands],
                             config.MAX_TITLE_KW, log=log) if cands else []
        source = "gemini" if picked else "rule"
        if picked:
            picked = keywords.order_by_rank(cands, picked, config.MAX_TITLE_KW)
        else:
            picked = keywords.rule_pick(cands, config.MAX_TITLE_KW)
        out["title1"] = {"rows": len(rows), "cands": len(cands),
                         "picked": picked, "source": source}
        log(f"       [상품명1] {len(rows)}행 → 후보 {len(cands)} → "
            f"{len(picked)}개 선택 ({source})")
        log(f"              {picked}")
        _log_step(base, "title1", picked=picked, candidates=len(cands),
                  source=source, message=f"표 {len(rows)}행")

    out["elapsed_sec"] = round(time.time() - started, 1)
    out["gemini"] = dict(gemini.stats)
    log(f"[샘플] 완료 ({out['elapsed_sec']}초)  "
        f"Gemini 호출 {gemini.stats['call']} / 성공 {gemini.stats['ok']} / "
        f"429 {gemini.stats['http429']}")
    return out


def run_sample(client, folder_name: str, lcp_code: str = None, log=print) -> dict:
    """대상 1건 골라서 미리보기까지."""
    target = pick_target(client, folder_name, lcp_code, log=log)
    if not target:
        return {"error": "대상 없음"}
    return preview_one(client, folder_name, target, log=log)
