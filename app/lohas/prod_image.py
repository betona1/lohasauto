"""
대표이미지 — **원본 / 현재(AI) 이미지 URL** 을 읽고, 현재이미지를 승인한다.

화면에서는 상품정보 목록의 [대표이미지] 팝업이 이미지 서버를 iframe 으로
띄운다. 그 iframe 은 리액트 앱이고, 실제 값은 **다른 포트의 API** 에서
온다(2026-09-10 실측).

    팝업   /manager/commercial/commercial_ss_image_edit/popup/ok/no/{no}
             └ <iframe src="http://110.15.200.93:5000/images_shopn/{uid}/{no}">
    API    POST http://110.15.200.93:5030/myprod/product   uid, no
             → resp.data.org  {listImg, main1Img, main2Img, main3Img, detail[]}
               resp.data.edit {같은 꼴}      ← AI 가 만든 현재 이미지
               resp.data.ai   'O' 면 AI 이미지가 있다

  · `org`  = 원본 (images3/...)
  · `edit` = 현재 (images_ai/...)

승인은 팝업의 [현재이미지 승인적용] 버튼 그대로다 — `tieForm` 을 자기 URL 로
보내는 것이 전부다.

    POST <팝업 URL>   action_mode=edit
"""
import re

IMG_APP = "http://110.15.200.93:5000"
IMG_API = "http://110.15.200.93:5030"
POPUP = ("http://com.exponet.co.kr/manager/commercial/"
         "commercial_ss_image_edit/popup/ok/no/{no}")

_IFRAME = re.compile(r'<iframe[^>]*id="main_2"[^>]*src="([^"]+)"')
_UID = re.compile(r"/images_shopn/([0-9]+)/([0-9]+)")


def popup_url(no) -> str:
    return POPUP.format(no=no)


def fetch_uid(session, no, timeout=40) -> str:
    """팝업 HTML 에서 이미지 서버의 uid 를 읽는다. 계정마다 다르다."""
    r = session.get(popup_url(no), timeout=timeout)
    r.encoding = "utf-8"
    if "loginForm" in r.text:
        raise RuntimeError("세션 만료")
    m = _IFRAME.search(r.text)
    if not m:
        return ""
    u = _UID.search(m.group(1))
    return u.group(1) if u else ""


def fetch_images(session, no, uid: str = "", timeout=30) -> dict:
    """
    반환
      {'uid', 'ai': bool,
       'org':  {'list','main1','main2','main3','detail':[...]},
       'edit': {같은 꼴}}

    `edit` 가 비어 있으면 아직 AI 이미지가 없는 것이다.
    """
    uid = uid or fetch_uid(session, no, timeout=timeout)
    if not uid:
        return {"uid": "", "ai": False, "org": {}, "edit": {}}
    r = session.post(
        IMG_API + "/myprod/product",
        data={"uid": str(uid), "no": str(no)},
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Referer": f"{IMG_APP}/images_shopn/{uid}/{no}"},
        timeout=timeout)
    d = (r.json() or {}).get("resp") or {}
    if d.get("msg") != "success":
        return {"uid": uid, "ai": False, "org": {}, "edit": {},
                "error": str(d.get("msg"))[:80]}
    data = d.get("data") or {}

    def side(k):
        v = data.get(k) or {}
        return {"list": v.get("listImg") or "",
                "main1": v.get("main1Img") or "",
                "main2": v.get("main2Img") or "",
                "main3": v.get("main3Img") or "",
                "detail": [x for x in (v.get("detail") or []) if x]}

    return {"uid": uid, "ai": str(data.get("ai") or "").upper() == "O",
            "org": side("org"), "edit": side("edit"),
            "row": data.get("row") or {}}


def approve(session, no, timeout=40) -> dict:
    """
    [현재이미지 승인적용]. 팝업의 `tieForm` 을 그대로 보낸다.

    **사람이 눈으로 보고 누르는 자리다.** 화면에서 이미지를 보여준 뒤에만
    부른다(2026-09-10 사용자 지시).
    """
    url = popup_url(no)
    r = session.post(url, data={"action_mode": "edit"}, timeout=timeout)
    r.encoding = "utf-8"
    if "loginForm" in r.text:
        raise RuntimeError("세션 만료")
    return {"ok": r.status_code == 200, "http": r.status_code}
