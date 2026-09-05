"""
상품분석(ALL 상품분석) 엔진.

화면에서는 [상품정보 수정] -> 팝업 -> [상품분석] 클릭 순서지만,
팝업의 JS 를 뜯어보면 실제로는 내부 분석서버에 AJAX 두 번을 던지는 게 전부다.

  startAnalysis() -> makeTask()   : POST http://<ip>:3403/ss_site/analysis
                                    -> {"msg":"success","result":<analysis_no>}
  checkStatus()                   : POST http://<ip>:3403/ss_site/check_analysis
                                    -> {"result":{"state":"N"|"P", "state_msg":...}}
                                       N=완료, P=진행중

그래서 팝업창을 20개씩 띄울 필요 없이 HTTP 로 걸고 바로 다음 상품으로 넘어갈 수 있다.
이미 분석된 상품은 팝업 HTML 의 `let analysis_date = "2026-..."` 로 클릭 전에 판별한다.
"""
import re
import time

import requests

from . import constants as C

POPUP_URL = C.BASE + "/manager/commercial/commercial_ss_image_attr/popup/ok/no/{no}"

# 팝업에서 뽑아내는 값들
_RE_TOKEN = re.compile(r'var\s+token\s*=\s*"([^"]*)"')
_RE_IP = re.compile(r'var\s+ip\s*=\s*"([^"]*)"')
_RE_UID = re.compile(r'var\s+uid\s*=\s*"([^"]*)"')
_RE_CID = re.compile(r'var\s+commercial_id\s*=\s*"([^"]*)"')
_RE_ADATE = re.compile(r'let\s+analysis_date\s*=\s*"([^"]*)"')
_RE_START = re.compile(r"startAnalysis\((\d+),\s*'([^']+)',\s*(\d+)\)")


class AnalysisError(RuntimeError):
    pass


def parse_popup(html: str) -> dict:
    """상품정보 팝업 HTML 에서 분석에 필요한 값 추출."""
    def g(rx, default=""):
        m = rx.search(html)
        return m.group(1) if m else default

    info = {
        "token": g(_RE_TOKEN),
        "ip": g(_RE_IP),
        "uid": g(_RE_UID),
        "commercial_id": g(_RE_CID),
        "analysis_date": g(_RE_ADATE),
        "product_code": "",
        "product_id": "",
    }
    m = _RE_START.search(html)
    if m:
        info["uid"] = info["uid"] or m.group(1)
        info["product_code"] = m.group(2)
        info["product_id"] = m.group(3)

    # 팝업의 analysis_date 가 '20..' 으로 시작하면 이미 분석완료
    info["already_done"] = info["analysis_date"].startswith("20")
    return info


def fetch_popup(session, no: str, timeout: int = 60) -> dict:
    """상품정보 팝업을 받아 분석 정보 추출."""
    r = session.get(POPUP_URL.format(no=no), timeout=timeout)
    r.encoding = "utf-8"
    if "loginForm" in r.text:
        raise AnalysisError("세션 만료")
    info = parse_popup(r.text)
    info["product_no"] = str(no)
    return info


def _post(url: str, data: dict, timeout: int = 60, retries: int = 2):
    """
    분석서버는 keep-alive 연결을 잘 끊어서, 매번 새 연결 + 재시도로 보낸다.
    """
    last = None
    for _ in range(retries + 1):
        s = requests.Session()
        s.headers.update({"Connection": "close",
                          "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        try:
            return s.post(url, data=data, timeout=timeout)
        except Exception as e:
            last = e
            time.sleep(0.6)
        finally:
            s.close()
    raise AnalysisError(f"분석서버 통신 실패: {last}")


def start_analysis(info: dict, timeout: int = 60) -> dict:
    """
    상품분석 작업 생성 (화면의 '상품분석' 버튼과 동일).
    반환: {'ok': bool, 'analysis_no': str, 'msg': str}
    """
    if not info.get("token") or not info.get("ip"):
        return {"ok": False, "analysis_no": "", "msg": "토큰/서버주소 없음"}

    r = _post(
        f"http://{info['ip']}:3403/ss_site/analysis",
        {
            "token": info["token"],
            "title": "단품_" + info["ip"],
            "uid": info["uid"],
            "ip": info["ip"],
            "commercial_id": info["commercial_id"],
            "product_code": info["product_code"],
        },
        timeout=timeout,
    )
    try:
        d = r.json()
    except Exception:
        return {"ok": False, "analysis_no": "", "msg": f"응답 파싱 실패: {r.text[:120]}"}

    if d.get("msg") == "error":
        return {"ok": False, "analysis_no": "", "msg": str(d.get("result"))[:200]}
    return {"ok": True, "analysis_no": str(d.get("result", "")), "msg": "success"}


def check_analysis(info: dict, analysis_no: str, timeout: int = 30) -> dict:
    """
    분석 상태 조회.
    반환: {'state': 'N'|'P'|'?', 'msg': str, 'done': bool}
    """
    try:
        r = _post(
            f"http://{info['ip']}:3403/ss_site/check_analysis",
            {"token": info["token"], "uid": info["uid"], "no": analysis_no},
            timeout=timeout, retries=1,
        )
        d = r.json()
    except Exception as e:
        return {"state": "?", "msg": f"조회실패: {e}", "done": False}

    if d.get("msg") == "error":
        return {"state": "?", "msg": str(d.get("result"))[:200], "done": False}

    res = d.get("result") or {}
    state = str(res.get("state", "?"))
    return {"state": state,
            "msg": str(res.get("state_msg", "")),
            "done": state == "N"}
