"""
HTTP 직접 조회 엔진.

브라우저(Selenium)로 매 검색마다 페이지를 다시 그리면 1회 60초가 걸리지만,
로그인 쿠키(PHPSESSID)만 있으면 검색 POST 를 그대로 재현할 수 있어 0.5~7초로 끝난다.

두 가지 이점이 있다.
  1) 속도  : 12칸 매트릭스 점검이 15분 -> 1분 미만
  2) 정확도: viewnum 을 크게 주면 화면의 1000행 상한이 사라져
             2000건 초과 칸도 정확히 셀 수 있다 (실측: 4,299행까지 확인)

HTML 파싱은 bs4 대신 행 단위 분할 + 정규식을 쓴다.
12MB 응답 기준 bs4 5.1초 -> 0.04초 (결과는 완전 동일함을 실측 검증).
"""
import json
import re
import time

import requests

from .. import config
from . import constants as C

# 검색 POST 대상 (form action="" 이라 현재 URL 로 제출된다)
SEARCH_URL = f"{C.BASE}/manager/commercial/commercial_ss_image/p/1"

# 화면 상한(1000)을 넘기기 위한 값. 5000 이상이면 제한이 풀리는 것을 실측 확인.
HTTP_VIEWNUM = "100000"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36")

_LCP_RE = re.compile(r"LCP_[A-Z0-9_]+")
_LCODE_RE = re.compile(r">\s*(L\d{4,})\s*<")
_EDITATTR_RE = re.compile(r"editAttr\(['\"]?(\d+)['\"]?\)")
_EDITIMG_RE = re.compile(r"editImg\(['\"]?(\d+)['\"]?,\s*(\d+)\)")


class SessionExpired(RuntimeError):
    """쿠키 세션이 만료됨 -> 재로그인 필요."""


class LohasHttp:
    def __init__(self, cookies: dict):
        self.session = requests.Session()
        self.session.cookies.update(cookies or {})
        self.session.headers.update({"User-Agent": UA, "Referer": SEARCH_URL})

    # ---------------------------------------------------------------- 생성

    @classmethod
    def from_cookie_file(cls, path):
        """browser.save_cookies() 가 남긴 파일에서 세션 생성."""
        raw = json.loads(path.read_text(encoding="utf-8"))
        cookies = {c["name"]: c["value"] for c in raw if c.get("name")}
        if not cookies:
            raise SessionExpired("저장된 쿠키가 비어 있습니다.")
        return cls(cookies)

    @classmethod
    def from_driver(cls, driver):
        """로그인된 셀레늄 드라이버에서 곧바로 세션 생성."""
        cookies = {c["name"]: c["value"] for c in driver.get_cookies()}
        return cls(cookies)

    # ---------------------------------------------------------------- 요청

    def _payload(self, folder: str, dest_list: str = "all",
                 dest_attr: str = "all", viewnum: str = None,
                 order: str = "asc") -> dict:
        return {
            # searchNow() 가 세팅하는 값. 없으면 결과 그리드가 비어서 온다.
            "action_mode": "search",
            "categoryname_select": "master",
            "site_categoryname_search": folder,
            "site_categoryname_search2": "",
            "dest_list": dest_list,      # 대표이미지 상태
            "dest_attr": dest_attr,      # 상품정보 상태
            "attribute": "all",
            "dest_detail": "all",
            "dest_cate": "all",
            "fc": "product_code",
            "order": order,
            "fv": "",
            "viewnum": viewnum or HTTP_VIEWNUM,
            "categoryname_select_ai": "master",
            "site_categoryname_search_ai": folder,
            "site_categoryname_search2_ai": "",
        }

    def _post(self, data: dict, timeout: int = 180) -> str:
        r = self.session.post(SEARCH_URL, data=data, timeout=timeout)
        r.encoding = "utf-8"
        html = r.text
        if 'id="loginForm"' in html or "name=\"loginForm\"" in html:
            raise SessionExpired("세션이 만료되었습니다.")
        return html

    def is_logged_in(self) -> bool:
        try:
            r = self.session.get(C.MANAGER_URL, timeout=30)
            r.encoding = "utf-8"
            return "loginForm" not in r.text
        except Exception:
            return False

    # ---------------------------------------------------------------- 파싱

    @staticmethod
    def parse_rows(html: str) -> list:
        """
        결과 그리드에서 (광고상품코드, 로하스상품코드) 목록 추출.
        행 단위로 자른 뒤 정규식 -> 12MB 응답도 0.04초.
        """
        rows = []
        for chunk in html.split("<tr"):
            if "editImg(" not in chunk:
                continue
            m_lcp = _LCP_RE.search(chunk)
            if not m_lcp:
                continue
            m_lc = _LCODE_RE.search(chunk)
            if m_lc:
                lcode = m_lc.group(1)
            else:
                # 폴백 : editImg 의 두번째 인자 (화면 표기는 7자리 0채움)
                m_img = _EDITIMG_RE.search(chunk)
                lcode = "L" + m_img.group(2).zfill(7) if m_img else ""
            rows.append((m_lcp.group(0), lcode))
        return rows

    @staticmethod
    def parse_rows_full(html: str) -> list:
        """(광고상품코드, 로하스상품코드, 상품정보팝업 no) 목록.
        팝업 no 는 editAttr(no) 에서 뽑는다 - 상품분석에 필요하다."""
        rows = []
        for chunk in html.split("<tr"):
            if "editAttr(" not in chunk:
                continue
            m_lcp = _LCP_RE.search(chunk)
            m_no = _EDITATTR_RE.search(chunk)
            if not (m_lcp and m_no):
                continue
            m_lc = _LCODE_RE.search(chunk)
            if m_lc:
                lcode = m_lc.group(1)
            else:
                m_img = _EDITIMG_RE.search(chunk)
                lcode = "L" + m_img.group(2).zfill(7) if m_img else ""
            rows.append((m_lcp.group(0), lcode, m_no.group(1)))
        return rows

    @staticmethod
    def parse_folders(html: str) -> list:
        """마스터 폴더 select 의 옵션 파싱 -> [{name, raw_label, option_value, site_count}]"""
        from .folders import parse_folder_label

        m = re.search(
            r'<select[^>]*name=["\']site_categoryname_search["\'][^>]*>(.*?)</select>',
            html, re.S)
        if not m:
            return []

        out, seen = [], set()
        for om in re.finditer(
                r'<option[^>]*value=["\'](.*?)["\'][^>]*>(.*?)</option>',
                m.group(1), re.S):
            value = om.group(1).strip()
            text = re.sub(r"<[^>]+>", "", om.group(2))
            text = " ".join(text.replace("&nbsp;", " ").split())
            if not text or text in ("전체", "선택", "-"):
                continue
            parsed = parse_folder_label(text)
            if not parsed["name"] or parsed["name"] in seen:
                continue
            seen.add(parsed["name"])
            parsed["option_value"] = value
            out.append(parsed)
        return out

    # ---------------------------------------------------------------- 공개 API

    def search(self, folder: str, dest_list: str = "all",
               dest_attr: str = "all", viewnum: str = None) -> dict:
        """검색 1회. 반환: {'rows': [(lcp, lcode)...], 'elapsed': 초, 'bytes': n}"""
        t0 = time.time()
        html = self._post(self._payload(folder, dest_list, dest_attr, viewnum))
        rows = self.parse_rows(html)
        return {"rows": rows, "elapsed": round(time.time() - t0, 2),
                "bytes": len(html)}

    def search_full(self, folder: str, dest_list: str = "all",
                    dest_attr: str = "all", viewnum: str = None) -> dict:
        """검색 1회 (팝업 no 포함). 반환: {'rows': [(lcp, lcode, no)...], ...}"""
        t0 = time.time()
        html = self._post(self._payload(folder, dest_list, dest_attr, viewnum))
        return {"rows": self.parse_rows_full(html),
                "elapsed": round(time.time() - t0, 2), "bytes": len(html)}

    def fetch_folders(self) -> list:
        """마스터 폴더 목록 (검색 없이 페이지만 받아서 파싱)."""
        r = self.session.get(SEARCH_URL, timeout=60)
        r.encoding = "utf-8"
        if "loginForm" in r.text:
            raise SessionExpired("세션이 만료되었습니다.")
        return self.parse_folders(r.text)
