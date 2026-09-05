"""
조회 세션 확보.

가능하면 저장된 쿠키로 HTTP 클라이언트를 만들고(브라우저 아예 안 뜸),
쿠키가 없거나 만료됐을 때만 브라우저를 띄워 로그인 후 쿠키를 갱신한다.
"""
from .browser import cookie_path, open_logged_in_browser, save_cookies
from .http_client import LohasHttp


def get_client(headless: bool = False, monitor: int = None,
               log=print, allow_login: bool = True) -> LohasHttp:
    """
    HTTP 조회용 클라이언트 반환.
    allow_login=False 면 쿠키가 없을 때 예외를 던진다(브라우저를 띄우지 않음).
    """
    path = cookie_path()
    if path.exists():
        try:
            client = LohasHttp.from_cookie_file(path)
            if client.is_logged_in():
                log("[세션] 저장된 쿠키 사용 - 브라우저 없이 조회합니다.")
                return client
            log("[세션] 쿠키 세션 만료.")
        except Exception as e:
            log(f"[세션] 쿠키 사용 불가: {e}")

    if not allow_login:
        raise RuntimeError("사용 가능한 로그인 쿠키가 없습니다.")

    log("[세션] 브라우저로 로그인해 쿠키를 갱신합니다.")
    driver = open_logged_in_browser(
        headless=headless, log=log, monitor=monitor, use_cookies=False)
    try:
        save_cookies(driver, log=log)
        client = LohasHttp.from_driver(driver)
        log("[세션] 쿠키 갱신 완료 - 다음 실행부터 브라우저 없이 조회합니다.")
        return client
    finally:
        try:
            driver.quit()
        except Exception:
            pass
