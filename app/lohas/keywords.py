"""
키워드 후보 필터 / 우선순위 규칙.

04_로하스(lohas-automation)의 판단 로직을 이식했다. 브라우저와 무관한 순수 파이썬이라
그대로 옮겨도 동작하며, 아래 두 가지를 개선했다.

  1) Gemini 실패 시 원본은 빈 목록을 돌려줘 태그가 0개가 됐다.
     -> rule_pick() 으로 규칙 기반 선택이 실제로 동작하게 했다.
  2) 금지어 사전을 data/banned_words.txt 로 분리했다.
"""
import re
from functools import lru_cache

from .. import config

# 숫자/색상/갯수가 들어간 키워드는 상품명·태그로 부적합해 제외한다
_COLORS = (
    "빨강", "파랑", "노랑", "초록", "검정", "검점", "흰색", "화이트", "블랙",
    "레드", "블루", "그린", "옐로", "핑크", "퍼플", "브라운", "그레이", "네이비",
    "베이지", "카키", "민트", "오렌지", "실버", "골드", "은색", "금색", "회색",
    "보라", "주황", "남색", "하늘색", "연두",
)
_COUNT_RE = re.compile(r"\d+\s*(개|매|p|P|EA|ea|세트|셋트|장|팩|入|입)")
_DIGIT_RE = re.compile(r"\d")


@lru_cache(maxsize=1)
def banned_set() -> frozenset:
    """상품명 금지어 사전 (data/banned_words.txt)."""
    out = set()
    try:
        with open(config.BANNED_FILE, encoding="utf-8", errors="replace") as f:
            for line in f:
                w = line.strip()
                if w and not w.startswith("#"):
                    out.add(w)
    except Exception:
        pass
    return frozenset(out)


def has_banned(name: str) -> bool:
    """금지어 사전의 단어를 포함하는가."""
    if not name:
        return False
    words = banned_set()
    if name in words:
        return True
    return any(w and w in name for w in words)


def has_digit(name: str) -> bool:
    return bool(_DIGIT_RE.search(name or ""))


def has_color(name: str) -> bool:
    return any(c in (name or "") for c in _COLORS)


def has_count(name: str) -> bool:
    return bool(_COUNT_RE.search(name or ""))


def title_excluded(name: str) -> bool:
    """숫자/색상/갯수가 들어간 키워드는 제외."""
    return has_digit(name) or has_color(name) or has_count(name)


def usable(name: str) -> bool:
    """선택 가능한 키워드인가 (금지어/숫자/색상/갯수 모두 통과)."""
    n = (name or "").strip()
    return bool(n) and not has_banned(n) and not title_excluded(n)


def filter_candidates(rows: list, tag_only: bool = True) -> list:
    """
    표에서 읽은 행 목록에서 선택 가능한 후보만 남긴다.
    rows: [{name, prio, views, tag, used, banned}, ...]
    """
    out = []
    for r in rows:
        if tag_only and not r.get("tag"):
            continue
        if r.get("banned") or r.get("used"):     # 금지어열/사용여부열 채워진 행 제외
            continue
        if not usable(r.get("name", "")):
            continue
        out.append(r)
    return out


def rank(cands: list) -> list:
    """
    선택 순서 정렬.
      1순위 조회수 1000 이하(경쟁 낮음)  2순위 우선순위 높은 것(태그사전+추천)
      3순위 조회수 낮은 것
    """
    return sorted(
        cands,
        key=lambda c: (0 if (c.get("views") or 0) <= 1000 else 1,
                       -(c.get("prio") or 0),
                       c.get("views") or 0),
    )


def rule_pick(cands: list, n: int) -> list:
    """
    규칙 기반 선택 (Gemini 없거나 실패했을 때).

    원본은 이 경우 빈 목록을 돌려줘 태그가 0개로 저장됐다.
    여기서는 정렬 순서대로 n개를 고른다.
    """
    return [c["name"] for c in rank(cands)][:n]


def order_by_rank(cands: list, picked: list, n: int) -> list:
    """
    Gemini 가 고른 이름들을 rank 순서로 재정렬해 최대 n개.
    (관련 없는 키워드로 채우지 않는다 - 04 의 정책 유지)
    """
    picked_set = set(picked)
    return [c["name"] for c in rank(cands) if c["name"] in picked_set][:n]
