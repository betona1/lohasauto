"""로하스(exponet) 페이지 URL / XPath 상수. LOHASPIC 원본과 동일하게 유지."""

BASE = "http://com.exponet.co.kr"

LOGIN_URL = f"{BASE}/member/"
MANAGER_URL = f"{BASE}/manager/"
TIEDLIST_URL = f"{BASE}/manager/commercial/commercial_tiedlist"
MANAGE_URL = f"{BASE}/manager/commercial/commercial_manage"
# ★ 상품정보관리(상품분석) 페이지 - 이번 작업의 메인
SS_IMAGE_URL = f"{BASE}/manager/commercial/commercial_ss_image/p/1"

# ---- 로그인 폼 XPath (LOHASPIC lohaspicps6.py 와 동일) ----
LOGIN_ID_XPATH = (
    '//*[@id="loginForm"]/div/div/div/table/tbody/tr/td[1]'
    '/table/tbody/tr[1]/td[2]/input'
)
LOGIN_PW_XPATH = (
    '//*[@id="loginForm"]/div/div/div/table/tbody/tr/td[1]'
    '/table/tbody/tr[2]/td[2]/input'
)
LOGIN_BTN_XPATH = '//*[@id="loginForm"]/div/div/div/table/tbody/tr/td[2]/input'

# ---- 결과 그리드 ----
GRID_ROW_SELECTORS = (
    "#tiedlistForm table.grid_tbl tbody tr",
    "table.grid_tbl tbody tr",
    "#tiedlistForm table tbody tr",
    "table tbody tr",
)
GRID_HEADER_SELECTORS = (
    "table.grid_tbl tr th",
    "table.grid_tbl tr:first-child td",
    "#tiedlistForm table tr th",
)

# ---- 컬럼 헤더 후보 ----
COL_LCP = ("광고상품코드", "광고코드", "LCP코드")
COL_LCODE = ("로하스상품코드", "상품코드", "L코드", "로하스코드")
COL_NAME = ("상품명", "광고상품명", "제목")
COL_IMAGE = ("대표이미지", "대표이미지등록", "대표이미지승인", "이미지", "썸네일")
COL_INFO = ("상품정보", "상품정보수정", "정보수정", "상품정보등록", "상세정보")

# ---- 상태 필터 콤보 (실측으로 확정) ----
# commercial_ss_image 는 상태를 셀 텍스트가 아니라 버튼 CSS 클래스로만 표시하고,
# 그 클래스는 2진값(btn_m*=최종완료 / btn_z*=그 외)이라 세부 상태를 구분할 수 없다.
# 따라서 정확한 수량은 아래 필터 콤보를 걸고 검색한 결과 행수로 센다.

# 대표이미지 컬럼 필터 (select name="dest_list")
IMAGE_FILTER_NAME = "dest_list"
IMAGE_FILTERS = (
    ("미작업", "none"),
    ("이미지작업", "done"),        # 작업했지만 승인 전
    ("이미지승인완료", "allow"),    # ★ 최종 승인
)

# 상품정보 컬럼 필터 (select name="dest_attr")
INFO_FILTER_NAME = "dest_attr"
INFO_FILTERS = (
    ("미작업", "none"),
    ("저장완료", "save"),          # ★ 작업 완료
    ("제외", "exclude"),
    ("보류", "hold"),
)

# 정렬 콤보 (1000행 상한을 넘길 때 역순으로 한 번 더 읽어 합집합을 구한다)
ORDER_NAME = "order"
ORDER_ASC = "asc"
ORDER_DESC = "desc"

# 한 번에 조회 가능한 최대 행수 (이 페이지는 URL 페이징이 동작하지 않음)
MAX_ROWS_PER_SEARCH = 1000

# ★ 작업대상 정의 : 대표이미지 승인완료 + 상품정보 미작업
TARGET_IMAGE = "이미지승인완료"
TARGET_INFO = "미작업"
TARGET_IMAGE_VALUE = "allow"
TARGET_INFO_VALUE = "none"

# 상태 셀 버튼 클래스 (참고용 - 2진값이라 보조 판정에만 사용)
CLASS_DONE_PREFIX = "btn_m"   # 최종완료
CLASS_TODO_PREFIX = "btn_z"   # 그 외
