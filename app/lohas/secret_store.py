"""
비밀번호를 `.env` 에 두지 않고 **사내 100번 서버에서 그때그때 받아온다.**

`.env` 는 S: 공유폴더에 있어서 사무실 PC 누구나 열어볼 수 있다. 메일
비밀번호는 거기에 적지 않는 편이 낫다 — 메일함은 다른 계정들의 비밀번호
재설정 링크가 도착하는 곳이라 사실상 열쇠 꾸러미다(2026-09-11 사용자).

100번 서버의 `gmarket_cpc` 백엔드가 계정을 암호화해 들고 있다
(`cpc.models.EmailAccount` / `cpc.email_service.decrypt_password`).
여기서는 그 값을 **메모리로만** 받아온다 — 디스크에 쓰지 않는다.

    MAIL_SECRET_SOURCE=ai100      # 비우면 .env 의 MAIL_n_PASS 를 그대로 쓴다

⚠️ **이것만으로 안전해지지는 않는다.** 같은 `.env` 에 100번 서버 접속
비밀번호(`ai100pw`)가 평문으로 있어서, 그 파일을 볼 수 있는 사람은 결국
여기로 같은 값을 꺼낼 수 있다. 제대로 막으려면 메일 계정마다
**애플리케이션 비밀번호**를 따로 발급해 쓰거나 `.env` 를 공유폴더 밖으로
옮겨야 한다. 사용자에게 알렸다.
"""
import os
import shlex

_cache = {}          # {메일주소: 비밀번호} - 프로세스가 살아 있는 동안만

DJANGO_DIR = "~/projects/ai100/viewer/gmarket_cpc/backend"


def source() -> str:
    return (os.getenv("MAIL_SECRET_SOURCE") or "").strip().lower()


def available() -> bool:
    return source() == "ai100" and bool(os.getenv("ai100ip")
                                        and os.getenv("ai100id"))


def _connect():
    import paramiko
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(os.getenv("ai100ip"), username=os.getenv("ai100id"),
              password=os.getenv("ai100pw"), timeout=15)
    return c


def mail_password(address: str, log=print) -> str:
    """
    한 메일 계정의 값을 받아온다. 실패하면 빈 문자열.

    **받아온 값을 로그에 찍지 않는다.** 길이만 남긴다.
    """
    if address in _cache:
        return _cache[address]
    if not available():
        return ""
    # 서버에서 딱 한 줄만 출력시킨다. 서버에도 흔적을 남기지 않는다.
    snippet = ("from cpc.email_service import decrypt_password;"
               "from cpc.models import EmailAccount;"
               "print(decrypt_password(EmailAccount.objects.get("
               f"email_address={address!r}).password_enc))")
    cmd = (f"cd {DJANGO_DIR} && "
           f"python3 manage.py shell -c {shlex.quote(snippet)}")
    try:
        c = _connect()
        try:
            _, out, err = c.exec_command(f"bash -lc {shlex.quote(cmd)}",
                                         timeout=60)
            lines = out.read().decode("utf-8", "replace").strip().splitlines()
            rc = out.channel.recv_exit_status()
            msg = err.read().decode("utf-8", "replace").strip()
        finally:
            c.close()
    except Exception as e:
        log(f"  [계정] {address} 서버 접속 실패: {str(e)[:80]}")
        return ""
    if rc != 0 or not lines:
        tail = msg.splitlines()[-1][:80] if msg else ""
        log(f"  [계정] {address} 받지 못했습니다 (exit {rc}) {tail}")
        return ""
    val = lines[-1]
    _cache[address] = val
    log(f"  [계정] {address} 받았습니다 ({len(val)}자)")
    return val


def fill(mb: dict, log=print) -> dict:
    """메일함 설정이 비어 있으면 서버에서 채워 넣는다."""
    if mb.get("password") or not available():
        return mb
    return dict(mb, password=mail_password(mb["user"], log=log))
