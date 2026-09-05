"""
Gemini 기반 키워드 선택.

04_로하스의 로직을 이식하면서 429(할당량 초과) 문제를 구조적으로 손봤다.
원본은 429 실패가 2,030회로 전체 실패의 88% 였고, 백오프는 사후 대응이라
회복해도 곧바로 다시 한도를 쳤다.

바꾼 것
  1) 호출 간격 강제 (GEMINI_MIN_INTERVAL) - 한도를 치기 전에 막는다
  2) 429 응답의 retryDelay 를 읽어 그만큼만 기다린다 (고정 15/30/45초 대신)
  3) 검수(verify) 호출에 이미지를 붙이지 않는다 (GEMINI_VERIFY_IMAGES=0)
     - 검수가 선택보다 호출이 많은데 이미지가 토큰의 대부분이라 절감폭이 크다
  4) 연속 429 가 쌓이면 일정 시간 Gemini 를 끄고 규칙 기반으로 넘어간다
     (매번 90초씩 까먹으며 멈추는 것보다 낫다)
"""
import base64
import json
import re
import threading
import time
import urllib.error
import urllib.request

from .. import config

API_URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
           "{model}:generateContent?key={key}")

_lock = threading.Lock()
_last_call = 0.0          # 마지막 호출 시각 (간격 강제용)
_cooldown_until = 0.0     # 연속 429 시 쉬는 시각
_no_thinking = False      # thinkingConfig 를 거부하는 모델이면 True
_fail_streak = 0

# 통계 (UI 표시용)
stats = {"call": 0, "ok": 0, "http429": 0, "fail": 0, "skip_cooldown": 0,
         "in_tokens_est": 0}


def available() -> bool:
    return bool(config.GEMINI_API_KEY)


def reset_stats():
    for k in stats:
        stats[k] = 0


# ------------------------------------------------------------------ 이미지

def build_image_parts(urls, limit=None, log=print) -> list:
    """이미지 URL -> inline_data 파트. 상품당 한 번만 만들어 재사용한다."""
    if isinstance(urls, str):
        urls = [urls]
    limit = config.GEMINI_MAX_IMAGES if limit is None else limit
    parts = []
    for u in [u for u in (urls or []) if u][:limit]:
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
            raw = urllib.request.urlopen(req, timeout=15).read()
            if len(raw) > 5_000_000:
                continue
            ul = u.lower()
            mime = ("image/png" if ul.endswith(".png")
                    else "image/webp" if ul.endswith(".webp")
                    else "image/gif" if ul.endswith(".gif")
                    else "image/jpeg")
            parts.append({"inline_data": {"mime_type": mime,
                                          "data": base64.b64encode(raw).decode()}})
        except Exception as e:
            log(f"    [Gemini] 이미지 실패({str(e)[:40]})")
    return parts


# ------------------------------------------------------------------ 호출

def _respect_interval():
    """마지막 호출로부터 최소 간격을 지킨다 (429 예방의 핵심)."""
    global _last_call
    with _lock:
        gap = time.time() - _last_call
        wait = config.GEMINI_MIN_INTERVAL - gap
        if wait > 0:
            time.sleep(wait)
        _last_call = time.time()


def _retry_delay(err_body: str, default: float) -> float:
    """429 응답 본문의 retryDelay(예: '31s')를 읽는다."""
    m = re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', err_body or "")
    if m:
        try:
            return min(float(m.group(1)) + 1, 120.0)
        except ValueError:
            pass
    return default


def _post(body: dict, timeout: int = 60, log=print):
    """1회 호출 + 429 재시도. 성공 시 응답 dict, 실패 시 None."""
    global _cooldown_until, _fail_streak, _no_thinking

    if time.time() < _cooldown_until:
        stats["skip_cooldown"] += 1
        return None

    # thinkingConfig 는 모델마다 받는 것과 아닌 것이 있다. lite 계열은 400 을
    # 돌려준다("Request contains an invalid argument"). 한 번 거부당하면
    # 그 뒤로는 빼고 보낸다 - 안 그러면 전건이 조용히 실패한다(실측 54/54).
    if _no_thinking:
        body = _strip_thinking(body)
    url = API_URL.format(model=config.GEMINI_MODEL, key=config.GEMINI_API_KEY)
    data = json.dumps(body).encode("utf-8")
    stats["in_tokens_est"] += _estimate_tokens(body)

    for attempt in range(config.GEMINI_RETRIES + 1):
        _respect_interval()
        stats["call"] += 1
        try:
            req = urllib.request.Request(
                url, data=data, headers={"Content-Type": "application/json"})
            resp = json.load(urllib.request.urlopen(req, timeout=timeout))
            stats["ok"] += 1
            _fail_streak = 0
            return resp
        except urllib.error.HTTPError as e:
            body_txt = ""
            try:
                body_txt = e.read().decode("utf-8", "replace")
            except Exception:
                pass
            if e.code == 429:
                stats["http429"] += 1
                _fail_streak += 1
                if attempt < config.GEMINI_RETRIES:
                    wait = _retry_delay(body_txt, 15.0 * (attempt + 1))
                    log(f"    [Gemini] 429 - {wait:.0f}초 후 재시도 "
                        f"{attempt + 1}/{config.GEMINI_RETRIES}")
                    time.sleep(wait)
                    continue
                # 연속으로 계속 막히면 잠시 끈다
                if _fail_streak >= 5:
                    _cooldown_until = time.time() + 300
                    log("    [Gemini] 429 연속 - 5분간 규칙 기반으로 전환")
            if e.code == 400 and not _no_thinking and _has_thinking(body):
                _no_thinking = True
                log("    [Gemini] 이 모델은 thinkingConfig 를 받지 않습니다 "
                    "- 빼고 재시도")
                body = _strip_thinking(body)
                data = json.dumps(body).encode("utf-8")
                continue
            stats["fail"] += 1
            log(f"    [Gemini] HTTP {e.code} - 규칙 기반 대체")
            return None
        except Exception as e:
            stats["fail"] += 1
            log(f"    [Gemini] 호출 실패({str(e)[:50]}) - 규칙 기반 대체")
            return None
    return None


def _has_thinking(body: dict) -> bool:
    return "thinkingConfig" in (body.get("generationConfig") or {})


def _strip_thinking(body: dict) -> dict:
    gc = dict(body.get("generationConfig") or {})
    gc.pop("thinkingConfig", None)
    return {**body, "generationConfig": gc}


def _estimate_tokens(body: dict) -> int:
    """대략적인 입력 토큰 추정 (이미지 1장 ≈ 800, 한글 1자 ≈ 1)."""
    n = 0
    for part in body.get("contents", [{}])[0].get("parts", []):
        if "inline_data" in part:
            n += 800
        else:
            n += len(part.get("text", "")) // 2
    return n


# ------------------------------------------------------------------ 선택

def _prompt_pick(product_name: str, candidates: list, n: int) -> str:
    return (
        "너는 쇼핑몰 상품 태그/상품명 전문가야.\n"
        f"[원상품명] {product_name or '(없음)'}\n"
        f"[후보 키워드] {', '.join(candidates)}\n\n"
        "첨부한 상품 이미지(속 텍스트·제품설명 포함)와 원상품명을 보고, "
        "이 상품에 어울리는 키워드를 후보 중에서 골라줘.\n"
        "- 조금이라도 상품과 관련되면 모두 포함해. 관련된 걸 빠뜨리지 마.\n"
        f"- 최대 {n}개까지. 관련된 게 {n}개보다 많으면 가장 관련성 높은 {n}개.\n"
        "- 색상/숫자/갯수/브랜드/캐릭터명이 들어간 키워드는 제외.\n"
        "반드시 후보 목록 안에서만 고르고, 고른 키워드만 콤마로 구분해 한 줄로. 설명 금지."
    )


def _prompt_verify(product_name: str, candidates: list) -> str:
    return (
        "너는 쇼핑몰 상품 키워드 검수자야.\n"
        f"[원상품명] {product_name or '(없음)'}\n"
        f"[선택된 키워드] {', '.join(candidates)}\n\n"
        "원상품명을 보고 선택된 키워드 중 이 상품과 '명백히 관련 없는' 것만 빼줘. "
        "조금이라도 관련되면 반드시 유지해.\n"
        "남긴 키워드만 콤마로 구분해 한 줄로. 설명 금지."
    )


def _match_back(text: str, candidates: list, cap: int) -> list:
    """모델이 뱉은 단어를 후보 목록에 되맞춘다 (정확일치 우선, 긴 후보 우선)."""
    picked = [w.strip() for w in (text or "").replace("\n", ",").split(",") if w.strip()]
    result, used = [], set()
    for pw in picked:
        match = next((c for c in candidates if c not in used and c == pw), None)
        if match is None:
            for c in sorted([c for c in candidates if c not in used],
                            key=len, reverse=True):
                if pw and (pw in c or c in pw):
                    match = c
                    break
        if match:
            result.append(match)
            used.add(match)
        if len(result) >= cap:
            break
    return result[:cap]


def pick(image_parts, product_name: str, candidates: list, n: int,
         verify: bool = None, log=print) -> list:
    """
    후보 중 어울리는 키워드를 고른다. 키가 없거나 실패하면 [] (호출측에서 규칙 기반).

    verify 는 2차 검수 호출. 기본은 .env 의 GEMINI_VERIFY.
    검수에는 이미지를 붙이지 않아 토큰을 크게 아낀다.
    """
    if not available() or not candidates:
        return []

    parts = list(image_parts or []) + [{"text": _prompt_pick(product_name, candidates, n)}]
    body = {"contents": [{"parts": parts}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1024,
                                 "thinkingConfig": {"thinkingBudget": 0}}}
    resp = _post(body, log=log)
    if not resp:
        return []

    cand = (resp.get("candidates") or [{}])[0]
    text = ""
    for pt in (cand.get("content") or {}).get("parts") or []:
        if isinstance(pt, dict) and pt.get("text"):
            text = pt["text"]
            break
    if not text:
        log(f"    [Gemini] 응답 없음(finish={cand.get('finishReason')}) - 규칙 대체")
        return []

    picked = _match_back(text, candidates, n)
    log(f"    [Gemini] 선택 {len(picked)}개: {picked}")
    if not picked:
        return []

    use_verify = config.GEMINI_VERIFY if verify is None else verify
    if not use_verify:
        return picked

    vparts = []
    if config.GEMINI_VERIFY_IMAGES:
        vparts = list(image_parts or [])
    vparts.append({"text": _prompt_verify(product_name, picked)})
    vbody = {"contents": [{"parts": vparts}],
             "generationConfig": {"temperature": 0.2, "maxOutputTokens": 512,
                                  "thinkingConfig": {"thinkingBudget": 0}}}
    vresp = _post(vbody, log=log)
    if not vresp:
        return picked

    vcand = (vresp.get("candidates") or [{}])[0]
    vtext = ""
    for pt in (vcand.get("content") or {}).get("parts") or []:
        if isinstance(pt, dict) and pt.get("text"):
            vtext = pt["text"]
            break
    kept = _match_back(vtext, picked, len(picked)) if vtext else []
    if kept:
        if len(kept) != len(picked):
            log(f"    [Gemini] 검수 {len(picked)} → {len(kept)}개: {kept}")
        return kept
    return picked


# ------------------------------------------------------------ 카테고리 선택

def _prompt_category(product_name: str, wish: str, markets: str,
                     candidates: list) -> str:
    lines = [f"{i + 1}. [{c['code']}] {c['name']}  (매칭 {c.get('cnt', 0)}개)"
             for i, c in enumerate(candidates)]
    return (
        "너는 네이버 스마트스토어 카테고리 분류 전문가야.\n"
        f"[상품명] {product_name or '(없음)'}\n"
        f"[희망검색어] {wish or '(없음)'}\n"
        f"[타 마켓 분류] {markets or '(없음)'}\n\n"
        "[후보 카테고리]\n" + "\n".join(lines) + "\n\n"
        "이 상품이 실제로 들어가야 할 카테고리를 후보 중에서 하나만 골라줘.\n"
        "- '매칭 개수'는 이름이 비슷한 다른 상품 수일 뿐이다. 개수가 많다고 "
        "정답이 아니다. 상품 자체가 무엇인지로 판단해.\n"
        "- 타 마켓 분류가 있으면 강한 힌트다.\n"
        "반드시 후보의 대괄호 안 코드만 한 줄로 출력해. 설명 금지."
    )


def pick_category(product_name: str, candidates: list, wish: str = "",
                  markets: str = "", log=print) -> dict:
    """
    후보 카테고리 중 하나를 AI 가 고른다.
    반환: {'code', 'name', 'source': 'ai'} / 실패하면 {} (호출측이 규칙으로 대체)

    '건수 1위' 규칙이 접전 구간에서 74% 밖에 안 되는 것을 실측했기 때문에,
    거기서만 쓰라고 만든 것이다. 이미지 없이 텍스트만 보낸다(토큰 절약).
    """
    if not available() or not candidates:
        return {}

    body = {"contents": [{"parts": [{"text": _prompt_category(
        product_name, wish, markets, candidates)}]}],
        # 코드만 받으면 되지만 여유를 준다. 64 로 두니 '50' 처럼 잘려 나왔다.
        "generationConfig": {"temperature": 0.0, "maxOutputTokens": 256,
                             "thinkingConfig": {"thinkingBudget": 0}}}
    resp = _post(body, log=log)
    if not resp:
        return {}

    cand = (resp.get("candidates") or [{}])[0]
    text = ""
    for pt in (cand.get("content") or {}).get("parts") or []:
        if isinstance(pt, dict) and pt.get("text"):
            text = pt["text"]
            break
    if not text:
        return {}

    codes = {str(c["code"]): c for c in candidates}
    import re as _re
    for tok in _re.findall(r"\d{4,}", text):
        if tok in codes:
            c = codes[tok]
            return {"code": tok, "name": c["name"], "source": "ai"}
    log(f"    [Gemini] 후보에 없는 답: {text.strip()[:60]}")
    return {}

def _prompt_tags(product_name, brand, maker, cands):
    lines = [f"{i + 1}. {c}" for i, c in enumerate(cands)]
    return (
        "쇼핑몰 상품에 붙일 검색 태그를 검수한다."
        + chr(10) + chr(10)
        + f"상품명: {product_name}" + chr(10)
        + f"브랜드: {brand or '-'} / 제조사: {maker or '-'}"
        + chr(10) + chr(10)
        + "아래 태그 후보 중 **이 상품에 붙이면 안 되는 것**의 번호만 고른다."
        + chr(10)
        + "빼야 하는 경우는 두 가지다." + chr(10)
        + " (1) 다른 회사의 브랜드·상표가 들어간 것."
        + " 단 그 이름이 위 상품명·브랜드·제조사에 있으면 빼지 않는다." + chr(10)
        + " (2) 이 상품에 없는 기능·형태·용도·규격을 말하는 것."
        + chr(10) + chr(10)
        + chr(10).join(lines)
        + chr(10) + chr(10)
        + "빼야 할 번호만 쉼표로 answer 에 적는다. 뺄 것이 없으면 none."
        + chr(10)
        + '형식: {"answer": "3,7,12"}'
    )


def filter_tags(product_name: str, cands: list, brand: str = "",
                maker: str = "", log=print) -> dict:
    """
    태그 후보 중 이 상품에 맞지 않는 것을 AI 가 골라낸다.

    규칙 기반으로는 안 되는 부분만 맡긴다 — 타사 브랜드와 안 맞는 기능이다.
    `lcp_product.brand/maker` 사전으로는 '하츠'(사전엔 '바이하츠' 만 있음)를
    못 잡고 '국산' 같은 일반어는 오탐이 났다 (2026-09-05 실측).

    반환: {'drop': [키워드], 'ok': bool}. 실패하면 ok=False 로 돌려주고
    호출 쪽은 후보를 그대로 쓴다 (빈 목록을 돌려 태그 0개가 되면 안 된다).
    """
    if not available() or not cands:
        return {"drop": [], "ok": False}

    body = {"contents": [{"parts": [{"text": _prompt_tags(
        product_name, brand, maker, cands)}]}],
        "generationConfig": {"temperature": 0.0, "maxOutputTokens": 256,
                             "thinkingConfig": {"thinkingBudget": 0}}}
    resp = _post(body, log=log)
    if not resp:
        return {"drop": [], "ok": False}

    cand = (resp.get("candidates") or [{}])[0]
    text = ""
    for pt in (cand.get("content") or {}).get("parts") or []:
        if isinstance(pt, dict) and pt.get("text"):
            text = pt["text"]
            break
    if not text:
        return {"drop": [], "ok": False}
    if "none" in text.lower():
        return {"drop": [], "ok": True}

    import re as _re
    drop = []
    for tok in _re.findall(r"[0-9]+", text):
        i = int(tok) - 1
        if 0 <= i < len(cands) and cands[i] not in drop:
            drop.append(cands[i])
    # 전부 빼라고 하면 판단을 믿지 않는다. 태그는 최소 1개는 있어야 한다.
    if len(drop) >= len(cands):
        log("    [Gemini] 후보를 전부 빼라고 함 - 무시하고 규칙대로 진행")
        return {"drop": [], "ok": False}
    return {"drop": drop, "ok": True}
