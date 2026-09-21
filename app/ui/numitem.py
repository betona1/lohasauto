"""
**표 정렬을 숫자로** 한다.

`QTableWidgetItem` 은 화면에 적힌 글자로 비교한다. 그래서 광고비 열을
정렬하면 `10,356원` 이 `9,000원` 보다 앞에 오고, 90원이 맨 위에 뜬다
(2026-09-16 사용자: 광고비로 소팅하면 90원부터 최고가로 뜬다).

고치는 방법은 둘이다.

    1. 숫자를 `Qt.EditRole` 에 넣는다  → 화면 글자가 `38582` 로 바뀐다
    2. 정렬만 숫자로 바꾼다            → 화면은 `38,582원` 그대로

`38,582원` 처럼 단위를 붙여 읽는 편이 훨씬 낫기 때문에 2번을 쓴다.
비교값은 `Qt.UserRole` 에 따로 넣고 `__lt__` 만 갈아 끼운다.

글자에서 숫자를 뽑는 일은 `parse_num` 이 한다 — 쉼표·단위(`원 % 개 건
회 명 일`)·부호를 떼고 실수로 읽는다. 숫자가 아니면 `None` 이고, 그때는
원래대로 글자로 비교한다(상품명·LCP코드 열).
"""
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QTableWidgetItem

# 앞의 +/- 와 소수점까지 읽는다. `-35,035원` `+1,200원` `27.0%` `1.2배`
_NUM = re.compile(
    r"^[\s]*([+-]?[\d,]*\.?\d+)\s*"
    r"(원|%p|%|개입|개|건|회|명|일|시|분|초|위|점|자|배|번|p"
    r"|EA|매|ml|g|kg|cm|mm)?\s*$",
    re.IGNORECASE)


def parse_num(s):
    """표에 적힌 글자에서 비교용 숫자를 뽑는다. 못 읽으면 None."""
    if isinstance(s, (int, float)) and not isinstance(s, bool):
        return float(s)
    t = str(s).strip()
    if not t or t in ("-", "—", ""):
        return None
    m = _NUM.match(t)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


class NumItem(QTableWidgetItem):
    """
    숫자로 비교하는 칸.

    `Qt.UserRole` 에 숫자가 있으면 그것으로, 없으면 글자로 비교한다.
    한쪽만 숫자인 경우(합계 줄의 `-` 같은 것)는 **숫자를 위로** 올린다 —
    빈칸이 1위로 올라오면 정렬한 보람이 없다.
    """

    def __lt__(self, other):
        a = self.data(Qt.UserRole)
        b = other.data(Qt.UserRole) if isinstance(other, QTableWidgetItem) \
            else None
        if a is not None and b is not None:
            return a < b
        if a is not None:
            return False        # 숫자가 글자보다 뒤 → 내림차순에서 위로
        if b is not None:
            return True
        # **`super().__lt__(other)` 를 부르면 안 된다.** PySide6 에서는
        # C++ 쪽이 파이썬 재정의를 다시 불러 무한 재귀에 빠진다 — 창도
        # 못 띄우고 멈췄다(2026-09-16). 글자 비교는 여기서 직접 한다.
        ta = self.text() or ""
        tb = (other.text() or "") if isinstance(other, QTableWidgetItem) \
            else str(other)
        return ta.casefold() < tb.casefold()


def make_item(v, right=False, color="", bold=False, num=None):
    """
    표 칸 하나. 글자는 준 그대로 두고 **정렬만 숫자로** 맞춘다.

        num  비교에 쓸 숫자를 직접 줄 때. 안 주면 글자에서 읽는다.
    """
    it = NumItem(str(v))
    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
    n = parse_num(v) if num is None else float(num)
    if n is not None:
        it.setData(Qt.UserRole, n)
    if right:
        it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    if color:
        it.setForeground(QColor(color))
    if bold:
        f = it.font()
        f.setBold(True)
        it.setFont(f)
    return it
