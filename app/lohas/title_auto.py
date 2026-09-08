"""
상품명1 자동 작성.

사이트가 하는 일을 그대로 재현한다. 브라우저는 필요 없다 — 상품명 탭 HTML
안에 필요한 게 전부 들어 있다(2026-09-05 실측).

  const titleKwData = [{
      relKeyword            키워드
      terms                 **사이트가 쪼갠 형태소**. 이게 상품명에 들어간다
      monthlyMobileQcCnt    조회수(모바일)
      monthlyPcQcCnt        조회수(PC)
      ban_word / ban_reason 금지어
  }, ...]

키워드를 누르면 그 `terms` 가 순서대로 쌓이고, **이미 있는 형태소는 안 들어간다**.
상품명 = `wordOrder.join(' ')`. 동의어 변환도 사이트가 terms 로 준다 —
'스텐브러쉬' 를 누르면 '스테인리스 브러쉬' 가 들어간다.

  25자   넘어가면 권장 경고 팝업 (막지는 않는다)
  50자   초과하면 체크가 취소된다

저장 전에 검색최적화를 반드시 통과해야 한다.

  검색최적화  POST http://<ip>:3403/ss_site/title_check
              token uid commerce_id cate_1..4 title
              -> result.cause[] / result.etc[] 가 둘 다 비면 '이상 없음'
  저장        POST <상품명 탭 URL>  mode=save  title  title_tag  shipping
              (title_tag = 눌러둔 키워드들을 쉼표로 이은 것)

상품명은 태그와 규칙이 다르다 — **조회수가 큰 키워드를 쓴다.** 노출 자체가
목적이라 작은 키워드로는 잡히지 않는다.
"""
import collections
import json
import re

from . import keywords, tabs, tag_auto

_TOKEN_RE = re.compile(r"[A-Za-z]+[0-9]*|[0-9]+[A-Za-z]*|[가-힣]+")
# 원상품명에서 그대로 가져다 쓸 수 있는 용량·수량 표기
_QTY_IN_TEXT = re.compile(
    r"[0-9]+(?:\.[0-9]+)?(?:KG|G|ML|L|K|CM|MM|W|개입|개|입|팩|봉|매|장|정"
    r"|스틱|EA|P)(?![0-9A-Za-z가-힣])", re.I)
_QTY_TOKEN = re.compile(
    r"^[0-9]+(?:\.[0-9]+)?(?:KG|G|ML|L|K|CM|MM|W|개입|개|입|팩|봉|매|장|정"
    r"|스틱|EA|P)$", re.I)

CLICK_ONLY = True      # 상품명은 **후보 클릭으로만** 만든다 (사용자 2026-09-06).
                       # 입력칸 직접 입력이 되긴 하지만, 후보에 없는 말이 붙어
                       # 상품과 안 맞는 상품명이 나왔다. 짧아지더라도 클릭만 쓴다.
MAX_LEN = 50          # 사이트 상한
WARN_LEN = 25         # 사이트 권장선 (넘으면 경고창)
TARGET_MIN = 25       # 최소한 여기까지는 채운다
TARGET_MAX = 29

_RE_KWDATA = re.compile(r"const titleKwData = (\[.*?\]);", re.S)
_RE_VAR = re.compile(r'var\s+{}\s*=\s*"([^"]*)"')
_RE_INPUT_VAL = re.compile(r'id="{}"[^>]*value="([^"]*)"')


def _var(html, name):
    m = re.search(_RE_VAR.pattern.format(name), html)
    return m.group(1) if m else ""


def _input_val(html, el_id):
    m = re.search(_RE_INPUT_VAL.pattern.format(el_id), html)
    return m.group(1) if m else ""


def views_of(d) -> int:
    def n(v):
        try:
            return int(str(v or 0).split(".")[0])
        except ValueError:
            return 0
    return n(d.get("monthlyMobileQcCnt")) + n(d.get("monthlyPcQcCnt"))


def fetch_page(session, no, timeout=60) -> dict:
    """상품명1 탭에서 후보와 페이지 변수를 통째로 읽는다."""
    r = session.get(tabs.URL_TITLE1.format(no=no), timeout=timeout)
    r.encoding = "utf-8"
    if "loginForm" in r.text:
        raise RuntimeError("세션 만료")
    h = r.text
    m = _RE_KWDATA.search(h)
    cands = json.loads(m.group(1)) if m else []
    return {
        "html": h,
        "candidates": cands,
        "token": _var(h, "token"), "ip": _var(h, "ip"),
        "uid": _var(h, "uid"), "commerce_id": _var(h, "commerce_id"),
        "cate": [_var(h, f"cate_{i}") for i in range(1, 5)],
        "shipping": _input_val(h, "shippingProductInput"),
        "current": _input_val(h, "finalProductNameInput"),
    }


_BANS = None


def _brand_bans() -> set:
    global _BANS
    if _BANS is None:
        from .. import db as _db
        try:
            _BANS = _db.title_bans()
        except Exception:
            _BANS = set()
    return _BANS


def fuzzy_count(text: str, word: str) -> int:
    """
    `word` 가 몇 번 나오나. **한 글자 차이(첫소리 같음)까지 같은 말로 센다.**

    후보 표에는 '헹거'·'스덴' 처럼 잘못 적힌 것이 섞여 있고, 사이트의
    유의어 검사는 그것도 같은 말로 본다. 글자 그대로만 세면 '행거' 를
    한 번으로 세고 '시스템헹거' 를 또 넣게 된다(2026-09-05).
    """
    if not text or not word:
        return 0
    n, w = len(text), len(word)
    if w < 2:
        return text.count(word)
    cnt = i = 0
    while i <= n - w:
        seg = text[i:i + w]
        diff = [(a, b) for a, b in zip(seg, word) if a != b]
        if not diff or (len(diff) == 1 and tag_auto._same_onset(*diff[0])):
            cnt += 1
            i += w
        else:
            i += 1
    return cnt


def common_words(names: list, ratio: float = 1.0) -> list:
    """그 LCP 상품명들에 공통으로 든 낱말. 판정은 `tag_auto` 에 있다."""
    return tag_auto.common_words(names, ratio)


def sibling_words(lcp_code: str, skip_l: str = "") -> dict:
    """
    같은 LCP 의 다른 L코드 상품명에 이미 쓰인 낱말과 횟수.

    이걸 뒤로 밀어야 상품마다 다른 상품명이 나온다.
    """
    import collections
    import re as _re
    out = collections.Counter()
    try:
        from .. import db as _db5
        with _db5.sqlite_conn() as c:
            for r in c.execute("SELECT l_code, title1 FROM lcode_attr "
                               "WHERE lcp_code = ? AND title1 <> ''",
                               (lcp_code,)):
                if skip_l and r["l_code"] == skip_l:
                    continue
                out.update(_re.findall(r"[0-9A-Za-z가-힣]+", r["title1"]))
    except Exception:
        pass
    return out


def leaf_name(cid: str) -> str:
    """카테고리의 마지막 칸 이름. 그 상품의 **품목**이다 ('... / 에코백')."""
    try:
        from .. import db as _db7
        return (_db7.category_name(cid) or "").split("/")[-1].strip()
    except Exception:
        return ""


def spec_base(own_name: str, cid: str = "") -> str:
    """
    규격을 잴 때 기준이 되는 글. 원상품명 + **그 카테고리 이름**이다.

    카테고리 이름에 있는 말은 그 상품에 원래 있는 성질이다. '공기청정기필터'
    카테고리 상품의 상품명에서 '필터' 를 빼면 그 상품이 무엇인지 사라진다
    (2026-09-05).
    """
    if not cid:
        return own_name or ""
    try:
        from .. import db as _db4
        return (own_name or "") + " " + _db4.category_name(cid)
    except Exception:
        return own_name or ""


def _usable(d, own_words: set, own_name: str, base: str = None,
            common: list = None, loose: set = None) -> bool:
    """
    쓸 수 있는 후보인가.

    금지어는 피하되, **그 말이 원상품명에 있으면 쓴다** — 우리 상품이 실제로
    그런 물건이면 금지어 표시와 무관하게 필요한 말이다(사용자 지침).
    규격·형태·재질이 원상품명과 어긋나면 뺀다(태그와 같은 규칙).
    """
    kw = (d.get("relKeyword") or "").strip()
    if not kw:
        return False
    ban = (d.get("ban_word") or "").strip()
    if ban and ban not in own_name:
        return False
    if keywords.has_banned(kw) and kw not in own_name:
        return False
    # 타사 브랜드 - 후보 표에는 같은 카테고리의 남의 제품명이 섞여 온다.
    # 우리 원상품명에 그 말이 있으면(우리가 파는 그 브랜드면) 예외로 쓴다.
    for b in _brand_bans():
        if b in kw and b not in own_name:
            return False
    # 규격은 **실제로 들어갈 말**로 재야 한다. 사이트는 누를 때 표기를
    # 고쳐 넣는다 - '스덴채반' 을 누르면 '스텐채반' 이 들어간다. relKeyword
    # 만 보면 '스덴' 은 재질로 안 잡혀 그대로 통과했다(2026-09-05).
    # 숫자가 든 후보는 그 숫자가 상품명에 있어야 한다. 340g 짜리 키워드를
    # 3.1Kg 상품에 붙이면 안 된다(2026-09-06 사용자 지적).
    base_txt = base if base is not None else own_name
    if not tag_auto.numbers_ok(kw + " " + " ".join(d.get("terms") or []),
                               base_txt):
        return False
    sp = tag_auto.specs_of(kw + " " + " ".join(d.get("terms") or []))
    sp = tag_auto.loosen(sp, loose)     # 느슨한 카테고리는 원단·형태를 뺀다
    if sp and not sp <= tag_auto.specs_of(base if base is not None else own_name):
        return False
    # 그 상품의 **특징**은 원상품명에 있을 때만 쓴다. 지퍼가 달렸는지,
    # 보냉인지, 무슨 색인지는 상품명 말고는 알 방법이 없다(2026-09-06).
    if (common and not (loose and "modifier" in loose)
            and not tag_auto.modifier_ok(
                kw, base if base is not None else own_name, common)):
        return False
    return True


def datalab_words(cid: str, own_name: str, exclude: set, top: int = 40,
                  kin: str = "", log=print) -> list:
    """
    데이터랩 인기키워드를 상품명 보충용으로 가져온다.

    상품명 확인란은 **직접 입력이 된다**(`handleManualInput`). 후보 목록에
    없는 말도 넣을 수 있고 검색최적화도 통과한다(2026-09-05 '타공판' 으로 실측).
    그래서 사이트 후보만으로 25자를 못 채울 때 여기서 보충한다.

    태그와 달리 상품명은 **큰 키워드가 필요**하므로 조회수 높은 순으로 준다.
    다만 규격·재질이 어긋나거나 금지어인 것은 태그와 같은 기준으로 뺀다.
    """
    from . import datalab, keywords

    if not cid or not datalab.base():
        return []
    try:
        rows = datalab.category_keywords_with_views(cid, top=top, log=log)
    except Exception as e:
        log(f"  [상품명] 데이터랩 실패 {str(e)[:50]}")
        return []
    own_specs = tag_auto.specs_of(spec_base(own_name, cid))
    vocab = tag_auto.brand_vocab()
    out = []
    for r in rows:
        kw = (r.get("keyword") or "").strip()
        if not kw or kw.upper() in exclude:
            continue
        if keywords.has_banned(kw) and kw not in own_name:
            continue
        sp = tag_auto.specs_of(kw)
        if sp and not sp <= own_specs:
            continue
        if tag_auto.foreign_brand(kw, own_name, vocab):
            continue
        # 그 카테고리 인기어라도 **다른 물건**이면 안 된다. 철제 행거에
        # '조립식이불장' 이 붙었다(2026-09-05). 기준은 원상품명 + 카테고리
        # 이름 + 지금까지 넣은 말이다.
        if kin and not tag_auto.head_ok(kw, kin):
            continue
        out.append(kw)
    return out


def build_title(page: dict, own_name: str, *, want_max: int = TARGET_MAX,
                use_ai: bool = True, brand: str = "", maker: str = "",
                cid: str = "", ban_terms: set = None, cap_terms: set = None,
                cap: int = 0, avoid: dict = None, must: list = None,
                own_uniq: set = None, log=print) -> dict:
    """
    누를 키워드를 정하고 그 결과 상품명을 만든다. 저장은 하지 않는다.

    순서
      1) 원상품명을 띄어쓰기로 쪼갠 낱말과 겹치는 후보부터 (조회수 높은 순)
      2) 그다음 AI 가 '이 상품에 어울린다' 고 고른 순서대로
      3) 그래도 모자라면 남은 후보를 조회수 높은 순으로

    이미 들어간 형태소만 있는 키워드는 눌러도 변화가 없으므로 건너뛴다.
    사이트가 그렇게 동작한다(중복 형태소는 안 들어간다).
    """
    own_name = own_name or ""
    own_words = tag_auto.words_of(own_name)

    # 그 카테고리에 쌓인 사용자 지침 (같은 카테고리가 다시 나오면 그대로 쓴다)
    try:
        from .. import db as _db6
        rules = _db6.cat_rules(cid, "title")
    except Exception:
        rules = {"ban": set(), "need_name": set(), "must": []}
    if rules["must"]:
        must = list(must or []) + [w for w in rules["must"]
                                   if w not in (must or [])]

    # 규격 기준 글. 아래의 base(= 후보 목록)와 이름이 겹치면 안 된다.
    spec_txt = spec_base(own_name, cid)
    # 꾸밈말 판정의 기준이 되는 품목. LCP 공통 낱말이 없으면 카테고리
    # 마지막 칸을 쓴다 - 상품명이 제각각인 LCP 도 판정이 되게 한다
    # (2026-09-06 LCP_LHA_B914810 은 공통 낱말이 없어 규칙이 헛돌았다).
    heads = list(must or []) or [x for x in [leaf_name(cid)] if x]
    pool = [d for d in page["candidates"]
            if _usable(d, own_words, own_name, spec_txt, heads,
                       rules.get("loose"))]
    if rules["need_name"]:
        # 사용자가 '상품명에 없으면 넣지 말라' 고 한 말들
        pool = [d for d in pool
                if not any(w in d["relKeyword"] and w not in spec_txt
                           for w in rules["need_name"])]
    if rules["ban"]:
        pool = [d for d in pool
                if not any(w in d["relKeyword"] and w not in own_name
                           for w in rules["ban"])]

    # 브랜드는 **그 L코드 원상품명에 있을 때만** 넣는다. 같은 LCP 라도 L코드마다
    # 제조사가 다르다 - 해림바스 LCP 안의 '이누스' 상품에 '해림' 이 붙었다
    # (2026-09-05). 우리 브랜드든 남의 브랜드든 판단 기준은 원상품명이다.
    own_brand = {w for v in (brand, maker) for w in _TOKEN_RE.findall(v or "")
                 if len(w) >= 2 and w not in own_name}
    if own_brand:
        pool = [d for d in pool
                if not any(b in d["relKeyword"] for b in own_brand)]

    if ban_terms:
        # 검색최적화가 '유의어 반복' 으로 지적한 형태소는 아예 후보에서 뺀다.
        # 만들고 나서 빼면 그만큼 짧아지므로, 처음부터 빼고 25자를 채운다.
        pool = [d for d in pool
                if not (set(d.get("terms") or []) & set(ban_terms))]
    if not pool:
        return {"picked": [], "order": [], "title": "", "len": 0, "pool": 0,
                "ai": 0}

    # AI 가 이 상품에 맞는 것만 추려 순서를 매긴다. 실패하면 규칙만 쓴다.
    ai_order = []
    if use_ai:
        from . import gemini
        names = [d["relKeyword"] for d in pool]
        r = gemini.pick_title_keywords(
            own_name, names, brand, maker, log=lambda *_: None)
        picked_names, ban_names = r.get("pick") or [], set(r.get("ban") or [])
        if ban_names:
            # 남의 브랜드 등 - 길이를 채우려고 예비로도 꺼내 쓰면 안 된다
            pool = [d for d in pool if d["relKeyword"] not in ban_names]
        if picked_names:
            by = {d["relKeyword"]: d for d in pool}
            ai_order = [by[n] for n in picked_names if n in by]
            log(f"  [상품명] 후보 {len(names)}개 -> AI 선별 {len(ai_order)}개"
                f" / 금지 {len(ban_names)}개")

    # 사람이 이 카테고리 상품명에 자주 넣은 말을 우선한다.
    # 4,723건을 분석해 카테고리별로 '원상품명에 없던 말' 을 세어뒀다
    # (`title_pattern`). 데코용품이면 장식·트리·오너먼트, 발매트면
    # 매트·발판·현관·미끄럼방지 같은 것들이다.
    learned = {}
    if cid:
        try:
            from .. import db as _db2
            learned = _db2.title_patterns(cid, min_n=2)
        except Exception:
            learned = {}

    def learn_score(d):
        """이 후보가 배운 말을 몇 번어치 담고 있나 (클수록 먼저)."""
        return sum(learned.get(t, 0) for t in (d.get("terms") or []))

    def kin_score(d):
        """같은 종류인가. 물걸레 청소포에 '나노코팅' 이 붙는 것을 뒤로 민다."""
        return 1 if tag_auto.head_ok(d.get("relKeyword") or "", own_name) else 0

    # 사람이 만든 상품명 4,723건에 한 번도 안 쓰인 말은 **상표일 가능성이
    # 높다**. 막지는 않고 뒤로 민다 - 25자에서 끊기므로 사실상 안 들어간다.
    # '닥터피엘'·'탑브랜드'·'대림' 이 이렇게 걸러진다(2026-09-05 사용자 지적).
    try:
        from .. import db as _db3
        vocab = _db3.title_word_vocab()
    except Exception:
        vocab = set()

    def known_score(d):
        if not vocab:
            return 1
        for t in (d.get("terms") or []):
            if t not in vocab and t not in own_name:
                return 0
        return 1

    def avoid_score(d):
        """형제가 이미 쓴 말은 뒤로. 같은 LCP 에 같은 상품명이 줄줄이 나오면
        그 LCP 가 가져가는 검색어가 하나로 끝난다(2026-09-05 사용자 지적)."""
        if not avoid:
            return 0
        return -sum(avoid.get(x, 0) for x in (d.get("terms") or []))

    def ban_score(d):
        """
        금지 사전에 든 말(교사용·대림 …)은 **맨 뒤로**.

        원상품명에 있으면 쓸 수는 있지만 '겨우 넣는 정도' 다 — 다른 후보가
        다 떨어졌을 때만 들어간다(사용자 지침 2026-09-06).
        """
        kw = d.get("relKeyword") or ""
        return 0 if any(b in kw for b in _brand_bans()) else 1

    def uniq_score(d):
        """**그 상품만 가진 원상품명 낱말**이 든 후보를 맨 먼저 누른다.

        같은 LCP 는 후보 표가 똑같이 내려온다. 그래서 조회수 순으로만 누르면
        16건이 전부 같은 상품명이 된다(2026-09-06 LCP_LHA_B915384 지적).
        구별되는 것은 원상품명뿐이다 — '일러스트 색종이' 는 일러스트를,
        '꽃나래 학접기' 는 꽃나래를 먼저 넣어야 상품마다 달라진다.

        절반 넘는 형제가 함께 쓰는 낱말(여기선 '색종이')은 구별점이 아니므로
        부르는 쪽에서 미리 빼고 넘긴다.
        """
        if not own_uniq:
            return 0
        kw = d.get("relKeyword") or ""
        terms = set(d.get("terms") or [])
        return 1 if any(w in kw or w in terms for w in own_uniq) else 0

    def must_score(d):
        """그 LCP 공통 낱말이 든 후보를 먼저 누른다 — 그 물건이 무엇인지가
        상품명에서 빠지면 안 된다."""
        if not must:
            return 0
        txt = (d.get("relKeyword") or "") + " " + " ".join(d.get("terms") or [])
        return 1 if any(w in txt for w in must) else 0

    def rank(d):
        return (ban_score(d), uniq_score(d), must_score(d), known_score(d),
                kin_score(d), avoid_score(d), learn_score(d), views_of(d))

    # AI 가 고른 것을 앞에 두되, **뺀 것도 뒤에 붙여 예비로 쓴다.**
    # AI 만 쓰면 42개 중 7개만 남아 20자에서 멈추는 일이 생긴다
    # (2026-09-05 LCP_LHA_B914708). 규칙을 이미 통과한 후보들이라
    # 25자를 채우는 데 쓰는 것은 문제가 없다.
    base = ai_order or pool
    spare = [d for d in pool if d not in base]
    spare.sort(key=views_of, reverse=True)

    near = [d for d in base if tag_auto.words_of(d["relKeyword"]) & own_words]
    near.sort(key=rank, reverse=True)
    rest = [d for d in base if d not in near]
    if learned or not ai_order:
        rest.sort(key=rank, reverse=True)
    spare.sort(key=rank, reverse=True)
    ordered = near + rest + spare

    # **이 상품만의 낱말은 AI 선별보다 앞이다.** AI 가 안 고르면 예비(spare)
    # 로 밀려 25자 안에 못 들어간다 - '일러스트 색종이 A/B/C' 세 건이
    # 일러스트 없이 똑같은 상품명이 된 이유다(2026-09-06).
    if own_uniq:
        head = [d for d in ordered if uniq_score(d)]
        ordered = head + [d for d in ordered if d not in head]

    from .. import db as _db
    try:
        syn = _db.synonym_map(min_seen=3)      # 양면=리버시블 같은 표기 차이
    except Exception:
        syn = {}

    # 사람이 화면에서 하는 순서 그대로 한 번에 하나씩 눌러 본다.
    #   - 새 형태소만 뒤에 붙는다 (이미 있으면 눌러도 변화 없음 = 헛클릭)
    #   - 글자수는 화면 카운터와 같다: wordOrder.join(' ').length
    #   - **25자를 넘어가면 사이트가 경고창을 띄운다. 거기서 멈춘다.**
    #     29자에서 미리 멈추면 짧은 상품명이 나온다 - 넘겨놓고 띄어쓰기를
    #     지워 25~29자로 맞추는 게 사람이 하는 방식이다(사용자 2026-09-05)
    #   - 50자를 넘으면 사이트가 체크를 취소한다
    # 사이트는 **같은 말이 여러 번** 들어간 상품명을 '유의어 포함 반복된 단어'
    # 로 막는다 — '주방놀이 소꿉놀이 역할놀이' 처럼. 지적받으면 그 말을 몇 개
    # 까지만 허용할지(cap) 정해 다시 만든다(2026-09-05 실측: 137건이 이걸로
    # 저장에 실패했다).
    caps = {t: cap for t in (cap_terms or ())} if cap else {}
    seen_cap = {t: 0 for t in caps}

    def cap_text(d):
        """실제로 들어갈 글자. 후보 이름은 오타여도 들어가는 말은 고쳐진다 —
        '이동식헹거' 를 누르면 '이동형행거' 가 들어간다(2026-09-05)."""
        return (d.get("relKeyword") or "") + " " + " ".join(d.get("terms") or [])

    def cap_ok(d):
        txt = cap_text(d)
        for t, lim in caps.items():
            if seen_cap[t] + fuzzy_count(txt, t) > lim:
                return False
        return True

    def cap_add(d):
        txt = cap_text(d)
        for t in caps:
            seen_cap[t] += fuzzy_count(txt, t)

    def cap_word(w):
        """원상품명·데이터랩에서 직접 넣는 낱말도 같은 제한을 받는다.
        여기를 빼놨더니 '행거' 가 네 번 들어갔다(2026-09-05)."""
        for t, lim in caps.items():
            if seen_cap[t] + fuzzy_count(w, t) > lim:
                return False
        for t in caps:
            seen_cap[t] += fuzzy_count(w, t)
        return True

    picked, order, steps = [], [], []
    adds = []                              # picked 와 짝 - 그 클릭이 붙인 형태소
    for d in ordered:
        if not cap_ok(d):
            continue
        terms = [t for t in (d.get("terms") or []) if t]
        # 형제가 이미 다 쓴 말만 붙는 후보는 **처음에는 건너뛴다**. 25자를
        # 못 채우면 아래 채우기 단계에서 다시 꺼내 쓴다. 이 단계가 없으면
        # 원상품명이 괄호 안 무늬만 다른 형제들(나라야 접이식 장바구니 6종)이
        # 똑같은 상품명을 받는다(2026-09-06 사용자 지적).
        # 단 **그 상품 원상품명에만 있는 말**은 형제가 썼어도 넣는다.
        # '일러스트 색종이' 의 '일러스트' 를 형제중복으로 빼버리면 그 상품을
        # 가리키는 유일한 말이 사라진다(2026-09-06 LCP_LHA_B915384).
        if (avoid and terms and not uniq_score(d)
                and all(avoid.get(t, 0) for t in terms)):
            steps.append({"kw": d["relKeyword"], "added": [],
                          "len": len(" ".join(order)), "skip": "형제중복"})
            continue
        pv = preview_click(order, terms, syn)
        # 한 글자짜리만 붙는 클릭은 누르지 않는다. '휴지케이스' 가 이미 있는데
        # '각휴지케이스' 를 누르면 '각' 하나만 붙어 상품명이 '... 사각 각 롤
        # 빈티지 ...' 처럼 조각난다(2026-09-06).
        if pv["added"] and all(len(x) <= 1 for x in pv["added"]):
            steps.append({"kw": d["relKeyword"], "added": [],
                          "len": len(" ".join(order)), "skip": "조각"})
            continue
        if not pv["added"]:
            steps.append({"kw": d["relKeyword"], "added": [],
                          "len": len(" ".join(order)), "skip": "헛클릭"})
            continue
        if pv["len"] > MAX_LEN:
            steps.append({"kw": d["relKeyword"], "added": [],
                          "len": len(" ".join(order)), "skip": "50자 초과"})
            continue
        before = len(" ".join(order))
        order += pv["added"]
        picked.append(d)
        adds.append(pv["added"])
        cap_add(d)
        steps.append({"kw": d["relKeyword"], "added": pv["added"],
                      "len": pv["len"], "warn": before < WARN_LEN <= pv["len"]})
        if pv["len"] >= WARN_LEN:
            break                      # 25자 경고 - 여기서 멈춘다

    # 길이 맞추기 — 사용자가 정한 순서대로 한다.
    #   1) 넘치면 **띄어쓰기를 지운다** (키워드를 빼지 않는다. 빼면 짧아진다)
    #   2) 그래도 안 되면 마지막 키워드를 빼고 **더 짧은 키워드**로 바꿔 넣는다
    #   3) 그래도 25자에 못 미치면 원상품명에서 용량·수량 낱말을 1~2개 빌린다
    def joined():
        return " ".join(order)

    def add(d, mark):
        """후보 하나를 눌러 본다. 붙었으면 True."""
        if not cap_ok(d):
            return False
        pv = preview_click(order, [t for t in (d.get("terms") or []) if t], syn)
        if not pv["added"] or pv["len"] > MAX_LEN:
            return False
        if all(len(x) <= 1 for x in pv["added"]):
            return False                    # 한 글자 조각만 붙는다
        cap_add(d)
        order.extend(pv["added"])
        picked.append(d)
        adds.append(pv["added"])
        steps.append({"kw": d["relKeyword"], "added": pv["added"],
                      "len": pv["len"], mark: True})
        return True

    # 너무 많이 넘치면 마지막 클릭을 되돌린다. 띄어쓰기를 다섯 개나 지우면
    # 뒤가 '각롤빈티지점보롤전용케이스' 처럼 한 덩어리가 된다(2026-09-06).
    # 되돌려도 25자가 지켜질 때만 되돌린다 - 짧아지는 것이 더 나쁘다.
    while len(joined()) > TARGET_MAX + 2 and len(picked) > 1:
        cut = adds[-1]
        trial = order[:len(order) - len(cut)]
        if len(tighten(" ".join(trial))) < TARGET_MIN:
            break
        order = trial
        picked.pop()
        adds.pop()
        steps.append({"kw": "(되돌림)", "added": [], "len": len(joined()),
                      "skip": "띄어쓰기 과다"})

    # (1) 넘칠 때 — 띄어쓰기를 다 지워도 29자를 넘으면, 그때만 마지막 키워드를
    #     빼고 **더 짧은 키워드**로 바꿔 넣는다. 띄어쓰기로 해결되면 손대지 않는다
    #     (빼기부터 하면 상품명이 짧아진다 - 사용자 지침 2026-09-05).
    dropped = set()
    for _ in range(8):
        if len(joined().replace(" ", "")) <= TARGET_MAX or len(picked) <= 1:
            break
        d0 = picked.pop()
        cut = adds.pop()
        del order[len(order) - len(cut):]
        dropped.add(d0["relKeyword"])
        steps.append({"kw": d0["relKeyword"], "added": [],
                      "len": len(joined()), "skip": "길이초과 제거"})
        for d in ordered:                  # 자리를 짧은 것으로 메운다
            if d in picked or d["relKeyword"] in dropped:
                continue
            if len("".join(d.get("terms") or [])) >= len("".join(cut)):
                continue
            if add(d, "swap"):
                break

    tries = 0
    while len(tighten(joined())) < TARGET_MIN and tries < 40:
        tries += 1
        left = [d for d in ordered if d not in picked]
        # 짧은 것부터 - 길이를 조금씩 올려 29자를 넘기지 않게
        left.sort(key=lambda d: len("".join(d.get("terms") or [])))
        grew = False
        for d in left:
            if d["relKeyword"] in dropped:
                continue
            if add(d, "fill"):
                grew = True
                break
        if not grew:
            break

    # 원상품명의 **용량·수량**은 후보에 없어도 넣는다. 지어낸 말이 아니라
    # 그 상품 이름에 적힌 사실이기 때문이다(2026-09-06 사용자 지시).
    # 그 밖의 낱말을 직접 치는 것은 CLICK_ONLY 에서 막는다.
    #
    # **길이가 모자랄 때만이 아니라 늘 넣는다.** 용량·수량은 사는 사람이
    # 먼저 보는 값이다 - 500g 인지 5kg 인지가 상품명에 없으면 안 된다
    # (2026-09-07 사용자: "제품의 용량은 될수있으면 넣어주자").
    # 자리가 없으면 뒤쪽 클릭을 하나 빼고 그 자리에 넣는다.
    for w in borrow_from_name(own_name, order):
        if not _QTY_TOKEN.match(w):
            continue
        if w in order:
            continue
        while len(joined() + " " + w) > TARGET_MAX and len(order) > 2:
            order.pop()                     # 뒤에서부터 자리를 비운다
        if len(joined() + " " + w) > TARGET_MAX or not cap_word(w):
            continue
        order.append(w)
        steps.append({"kw": w, "added": [w], "len": len(joined()),
                      "qty": True})

    if len(tighten(joined())) < TARGET_MIN:
        for w in borrow_from_name(own_name, order):
            if CLICK_ONLY and not _QTY_TOKEN.match(w):
                continue
            if len(joined() + " " + w) > TARGET_MAX:
                continue
            if not cap_word(w):
                continue
            order.append(w)
            steps.append({"kw": w, "added": [w], "len": len(joined()),
                          "qty": True})
            if len(tighten(joined())) >= TARGET_MIN:
                break

    if not CLICK_ONLY and len(tighten(joined())) < TARGET_MIN:
        for w in borrow_from_name(own_name, order):
            if len(joined() + " " + w) > TARGET_MAX:
                continue
            if not cap_word(w):
                continue
            order.append(w)
            steps.append({"kw": w, "added": [w], "len": len(joined()),
                          "borrow": True})
            if len(tighten(joined())) >= TARGET_MIN:
                break

    # 사이트 후보를 다 써도 25자에 못 미치면 데이터랩에서 직접 입력으로 채운다.
    extra_used = []
    if not CLICK_ONLY and cid and len(" ".join(order)) < TARGET_MIN:
        have = {w.upper() for w in order}
        for kw in datalab_words(cid, own_name, have,
                                kin=spec_txt + " " + " ".join(order),
                                log=log):
            cur = len(" ".join(order))
            if cur >= TARGET_MIN:
                break
            if kw.upper() in have or len(" ".join(order + [kw])) > MAX_LEN:
                continue
            if not cap_word(kw):
                continue
            order.append(kw)
            adds.append([kw])
            have.add(kw.upper())
            extra_used.append(kw)
            steps.append({"kw": kw, "added": [kw],
                          "len": len(" ".join(order)), "datalab": True})

    # 그래도 공통 낱말이 빠졌으면 직접 넣는다. 상품명 칸은 직접 입력이
    # 되므로(handleManualInput) 후보에 없어도 넣을 수 있다.
    for w in ([] if CLICK_ONLY else (must or [])):
        cur = " ".join(order)
        # 띄어쓰기를 빼고 견준다 - '냄새 차단' 이 있는데 '냄새차단' 을 또
        # 넣어 같은 말이 두 번 들어갔다(2026-09-06)
        if w.replace(" ", "") in cur.replace(" ", ""):
            continue
        # 붙여서 줄일 것을 기대하지 않는다. 그러면 뒤가 한 덩어리로 뭉친다
        # ('배수구욕실바닥배수냄새차단'). 띄어쓴 그대로 29자에 들어와야 넣는다
        if len(cur + " " + w) > TARGET_MAX:
            continue
        if not cap_word(w):
            continue
        order.append(w)
        steps.append({"kw": w, "added": [w], "len": len(" ".join(order)),
                      "must": True})

    title = " ".join(order)
    return {"picked": picked, "order": order, "title": title,
            "len": len(title), "pool": len(pool), "ai": len(ai_order),
            "steps": steps, "datalab": extra_used}


def borrow_from_name(own_name: str, order: list) -> list:
    """
    원상품명에서 빌려 쓸 낱말. **용량·수량을 먼저** 준다.

    후보가 바닥나 25자를 못 채울 때 쓴다. 그 상품 이름에 실제로 있는 말이라
    틀릴 위험이 가장 낮다(사용자 지침 2026-09-05).
    """
    have = {w.upper() for w in order}
    # 용량·수량은 '12개' 처럼 숫자와 단위가 붙어 있어야 뜻이 산다.
    # 낱말로 쪼개면 '12' 와 '개' 로 갈라져 쓸 수 없다(2026-09-06).
    qty = [w for w in _QTY_IN_TEXT.findall(own_name or "")
           if w.upper() not in have]
    words = [w for w in _TOKEN_RE.findall(own_name or "")
             if len(w) >= 2 and w.upper() not in have]
    words = qty + [w for w in words if w not in qty]
    spec, plain = [], []
    for w in words:
        (spec if tag_auto.specs_of(w) else plain).append(w)
    out, seen = [], set()
    for w in spec + plain:
        if w.upper() in seen:
            continue
        seen.add(w.upper())
        out.append(w)
    return out


def tighten(title: str, lo: int = TARGET_MIN, hi: int = TARGET_MAX) -> str:
    """
    길이를 목표 구간(25~29자)으로 다듬는다. 사람이 하는 방식 그대로다.

    hi 를 넘으면 **뒤에서부터 띄어쓰기를 지운다** — '빨간 색연필' -> '빨간색연필'.
    띄어쓰기 하나를 지울 때마다 한 글자가 준다. 그래도 안 되면 마지막 낱말을
    뺀다(그 경우 lo 밑으로 떨어지면 다시 붙인다 - 짧은 쪽이 더 나쁘다).
    """
    t = " ".join(title.split())
    while len(t) > hi and " " in t:      # 뒤쪽 띄어쓰기부터 지운다
        i = t.rfind(" ")
        t = t[:i] + t[i + 1:]
    if len(t) <= hi:
        return t
    # 띄어쓰기를 다 지워도 넘치면 마지막 낱말을 뺀다
    parts = " ".join(title.split()).split(" ")
    while len(parts) > 1:
        cut = " ".join(parts[:-1])
        if len(cut) <= hi:
            return cut if len(cut) >= lo else t[:hi]
        parts = parts[:-1]
    return t[:hi]


def title_check(session, page: dict, title: str, timeout=60) -> dict:
    """
    검색최적화. 화면의 [검색최적화] 버튼과 같은 요청이다.

    반환 {'ok': 이상없음, 'cause': [...], 'etc': [...]}
    """
    if not page.get("token") or not page.get("ip"):
        raise RuntimeError("상품명 탭에서 token/ip 를 찾지 못했습니다")
    data = {"token": page["token"], "uid": page["uid"],
            "commerce_id": page["commerce_id"], "title": title}
    for i, c in enumerate(page["cate"], 1):
        if c:
            data[f"cate_{i}"] = c
    r = session.post(f"http://{page['ip']}:3403/ss_site/title_check",
                     data=data, timeout=timeout)
    r.encoding = "utf-8"
    d = json.loads(r.text)
    if d.get("msg") == "error":
        raise RuntimeError(f"title_check 오류: {str(d.get('result'))[:120]}")
    res = d.get("result") or {}
    cause = res.get("cause") or []
    etc = res.get("etc") or []
    return {"ok": not cause and not etc, "cause": cause, "etc": etc}


def save_title(session, no, title: str, picked_keywords: list,
               shipping: str = "", timeout=60) -> dict:
    """
    상품명 저장. onTitleSave() 를 그대로 재현한다.

    title_tag 에는 눌러둔 키워드를 쉼표로 이어 보낸다 — 사이트가 어떤 키워드로
    만든 상품명인지 기록해 두는 값이다.
    """
    r = session.post(
        tabs.URL_TITLE1.format(no=no),
        data={"mode": "save", "title": title,
              "title_tag": ",".join(picked_keywords), "shipping": shipping or ""},
        timeout=timeout)
    r.encoding = "utf-8"
    if "loginForm" in r.text:
        raise RuntimeError("세션 만료")
    got = _input_val(r.text, "finalProductNameInput")
    return {"ok": got.strip() == title.strip(), "saved": got, "sent": title}

def build_and_check(session, page: dict, own_name: str, *, tries: int = 4,
                    brand: str = "", maker: str = "", use_ai: bool = True,
                    cid: str = "", avoid: dict = None, must: list = None,
                    own_uniq: set = None, log=print) -> dict:
    """
    상품명을 만들고 검색최적화를 통과할 때까지 다듬는다.

    사이트가 자주 돌려주는 지적이 '유의어 포함 반복된 단어' 다 — '전동드릴'
    '전동' '전동드라이버' 처럼 같은 뿌리가 겹치면 거절한다(2026-09-05 실측).
    그 단어를 빼고 다시 만든다.

    반환 {'title', 'picked', 'check', 'ok', 'tries'}
    """
    banned_terms = set()      # 아예 빼는 말 (금지어·가격어 등)
    cap_terms = set()         # 개수만 줄이는 말 (유의어 반복 지적)
    last = None
    for attempt in range(1, tries + 1):
        # 1차는 그냥, 2차부터는 지적받은 말을 2개까지, 3차는 1개까지만.
        # 아예 빼버리면 그 상품의 핵심어가 사라진다 ('놀이' 를 다 빼면
        # 주방놀이·소꿉놀이가 통째로 없어진다).
        cap = 0 if attempt <= 1 else max(1, 4 - attempt)
        b = build_title(page, own_name, brand=brand, maker=maker,
                        use_ai=use_ai, cid=cid, ban_terms=banned_terms,
                        cap_terms=cap_terms, cap=cap, avoid=avoid,
                        must=must, own_uniq=own_uniq, log=lambda *_: None)
        title = tighten(b["title"])
        if not title:
            return {"title": "", "picked": [], "check": None, "ok": False,
                    "tries": attempt}
        chk = title_check(session, page, title)
        last = {"title": title, "picked": b["picked"], "check": chk,
                "ok": chk["ok"], "tries": attempt}
        if chk["ok"]:
            return last
        hit = {t for c in chk["cause"] for t in (c.get("term") or [])}
        log(f"  [상품명] {attempt}차 지적 {sorted(hit)} : "
            + "; ".join(c.get("cause", "")[:40] for c in chk["cause"][:2]))
        # '유의어 반복' 은 그 말을 다 빼면 핵심어가 사라지므로 개수만 줄이고,
        # 금지어·가격어 같은 지적은 그 말을 아예 뺀다.
        why = " ".join(c.get("cause", "") for c in chk["cause"])
        if "반복" in why:
            cap_terms |= hit
        else:
            banned_terms |= hit
        if not hit:
            break                       # 무엇을 뺄지 모르면 그만
    return last or {"title": "", "picked": [], "check": None, "ok": False,
                    "tries": 0}

def normalize_terms(terms: list, syn: dict = None) -> list:
    """
    형태소를 대표어로 모은다.

    로하스는 '양면' 을 누르면 '리버시블' 을 넣는다. 표기만 다른 말이 두 번
    들어가면 검색최적화가 '유의어 반복' 으로 막으므로, 미리 같은 것으로 보고
    글자수를 셈해야 한다. 사전은 `synonym` 테이블에서 온다(로하스가 실제로
    쓰는 대응을 긁어 모은 것).
    """
    from .. import db as _db

    if syn is None:
        syn = _db.synonym_map(min_seen=3)
    out = []
    for t in terms or []:
        u = t
        seen = {u}
        while True:
            v = syn.get(u)
            if not v or v in seen:
                break
            u = v
            seen.add(u)
        out.append(u)
    return out


def preview_click(order: list, terms: list, syn: dict = None) -> dict:
    """
    이 키워드를 누르면 어떻게 되는지 **누르기 전에** 계산한다.

    반환 {'added': 새로 들어갈 말, 'len': 눌렀을 때 글자수, 'skip': 이유}
    화면의 글자수 카운터(`wordOrder.join(' ').length`)와 같은 값이다.
    """
    norm_order = normalize_terms(order, syn)
    new = []
    for t, n in zip(terms or [], normalize_terms(terms, syn)):
        if n not in norm_order and t not in order:
            new.append(t)
            norm_order.append(n)
    if not new:
        return {"added": [], "len": len(" ".join(order)), "skip": "헛클릭"}
    n = len(" ".join(order + new))
    skip = ""
    if n > MAX_LEN:
        skip = f"{MAX_LEN}자 초과"
    return {"added": new, "len": n, "skip": skip}


def mine_synonyms(candidates: list) -> list:
    """
    후보 목록에서 로하스의 **동의어 대응**을 뽑는다.

    로하스는 키워드를 형태소로 쪼개면서 표기를 대표어로 바꾼다.

        relKeyword '스텐브러쉬'  ->  terms ['스테인리스', '브러쉬']
                    ~~~~                    ~~~~~~~~~~
        남는 표기 '스텐' 이 대표어 '스테인리스' 에 대응한다

    그래서 '스텐...' 을 눌러도 이미 '스테인리스' 가 들어가 있으면 화면에
    아무 변화가 없다. 이 대응을 모아두면 헛클릭을 미리 걸러낼 수 있다.

    반환 [(표기, 대표어, 예시키워드), ...]
    """
    out = []
    for d in candidates or []:
        kw = (d.get("relKeyword") or "").strip()
        terms = [t for t in (d.get("terms") or []) if t]
        if not kw or not terms:
            continue
        if "".join(terms) == kw.replace(" ", ""):
            continue                      # 그냥 쪼갠 것 - 바뀐 표기가 없다
        present = [t for t in terms if t in kw]
        missing = [t for t in terms if t not in kw]
        if len(missing) != 1:
            continue                      # 둘 이상 바뀌면 어느 쪽인지 모른다
        rest = kw
        for t in present:
            rest = rest.replace(t, "", 1)
        rest = rest.strip()
        if rest and rest != missing[0]:
            out.append((rest, missing[0], kw))
    return out
