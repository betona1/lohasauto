"""
로하스 로그인 / 브라우저 유틸.

로그인 절차는 LOHASPIC(lohaspicps6.py `_open_loggedin_browser`) 을 그대로 복사했다.
차이점은 두 가지뿐:
  1) 계정을 config.txt 가 아니라 .env 에서 읽는다.
  2) 헤드리스 모드일 때는 클립보드+Ctrl+V 가 동작하지 않으므로 send_keys 로 입력한다.
"""
import json
import re
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .. import config
from . import constants as C
from .monitors import window_geometry


class LoginError(RuntimeError):
    pass


def create_driver(headless: bool = False, monitor: int = None):
    """
    크롬 드라이버 생성.

    monitor : 1-based 모니터 번호. 지정하면 그 모니터에 창을 띄운다.
              None 이면 .env 의 BROWSER_MONITOR 를 쓴다. 0 이면 기본 동작.
    """
    if monitor is None:
        monitor = config.BROWSER_MONITOR

    geom = None if headless else window_geometry(monitor, margin=0)

    options = ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-gpu")
    elif geom:
        # 창이 처음부터 해당 모니터에서 뜨도록 시작 인자로 지정
        x, y, w, h = geom
        options.add_argument(f"--window-position={x},{y}")
        options.add_argument(f"--window-size={w},{h}")
    options.add_argument("--log-level=3")
    options.add_experimental_option("excludeSwitches", ["enable-logging"])

    if config.CHROME_DRIVER_PATH:
        service = ChromeService(executable_path=config.CHROME_DRIVER_PATH)
    else:
        service = ChromeService()

    driver = webdriver.Chrome(service=service, options=options)
    driver.implicitly_wait(10)

    if not headless:
        try:
            if geom:
                # 시작 인자가 무시되는 경우가 있어 한 번 더 확정
                x, y, w, h = geom
                driver.set_window_rect(x=x, y=y, width=w, height=h)
            else:
                driver.maximize_window()
        except Exception:
            pass
    return driver


def _find_login_input(driver, xpath: str, css_fallback: str):
    """고정 XPath 우선, 실패하면 CSS 로 폴백."""
    try:
        return driver.find_element(By.XPATH, xpath)
    except Exception:
        try:
            driver.implicitly_wait(0)
            els = [e for e in driver.find_elements(By.CSS_SELECTOR, css_fallback)
                   if e.is_displayed()]
        finally:
            driver.implicitly_wait(10)
        if not els:
            raise
        return els[0]


def _type_via_clipboard(el, text: str) -> None:
    """LOHASPIC 원본 방식: 클릭 → 클립보드 복사 → Ctrl+V

    주의: 이 방식은 OS 전역 키입력이라 사용자가 다른 창을 클릭하면
    엉뚱한 곳에 붙고 로그인이 실패한다. 그래서 폴백으로만 쓴다.
    """
    import pyautogui
    import pyperclip

    el.click()
    pyperclip.copy(text)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.5)


def _type_via_keys(el, text: str) -> None:
    """send_keys 직접 입력 (창 포커스와 무관, 클립보드도 건드리지 않음)"""
    try:
        el.clear()
    except Exception:
        pass
    el.send_keys(text)
    time.sleep(0.2)


def _field_value(el) -> str:
    try:
        return el.get_attribute("value") or ""
    except Exception:
        return ""


def _type_verified(el, text: str, what: str, allow_clipboard: bool, log=print) -> bool:
    """
    send_keys 로 먼저 넣고 값이 실제로 들어갔는지 확인한다.
    비어 있으면(입력 차단 등) 클립보드 방식으로 한 번 더 시도.
    반환: 최종 입력 성공 여부
    """
    try:
        _type_via_keys(el, text)
    except Exception:
        pass

    if _field_value(el) == text:
        return True

    if not allow_clipboard:
        log(f"[로그인] {what} 직접입력 실패 (헤드리스라 클립보드 폴백 불가)")
        return False

    log(f"[로그인] {what} 직접입력이 반영되지 않아 클립보드 방식으로 재시도")
    try:
        _type_via_clipboard(el, text)
    except Exception:
        return False
    return _field_value(el) == text


def login(driver, headless: bool = False, log=print) -> None:
    """로하스 로그인. 실패 시 LoginError."""
    if not config.credentials_ok():
        raise LoginError(
            ".env 에 LOHAS_ID / LOHAS_PW 가 설정되지 않았습니다."
        )

    driver.get(C.LOGIN_URL)
    log(f"[로그인] 페이지 접속 : {C.LOGIN_URL}")

    # 헤드리스가 아니어도 send_keys 를 우선 쓴다.
    # 클립보드+Ctrl+V 는 전역 키입력이라 사용자가 다른 창을 만지면 실패하기 때문.
    allow_clipboard = not headless

    id_el = _find_login_input(
        driver, C.LOGIN_ID_XPATH,
        "#loginForm input[type='text'], #loginForm input:not([type])",
    )
    if not _type_verified(id_el, config.LOHAS_ID, "ID", allow_clipboard, log):
        raise LoginError("ID 입력칸에 값을 넣지 못했습니다.")

    pw_el = _find_login_input(
        driver, C.LOGIN_PW_XPATH, "#loginForm input[type='password']"
    )
    if not _type_verified(pw_el, config.LOHAS_PW, "PW", allow_clipboard, log):
        raise LoginError("PW 입력칸에 값을 넣지 못했습니다.")

    try:
        btn = driver.find_element(By.XPATH, C.LOGIN_BTN_XPATH)
        btn.click()
    except Exception:
        # 버튼을 못 찾으면 PW 칸에서 엔터
        from selenium.webdriver.common.keys import Keys
        pw_el.send_keys(Keys.ENTER)

    accept_all_alerts(driver)
    _wait_login_result(driver, timeout=20)

    if not _is_logged_in(driver):
        raise LoginError(
            "로그인에 실패한 것으로 보입니다.\n"
            "· .env 의 LOHAS_ID / LOHAS_PW 를 확인해주세요.\n"
            "· 헤드리스 모드에서 실패한다면 체크를 해제하고 다시 시도해주세요."
        )
    log(f"[로그인] 완료 ({config.masked_id()})")


def _wait_login_result(driver, timeout: int = 20) -> None:
    """
    로그인 버튼 클릭 후 페이지 전환을 기다린다.
    (기존엔 1초만 쉬어서, 전환 전에 실패로 오판하는 문제가 있었다)
    """
    end = time.time() + timeout
    while time.time() < end:
        try:
            driver.implicitly_wait(0)
            if not driver.find_elements(By.ID, "loginForm"):
                return
        except Exception:
            return
        finally:
            driver.implicitly_wait(10)
        accept_all_alerts(driver, max_loops=2)
        time.sleep(0.4)


def _is_logged_in(driver) -> bool:
    """로그인 폼이 사라졌으면 성공으로 본다."""
    try:
        driver.implicitly_wait(0)
        return not driver.find_elements(By.ID, "loginForm")
    except Exception:
        return True
    finally:
        driver.implicitly_wait(10)


# ------------------------------------------------------------------ 쿠키 로그인

def cookie_path():
    """계정별 쿠키 파일 경로 (data/ 아래, .gitignore 대상)."""
    safe = re.sub(r"[^A-Za-z0-9_.@-]", "_", config.LOHAS_ID or "default")
    return config.SQLITE_PATH.parent / f"cookies_{safe}.json"


def save_cookies(driver, log=print) -> bool:
    """로그인 성공 직후의 세션 쿠키를 저장."""
    try:
        path = cookie_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(driver.get_cookies(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return True
    except Exception as e:
        log(f"[쿠키] 저장 실패: {e}")
        return False


def clear_cookies(log=print) -> None:
    try:
        path = cookie_path()
        if path.exists():
            path.unlink()
            log("[쿠키] 저장된 쿠키 삭제")
    except Exception:
        pass


def try_cookie_login(driver, log=print) -> bool:
    """
    저장된 쿠키로 로그인 상태 복원 시도.
    세션이 만료됐으면 False -> 호출측에서 일반 로그인으로 폴백한다.
    """
    path = cookie_path()
    if not path.exists():
        return False

    try:
        cookies = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not cookies:
        return False

    try:
        # add_cookie 는 같은 도메인에 있어야 동작한다
        driver.get(C.MANAGER_URL)
        for ck in cookies:
            c = {k: v for k, v in ck.items()
                 if k in ("name", "value", "path", "domain", "secure", "httpOnly")}
            if "expiry" in ck and ck["expiry"]:
                try:
                    c["expiry"] = int(ck["expiry"])
                except Exception:
                    pass
            try:
                driver.add_cookie(c)
            except Exception:
                c.pop("domain", None)
                try:
                    driver.add_cookie(c)
                except Exception:
                    continue

        driver.get(C.MANAGER_URL)
        accept_all_alerts(driver, max_loops=3)
        if _is_logged_in(driver):
            return True
        log("[쿠키] 세션이 만료되어 일반 로그인으로 진행합니다.")
        return False
    except Exception as e:
        log(f"[쿠키] 복원 실패: {e}")
        return False


def open_logged_in_browser(headless: bool = False, log=print,
                           monitor: int = None, use_cookies: bool = None):
    """드라이버 생성 + 로그인까지 끝난 브라우저 반환.

    저장된 쿠키가 있으면 로그인 단계를 건너뛰고, 실패하면 일반 로그인으로 폴백한다.
    """
    if use_cookies is None:
        use_cookies = config.USE_COOKIE_LOGIN

    driver = create_driver(headless=headless, monitor=monitor)
    try:
        if use_cookies and try_cookie_login(driver, log=log):
            log(f"[로그인] 저장된 쿠키로 세션 복원 ({config.masked_id()})")
            return driver

        login(driver, headless=headless, log=log)
        if use_cookies and save_cookies(driver, log=log):
            log("[쿠키] 세션 저장 완료 (다음 실행부터 로그인 생략)")
    except Exception:
        try:
            driver.quit()
        except Exception:
            pass
        raise
    return driver


# ------------------------------------------------------------------ 공통 유틸

def accept_all_alerts(driver, max_loops: int = 10) -> int:
    """연속으로 뜨는 alert 전부 확인 (LOHASPIC `_accept_all_alerts` 동일)."""
    count = 0
    for _ in range(max_loops):
        try:
            WebDriverWait(driver, 2).until(EC.alert_is_present())
            driver.switch_to.alert.accept()
            count += 1
            time.sleep(0.2)
        except Exception:
            break
    return count


def enter_content_frame(driver) -> bool:
    """
    검색영역이 iframe/frame 안에 있는 구형 관리자 페이지 대응.
    메인 문서에 select 가 없으면 프레임을 하나씩 들어가서 찾는다.
    """
    try:
        driver.implicitly_wait(0)

        driver.switch_to.default_content()
        if driver.find_elements(By.TAG_NAME, "select"):
            return True

        frames = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
        for i in range(len(frames)):
            try:
                driver.switch_to.default_content()
                fr = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")[i]
                driver.switch_to.frame(fr)
                if driver.find_elements(By.TAG_NAME, "select"):
                    return True
            except Exception:
                continue

        driver.switch_to.default_content()
        return False
    finally:
        driver.implicitly_wait(10)


def visible_selects(driver) -> list:
    """페이지에 보이는 select 목록 (암시적 대기 끄고 빠르게 스캔)."""
    try:
        driver.implicitly_wait(0)
        elems = driver.find_elements(By.TAG_NAME, "select")
    except Exception:
        elems = []
    finally:
        driver.implicitly_wait(10)

    out = []
    for el in elems:
        try:
            if el.is_displayed():
                out.append(el)
        except Exception:
            continue
    return out


def describe(el) -> str:
    """디버깅용 요소 설명 문자열."""
    try:
        tag = el.tag_name
    except Exception:
        return "<알수없음>"

    parts = [f"<{tag}"]
    for attr in ("id", "name", "value", "class", "onclick", "title"):
        try:
            v = el.get_attribute(attr)
        except Exception:
            v = None
        if v:
            v = " ".join(str(v).split())
            if len(v) > 60:
                v = v[:60] + "..."
            parts.append(f'{attr}="{v}"')
    parts.append(">")
    try:
        txt = " ".join((el.text or "").split())
        if txt:
            parts.append(f"텍스트='{txt[:40]}'")
    except Exception:
        pass
    return " ".join(parts)
