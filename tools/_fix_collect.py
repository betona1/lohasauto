"""collect.py 의 깨진 정규식 2줄 복구 (1회용)."""
import pathlib
import sys

p = pathlib.Path(__file__).resolve().parent.parent / "app" / "lohas" / "collect.py"
lines = p.read_text(encoding="utf-8").splitlines(keepends=True)

fixed = 0
for i, ln in enumerate(lines):
    if ln.startswith("_RE_FK = re.compile("):
        lines[i] = (
            "_RE_FK = re.compile(\n"
            "    r'<textarea[^>]*name=[\"\\']fk[\"\\'][^>]*>(.*?)</textarea>', re.S)\n"
        )
        fixed += 1
    elif 're.sub(r"<(script|style)[^>]*>.*?</>"' in ln:
        lines[i] = ln.replace('.*?</>"', '.*?</\\\\1>"')
        fixed += 1

if fixed != 2:
    print(f"!! 예상 2줄, 실제 {fixed}줄")
    sys.exit(1)

p.write_text("".join(lines), encoding="utf-8")
print("2줄 복구 완료")
