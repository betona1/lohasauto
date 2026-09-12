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
import collections
import re

from .. import db
from . import keywords, tabs

_WORD_RE = re.compile(r"[0-9A-Za-z가-힣]+")

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


def common_words(names: list, ratio: float = 1.0) -> list:
    """
    그 LCP 상품명들에 **공통으로 들어간 낱말**. 그 물건이 무엇인지를 말한다.

    배수구 트랩 9종의 상품명에 전부 '트랩' 이 있는데 상품명에서 빠지면
    말이 안 된다(2026-09-06 사용자 지침). 이런 말은 될 수 있으면 넣는다.

    `ratio` 는 몇 할의 상품명에 있어야 공통으로 볼지다(1.0 = 전부).
    """
    if not names:
        return []
    seen = collections.Counter()
    for nm in names:
        for w in set(_WORD_RE.findall(nm or "")):
            if len(w) >= 2 and not w.isdigit():
                seen[w] += 1
    need = max(2, int(len(names) * ratio))
    out = [w for w, n in seen.most_common() if n >= need]
    # 긴 말을 앞에 둔다 - '배수구트랩' 이 '트랩' 보다 낫다
    out.sort(key=len, reverse=True)
    return out


# 카테고리 이름 끝에 붙는 **품목 이름**. 여기 있는 것만 꼬리로 쓴다.
_ITEM_TAILS = {
    "인형", "시계", "스티커", "케이블", "블록", "블럭", "앞치마", "가방",
    "의자", "침대", "선반", "매트", "쿠션", "방석", "커버", "시트지",
    "정리함", "수납함", "바구니", "휴지통", "디스펜서", "그릇", "컵",
    "액자", "조명", "화분", "행거", "옷걸이", "거치대", "받침대", "필터",
}


def head_words(names: list, cid: str = "") -> list:
    """
    그 LCP 의 **품목 이름**. 꾸밈말 판정(`modifier_ok`)의 기준이다.

    전부에 든 낱말 → 절반에 든 낱말 → 카테고리 이름 순으로 찾는다.
    '전부에 들어야 한다' 고만 하면 상품명이 제각각인 LCP 에서 빈 목록이
    나오고, 그러면 꾸밈말 판정이 통째로 헛돈다 — 기린·사자 인형에
    '아보카도인형' 이 그냥 붙었다(2026-09-09 LCP_LHA_B915473).
    """
    out = []
    for ratio in (1.0, 0.5):
        got = common_words(names or [], ratio)
        if got:
            out = list(got)
            break
    # 공통 낱말을 찾았어도 **카테고리 이름을 같이 넣는다.** 공통 낱말이
    # '동물인형' 뿐이면 '물개인형' 은 품목 이름을 안 품은 것으로 보여
    # 판정에서 빠진다 - 사자 인형에 '물개·녹음·모찌' 가 붙었다
    # (2026-09-09 LCP_LHA_B915483).
    leaf = str(cat_name_of(cid) if cid else "").split("/")[-1].strip()
    leaf_pieces = [p for p in leaf.replace("/", " ").split() if p]
    for p in leaf_pieces:
        if p not in out:
            out.append(p)
    # '봉제인형' 의 '인형' 도 품목 이름이다. 이것까지 넣어야 '아보카도인형'
    # 이 '꾸밈말 + 품목' 꼴로 보여 판정 대상이 된다.
    # 꼬리는 **카테고리 이름에서만** 잘라 낸다. LCP 공통 낱말에서 자르면
    # '프리미엄블럭' 이 '블럭' 을 낳고, 그 순간 '블럭놀이·호환블럭·조립블럭'
    # 이 전부 꾸밈말 판정 대상이 된다. 원상품명이 'f04 프리미엄블럭(PB9013)'
    # 처럼 부실하면 근거가 없어 통째로 잘린다
    # (2026-09-09 LCP_LHA_B915484 - 53개 후보 중 6개만 남았다).
    # 꼬리는 **뜻이 있는 낱말**이어야 한다. 두 글자만 잘라내면 '데코스티커'
    # 에서 '티커' 같은 조각이 나오고, 그 순간 '벽지스티커·벽스티커' 가 전부
    # 꾸밈말 판정 대상이 된다(2026-09-09 LCP_LHA_B915564).
    # 세 글자부터 잘라 보고, 아는 품목 이름일 때만 쓴다.
    for p in [x for x in out if x in leaf_pieces]:
        for k in (3, 2):
            tail = p[-k:]
            if len(p) > k and tail in _ITEM_TAILS and tail not in out:
                out.append(tail)
                break
    return out or list(common_words(names or [], 0.5))


def child_names(session, rows: list) -> list:
    """그 LCP L코드들의 원상품명. 특성·같은종류 판정에 모두 쓴다."""
    out = []
    for r in rows:
        try:
            out.append(tabs.fetch_attr(
                session, r["product_no"]).get("product_name", "") or "")
        except Exception:
            out.append("")
    return out


def own_words(lcp_code: str) -> str:
    """이 상품의 상품명·브랜드·제조사. 여기 들어간 이름은 통과시킨다."""
    with db.sqlite_conn() as c:
        r = c.execute("SELECT product_name, brand, maker FROM lcp_product "
                      "WHERE lcp_code = ?", (lcp_code,)).fetchone()
    if not r:
        return ""
    return " ".join(x for x in (r["product_name"], r["brand"], r["maker"]) if x)


def _same_onset(a: str, b: str) -> bool:
    """두 한글 음절의 첫소리가 같은가. 저/져 는 같고, 함/포 는 다르다."""
    def onset(ch):
        n = ord(ch) - 0xAC00
        return n // 588 if 0 <= n < 11172 else -1
    oa, ob = onset(a), onset(b)
    return oa >= 0 and oa == ob


# 상품의 **특징**이 아니라 쓰는 사람·쓰는 곳·느낌을 말하는 말들.
# 이런 것은 원상품명에 없어도 쓸 수 있다. 나머지 꾸밈말(지퍼·보냉·방수·색상
# 처럼 그 상품에 실제로 있어야 하는 것)은 원상품명에 있어야 한다
# (2026-09-06 사용자 지시: "해당상품의 특징 같은것은 상품명에 있어야만").
ALLOW_MOD = {
    "여성", "여자", "남성", "남자", "학생", "아이", "어린이", "유아", "성인",
    # 누가 쓰는가 — 인형·완구에서 특히 흔하다. 이게 빠져 있어서
    # '남아애착인형'·'애기애착인형' 이 근거 없음으로 잘렸다(2026-09-09).
    "남아", "여아", "남아용", "여아용", "여자아이", "남자아이",
    "애기", "아기", "아기용", "키즈", "신생아", "돌", "돌쟁이", "유아용",
    "초등", "중학생", "고등학생", "청소년", "커플", "친구", "조카",
    # 인형을 어떻게 쓰는가 — 어느 인형에나 해당한다. 이게 없어서
    # '애착인형' 이 근거 없음으로 잘렸다(2026-09-09 LCP_LHA_B915483).
    "애착", "수면", "포옹", "안는", "안고자는", "잠자는", "돌잡이",
    "가정용", "업소용", "사무용", "사무실", "가정", "원룸", "자취", "혼자",
    "인테리어", "휴대용", "다용도", "실내", "실외", "야외",
    "예쁜", "이쁜", "귀여운", "감성", "데일리", "기본", "인기", "추천",
    "선물", "답례품", "단체", "생활", "주방", "욕실", "화장실", "거실",
}


# 원단·형태 — 보통은 원상품명에 있어야 쓴다. 다만 **패션잡화처럼 원상품명이
# 부실한 카테고리**에서는 이것까지 막으면 쓸 말이 남지 않는다. 그런 카테고리는
# `cat_rule` 에 loose 로 등록해 이 목록만 풀어준다(2026-09-06 사용자 지시).
SOFT_SPECS = {
    "데님", "청지", "광목", "황마", "마직", "캔버스", "니트", "나일론",
    "부직포", "코튼", "면", "린넨", "폴리", "모직", "스웨이드", "벨벳",
    "메쉬", "매쉬", "타포린", "가죽", "인조가죽",
    "접이식", "양면", "단면", "슬림", "와이드", "대형", "중형", "소형",
    "미니", "특대", "손잡이", "지퍼", "포켓", "주머니",
}


def loosen(specs: set, loose: set) -> set:
    """느슨한 카테고리에서는 원단·형태를 규격에서 뺀다. 수량·색상은 그대로."""
    if not loose or "spec" not in loose:
        return specs
    return {x for x in specs if x not in SOFT_SPECS}


def modifier_ok(name: str, base: str, common: list) -> bool:
    """
    후보에서 **품목 이름을 뺀 나머지(꾸밈말)** 가 원상품명에 있는가.

        보냉에코백  -> '에코백' 은 이 LCP 품목  -> 꾸밈말 '보냉'
        '보냉' 이 원상품명에 없으면 못 쓴다 - 보냉인지 알 수 없기 때문이다.

    쓰는 사람·쓰는 곳을 말하는 꾸밈말(여성·업소용·인테리어)은 예외다.
    품목 이름을 못 찾으면(공통 낱말이 없으면) 판정하지 않는다.
    """
    if not common:
        return True
    # 품목 이름을 아예 안 품은 후보는 '꾸밈말+품목' 꼴이 아니다. 다른 이름
    # (강냉이의 '튀밥' 같은 딴이름)일 수 있으므로 여기서 판단하지 않는다.
    # 이걸 빼먹어서 '튀밥' 이 특징 근거없음으로 잘렸다(2026-09-06).
    if not any(w in name for w in common):
        return True
    rest = name
    for w in sorted(common, key=len, reverse=True):
        rest = rest.replace(w, " ")
    b = (base or "")
    for tok in _WORD_RE.findall(rest):
        if len(tok) < 2:
            continue
        if tok in b or tok in ALLOW_MOD:
            continue
        if any(tok in w or w in tok for w in ALLOW_MOD):
            continue
        # 꾸밈말이 두 낱말이 붙은 꼴일 수 있다 - '토끼애착인형' 에서 품목
        # '인형' 을 떼면 '토끼애착' 이 남는데, 그 안의 '토끼' 는 원상품명에
        # 있다. 통째로만 견주면 맞는 태그가 막힌다(2026-09-09).
        if any(tok[i:i + k] in b
               for k in range(2, len(tok) + 1)
               for i in range(0, len(tok) - k + 1)):
            continue
        return False
    return True


def _is_cat_word(name: str, cat_name: str) -> bool:
    """
    카테고리 이름과 같은 말인가. 네이버는 이런 말을 태그로 받지 않는다.

    마지막 칸이 '팝콘/강냉이류' 처럼 여러 말을 담고 있으면 조각도 본다 —
    '팝콘'·'강냉이' 둘 다 안 들어가고 '튀밥' 만 들어갔다(2026-09-06 실측).
    """
    leaf = (cat_name or "").split("/")[-1].strip() if "/" not in cat_name         else cat_name
    words = set()
    for part in (cat_name or "").replace("/", " ").split():
        p = part.strip()
        if not p:
            continue
        words.add(p)
        if p.endswith("류") and len(p) > 2:
            words.add(p[:-1])
    n = (name or "").strip()
    return n in words


_NUM_RE = re.compile(r"[0-9]+(?:\.[0-9]+)?")


# '1+1' '2+1' 같은 **행사 표기**. 상품의 성질이 아니라 그때그때의 판촉이라
# 상품명·태그에 넣지 않는다(2026-09-07 사용자: "숫자 1+1 이런건 넣치말고 빼줘").
_PROMO_RE = re.compile(r"[0-9]+\s*\+\s*[0-9]+")


def promo_ok(name: str) -> bool:
    """행사 표기가 없으면 True. 있으면 쓰지 않는다."""
    return not _PROMO_RE.search(name or "")


def numbers_ok(name: str, base: str) -> bool:
    """
    후보에 박힌 **숫자**가 그 상품 이름에도 있는가.

    '스위트콘340' 을 3.1Kg 짜리에 붙이면 안 된다. 340g 짜리에만 쓴다.
    후보 목록은 조회수 순이라 그냥 첫 번째를 쓰면 남의 용량이 붙는다
    (2026-09-06 사용자 지적: "숫자 들어간 검색 키워드는 주의해서 넣을 것").

    '1+1' 같은 행사 표기는 **원상품명에 있어도** 넣지 않는다 - 판촉 문구지
    상품의 성질이 아니다(2026-09-07 사용자 지적: 생강차 '라떼 1+1').
    """
    if not promo_ok(name):
        return False
    b = base or ""
    for n in _NUM_RE.findall(name or ""):
        if n not in b:
            return False
    return True


def head_ok(name: str, own: str) -> bool:
    """
    후보의 **끝말(핵심 명사)** 이 그 LCP 상품명 어딘가에 있는가.

    후보 표에는 같은 카테고리의 **다른 물건**이 섞여 온다. 물걸레 청소포
    LCP 에 '대걸레탈수기' '마루왁스' '마루코팅제' 가 붙었다(2026-09-05).
    청소포와 탈수기는 아예 다른 물건이라 검색이 잘못 걸린다.

    끝에서 2~4글자 중 하나라도 그 LCP 상품명에 있으면 같은 종류로 본다.
      대걸레탈수기 -> 수기/탈수기/레탈수기 : 없음 -> 뺀다
      일회용청소포 -> 소포/청소포           : 있음 -> 쓴다
      걸레티슈     -> 티슈                  : 있음(물티슈) -> 쓴다
    상품명을 모르면(빈 문자열) 판단하지 않는다.
    """
    if not own:
        return True
    t = re.sub(r"[^0-9A-Za-z가-힣]", "", name)
    o = re.sub(r"[^0-9A-Za-z가-힣]", "", own).upper()
    if not t or not o:
        return True
    for k in (2, 3, 4):
        if len(t) < k:
            break
        tail = t[-k:].upper()
        if tail in o:
            return True
        # '도어클로져' 와 '도어클로저' 처럼 한 글자만 다른 표기도 같은 종류다.
        # 단 **첫소리(초성)가 같을 때만** — 저/져 는 같은 말이지만
        # 청소함/청소포 는 다른 물건이다(함 ㅎ, 포 ㅍ).
        if k >= 3:
            for i in range(len(o) - k + 1):
                diff = [(a, b) for a, b in zip(tail, o[i:i + k]) if a != b]
                if not diff:
                    return True
                if len(diff) == 1 and _same_onset(*diff[0]):
                    return True

    # 합성어 — 그 LCP 상품명의 낱말을 품고 있고, 끝 글자가 상품명 낱말의
    # 끝 글자와 같으면 같은 종류로 본다.
    #   일회용걸레포 : '걸레' 를 품고 끝이 '포'(청소포) -> 같은 종류
    #   대걸레탈수기 : '걸레' 는 품었지만 '기' 로 끝나는 낱말이 없다 -> 아니다
    words = [w for w in re.findall(r"[0-9A-Za-z가-힣]+", own or "")
             if len(w) >= 2]
    tu = t.upper()
    share = any(w.upper()[i:i + 2] in tu
                for w in words for i in range(len(w) - 1))
    if share and any(w[-1] == t[-1] for w in words):
        return True
    return False


def rules_for(cid) -> dict:
    """
    그 카테고리에 쌓인 사용자 지침. **태그도 상품명과 같은 규칙을 받는다.**

    예전에는 `_pool` 이 `rules` 를 받을 수 있는데도 부르는 쪽에서 안 넘겨,
    "카테고리별로 저장해 다음에 활용하라" 던 지침이 상품명에만 걸렸다
    (2026-09-07 보이차 태그에 운남성·고수차·숙차가 그대로 남았다).
    """
    try:
        from .. import db as _db

        return _db.cat_rules(str(cid or ""), "tag")
    except Exception:
        return {"ban": set(), "need_name": set(), "must": [], "loose": set()}


def cat_name_of(cid) -> str:
    try:
        from .. import db as _db

        return _db.category_name(str(cid or "")) or ""
    except Exception:
        return ""


def _pool(rows: list, exclude: set, own: str = "", vocab: set = None,
          dropped: list = None, kin: str = None, common: list = None,
          rules: dict = None, cat_name: str = "") -> list:
    """
    후보 행에서 쓸 수 있는 것만 남긴다.

    `kin` 은 같은 종류인지 재는 기준 글(그 LCP 의 L코드 원상품명 전부).
    아니라고 판정된 것은 버리지 않고 `kin=False` 로 표시해 **맨 뒤로 민다** —
    쓸 게 없을 때까지 안 쓰지만, 태그 수가 모자라면 쓴다.
    """
    out = []
    for r in rows:
        name = (r.get("name") or "").strip()
        if not name or name.upper() in exclude:
            continue
        if r.get("banned"):                 # 금지어 열이 채워진 행
            continue
        if not usable_tag(name):    # 금지어 / 색상 · 갯수 / 규격 아닌 숫자
            # 금지어라도 그 말이 이 LCP 상품명에 있으면 쓴다
            if not ban_exempt(name, (kin if kin is not None else own) or ""):
                continue
        # 네이버는 **카테고리 이름 자체**를 태그로 안 받는다. 그래서 에코백
        # 카테고리의 태그사전에 '에코백' 이 없고 오타 '애코백' 만 있었다
        # (2026-09-06 사용자 확인).
        if cat_name and _is_cat_word(name, cat_name):
            continue
        base_txt = (kin if kin is not None else own) or ""
        if not numbers_ok(name, base_txt):
            if dropped is not None:
                dropped.append(f"{name}(숫자 안 맞음)")
            continue
        # 그 상품에 실제로 있어야 하는 특징(지퍼·보냉·색상)은 원상품명에 있을 때만
        if not modifier_ok(name, base_txt, common or []):
            if dropped is not None:
                dropped.append(f"{name}(특징 근거없음)")
            continue
        if rules:
            if any(w in name and w not in base_txt
                   for w in rules.get("need_name") or ()):
                continue
            if any(w in name and w not in own for w in rules.get("ban") or ()):
                continue

        if vocab:
            b = foreign_brand(name, own, vocab)
            if b:
                if dropped is not None:
                    dropped.append(f"{name}({b})")
                continue
        out.append({"name": name,
                    "views": int(r.get("views") or 0),
                    "prio": int(r.get("prio") or 0),
                    # 이 LCP 와 같은 종류인가 (청소포 LCP 의 '마루왁스' = False).
                    # 그 LCP 공통 낱말을 품었으면 표기만 다른 같은 물건이다 —
                    # '점보롤통'·'점보롤카바' 를 걸러내면 안 된다(2026-09-06)
                    "kin": (any(w in name for w in (common or []))
                            or head_ok(name, kin if kin is not None else own))})
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
        # 같은 종류 먼저 -> 태그사전+추천 먼저 -> 조회수 낮은 것 먼저
        return (0 if c.get("kin", True) else 1,
                -(c["prio"] or 0), c["views"] or 0)

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
    # 수량(P·EA·매·입·개입)도 규격으로 본다. 넣지 않았더니 5P 짜리에
    # '10개입' 이 붙고 한 상품명 안에 '5P 10개입' 이 같이 들어갔다
    # (2026-09-05 LCP_LHA_B914790). 수량이 틀리면 반품 사유다.
    r"|[0-9]+(?:개입|매입|장입|스틱|켤레|개|매|입|팩|봉|포|정|캔|병|롤"
    r"|권|족|P|EA)(?![0-9])"
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
    r"|무선|유선|충전식|건전지|전동|수동|펌프|리필|거치대|받침대"
    # 식품의 **제형** — 티백인지 가루인지는 상품마다 다르다. 티백 상품에
    # '보이차분말' 태그가 붙었다(2026-09-07 사용자 지적). 원상품명에 있는
    # 제형만 쓴다.
    r"|티백|분말|가루|추출분말|스틱|액상|원액|과립|캡슐|타블렛"
    r"|(?<![변교순일상반전환])환(?![가-힣])"
    r"|볶은|덖은|발효|건조|생차|숙차|잎차|고형"
    # '환'(한약 환)은 한 글자라 '변환케이블'·'교환' 안에서도 잡혔다.
    # 앞글자로 가른다 - '홍삼환' 은 제형이 맞고 '변환' 은 아니다 (2026-09-08).
    # 고정이냐 이동이냐 — 한 상품명에 '고정식 ... 이동식' 이 같이 들어갔다
    # (2026-09-06 L2104632). 상품명에 있는 쪽만 쓴다.
    r"|고정식|이동식|이동형|고정형|무타공|타공|벽부착|자립형"
    # 앞치마의 끈 모양. 같은 상품에 'H앞치마' 와 'X형앞치마' 가 같이 붙어
    # 있었다 - 둘은 다른 모양이다 (2026-09-09 LCP_LHA_B915656).
    r"|H형|X형|H앞치마|X앞치마|목걸이형|목끈형|허리형|랩앞치마|반앞치마"
    # 기능 — 상품명에 없으면 그 기능이 있는지 알 수 없다. LED 아닌 샤워기에
    # 'LED샤워기' 가, 필터 없는 것에 '정수' 가 붙었다(2026-09-05 사용자 지적).
    r"|LED|led|ONOFF|온오프|정수|필터|절수|마사지|무드등|온도계|자동잠금"
    # 원산지·인증·효능 주장 — 상품명에 없으면 사실인지 알 수 없다.
    # 중국산 샤워기에 '국내산' 이 붙었다(2026-09-05 사용자 지적).
    # 색상 — 상품명에 없으면 그 색인지 알 수 없다.
    r"|블랙|화이트|골드|실버|핑크|그레이|네이비|레드|블루|그린"
    r"|베이지|브라운|아이보리|크롬|투톤|무광|유광"
    # 설치 자리 — 다른 물건을 가리킨다. 욕실 샤워기에 '싱크대' 가 붙었다.
    r"|싱크대|씽크대|세면대|욕조|변기|비데|현관문|방문"
    r"|화장대|화장품|옷장|신발장|냉장고|서랍장|침대|베란다"
    # 놓는 자리·가구 이름. 부품함에 '책장 선반 드레스룸 슬라이딩' 이 붙었다
    # (2026-09-08 LCP_LHA_B915822).
    r"|책장|책상|선반|드레스룸|붙박이|슬라이딩|수납장|테이블|식탁|벽면"
    r"|욕실|주방|거실|침실|현관|다용도실|세탁실|창고"
    # **무엇을 담는 물건인가**. 부품함에 '셋톱박스·약·옷·팬트리·책가방'
    # 이 붙었다(2026-09-08 LCP_LHA_B915822). 담는 대상이 다르면 다른 상품이다.
    r"|셋톱박스|리모컨|팬트리|펜트리|책가방|학용품|문구|서류|여권|통장"
    r"|약통|약|공구|볼트|나사|피스|양말|속옷|수건|기저귀|젖병|반찬|김치"
    r"|장난감|완구|토이|케이블|공구함|잡화|택배|의약품|옷정리|옷수납|신발정리"
    # 탈것 — 블록·완구는 **무엇을 만드는 것인지**가 상품 그 자체다.
    # 경찰 경비정 블록에 '비행기 자동차 소방차' 가 다 붙었다
    # (2026-09-09 사용자 지적 LCP_LHA_B915476). 긴 것을 먼저 적는다.
    r"|수상비행기|비행기|헬리콥터|헬기|소방차|경찰차|구급차|救急|경비정"
    r"|자동차|레이싱카|스포츠카|스쿨버스|버스|트럭|덤프|포크레인|굴착기"
    r"|기차|기관차|전철|지하철|오토바이|자전거|탱크|잠수함|요트|페리|보트"
    r"|우주선|로켓|드론|트랙터|불도저|크레인|사다리차|캠핑카|택시|열차"
    r"|부품|피스|서랍형|서랍식|서랍장"
    # 쓰는 곳이 특정 기관인 말. 옷걸이에 '교실' 은 어울리지 않는다
    # (2026-09-06 사용자 지침). 상품명에 있을 때만 쓴다.
    r"|교실|교사용|학교|유치원|어린이집|병원|기숙사|독서실"
    # 목욕바구니를 헬스장에 들고 가지는 않는다(2026-09-06 사용자 지침)
    r"|헬스장|헬스|체육관|수영장|찜질방|사우나|캠핑장"
    r"|국내산|국산|수입산|정품|친환경|무독성|무형광|항균|방수|살균"
    # 효능·등급 주장 — 상품명에 없으면 사실인지 알 수 없다.
    # '자국 없는 고급' 이 아무 바지걸이에나 붙었다(2026-09-05 사용자 지적).
    r"|자국|무자국|흠집|고급|프리미엄|명품|최고급|심플|모던|북유럽"
    r"|거품|버블|연수|비타민|아로마"
    # 재질 — 상품명에 없으면 그 재질인지 알 수 없다. 유리·대리석·알루미늄이
    # 빠져 있어서 대리석 선반에 '강화유리일자선반' 이 붙었다(2026-09-05).
    r"|스텐|스테인|스틸|스탠|스덴|스뎅|우드|원목|실리콘|아크릴|고무|가죽|인조가죽"
    # 원단 — '데님' 은 청바지 천이라 그 상품이 그 천이어야 쓴다
    # (2026-09-06 사용자: "데님도 청바지의 뜻이니 안되고 데일리 같은 단어는 됨")
    r"|데님|청지|청앞치마|청바지|광목|황마|마직|캔버스|캔퍼스|니트|나일론"
    r"|부직포|코튼|면|린넨|폴리"
    r"|모직|스웨이드|벨벳|메쉬|매쉬|타포린"
    r"|강화유리|유리|인조대리석|대리석|알루미늄|세라믹|도자기|법랑|무쇠|주철"
    r"|황동|구리|주석|티타늄|플라스틱|PVC|ABS|PET|라탄|대나무|등나무"
    # 철제·목재가 빠져 있어 **플라스틱 부품함에 '철제정리함'** 이 붙었다
    # (2026-09-08 LCP_LHA_B915822).
    r"|철제|철재|양철|함석|목재|MDF|합판|골판|부직"
    r"|패브릭|린넨|메탈|양은|타일|석재|한지|코르크"
    # 완구·문구의 재질. 펠트 배경판에 '종이' 가 붙었다
    # (2026-09-08 사용자 지적 LCP_LHA_B915472 - "펠트로된 유니아트 배경판").
    r"|펠트|종이|골판지|하드보드|우드락|폼보드|스티로폼|점토|지점토|찰흙"
    r"|봉제|극세사|양모|털실|EVA"
    # 동물 — 봉제인형·동물완구는 **어떤 동물인지가 상품 그 자체**다.
    # 토끼 애착인형에 '코끼리 공룡 돼지 문어 판다' 가 다 붙었다
    # (2026-09-08 사용자 지적 LCP_LHA_B915473 / B915483).
    # 긴 것을 먼저 적는다 - 정규식 교체는 앞에서부터 맞는 것을 고른다.
    r"|테디베어|곰인형|곰돌이|코끼리|공룡|돼지|문어|판다|사자|호랑이"
    r"|기린|원숭이|다람쥐|거북이|거북|돌고래|고래|상어|펭귄|토끼|래빗"
    r"|강아지|퍼피|고양이|햄스터|여우|사슴|늑대|오리|병아리|유니콘"
    r"|알파카|라마|너구리|수달|고슴도치|악어|개구리|물고기|앵무새"
    r"|얼룩말|코알라|캥거루|하마|코뿔소|낙타|나비|공작새"
    # 한 글자 동물(양·곰·소)은 그대로 두면 '양념'·'곰팡이' 에 걸린다.
    # 인형 뒤에 붙은 꼴로만 잡는다 - 사자 인형에 '양인형' 이 붙었다
    # (2026-09-08). '베어' 는 곰이다 - 토끼·원숭이에 '체리블라썸베어' 와
    # '케어베어' 가 붙었다.
    r"|양인형|양털|베어|카피바라|캐피바라|청룡|용띠|애견|구조대"
    r"|거미|공룡|상어|십이지신|12지신|바다동물|해양동물"
    # 개 품종. 사자·코끼리 인형에 '푸들인형' 이 붙었다 (2026-09-08).
    r"|푸들|치와와|포메라니안|포메|시츄|말티즈|진돗개|리트리버|비숑|웰시코기"
    # 한 글자 동물. '닭' 이 짱구 흰둥이 인형에 붙었다 (2026-09-09).
    r"|닭|쥐인형|소인형|말인형|새인형|뱀인형|용인형"
    # 크기·용도를 말하는 인형 낱말
    r"|왕인형|태명인형|태명"
    # 영상 단자 규격 — 단자가 다르면 아예 안 꽂힌다. DisplayPort 케이블에
    # 'HDMI·DVI·VGA' 태그가 통째로 붙어 있었다
    # (2026-09-08 사용자 지적 LCP_LHA_B915924).
    # 긴 것부터 적는다. 'DP' 는 짧아 마지막에 둔다.
    r"|디스플레이포트|썬더볼트|DISPLAYPORT|HDMI|DVI|VGA|RGB|SCART"
    r"|USB3\.1|USB3|USB2|C타입|타입C|TYPE\s*-?\s*C|TYPEC"
    r"|미니DP|MINIDP|DP"
    # 해상도 — 4K 케이블과 8K 케이블은 다른 물건이다.
    r"|8K|4K|2K|FHD|UHD|QHD|1080P|720P)", re.I)

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
    # 후보 표에는 '스덴채반' 처럼 잘못 적힌 것도 그대로 온다. 사이트는
    # 누르면 '스텐채반' 으로 고쳐 넣으므로 같은 재질로 봐야 한다(2026-09-05).
    "스탠": "스텐", "스덴": "스텐", "스뎅": "스텐",
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
    # 동물은 표기가 갈린다. 원상품명이 '콩지래빗' 인데 후보가 '토끼인형'
    # 이면 같은 동물이다 (2026-09-08).
    # 같은 단자·해상도를 다르게 적은 것 (2026-09-08 LCP_LHA_B915924).
    "디스플레이포트": "DP", "DISPLAYPORT": "DP",
    "베어": "곰인형", "캐피바라": "카피바라",
    "MINIDP": "미니DP",
    "TYPEC": "C타입", "TYPE-C": "C타입", "TYPE C": "C타입", "타입C": "C타입",
    "장난감": "완구", "토이": "완구",
    "래빗": "토끼", "퍼피": "강아지",
    "테디베어": "곰인형", "곰돌이": "곰인형",
}


_syn_cache = None


def synonyms() -> dict:
    """
    표기 -> 대표어. 손으로 적은 것 위에 **로하스가 실제로 쓰는 대응**을 얹는다.

    로하스는 키워드를 형태소로 쪼갤 때 표기를 대표어로 바꾼다 — '양면' 을
    누르면 '리버시블' 이 들어간다. 그 대응을 상품명 탭에서 긁어 `synonym`
    테이블에 쌓아뒀다(2026-09-05, 614쌍). 짐작으로 적은 목록보다 정확하다.
    3회 이상 확인된 것만 쓴다 — 한 번뿐인 건 형태소 분석이 튄 것일 수 있다.
    """
    global _syn_cache
    if _syn_cache is None:
        m = dict(_SYNONYM)
        try:
            for surface, normal in db.synonym_map(min_seen=3).items():
                a, b = surface.upper(), normal.upper()
                # 규격 판정에 쓰는 말끼리의 대응만 받는다.
                #   - 양쪽 다 _SPEC_RE 가 아는 말이어야 한다. '국산=국내산'
                #     같은 건 규격이 아니라 넣어봐야 쓸 데가 없다
                #   - 손으로 적은 것과 방향이 반대면 무시한다. 안 그러면
                #     '스테인->스텐' 과 '스텐->스테인리스' 가 서로 밀어내
                #     제자리로 돌아온다(2026-09-05 실측)
                if not (_SPEC_RE.fullmatch(a) and _SPEC_RE.fullmatch(b)):
                    continue
                if m.get(b) == a or a in m.values():
                    continue
                m.setdefault(a, b)
        except Exception:
            pass                      # 사전이 없어도 손으로 적은 것으로 돈다
        _syn_cache = m
    return _syn_cache


_QTY_RE = re.compile(r"^([0-9]+)(개입|매입|장입|스틱|켤레|개|매|입|팩|봉|포"
                     r"|정|캔|병|롤|권|족|P|EA)$")


def _qty(tok: str) -> str:
    """
    수량 표기를 하나로 모은다. 10P · 10개입 · 10EA 는 모두 같은 수량이다.

    이렇게 모아두지 않으면 '10P 바지걸이' 에 '10개입' 이 다른 규격으로 보여
    또 붙는다.
    """
    m = _QTY_RE.match(tok)
    return f"{int(m.group(1))}개" if m else tok


# 치수 '20x25cm' · '68X100CM' 의 조각을 뽑는다
_DIM_PARTS = re.compile(r"([0-9]+)\s*[xX*]\s*([0-9]+)\s*(CM|MM|cm|mm)?")


def specs_of(text: str) -> set:
    """
    상품명·키워드에서 규격·형태·재질을 뽑는다.

    같은 뜻인데 표기만 다른 것은 대표어로 모은다. '스테인리스' 와 '스텐' 이
    다르게 잡히면 재질이 같은데도 태그를 못 붙인다.
    """
    m = synonyms()
    out = set()
    # '20x25cm' 은 20cm X 25cm 다. 통째로만 내면 상품명의 '25cm' 가 근거
    # 없음으로 걸린다(2026-09-09). 조각도 같이 낸다.
    for a, b, unit in _DIM_PARTS.findall(text or ""):
        u = (unit or "").upper() or "CM"
        out.add(f"{a}{u}")
        out.add(f"{b}{u}")
    for x in _SPEC_RE.findall(text or ""):
        u = _qty(x.upper())
        seen = {u}
        while True:                   # 스텐레스 -> 스테인리스 처럼 여러 단계
            v = m.get(u)
            if not v or v in seen:    # 순환하면 멈춘다
                break
            u = v
            seen.add(u)
        out.add(u)
    return out


def spec_ok(cand_name: str, own_specs: set) -> bool:
    """
    후보에 박힌 특성이 이 상품의 것인가.

    후보에 특성이 없으면 누구에게나 쓸 수 있는 '공통' 이라 통과시킨다.
    특성이 있으면 **그 특성이 전부** 상품명에도 있어야 한다.

    처음엔 겹치기만 하면(`sp & own_specs`) 통과시켰다. 그러면 특성이 둘 이상
    박힌 후보가 한쪽만 맞아도 붙는다 — 'DP TO HDMI' 케이블에
    'MINIDPTOHDMI'(미니DP + HDMI)가 HDMI 하나로 통과했다
    (2026-09-08 LCP_LHA_B915924). 상품명 쪽 검사(`title_auto._usable`)는
    처음부터 부분집합이었다. 세 경로의 검사는 같아야 한다.
    """
    sp = specs_of(cand_name)
    if not sp:
        return True
    return sp <= own_specs


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


def ban_exempt(name: str, own_text: str) -> bool:
    """
    금지어지만 **그 말이 원상품명에 있으면** 쓴다 (문서에 적힌 규칙).

    '냉장고'·'세트' 가 금지어 사전에 들어 있어 '장난감냉장고'·'주방놀이세트'
    가 통째로 막혔다. 핑크퐁 냉장고 상품에 '냉장고' 를 못 쓰면 손해다
    (2026-09-06 LCP_LHA_B915352).
    """
    if not name or not own_text:
        return False
    for k in range(2, 7):
        for i in range(0, len(name) - k + 1):
            piece = name[i:i + k]
            if keywords.has_banned(piece) and piece in own_text:
                return True
    return False


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

    # 규격이 박힌 것은 **하드 차단** — 안 맞으면 아무에게도 안 준다.
    # 상품명에서 찾아낸 조각(key)은 **우선순위**로만 쓴다. 그것까지 차단하면
    # '워시'·'용기'·'린스' 같은 평범한 말이 전부 특정 상품 전용이 되어,
    # 그 말이 없는 상품은 태그를 몇 개 못 받는다(2026-09-05 LCP_LHA_B914696
    # 에서 19개 후보 중 17개가 그렇게 잠겼다).
    # 다른 물건으로 보이는 후보는 특성 배분에서도 뺀다. 맨 마지막 교차
    # 배분에서만 쓴다 - '물걸레 리필' 에 '대걸레탈수기' 가 '걸레' 조각으로
    # 걸려 들어왔다(2026-09-05).
    special = ([c for c in special if c.get("kin", True)]
               + [c for c in special if not c.get("kin", True)])
    kin_sp = [c for c in special if c.get("kin", True)]
    common = list(common) + [c for c in special if not c.get("kin", True)]
    special = kin_sp
    hard = [c for c in special if specs_of(c["name"])]
    soft = [c for c in special if not specs_of(c["name"])]

    out = []
    for i in range(n):
        mine = [c for c in hard if specs_of(c["name"]) <= specs_of(names[i])]
        mine += [c for c in soft if (c.get("key") or "") in up[i]
                 and c.get("key")]
        out.append(mine[:want])

    # 남은 soft 는 모자란 상품에 돌아가며 준다 (차단이 아니라 후순위)
    common = list(common) + [c for c in soft]

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

    # 남은 공통은 돌아가며 (교차 배분).
    # **같은 종류를 먼저 다 쓰고**, 그래도 모자랄 때만 다른 물건으로 보이는
    # 후보를 쓴다. 물걸레 청소포 상품에 '마루왁스'·'대걸레탈수기' 가 붙어
    # 사용자가 잡아냈다(2026-09-05). 형제와 태그가 겹치는 편이 낫다.
    # 다른 물건으로 보이는 후보는 **아예 쓰지 않는다**. 10개를 채우려고
    # 물걸레 청소포에 '마루왁스' 를 넣느니 9개로 두는 편이 낫다(절대규칙 4).
    # 태그가 하나도 없는 상품에 한해 맨 아래에서 꺼내 쓴다.
    kin_c = [c for c in common if c.get("kin", True)]
    alien_c = [c for c in common if not c.get("kin", True)]
    for bucket in (kin_c,):
        pos = 0
        for _ in range(len(bucket) * 2 + 2):
            if all(len(sh) >= want for sh in out):
                break
            moved = False
            for sh in out:
                if len(sh) >= want or not bucket:
                    continue
                have = {c["name"] for c in sh}
                for j in range(len(bucket)):
                    c = bucket[(pos + j) % len(bucket)]
                    if c["name"] not in have:
                        sh.append(c)
                        pos = (pos + j + 1) % len(bucket)
                        moved = True
                        break
            if not moved:
                break

    # 하나도 못 받은 상품은 공통에서라도 채운다 (태그 최소 1개)
    for sh in out:
        if not sh:
            sh.extend((kin_c or alien_c or ordered)[:want])
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

    plan_names = child_names(session, targets)
    kin_text = own + " " + " ".join(plan_names)
    common = common_words(plan_names)
    pool_all = _pool(tabs.fetch_tag_rows(session, head["product_no"]),
                     set(), own, vocab, drop_brand, kin=kin_text,
                     common=common)
    pool = [c for c in pool_all if c["name"].upper() not in used]
    source = "태그"
    if len(pool) < MIN_SHARE and pool_all:      # 형제와 같은 태그를 쓴다
        pool = pool_all
    if not pool:
        pool = _pool(tabs.fetch_title_rows(session, head["product_no"], 1),
                     used, own, vocab, drop_brand, kin=kin_text)
        source = "상품명"
        want = TITLE_FALLBACK
    if not pool:
        base["dropped_brand"] = drop_brand
        return base

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


def top_up_rows(session, rows: list, *, want: int = MAX_TAGS, log=print,
                should_stop=None) -> dict:
    """
    이미 붙어 있는 태그는 그대로 두고 **모자란 만큼만 더 채운다**.

    후보가 넉넉한 LCP 인데 어떤 L코드만 2~4개로 남는 일이 있다. 처음 나눌 때
    형제가 먼저 가져간 탓이다. 그럴 때 부르는 것이고, 규칙은 그대로다 —
    후보는 태그 표에서만, 조회수 1000 미만 먼저, 상품 특성이 맞아야 하고,
    **형제가 덜 쓴 것부터** 준다(같은 LCP 가 넓게 걸리도록).
    """
    # 한 LCP 안에서도 L코드마다 카테고리가 다를 수 있다. 후보 표는
    # 카테고리 기준으로 만들어지므로 **카테고리별로 나눠서** 처리한다.
    # 안 나누면 공룡 카테고리 후보가 다리미·블록 상품에도 붙는다
    # (2026-09-06 LCP_LHA_B915363 실측).
    cids = {str(r.get("etc_category") or "") for r in rows}
    if len(cids) > 1:
        out = {"ok": 0, "fail": 0, "skip": 0, "added": 0,
               "saved": [], "picks": {}}
        for cid in sorted(cids):
            part = [r for r in rows if str(r.get("etc_category") or "") == cid]
            res = top_up_rows(session, part, want=want, log=log, should_stop=should_stop)
            for k in ("ok", "fail", "skip", "added"):
                out[k] = out.get(k, 0) + int(res.get(k) or 0)
            out["saved"] += res.get("saved") or []
            out["picks"].update(res.get("picks") or {})
        return out

    if not rows:
        return {"ok": 0, "fail": 0, "skip": 0, "added": 0}

    have = {}
    for r in rows:
        try:
            have[r["l_code"]] = [t["text"] for t
                                 in tabs.fetch_saved_tags(session, r["product_no"])]
        except Exception as e:
            have[r["l_code"]] = []
            log(f"  !! {r['l_code']} 조회 실패 {str(e)[:50]}")

    head = rows[0]
    lcp = head.get("lcp_code") or ""
    own = own_words(lcp)
    vocab = brand_vocab()
    drop_brand = []
    names = child_names(session, rows)
    common = head_words(names, str(head.get("etc_category") or ""))
    raw = tabs.fetch_tag_rows(session, head["product_no"])
    _cid = head.get("etc_category") or ""
    pool = _pool(raw, set(), own, vocab, drop_brand,
                 kin=own + " " + " ".join(names), common=common,
                 rules=rules_for(_cid), cat_name=cat_name_of(_cid))
    if drop_brand:
        log(f"  [태그] 타사 브랜드 제외 {len(drop_brand)}개: "
            + ", ".join(drop_brand[:6]))
    ordered = _order(pool)
    alien = [c["name"] for c in ordered if not c.get("kin", True)]
    if alien:
        log(f"  [태그] 다른 물건으로 보이는 후보 {len(alien)}개는 맨 뒤로: "
            + ", ".join(alien[:6]))
    # 등록 불가 태그를 미리 걸러낸다. 저장 단계에서 빠지면 그 자리가 그냥
    # 비어 9개로 끝난다 - '가화행거' 가 그렇게 빠졌다(2026-09-05).
    try:
        chk = tabs.tag_search(session, head["product_no"],
                              [c["name"] for c in ordered])
        bad = {t["text"].upper() for t in chk.get("restricted") or []}
        if bad:
            log(f"  [태그] 등록 불가 {len(bad)}개 제외: "
                + ", ".join(sorted(bad)[:6]))
            ordered = [c for c in ordered if c["name"].upper() not in bad]
    except Exception as e:
        log(f"  [태그] 사전 검증 건너뜀 ({str(e)[:40]})")
    if not ordered:
        log("  - 태그 후보가 없습니다")
        return {"ok": 0, "fail": 0, "skip": len(rows), "added": 0}

    # 형제들이 이미 몇 번 쓰고 있나 - 적게 쓰인 것부터 준다
    used = collections.Counter()
    for ts in have.values():
        for t in ts:
            used[t.upper()] += 1

    plan = []
    for i, r in enumerate(rows):
        cur = list(have[r["l_code"]])
        if len(cur) >= want:
            continue
        mine = names[i] or ""
        my_specs = specs_of(mine)
        got = {x.upper() for x in cur}
        cands = sorted(ordered, key=lambda c: (used[c["name"].upper()],
                                               ordered.index(c)))
        # 1차는 엄격하게(특성이 맞는 것만), 그래도 모자라면 상품을 가르지 않는
        # 공통 후보까지 쓴다. 규격(20L·A4 같은 것)은 어느 경우에도 안 푼다 —
        # 틀린 규격이 붙으면 잘못된 검색에 걸린다(절대규칙 3).
        for strict in (True, False):
            for c in cands:
                if len(cur) >= want:
                    break
                nm = c["name"]
                if nm.upper() in got:
                    continue
                if not spec_ok(nm, my_specs):
                    continue
                if not c.get("kin", True) and cur:
                    continue        # 다른 물건 - 채우려고 넣지 않는다
                if strict:
                    key = dyn_key(nm, names, ordered)
                    if key and key not in mine:
                        continue
                cur.append(nm)
                got.add(nm.upper())
                used[nm.upper()] += 1
            if len(cur) >= want:
                break
        if len(cur) > len(have[r["l_code"]]):
            plan.append({**r, "proposed": cur, "source": "태그",
                         "before": len(have[r["l_code"]])})

    if not plan:
        log("  - 더 넣을 태그가 없습니다")
        return {"ok": 0, "fail": 0, "skip": len(rows), "added": 0}

    added = sum(len(p["proposed"]) - p["before"] for p in plan)
    log(f"  [태그보충] {len(plan)}건 / +{added}개")
    res = save_plan(session, plan, log=log, should_stop=should_stop)
    res["added"] = added
    return res


def fill_from_title(session, rows: list, *, want: int = MAX_TAGS,
                    log=print, should_stop=None) -> dict:
    """
    **예외 경로** — 로하스 태그사전에 그 카테고리 키워드가 없을 때만 쓴다.

    태그 후보 표가 비었거나 한두 개뿐이면 그 LCP 전체가 태그 1개로 끝난다.
    그럴 때 상품명 후보에서 가져온다. 절대규칙 1을 어기는 것이므로
    **사용자가 그렇게 하라고 할 때만** 부른다(2026-09-06 LCP_LHA_B914810,
    카테고리 '패션잡화/여성가방/에코백' 은 태그사전이 비어 있었다).

    고르는 기준은 태그와 같다 — 규격·수량·색상이 맞아야 하고, 같은 종류라야
    하고, **그 상품 원상품명과 겹치는 것을 먼저** 준다.
    """
    # 한 LCP 안에서도 L코드마다 카테고리가 다를 수 있다. 후보 표는
    # 카테고리 기준으로 만들어지므로 **카테고리별로 나눠서** 처리한다.
    # 안 나누면 공룡 카테고리 후보가 다리미·블록 상품에도 붙는다
    # (2026-09-06 LCP_LHA_B915363 실측).
    cids = {str(r.get("etc_category") or "") for r in rows}
    if len(cids) > 1:
        out = {"ok": 0, "fail": 0, "skip": 0, "added": 0,
               "saved": [], "picks": {}}
        for cid in sorted(cids):
            part = [r for r in rows if str(r.get("etc_category") or "") == cid]
            res = fill_from_title(session, part, want=want, log=log, should_stop=should_stop)
            for k in ("ok", "fail", "skip", "added"):
                out[k] = out.get(k, 0) + int(res.get(k) or 0)
            out["saved"] += res.get("saved") or []
            out["picks"].update(res.get("picks") or {})
        return out

    if not rows:
        return {"ok": 0, "fail": 0, "skip": 0, "added": 0}

    lcp = rows[0].get("lcp_code") or ""
    own = own_words(lcp)
    vocab = brand_vocab()
    names = child_names(session, rows)
    common = head_words(names, str(head.get("etc_category") or ""))
    kin_text = own + " " + " ".join(names)

    have = {}
    for r in rows:
        try:
            have[r["l_code"]] = [t["text"] for t
                                 in tabs.fetch_saved_tags(session, r["product_no"])]
        except Exception:
            have[r["l_code"]] = []

    used = collections.Counter()
    for ts in have.values():
        for t in ts:
            used[t.upper()] += 1

    plan = []
    for i, r in enumerate(rows):
        if should_stop and should_stop():
            break
        cur = list(have[r["l_code"]])
        if len(cur) >= want:
            continue
        mine = names[i] or ""
        my_specs = specs_of(mine)
        my_words = words_of(mine)
        drop = []
        pool = _pool(tabs.fetch_title_rows(session, r["product_no"], 1),
                     set(), own, vocab, drop, kin=kin_text, common=common)
        if not pool:
            continue
        # 그 상품 상품명과 겹치는 것 먼저, 그다음 형제가 덜 쓴 것 먼저
        pool.sort(key=lambda c: (-len(words_of(c["name"]) & my_words),
                                 used[c["name"].upper()],
                                 0 if c.get("kin", True) else 1,
                                 c["views"]))
        got = {x.upper() for x in cur}
        for c in pool:
            if len(cur) >= want:
                break
            nm = c["name"]
            if nm.upper() in got or not spec_ok(nm, my_specs):
                continue
            if not c.get("kin", True):
                continue
            cur.append(nm)
            got.add(nm.upper())
            used[nm.upper()] += 1
        if len(cur) > len(have[r["l_code"]]):
            plan.append({**r, "proposed": cur, "source": "상품명",
                         "before": len(have[r["l_code"]])})

    if not plan:
        log("  - 상품명 후보에서도 넣을 것이 없습니다")
        return {"ok": 0, "fail": 0, "skip": len(rows), "added": 0}
    added = sum(len(p["proposed"]) - p["before"] for p in plan)
    log(f"  [태그·상품명후보] {len(plan)}건 / +{added}개")
    res = save_plan(session, plan, log=log, should_stop=should_stop)
    res["added"] = added
    return res


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
    # 한 LCP 안에서도 L코드마다 카테고리가 다를 수 있다. 후보 표는
    # 카테고리 기준으로 만들어지므로 **카테고리별로 나눠서** 처리한다.
    # 안 나누면 공룡 카테고리 후보가 다리미·블록 상품에도 붙는다
    # (2026-09-06 LCP_LHA_B915363 실측).
    cids = {str(r.get("etc_category") or "") for r in rows}
    if len(cids) > 1:
        out = {"ok": 0, "fail": 0, "skip": 0, "added": 0,
               "saved": [], "picks": {}}
        for cid in sorted(cids):
            part = [r for r in rows if str(r.get("etc_category") or "") == cid]
            res = apply_to_rows(session, part, want=want, overwrite=overwrite, use_ai=use_ai,
                             fill_more=fill_more, fill_to=fill_to,
                             log=log, should_stop=should_stop,
                             progress=progress)
            for k in ("ok", "fail", "skip", "added"):
                out[k] = out.get(k, 0) + int(res.get(k) or 0)
            out["saved"] += res.get("saved") or []
            out["picks"].update(res.get("picks") or {})
        return out

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

    names = child_names(session, targets)
    kin_text = own + " " + " ".join(names)
    common = head_words(names, str(head.get("etc_category") or ""))
    raw = tabs.fetch_tag_rows(session, head["product_no"])
    _cid = head.get("etc_category") or ""
    _rules, _cname = rules_for(_cid), cat_name_of(_cid)
    pool_all = _pool(raw, set(), own, vocab, drop_brand,   # 형제가 쓰는 것 포함
                     kin=kin_text, common=common,
                     rules=_rules, cat_name=_cname)
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
    if len(pool_all) < MIN_SHARE:
        # 로하스 태그사전에 그 카테고리 키워드가 아예 없는 경우가 있다.
        # 패션잡화가 그렇다 - '에코백' 카테고리는 후보가 1개(그것도 오타)뿐이라
        # 그 LCP 14종이 전부 태그 1개로 끝났다(2026-09-06 사용자 확인).
        # 이럴 때는 어쩔 수 없이 상품명 후보에서 10개까지 가져온다.
        pool = _pool(tabs.fetch_title_rows(session, head["product_no"], 1),
                     used, own, vocab, drop_brand, kin=kin_text,
                     common=common, rules=_rules, cat_name=_cname)
        source = "상품명"
        log(f"  [태그] 태그 후보가 {len(pool_all)}개뿐 - 상품명 후보에서 "
            f"{want}개까지 씁니다")
    if not pool:
        log("  - 넣을 태그가 없습니다")
        return {"ok": 0, "fail": 0, "skip": skip + len(targets),
                "saved": [], "picks": {}}

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

    # 조회수 1000 미만 우선은 **권고**다. 하드 컷이 아니다 — 모자라면 그
    # 이상으로 채워 개수를 맞춘다(로하스 지침 5번, CLAUDE.md 4-5).
    # 잘라내고 있어서 보이차 LCP 에서 가장 일반적인 '중국보이차'(1670)가
    # 통째로 빠지고 조회수 0짜리 '보이차분' 만 남았다(2026-09-07 사용자 지적).
    # `_order` 가 이미 1000 이상을 뒤로 보내므로 순서만 지키면 된다.
    high = [c for c in ordered if c["views"] >= LOW_VIEWS]
    ordered = low_only(ordered) + high
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
