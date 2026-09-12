"""
메일함 읽기 — 스마트스토어 경고 메일 · 검색광고 비용 알림을 받아온다.

**네이트는 POP3 를 없앴다.** 2026-09-11 에 직접 붙어 확인했다.

    pop3.nate.com:995   -ERR POP3 service has been permanently discontinued.
    imap.nate.com:993   * OK [CAPABILITY IMAP4rev1 ...]   ← 이쪽만 산다
    pop.naver.com:995   +OK Naver Popper starting
    imap.naver.com:993  * OK [CAPABILITY IMAP4rev1 ...]

그래서 기본을 IMAP 으로 둔다. POP3 는 읽고 나면 서버에서 지워지는 설정이
흔해서, 사람이 나중에 같은 메일을 보려면 곤란하다. IMAP 은 **읽음 표시도
건드리지 않고**(`BODY.PEEK`) 훑을 수 있다.

⚠️ 2단계 인증을 켠 계정은 **로그인 비밀번호가 아니라 애플리케이션
비밀번호**를 써야 한다. 네이버는 메일 환경설정에서 IMAP/POP3 를 '사용함'
으로 켜 두어야 접속 자체가 된다.

설정은 `.env` 의 MAIL_1_* ~ MAIL_9_* (`config.mailboxes()`).
"""
import datetime
import email
import email.header
import email.utils
import imaplib
import poplib
import re
import ssl

from .. import config
from . import secret_store

imaplib._MAXLINE = 10_000_000
poplib._MAXLINE = 10_000_000

# 무엇을 건져낼지. 제목/보낸이에 이 말이 있으면 그 갈래로 분류한다.
RULES = [
    ("스토어경고", re.compile(
        r"경고|제재|판매금지|판매 금지|이용정지|이용 정지|위반|반려|"
        r"신고|패널티|페널티|조치|시정|삭제 요청|품절 관리|"
        r"상품등록 실패|등록 실패|노출 제한")),
    ("광고비", re.compile(
        r"비즈머니|잔액|충전|소진|광고비|결제|세금계산서|환불|"
        r"예산|과금|일예산|정산")),
    ("광고알림", re.compile(r"검색광고|searchad|광고 ?시스템|캠페인|파워링크")),
]
STORE_SENDERS = re.compile(
    r"smartstore|naver\.com|commerce|shopping|셀러|스마트스토어", re.I)


def _dec(raw) -> str:
    """MIME 인코딩된 헤더를 사람이 읽는 글자로."""
    if not raw:
        return ""
    out = []
    for txt, enc in email.header.decode_header(str(raw)):
        if isinstance(txt, bytes):
            try:
                out.append(txt.decode(enc or "utf-8", "replace"))
            except LookupError:
                out.append(txt.decode("utf-8", "replace"))
        else:
            out.append(txt)
    return "".join(out).strip()


def _body(msg) -> str:
    """본문 평문. HTML 뿐이면 태그를 털어낸다."""
    html = ""
    for part in (msg.walk() if msg.is_multipart() else [msg]):
        if part.get_content_maintype() == "multipart":
            continue
        if part.get_filename():
            continue
        try:
            raw = part.get_payload(decode=True) or b""
            txt = raw.decode(part.get_content_charset() or "utf-8", "replace")
        except Exception:
            continue
        if part.get_content_subtype() == "plain":
            return txt.strip()
        html = html or txt
    if html:
        html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
        html = re.sub(r"(?s)<[^>]+>", " ", html)
        return re.sub(r"\s+", " ", html).strip()
    return ""


def classify(subject: str, sender: str) -> str:
    """어느 갈래인지. 해당 없으면 빈 문자열."""
    txt = f"{subject} {sender}"
    for name, rx in RULES:
        if rx.search(txt):
            if name == "스토어경고" and not STORE_SENDERS.search(sender):
                continue
            return name
    return ""


def _one(msg, uid: str, box: str) -> dict:
    subj = _dec(msg.get("Subject"))
    frm = _dec(msg.get("From"))
    try:
        dt = email.utils.parsedate_to_datetime(msg.get("Date"))
        when = dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        when = ""
    return {"box": box, "uid": str(uid), "date": when, "from": frm,
            "subject": subj, "kind": classify(subj, frm),
            "body": _body(msg)[:4000],
            "message_id": (msg.get("Message-ID") or "").strip()}


def _fetch_imap(mb: dict, days: int, log=print) -> list:
    since = (datetime.date.today() - datetime.timedelta(days=days))
    ctx = ssl.create_default_context()
    M = imaplib.IMAP4_SSL(mb["host"], mb["port"], ssl_context=ctx)
    try:
        M.login(mb["user"], mb["password"])
        M.select(mb["folder"], readonly=True)     # 읽음 표시를 건드리지 않는다
        typ, data = M.search(None, "SINCE",
                             since.strftime("%d-%b-%Y"))
        ids = (data[0].split() if data and data[0] else [])
        log(f"  [{mb['name']}] {since} 이후 {len(ids)}통")
        out = []
        for i in ids:
            typ, d = M.fetch(i, "(BODY.PEEK[])")
            if typ != "OK" or not d or not isinstance(d[0], tuple):
                continue
            out.append(_one(email.message_from_bytes(d[0][1]),
                            i.decode(), mb["name"]))
        return out
    finally:
        try:
            M.logout()
        except Exception:
            pass


def _fetch_pop3(mb: dict, days: int, log=print) -> list:
    """POP3 는 날짜 검색이 없어 뒤에서부터 훑고 날짜로 자른다."""
    cut = datetime.datetime.now().astimezone() - datetime.timedelta(days=days)
    P = poplib.POP3_SSL(mb["host"], mb["port"],
                        context=ssl.create_default_context())
    try:
        P.user(mb["user"])
        P.pass_(mb["password"])
        n = len(P.list()[1])
        log(f"  [{mb['name']}] 사서함 {n}통 - 뒤에서부터 {days}일치")
        out = []
        for i in range(n, 0, -1):
            raw = b"\n".join(P.retr(i)[1])
            msg = email.message_from_bytes(raw)
            try:
                dt = email.utils.parsedate_to_datetime(msg.get("Date"))
                if dt and dt.astimezone() < cut:
                    break                  # 더 옛날 것뿐이다
            except Exception:
                pass
            out.append(_one(msg, i, mb["name"]))
        return out
    finally:
        try:
            P.quit()
        except Exception:
            pass


def fetch(mb: dict, days: int = None, log=print) -> list:
    days = config.MAIL_DAYS if days is None else days
    if mb["proto"] == "pop3":
        if "nate" in mb["host"]:
            raise RuntimeError(
                "네이트는 POP3 를 종료했습니다 - imap.nate.com:993 을 쓰십시오")
        return _fetch_pop3(mb, days, log)
    return _fetch_imap(mb, days, log)


def fetch_all(days: int = None, only_hit: bool = True, log=print) -> list:
    """설정된 메일함을 다 훑는다. `only_hit` 면 분류된 것만 돌려준다."""
    rows = []
    for mb in config.mailboxes():
        # `.env` 에 안 적었으면 사내 서버에서 받아온다 (secret_store)
        mb = secret_store.fill(mb, log=log)
        if not mb["password"]:
            log(f"  [{mb['name']}] 비밀번호가 없습니다 - 건너뜀")
            continue
        try:
            got = fetch(mb, days, log=log)
        except Exception as e:
            log(f"  [{mb['name']}] !! {str(e)[:90]}")
            continue
        hit = [r for r in got if r["kind"]] if only_hit else got
        log(f"  [{mb['name']}] {len(got)}통 중 {len(hit)}통 해당")
        rows += hit
    rows.sort(key=lambda r: r["date"], reverse=True)
    return rows


def check(mb: dict) -> dict:
    """접속만 해본다. 설정을 맞출 때 쓴다."""
    try:
        if mb["proto"] == "pop3":
            P = poplib.POP3_SSL(mb["host"], mb["port"],
                                context=ssl.create_default_context())
            P.user(mb["user"])
            P.pass_(mb["password"])
            n = len(P.list()[1])
            P.quit()
            return {"ok": True, "count": n}
        M = imaplib.IMAP4_SSL(mb["host"], mb["port"],
                              ssl_context=ssl.create_default_context())
        M.login(mb["user"], mb["password"])
        typ, d = M.select(mb["folder"], readonly=True)
        M.logout()
        return {"ok": typ == "OK", "count": int(d[0]) if typ == "OK" else 0}
    except Exception as e:
        return {"ok": False, "error": str(e)[:160]}
