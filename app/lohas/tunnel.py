"""
SSH 터널 — 외부망(집)에서 사내 서비스에 붙기 위한 통로.

사내 MySQL 미러와 데이터랩 서버는 사설 IP라 밖에서 직접 닿지 않는다. SSH(22)만
공인 IP 로 열려 있으므로, 거기로 한 번 들어가서 두 서비스를 로컬 포트로 끌어온다.

    내 PC:13306  --ssh-->  <SSH_HOST>  -->  <MySQL>:3306    (MySQL)
    내 PC:18900  --ssh-->  <SSH_HOST>  -->  <데이터랩>:8900  (데이터랩)

이러면 .env 는 127.0.0.1 만 가리키면 되고, MySQL 3306 을 인터넷에 직접
열어둘 필요가 없다.

paramiko 만 쓴다. sshtunnel 패키지도 같은 일을 하지만 paramiko 5 에서
`DSSKey` 를 없앤 뒤로 import 조차 되지 않아 직접 만들었다.

터널이 안 열려도 프로그램은 죽지 않는다. 미러와 데이터랩만 꺼지고 로하스
작업(조회·카테고리·키워드·저장)은 공인 IP 라 그대로 된다.
"""
import select
import socket
import socketserver
import threading

from .. import config

_client = None
_servers = []
_lock = threading.Lock()
_last_error = ""


# ------------------------------------------------------------------ 상태

def wanted() -> bool:
    """지금 이 자리에서 터널을 써야 하는가."""
    return config.TUNNEL_ENABLED and config.is_external()


def is_up() -> bool:
    if not _client:
        return False
    tr = _client.get_transport()
    return bool(tr and tr.is_active())


def last_error() -> str:
    return _last_error


def status() -> str:
    if not config.TUNNEL_ENABLED:
        return "SSH 터널: 꺼짐"
    if not config.is_external():
        return "SSH 터널: 사내망이라 불필요"
    if is_up():
        return (f"SSH 터널: 연결됨 ({config.SSH_USER}@{config.SSH_HOST}) "
                f"MySQL→{config.TUNNEL_DB_PORT} / "
                f"데이터랩→{config.TUNNEL_DATALAB_PORT}")
    return "SSH 터널: 연결 안 됨" + (f" - {_last_error}" if _last_error else "")


# ------------------------------------------------------------------ 열고 닫기

def start(log=print) -> bool:
    """
    터널을 연다. 이미 열려 있으면 그대로 둔다.
    실패해도 예외를 올리지 않는다 — 부가 기능이 꺼질 뿐이다.
    """
    global _client, _last_error

    if not wanted():
        return False
    with _lock:
        if is_up():
            return True
        try:
            import paramiko
        except ImportError:
            _last_error = "paramiko 미설치 (pip install paramiko)"
            log(f"[터널] {_last_error}")
            return False

        pairs = []
        if config.TUNNEL_DB_TARGET:
            pairs.append((config.TUNNEL_DB_PORT,
                          _hostport(config.TUNNEL_DB_TARGET, 3306), "MySQL"))
        if config.TUNNEL_DATALAB_TARGET:
            pairs.append((config.TUNNEL_DATALAB_PORT,
                          _hostport(config.TUNNEL_DATALAB_TARGET, 8900),
                          "데이터랩"))
        if not pairs:
            _last_error = "터널 대상이 없습니다"
            return False

        cli = paramiko.SSHClient()
        cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        auth = {}
        if config.SSH_KEY:
            auth["key_filename"] = config.SSH_KEY
            if config.SSH_KEY_PASSPHRASE:
                auth["passphrase"] = config.SSH_KEY_PASSPHRASE
        else:
            auth["password"] = config.SSH_PASSWORD
            auth["allow_agent"] = False
            auth["look_for_keys"] = False

        try:
            cli.connect(config.SSH_HOST, port=config.SSH_PORT,
                        username=config.SSH_USER,
                        timeout=config.SSH_TIMEOUT, **auth)
            tr = cli.get_transport()
            tr.set_keepalive(30)
        except Exception as e:
            _last_error = f"{type(e).__name__}: {str(e)[:90]}"
            log(f"[터널] SSH 접속 실패 - {_last_error}")
            log("[터널] 미러·데이터랩만 꺼집니다. 로하스 작업은 그대로 됩니다.")
            return False

        _client = cli
        log(f"[터널] 연결됨 {config.SSH_USER}@{config.SSH_HOST}:"
            f"{config.SSH_PORT}")
        for local_port, (rhost, rport), label in pairs:
            try:
                _serve(local_port, rhost, rport)
                log(f"[터널]   127.0.0.1:{local_port} -> {rhost}:{rport}"
                    f"  ({label})")
            except OSError as e:
                log(f"[터널]   ! {label} 로컬 포트 {local_port} 사용 불가: "
                    f"{str(e)[:60]}")
        _last_error = ""
        return True


def stop(log=print) -> None:
    global _client
    with _lock:
        for srv in _servers:
            try:
                srv.shutdown()
                srv.server_close()
            except Exception:
                pass
        _servers.clear()
        if _client is not None:
            try:
                _client.close()
                log("[터널] 닫힘")
            except Exception:
                pass
            _client = None


def ensure(log=print) -> bool:
    """끊겼으면 다시 연다. 주기 작업 앞에서 부르면 된다."""
    if not wanted():
        return False
    if is_up():
        return True
    stop(log=lambda *_: None)
    return start(log=log)


# ------------------------------------------------------------------ 포워딩

def _serve(local_port: int, remote_host: str, remote_port: int) -> None:
    """로컬 포트를 열고, 들어오는 접속마다 SSH 채널을 뚫어 잇는다."""

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            try:
                chan = _client.get_transport().open_channel(
                    "direct-tcpip", (remote_host, remote_port),
                    self.request.getpeername())
            except Exception:
                return
            if chan is None:
                return
            try:
                _pump(self.request, chan)
            finally:
                for x in (chan, self.request):
                    try:
                        x.close()
                    except Exception:
                        pass

    srv = _ThreadedTCPServer(("127.0.0.1", local_port), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    _servers.append(srv)


class _ThreadedTCPServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


def _pump(sock, chan) -> None:
    """양방향으로 그대로 흘려보낸다."""
    while True:
        try:
            r, _, _ = select.select([sock, chan], [], [], 1.0)
        except Exception:
            return
        if sock in r:
            try:
                data = sock.recv(16384)
            except (OSError, socket.error):
                return
            if not data:
                return
            chan.sendall(data)
        if chan in r:
            data = chan.recv(16384)
            if not data:
                return
            try:
                sock.sendall(data)
            except (OSError, socket.error):
                return


def _hostport(value: str, default_port: int):
    host, _, port = str(value).partition(":")
    try:
        return host.strip(), int(port or default_port)
    except ValueError:
        return host.strip(), default_port
