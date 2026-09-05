"""
태그 자동 투입.

태그의 출처는 **로하스 태그 탭의 기본 태그 후보**다. 그 표는 그 상품에
저장된 카테고리를 기준으로 사이트가 만들어 준 것이라 상품과 맞다.

  1순위  태그 탭 후보 (`tbody-tag`)          — 여기서만 고른다. 최소 1개
  대체   태그 후보가 하나도 없을 때만
         상품명 탭 후보에서 **1개만** 가져온다

데이터랩은 태그 소스가 아니다. 카테고리(cid)별 인기키워드와 조회수를 모아
`datalab_keyword` 에 쌓는 용도다. 카테고리가 틀리면 로하스 후보도 틀리므로
카테고리를 먼저 바로잡아야 한다 — 2026-09-05 모형 CCTV 상품이 'CCTV' 로
잡혀 있어 후보가 전부 진짜 CCTV 였다.

선택 순서
  1) 조회수 1000 미만 먼저 (권고라 하드 컷이 아니다 — 모자라면 그 이상도)
  2) 그 안에서 태그사전+추천(prio 3) -> 추천(2) -> 태그사전(1)
  3) 같은 등급에서는 조회수가 높은 것부터

상품별 분포 — 한 LCP 의 L코드에 같은 태그만 넣으면 그 LCP 가 가져가는
검색어가 10개로 끝난다. 후보가 넉넉하면 L코드들에 나눠 준다(`distribute`).

타사 브랜드·안 맞는 기능은 규칙으로 못 거른다. `lcp_product.brand/maker`
사전으로는 '하츠'(사전엔 '바이하츠' 만 있다)를 못 잡고 '국산' 같은 일반어는
오탐이 났다. 그 판단은 `gemini.filter_tags()` 에 맡긴다(선택).
"""
import re

from .. import db
from . import keywords, tabs

MAX_TAGS = tabs.MAX_TAGS       # 사이트 상한 10
LOW_VIEWS = 1000               # 로하스 지침 — 이 미만을 우선한다
TITLE_FALLBACK = 1             # 태그 후보가 0일 때 상품명에서 가져올 개수
MIN_SHARE = 3                  # 상품당 이만큼은 줄 수 있을 때만 나눈다
FILL_MIN = 5                   # 이보다 적으면 데이터랩 키워드로 채운다

# 브랜드 칸에 브랜드가 아닌 값이 많이 들어와 있다. 그대로 사전으로 쓰면
# '국산주방수전' 이 '국산' 때문에 걸리는 식으로 오탐이 난다 (2026-09-05 실측).
_NOT_BRAND = {
    "상세페이지참조", "상세설명참조", "상세참조", "자체제작", "자체", "본사",
    "기타", "없음", "해당없음", "국산", "국내", "중국", "해외몰", "휴대용",
    "건식", "하나", "문화", "플랜", "아크", "비트", "매표", "진성", "영동",
    "OEM", "ETC", "UNK", "제작", "수입", "직수입", "협력사", "미표기",
}
_BRAND_MIN = 3                 # 두 글자 이하는 일반어와 겹쳐 오탐이 많다


def brand_vocab() -> set:
    """
    DB 에 등록된 브랜드·제조사 이름 모음.

    태그 후보 표에는 다른 회사 제품명이 섞여 온다. 다만 이 사전만으로는
    부족하다 — 실제 타사 브랜드가 사전에 없을 수 있다. 보조 수단이다.
    """
    out = set()
    with db.sqlite_conn() as c:
        for col in ("brand", "maker"):
            for r in c.execute(
                    f"SELECT DISTINCT {col} v FROM lcp_product "
                    f"WHERE {col} IS NOT NULL AND {col} != ''"):
                v = (r["v"] or "").strip()
                if len(v) < _BRAND_MIN or v in _NOT_BRAND:
                    continue
                # '(주)아트사인' -> '아트사인' 처럼 법인 표기를 떼고도 담는다
                for form in {v, v.replace("(주)", "").replace("주식회사", "")
                             .replace("주", "").strip()}:
                    if len(form) >= _BRAND_MIN and form not in _NOT_BRAND:
                        out.add(form.upper())
    return out


def foreign_brand(name: str, own: str, vocab: set) -> str:
    """
    이 상품 것이 아닌 브랜드가 키워드에 들어 있으면 그 브랜드를 돌려준다.

    own 에는 이 상품의 브랜드·제조사·상품명을 넣는다. 상품명에 들어 있는
    이름은 이 상품 것이므로 통과시킨다.
    """
    up = (name or "").upper()
    own_up = (own or "").upper()
    for b in vocab:
        if b in up and b not in own_up:
            return b
    return ""


def own_words(lcp_code: str) -> str:
    """이 상품의 상품명·브랜드·제조사. 여기 들어간 이름은 통과시킨다."""
    with db.sqlite_conn() as c:
        r = c.execute("SELECT product_name, brand, maker FROM lcp_product "
                      "WHERE lcp_code = ?", (lcp_code,)).fetchone()
    if not r:
        return ""
    return " ".join(x for x in (r["product_name"], r["brand"], r["maker"]) if x)


def _pool(rows: list, exclude: set, own: str = "", vocab: set = None,
          dropped: list = None) -> list:
    """후보 행에서 쓸 수 있는 것만 남긴다."""
    out = []
    for r in rows:
        name = (r.get("name") or "").strip()
        if not name or name.upper() in exclude:
            continue
        if r.get("banned"):                 # 금지어 열이 채워진 행
            continue
        if not usable_tag(name):    # 금지어 / 색상 · 갯수 / 규격 아닌 숫자
            continue
        if vocab:
            b = foreign_brand(name, own, vocab)
            if b:
                if dropped is not None:
                    dropped.append(f"{name}({b})")
                continue
        out.append({"name": name,
                    "views": int(r.get("views") or 0),
                    "prio": int(r.get("prio") or 0)})
    return out


def _order(cands: list) -> list:
    """
    고르는 순서.

      1) 조회수 1000 미만만 쓴다. 1000 이상은 뒤로 밀어두고, 1000 미만이
         하나도 없을 때만 꺼내 쓴다 (로하스 지침)
      2) 그 안에서 태그사전+추천(3) -> 추천(2) -> 태그사전(1)
      3) 같은 등급이면 **조회수가 낮은 것부터**. 큰 키워드는 경쟁이 심해
         노출이 안 잡힌다 - 작은 것부터 가져가는 게 맞다 (사용자 2026-09-05)
    """
    low = [c for c in cands if c["views"] < LOW_VIEWS]
    high = [c for c in cands if c["views"] >= LOW_VIEWS]

    def key(c):
        return (-(c["prio"] or 0), c["views"] or 0)

    return sorted(low, key=key) + sorted(high, key=key)


def low_only(cands: list) -> list:
    """조회수 1000 미만만."""
    return [c for c in cands if c["views"] < LOW_VIEWS]


def distribute(pool: list, n_targets: int, want: int = MAX_TAGS) -> list:
    """
    정렬된 후보를 상품 수만큼 나눈다.

    나누는 게 늘 이득은 아니다. 후보가 상품 수보다 적으면 상품마다 1개씩만
    돌아가 오히려 나빠진다 — 쌀 상품(후보 4개 / L코드 20건)을 나눠봤더니
    20건이 태그 1개씩만 받았다(2026-09-05). 그래서 **상품당 최소 MIN_SHARE
    개는 줄 수 있을 때만** 나누고, 아니면 모두에게 같은 상위 목록을 준다.

        후보 48 / 상품  7  ->  나눔. 건당 7개, 중복 없음
        후보  4 / 상품 20  ->  안 나눔. 20건 모두 같은 4개

    나눌 때는 돌아가며 하나씩 집어 주므로 각 상품이 상위·하위를 고루 받는다.
    """
    if n_targets <= 0:
        return []
    if not pool:
        return [[] for _ in range(n_targets)]

    if len(pool) < n_targets * MIN_SHARE:
        share = pool[:want]
        return [list(share) for _ in range(n_targets)]

    out = [[] for _ in range(n_targets)]
    per = min(want, max(1, -(-len(pool) // n_targets)))   # 올림 나눗셈
    i = 0
    for c in pool:
        for _ in range(n_targets):                 # 자리가 빈 상품을 찾는다
            slot = out[i % n_targets]
            i += 1
            if len(slot) < per:
                slot.append(c)
                break
    return out


# ---- 상품 특성(규격·형태) ----
# 같은 LCP 라도 L코드마다 규격과 형태가 다르다. 실측 예 (LCP_LHA_B914621):
#   L6195091  아크릴 부착용 꽂이판 A4        -> A4
#   L6242709  아크릴 부착용 꽂이판 B7 3EA    -> B7
#   L2957858  양면 POP꽂이 pop스탠드         -> POP · 스탠드 · 양면
# 후보에도 'A4거치대' 'POP홀더' 'T자형스탠드' 처럼 특성이 박힌 것이 섞여 있다.
# 그 특성이 그 상품의 것이 아니면 붙이면 안 된다 — A4 상품에 'A5거치대' 를
# 다는 셈이 되기 때문이다.
# 숫자+단위형(100W, 30M, 1KG)은 저장된 태그를 학습해서 넣었다. 형제끼리
# 태그가 갈린 541종을 분석해보니 상위가 전부 규격이었다(2026-09-05).
#   100W 4회 · A4 4회 · 50M 2회 · 30M 2회 · 50W 1회
_SPEC_RE = re.compile(
    r"(A[0-9]|B[0-9]|[0-9]{2,}X[0-9]{2,}"
    # 갯수 단위(P·EA·매·입)는 규격이 아니라 수량이라 넣지 않는다.
    # 단어경계(\b)를 붙이면 안 된다 - '100W투광등' 처럼 뒤에 한글이 오면
    # 한글도 단어문자라 경계가 생기지 않아 못 잡는다.
    r"|[0-9]+(?:\.[0-9]+)?(?:KW|W|CM|MM|ML|KG|M|L|G|인치|구|단)(?![0-9])"
    r"|자석|자성|마그넷|투명|스탠드|거치형|벽걸이|천장|부착형|부착식"
    r"|T자형|양면|단면|접이식|회전|모니터|집게|걸이|흡착|압축"
    r"|대형|중형|소형|특대|미니|맞춤"
    # 겉모양 — 상품명에 없으면 그 상품이 그 모양인지 알 수 없다.
    # '사각' 이 목록에 없어서 원형 휴지통에 '사각휴지통' 이 붙었다(2026-09-05).
    r"|사각|정사각|직사각|원형|라운드|타원|삼각|육각|반원"
    r"|슬림|와이드|뚜껑|페달|스윙|오픈|밀폐|덮개|손잡이|바퀴"
    r"|무선|유선|충전식|건전지|전동|수동"
    # 재질 — 상품명에 없으면 그 재질인지 알 수 없다. 유리·대리석·알루미늄이
    # 빠져 있어서 대리석 선반에 '강화유리일자선반' 이 붙었다(2026-09-05).
    r"|스텐|스테인|스틸|우드|원목|실리콘|아크릴|고무|가죽|인조가죽"
    r"|강화유리|유리|인조대리석|대리석|알루미늄|세라믹|도자기|법랑|무쇠|주철"
    r"|황동|구리|주석|티타늄|플라스틱|PVC|ABS|PET|라탄|대나무|등나무"
    r"|패브릭|린넨|메탈|양은|타일|석재|한지|코르크)", re.I)

# 상품명과 태그의 낱말 겹침을 보려고 쓴다
_TOKEN_RE = re.compile(r"[A-Za-z]+[0-9]*|[0-9]+[A-Za-z]*|[가-힣]{2,}")


def words_of(text: str) -> set:
    """상품명·태그를 낱말로 쪼갠다 (2글자 이상)."""
    return {w.upper() for w in _TOKEN_RE.findall(text or "") if len(w) >= 2}


# 같은 것을 다르게 적는 말들. 하나로 묶어 비교한다 — '스테인리스 선반' 에
# '스텐욕실선반' 을, '유리 선반' 에 '강화유리일자선반' 을 못 붙이던 문제
# (2026-09-05 LCP_LHA_B914667).
_SYNONYM = {
    "스테인": "스텐", "스테인리스": "스텐", "스테인레스": "스텐",
    "스텐레스": "스텐", "스틸": "스텐", "메탈": "스텐",
    "강화유리": "유리",
    "인조대리석": "대리석",
    "원목": "우드", "대나무": "우드", "등나무": "우드", "라탄": "우드",
    "주철": "무쇠",
    "린넨": "패브릭",
    "정사각": "사각", "직사각": "사각",
    "라운드": "원형", "반원": "원형",
    "특대": "대형", "왕대": "대형",
    "미니": "소형", "슬림": "소형",
    "자성": "자석", "마그넷": "자석",
    "부착식": "부착형", "흡착": "부착형",
    "덮개": "뚜껑",
}


def specs_of(text: str) -> set:
    """
    상품명·키워드에서 규격·형태·재질을 뽑는다.

    같은 뜻인데 표기만 다른 것은 대표어로 모은다. '스테인리스' 와 '스텐' 이
    다르게 잡히면 재질이 같은데도 태그를 못 붙인다.
    """
    out = set()
    for m in _SPEC_RE.findall(text or ""):
        u = m.upper()
        out.add(_SYNONYM.get(u, u))
    return out


def spec_ok(cand_name: str, own_specs: set) -> bool:
    """
    후보에 박힌 특성이 이 상품의 것인가.

    후보에 특성이 없으면 누구에게나 쓸 수 있는 '공통' 이라 통과시킨다.
    특성이 있으면 그 상품 상품명에도 있어야 한다.
    """
    sp = specs_of(cand_name)
    if not sp:
        return True
    return bool(sp & own_specs)


def usable_tag(name: str) -> bool:
    """
    태그로 쓸 수 있는 말인가.

    `keywords.usable()` 은 숫자가 들어가면 무조건 뺀다('3개입' 같은 갯수를
    막으려는 규칙이다). 그런데 태그에서는 규격이 중요하다 — A4 상품에
    'A4거치대' 를 못 다는 건 손해다. 그래서 **규격 코드만 남은 숫자**면
    통과시킨다.

        A4거치대   -> A4 를 떼면 '거치대'  숫자 없음  -> 통과
        메모홀더210 -> 떼도 210 이 남는다            -> 제외
        3개입      -> 갯수 규칙에 걸린다             -> 제외
    """
    n = (name or "").strip()
    if not n or keywords.has_banned(n):
        return False
    if keywords.has_color(n) or keywords.has_count(n):
        return False
    if keywords.has_digit(n):
        return not keywords.has_digit(_SPEC_RE.sub("", n))
    return True


def dyn_key(cand: str, names: list, pool: list = None) -> str:
    """
    이 후보를 가르는 조각을 상품명들에서 찾는다.

    후보 이름의 부분 문자열 중, **일부 상품명에만 들어 있는** 가장 긴 것을
    돌려준다. 낱말 단위로 비교하면 놓친다 — '풍선펌프' 와 '손펌프포함' 은
    낱말로는 안 겹치지만 '펌프' 를 공유한다(2026-09-05 실측).

        풍선펌프 + [.. 손펌프포함 ..(5건).. 은박풍선세트 ..(4건)]  ->  '펌프'
        LED풍선  + [.. LED 생일파티 ..(1건).. 나머지 ..]          ->  'LED'
        풍선장식  + [전부 '풍선' 을 가짐]                          ->  ''(공통)

    돌려준 조각이 상품명에 없으면 그 상품에는 붙이지 않는다.
    """
    n = len(names)
    if n < 2 or not cand:
        return ""
    up = [nm.upper() for nm in names]
    c = cand.upper()
    # 후보 대부분에 들어 있는 조각은 머리말이지 수식어가 아니다.
    # '국자' 는 후보 38개 중 35개에 있는데, 까오기 상품 이름에 '국자' 가
    # 없다는 이유로 가르는 조각이 되어 그 상품들이 태그를 1개밖에 못 받았다
    # (2026-09-05 LCP_LHA_B914661). 휴지통 LCP 의 '쓰레기통' 도 같은 경우다.
    heads = set()
    if pool:
        names_up = [(x.get("name") or "").upper() for x in pool]
        half = max(2, len(names_up) // 2)
        for size in range(len(c), 1, -1):
            for i in range(len(c) - size + 1):
                sub = c[i:i + size]
                if sum(1 for x in names_up if sub in x) > half:
                    heads.add(sub)

    for size in range(len(c), 1, -1):          # 긴 조각부터 = 더 구체적
        for i in range(len(c) - size + 1):
            sub = c[i:i + size]
            if sub in heads:
                continue
            hits = sum(1 for nm in up if sub in nm)
            if 0 < hits < n:
                return sub
    return ""


def learn_specs(names: list) -> set:
    """
    이 LCP 안에서 **상품을 가르는 낱말**을 상품명들에서 직접 뽑는다.

    일부 상품에만 있고 전부에는 없는 낱말이 그것이다. 미리 적어둔 규격
    목록으로는 못 잡는 것을 잡는다 — 풍선 LCP 9건 중 5건만 '펌프' 가 든
    세트였는데, 나머지 4건에도 '풍선펌프' 태그가 붙었다(2026-09-05).

    한 상품에만 있는 고유명(브랜드·모델명)까지 걸리지만, 그런 낱말은
    후보 태그에 잘 안 나오므로 실제로는 문제가 되지 않는다.
    """
    sets = [words_of(n) for n in names if n]
    if len(sets) < 2:
        return set()
    everywhere = set.intersection(*sets)
    anywhere = set.union(*sets)
    return {w for w in anywhere - everywhere if len(w) >= 2}


def split_by_names(ordered: list, names: list) -> tuple:
    """
    후보를 (공통, 특성) 으로 가른다.

    특성 = 규격이 박혔거나, 상품명들 사이에서 갈리는 조각을 가진 것.
    각 특성 후보에는 그 조각(key)을 함께 달아 둔다.
    """
    common, special = [], []
    for c in ordered:
        # 규격·형태가 박혀 있으면 그것으로 판정한다. 다듬어진 목록이라
        # 상품명에서 찾아낸 조각보다 믿을 만하다.
        if specs_of(c["name"]):
            special.append({**c, "key": ""})
            continue
        key = dyn_key(c["name"], names, ordered)
        if key:
            special.append({**c, "key": key})
        else:
            common.append(c)
    return common, special


def assign_by_names(ordered: list, names: list, want: int = MAX_TAGS) -> list:
    """
    상품마다 '자기에게 맞는 태그' 를 준다. 상품명만 보고 규칙으로 정한다.

      1) 특성 태그 — 후보를 가르는 조각(규격이든 낱말이든)이 그 상품
         상품명에 있어야 준다.
             풍선펌프 -> '펌프' 가 든 상품에만
             A4코팅기 -> 'A4' 가 든 상품에만
      2) 공통 태그 — 상품명과 겹치는 것을 먼저, 나머지는 돌아가며 나눈다.
         같은 태그가 여러 상품에 몰리지 않게 교차로 배분한다.
    """
    n = len(names)
    if n <= 0:
        return []
    common, special = split_by_names(ordered, names)
    up = [(nm or "").upper() for nm in names]

    out = []
    for i in range(n):
        mine = []
        for c in special:
            sp = specs_of(c["name"])
            if sp:
                # 태그에 박힌 규격이 **전부** 상품명에 있어야 한다.
                # 하나만 겹쳐도 통과시키면 '휴지통20L' 이 2L 상품에 붙는다.
                ok = sp <= specs_of(names[i])
            else:
                key = c.get("key") or ""
                ok = bool(key) and key in up[i]
            if ok:
                mine.append(c)
        out.append(mine[:want])

    # 공통은 상품명과 겹치는 것 먼저
    for i, sh in enumerate(out):
        if len(sh) >= want:
            continue
        have = {c["name"] for c in sh}
        ow = words_of(names[i])
        for c in common:
            if len(sh) >= want:
                break
            if c["name"] not in have and (words_of(c["name"]) & ow):
                sh.append(c)
                have.add(c["name"])

    # 남은 공통은 돌아가며 (교차 배분)
    pos = 0
    for _ in range(len(common) * 2 + 2):
        if all(len(sh) >= want for sh in out):
            break
        moved = False
        for sh in out:
            if len(sh) >= want or not common:
                continue
            have = {c["name"] for c in sh}
            for j in range(len(common)):
                c = common[(pos + j) % len(common)]
                if c["name"] not in have:
                    sh.append(c)
                    pos = (pos + j + 1) % len(common)
                    moved = True
                    break
        if not moved:
            break

    # 하나도 못 받은 상품은 공통에서라도 채운다 (태그 최소 1개)
    for sh in out:
        if not sh:
            sh.extend((common or ordered)[:want])
    return out


def datalab_pool(cid: str, exclude: set, own: str = "", vocab: set = None,
                 top: int = 200, log=print) -> list:
    """
    데이터랩 인기키워드를 태그 후보 형태로 바꿔 돌려준다.

    ⚠️ **태그로 쓰지 않는다.** 카테고리 인기키워드라 그 카테고리에서 팔리는
    남의 상품 이름이 대부분이다 — 컵 상품에 '대한판촉컵', 기저귀에
    '디펜드성인기저귀특대형' 이 딸려 온다(2026-09-05 실측). 태그가 모자라면
    상품명 후보 표에서 채운다. 이 함수는 키워드 풀을 쌓는 용도다.

    prio 는 0 으로 둔다 — 태그사전·추천 표시가 없는 값이라 로하스 후보보다
    항상 뒤로 밀린다.
    """
    from . import datalab

    if not cid or not datalab.base():
        return []
    try:
        rows = datalab.category_keywords_with_views(cid, top=top, log=log)
    except Exception as e:
        log(f"  ! 데이터랩 조회 실패: {str(e)[:60]}")
        return []

    out = []
    for r in rows:
        name = (r.get("keyword") or "").strip()
        if not name or name.upper() in exclude:
            continue
        if not keywords.usable(name):
            continue
        if vocab and foreign_brand(name, own, vocab):
            continue
        out.append({"name": name, "views": int(r.get("views") or 0),
                    "prio": 0})
    return out


def top_up(shares: list, extra: list, fill_to: int = MAX_TAGS,
           trigger: int = FILL_MIN, names: list = None) -> int:
    """
    태그가 `trigger` 개 미만인 상품을 `extra` 로 `fill_to` 까지 채운다.

    보충분에도 같은 규칙을 건다 — **조회수 1000 미만만**, 그리고 **상품명이
    가르는 조각은 그 상품에 있어야** 한다. 안 그러면 펌프 없는 상품에
    '고무풍선펌프' 가 붙는다(2026-09-05 실측).

    상품마다 다른 것이 들어가야 그 LCP 가 가져가는 검색어가 넓어지므로
    하나씩 돌아가며 준다.
    """
    extra = low_only(extra)
    if not extra:
        return 0
    up = [(n or "").upper() for n in (names or [])]
    added = 0
    pos = 0                       # extra 를 어디까지 나눠줬는지
    for i, sh in enumerate(shares):
        if len(sh) >= trigger:
            continue
        have = {c["name"].upper() for c in sh}
        mine = up[i] if i < len(up) else ""
        tried = 0
        while len(sh) < fill_to and tried < len(extra):
            c = extra[pos % len(extra)]
            pos += 1
            tried += 1
            if c["name"].upper() in have:
                continue
            if up:
                key = dyn_key(c["name"], names)
                if key and key not in mine:
                    continue
            sh.append(c)
            have.add(c["name"].upper())
            added += 1
            tried = 0             # 하나 넣었으면 다시 셈한다
    return added


def ai_filter(lcp_code: str, ordered: list, log=print) -> list:
    """
    타사 브랜드·이 상품에 없는 기능을 AI 가 걸러낸다.

    규칙으로 안 되는 부분만 맡긴다. 실패하거나 전부 빼라고 하면 원래 목록을
    그대로 돌려준다 — 태그가 0개가 되는 쪽이 더 나쁘다.
    """
    from . import gemini

    if not ordered:
        return ordered
    with db.sqlite_conn() as c:
        p = c.execute("SELECT product_name, brand, maker FROM lcp_product "
                      "WHERE lcp_code = ?", (lcp_code,)).fetchone()
    res = gemini.filter_tags(
        (p["product_name"] if p else "") or "",
        [x["name"] for x in ordered],
        (p["brand"] if p else "") or "",
        (p["maker"] if p else "") or "", log=lambda *_: None)
    if not res["ok"] or not res["drop"]:
        return ordered
    bad = set(res["drop"])
    kept = [x for x in ordered if x["name"] not in bad]
    if not kept:
        return ordered
    log(f"  [태그] AI 제외 {len(bad)}개: " + ", ".join(sorted(bad)[:8]))
    return kept


def log_tag_work(row: dict, tags: list, source: str = "태그") -> None:
    """
    태그를 넣은 사실을 task_log 에 남긴다.

    나중에 '무엇을 자동으로 넣었는지' 를 사람이 훑어보려면 기록이 있어야
    한다. 사이트에서 다시 읽어도 사람이 넣은 것과 구분이 안 된다.
    """
    try:
        db.save_task_log({
            "folder_name": db.get_job_folder(),
            "lcp_code": row.get("lcp_code") or "",
            "l_code": row.get("l_code") or "",
            "product_no": str(row.get("product_no") or ""),
            "step": "태그",
            "action": "자동입력",
            "status": "ok",
            "picked": list(tags),
            "source": source,
            "message": "",
        })
    except Exception:
        pass          # 기록 실패가 저장을 막으면 안 된다


def plan_rows(session, rows: list, *, want: int = MAX_TAGS,
              overwrite: bool = False, use_ai: bool = False, log=print,
              should_stop=None) -> dict:
    """
    L코드별로 **넣을 태그를 정하기만** 한다. 저장은 하지 않는다.

    화면에서 사람이 눈으로 보고 고칠 수 있게 하려고 계획과 저장을 갈랐다.
    `save_plan()` 에 그대로 넘기면 저장된다.

    반환 {'rows': [{l_code, product_no, current, proposed, source}],
          'source', 'pool', 'dropped_brand', 'dropped_ai', 'mode'}
    """
    have, out = {}, []
    for r in rows:
        if should_stop and should_stop():
            break
        try:
            have[r["l_code"]] = tabs.fetch_saved_tags(session, r["product_no"])
        except Exception as e:
            have[r["l_code"]] = []
            log(f"  !! {r['l_code']} 조회 실패 {str(e)[:50]}")

    targets = [r for r in rows if overwrite or not have.get(r["l_code"])]
    base = {"rows": [], "source": "", "pool": 0, "dropped_brand": [],
            "dropped_ai": [], "mode": ""}
    for r in rows:                       # 대상이 아니어도 현황은 보여준다
        base["rows"].append({
            "l_code": r["l_code"], "product_no": r["product_no"],
            "current": [t["text"] for t in have.get(r["l_code"], [])],
            "proposed": [], "source": ""})
    if not targets:
        return base

    used = set()
    if not overwrite:
        for ts in have.values():
            for t in ts:
                used.add(t["text"].upper())

    head = targets[0]
    lcp = head.get("lcp_code") or ""
    own = own_words(lcp)
    vocab = brand_vocab()
    drop_brand = []

    pool_all = _pool(tabs.fetch_tag_rows(session, head["product_no"]),
                     set(), own, vocab, drop_brand)
    pool = [c for c in pool_all if c["name"].upper() not in used]
    source = "태그"
    if len(pool) < MIN_SHARE and pool_all:      # 형제와 같은 태그를 쓴다
        pool = pool_all
    if not pool:
        pool = _pool(tabs.fetch_title_rows(session, head["product_no"], 1),
                     used, own, vocab, drop_brand)
        source = "상품명"
        want = TITLE_FALLBACK
    if not pool:
        base["dropped_brand"] = drop_brand
        return base

    # 상품별 원상품명을 먼저 읽는다 (특성 판정과 AI 분리에 둘 다 필요하다)
    plan_names = []
    for r in targets:
        try:
            plan_names.append(
                tabs.fetch_attr(session, r["product_no"]).get("product_name", ""))
        except Exception:
            plan_names.append("")

    ordered = _order(pool)
    high = [c for c in ordered if c["views"] >= LOW_VIEWS]
    ordered = low_only(ordered)          # 1000 이상은 쓰지 않는다
    n_before = len(ordered)
    drop_ai = []
    if use_ai and source == "태그":
        # 규격 태그는 AI 에 묻지 않는다 (apply_to_rows 와 같은 이유)
        common0, special0 = split_by_names(ordered, plan_names)
        kept = ai_filter(lcp, common0, log=log) if common0 else []
        drop_ai = [c["name"] for c in common0
                   if c["name"] not in {k["name"] for k in kept}]
        ordered = _order(kept + special0)

    # 저장 경로와 같은 규칙으로 나눈다. 예전에는 여기서만 단순 분배를 써서
    # 미리보기와 저장 결과가 달랐다 — A3 코팅기에 'A4코팅기' 가 붙었다.
    _, special = split_by_names(ordered, plan_names)
    if special:
        shares = assign_by_names(ordered, plan_names, want)
    else:
        shares = distribute(ordered, len(targets), want)
    for sh in shares:                    # 하나도 못 받으면 1000 이상에서 예외로
        if not sh and high:
            sh.append(high[0])
    by_l = {r["l_code"]: s for r, s in zip(targets, shares)}
    for row in base["rows"]:
        share = by_l.get(row["l_code"])
        if share:
            row["proposed"] = [c["name"] for c in share]
            row["source"] = source
    base.update({"source": source, "pool": n_before,
                 "dropped_brand": drop_brand, "dropped_ai": drop_ai,
                 "mode": "분배" if len(shares[0]) < len(ordered) else "동일"})
    return base


def save_plan(session, plan_rows_: list, *, log=print, should_stop=None,
              progress=None) -> dict:
    """
    `plan_rows()` 결과(또는 사람이 화면에서 고친 것)를 저장한다.

    태그 문자열만 있으면 되고, 검색코드는 여기서 tag_search 로 받는다.
    """
    ok = fail = skip = 0
    saved = []
    todo = [r for r in plan_rows_ if r.get("proposed")]
    for i, r in enumerate(todo, 1):
        if should_stop and should_stop():
            log("[태그] 사용자 중단")
            break
        try:
            res = tabs.tag_search(session, r["product_no"], r["proposed"])
            codes = {t["text"].upper(): t["code"] for t in res["ok"]}
            for t in res["x"]:
                codes.setdefault(t["text"].upper(), -1)
            bad = {t["text"].upper() for t in res["restricted"]}
            payload = [{"text": n, "code": codes.get(n.upper(), -1)}
                       for n in r["proposed"] if n.upper() not in bad]
            if not payload:
                skip += 1
                log(f"  - {r['l_code']} 등록 가능한 태그가 없습니다")
                continue
            tabs.save_tags(session, r["product_no"], payload)
            got = tabs.fetch_saved_tags(session, r["product_no"])
            if len(got) == len(payload):
                ok += 1
                saved.append(r)
                log_tag_work(r, [t["text"] for t in payload],
                             r.get("source") or "태그")
                log(f"  + {r['l_code']} " + ", ".join(t["text"] for t in payload))
            else:
                fail += 1
                log(f"  !! {r['l_code']} 저장 {len(got)}/{len(payload)}개")
        except Exception as e:
            fail += 1
            log(f"  !! {r['l_code']} {str(e)[:70]}")
        if progress:
            progress(i, len(todo))
    return {"ok": ok, "fail": fail, "skip": skip, "saved": saved}


def apply_to_rows(session, rows: list, *, want: int = MAX_TAGS,
                  overwrite: bool = False, use_ai: bool = False,
                  fill_more: bool = False, fill_to: int = MAX_TAGS,
                  log=print, should_stop=None, progress=None) -> dict:
    """
    한 LCP 의 L코드들에 태그를 넣는다.

    후보는 그 LCP 의 대표 한 건에서 읽는다. 같은 LCP 는 색·크기만 다른
    같은 상품이라 후보 표가 같다. 사람이 이미 달아둔 태그는 후보에서 빼고
    남은 것을 빈 L코드들에 나눠 준다.
    """
    ok = fail = skip = 0
    saved, picks = [], {}

    # 지금 상태를 먼저 읽는다. 사람이 손으로 넣어둔 것을 덮지 않기 위해서다.
    have = {}
    for r in rows:
        try:
            have[r["l_code"]] = tabs.fetch_saved_tags(session, r["product_no"])
        except Exception as e:
            have[r["l_code"]] = []
            log(f"  !! {r['l_code']} 조회 실패 {str(e)[:50]}")

    targets = [r for r in rows if overwrite or not have[r["l_code"]]]
    skip += len(rows) - len(targets)
    if not targets:
        return {"ok": 0, "fail": 0, "skip": skip, "saved": [], "picks": {}}

    # 형제가 이미 쓰고 있는 태그는 빼고 나눈다 (덮어쓰기면 전부 다시 나눈다).
    used = set()
    if not overwrite:
        for ts in have.values():
            for t in ts:
                used.add(t["text"].upper())

    head = targets[0]
    lcp = head.get("lcp_code") or ""
    own = own_words(lcp)
    vocab = brand_vocab()
    drop_brand = []

    raw = tabs.fetch_tag_rows(session, head["product_no"])
    pool_all = _pool(raw, set(), own, vocab, drop_brand)   # 형제가 쓰는 것 포함
    pool = [c for c in pool_all if c["name"].upper() not in used]
    source = "태그"
    if drop_brand:
        log(f"  [태그] 타사 브랜드 제외 {len(drop_brand)}개: "
            + ", ".join(drop_brand[:6]))

    # 형제를 빼고 나니 남는 게 없다 = 형제가 쓸 만한 후보를 다 쓰고 있다는 뜻.
    # 이럴 때 상품명으로 떨어지면 안 된다. 실제로 튀김바스켓 LCP 가 그렇게
    # 해서 5건이 태그 1개씩만 받았다(2026-09-05). 같은 상품이니 형제가 쓰는
    # 태그를 그대로 쓰는 편이 맞다.
    if len(pool) < MIN_SHARE and pool_all:
        if not pool:
            log(f"  [태그] 새 후보 없음 - 형제와 같은 태그를 씁니다")
        pool = pool_all
    if not pool:
        # 태그 후보 표 자체가 비었을 때만 상품명에서 1개를 가져온다.
        pool = _pool(tabs.fetch_title_rows(session, head["product_no"], 1),
                     used, own, vocab, drop_brand)
        source = "상품명"
        want = TITLE_FALLBACK
        log(f"  [태그] 태그 후보 표가 비었습니다 - 상품명에서 "
            f"{TITLE_FALLBACK}개만 씁니다")
    if not pool:
        log("  - 넣을 태그가 없습니다")
        return {"ok": 0, "fail": 0, "skip": skip + len(targets),
                "saved": [], "picks": {}}

    # 상품마다 규격·형태가 다르다. 각자의 원상품명을 먼저 읽는다.
    names = []
    for r in targets:
        try:
            names.append(
                tabs.fetch_attr(session, r["product_no"]).get("product_name", ""))
        except Exception:
            names.append("")

    ordered = _order(pool)
    if use_ai and source == "태그":
        # AI 에는 **공통 후보만** 물어본다.
        #
        # 규격이 박힌 후보는 LCP 대표 상품명 하나로 판단하면 안 된다. 실제로
        # 메모꽂이 LCP 의 대표 상품명이 A3 라서 AI 가 'A4메모홀더' 를 틀렸다고
        # 뺐는데, 그 LCP 안에는 A4 상품이 둘 있었다(2026-09-05). 규격은
        # L코드별 원상품명과 대조하는 규칙이 판단한다 - AI 가 볼 자리가 아니다.
        common0, special0 = split_by_names(ordered, names)
        kept = ai_filter(lcp, common0, log=log) if common0 else []
        ordered = _order(kept + special0)

    high = [c for c in ordered if c["views"] >= LOW_VIEWS]
    ordered = low_only(ordered)          # 1000 이상은 쓰지 않는다
    common, special = split_by_names(ordered, names)
    if special:
        shares = assign_by_names(ordered, names, want)
        mode = f"특성별 (공통 {len(common)} / 특성 {len(special)})"
        keys = sorted({c["key"] for c in special if c.get("key")})
        if keys:
            log(f"  [태그] 상품명이 가르는 조각: {', '.join(keys[:10])}")
    else:
        shares = distribute(ordered, len(targets), want)
        mode = "분배" if len(shares[0]) < len(ordered) else "동일"
    log(f"  [태그] {source} 후보 {len(ordered)}개 -> {len(targets)}건에 {mode}")

    # 상품명 후보로 채우지 않는다.
    #
    # 한때 '태그가 5개 미만이면 상품명 후보로 채운다' 를 넣었다가 되돌렸다.
    # 규칙은 처음부터 분명했다 — **태그 후보 표가 비었을 때만 상품명에서
    # 1개**다(위 source 판정). 상품명 탭 후보는 상품명에 쓰라고 주는 목록이지
    # 태그가 아니다. 5개 미만이라고 거기서 끌어오면 태그 표에 없는 말이
    # 태그로 들어간다(2026-09-05 LCP_LHA_B914646 에서 사용자가 지적).
    #
    # 후보가 적으면 적은 대로 둔다. 태그 수를 채우려고 규칙을 깨지 않는다.

    for i, (r, share) in enumerate(zip(targets, shares), 1):
        if should_stop and should_stop():
            log("[태그] 사용자 중단")
            break
        try:
            res = tabs.tag_search(session, r["product_no"],
                                  [c["name"] for c in share])
            codes = {t["text"].upper(): t["code"] for t in res["ok"]}
            for t in res["x"]:
                codes.setdefault(t["text"].upper(), -1)
            bad = {t["text"].upper() for t in res["restricted"]}

            payload = [{"text": c["name"],
                        "code": codes.get(c["name"].upper(), -1)}
                       for c in share if c["name"].upper() not in bad]
            if not payload:
                # 전부 등록 불가면 상위 후보에서 하나라도 채운다.
                for c in ordered:
                    if c["name"].upper() in bad:
                        continue
                    payload = [{"text": c["name"], "code": -1}]
                    break
            if not payload:
                skip += 1
                log(f"  - {r['l_code']} 등록 가능한 태그가 없습니다")
                continue

            tabs.save_tags(session, r["product_no"], payload)
            got = tabs.fetch_saved_tags(session, r["product_no"])
            if len(got) == len(payload):
                ok += 1
                saved.append(r)
                picks[r["l_code"]] = {"tags": share, "source": source}
                log_tag_work(r, [t["text"] for t in payload], source)
                log(f"  + {r['l_code']} [{source}] "
                    + ", ".join(t["text"] for t in payload))
            else:
                fail += 1
                log(f"  !! {r['l_code']} 저장 {len(got)}/{len(payload)}개")
        except Exception as e:
            fail += 1
            log(f"  !! {r['l_code']} {str(e)[:70]}")
        if progress:
            progress(i, len(targets))

    return {"ok": ok, "fail": fail, "skip": skip, "saved": saved,
            "picks": picks}
