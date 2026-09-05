"""
실행파일 빌드 — 파이썬 없는 PC 에 폴더째 복사해 쓰기 위한 것.

    python -X utf8 build_exe.py

만들어지는 것 : dist/lohasauto/
    lohasauto.exe          GUI
    .env                   설정 (빌드에 넣지 않고 여기로 복사한다)
    data/banned_words.txt  금지어 사전
    사내망_실행.bat / 외부망_실행.bat

.env 를 exe 안에 넣지 않는 이유 — API 키와 SSH 비밀번호가 들어 있어
exe 를 뜯으면 그대로 나온다. 밖에 두고 읽게 한다(config.app_root()).
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist" / "lohasauto"


def run(cmd):
    print(">", " ".join(cmd), flush=True)
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode:
        sys.exit(r.returncode)


def main():
    run([sys.executable, "-X", "utf8", "-m", "PyInstaller",
         "lohasauto.spec", "--noconfirm", "--clean"])

    if not DIST.exists():
        sys.exit(f"빌드 결과가 없습니다: {DIST}")

    # exe 가 실행 중에 읽는 것들을 exe 옆으로 옮긴다
    (DIST / "data").mkdir(exist_ok=True)
    shutil.copy2(ROOT / "data" / "banned_words.txt", DIST / "data")

    for name in ("사내망_실행.bat", "외부망_실행.bat"):
        src = ROOT / name
        if src.exists():
            # exe 용으로 파이썬 호출 부분만 바꾼 사본을 만든다
            txt = src.read_text(encoding="cp949")
            txt = txt.replace('start "" pythonw -X utf8 main.py',
                              'start "" lohasauto.exe')
            txt = txt.replace("python -X utf8 tools\check_env.py", "")
            for line in ("where python", "if errorlevel 1 goto NOPYTHON",
                         "python -c", "python -m pip install"):
                txt = "\n".join(l for l in txt.splitlines()
                                if not l.strip().startswith(line))
            (DIST / name).write_text(txt, encoding="cp949")

    env = ROOT / ".env"
    if env.exists():
        shutil.copy2(env, DIST / ".env")
        print("  .env 를 복사했습니다 — 배포 전에 내용을 확인하세요"
              " (API 키·비밀번호 포함)")
    else:
        shutil.copy2(ROOT / ".env.example", DIST / ".env")
        print("  .env 가 없어 .env.example 를 넣었습니다. 값을 채우세요")

    size = sum(f.stat().st_size for f in DIST.rglob("*") if f.is_file())
    print(f"\n완료 — {DIST}  ({size / 1048576:.0f} MB)")
    print("이 폴더를 통째로 복사해 lohasauto.exe 를 실행하면 됩니다.")


if __name__ == "__main__":
    main()
