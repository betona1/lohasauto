# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 빌드 설정.

파이썬이 깔려 있지 않은 직원 PC 에 그대로 복사해 쓰려고 만든다.
한 폴더(onedir) 방식이다 — onefile 은 실행할 때마다 임시폴더에 300MB 를
풀어서 시작이 10초 넘게 걸리고, PySide6 플러그인이 종종 깨진다.

  dist/lohasauto/
      lohasauto.exe        GUI (콘솔 없음)
      .env                 설정 - 빌드에 넣지 않는다. 배포할 때 따로 넣는다
      data/banned_words.txt

.env 를 실행파일 안에 넣지 않는 것이 중요하다. API 키와 SSH 비밀번호가
들어 있어서 exe 를 뜯으면 그대로 나온다. exe 옆에 두고 읽게 한다.

    python -X utf8 -m PyInstaller lohasauto.spec --noconfirm
"""
import os

block_cipher = None

# 지연 임포트라 PyInstaller 가 스스로 못 찾는 것들
hidden = [
    "pymysql", "pymysql.cursors",
    "paramiko",
    "pyperclip", "pyautogui",
    "requests", "dotenv",
    "selenium", "selenium.webdriver",
]

# 데이터 파일은 번들에 넣지 않는다.
#   PyInstaller 6 은 datas 를 exe 옆이 아니라 `_internal/` 안에 넣는데,
#   config.app_root() 는 exe 가 있는 폴더를 기준으로 삼는다. 경로가 어긋난다.
#   게다가 금지어 사전은 운영 중에 바뀌는 파일이라 밖에 있는 편이 낫다.
#   build_exe.py 가 빌드 뒤에 exe 옆으로 복사한다.
datas = []

a = Analysis(
    ["main.py"],
    pathex=[os.path.abspath(".")],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 안 쓰는 무거운 것들. PySide6 는 모듈이 많아 이걸 빼면 용량이 크게 준다.
    excludes=[
        # 이 PC 에 PyQt5 도 깔려 있다. PyInstaller 는 Qt 바인딩이 둘이면
        # 빌드를 중단한다 ("attempt to collect multiple Qt bindings").
        "PyQt5", "PyQt6", "PySide2",
        "tkinter", "matplotlib", "numpy", "pandas", "scipy", "PIL", "cv2",
        # 실제로 쓰는 PySide6 모듈은 QtCore / QtGui / QtWidgets / QtCharts 넷뿐이다.
        # QtCharts 를 뺐다가 exe 가 뜨자마자 ModuleNotFoundError 로 죽었다.
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
        "PySide6.QtQuick", "PySide6.QtQml", "PySide6.Qt3DCore",
        "PySide6.QtMultimedia", "PySide6.QtDataVisualization",
        "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning",
        "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtTest",
        "PySide6.QtDesigner", "PySide6.QtHelp",   # QtOpenGL 은 QtCharts 가 쓴다
        "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtSql",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="lohasauto",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # GUI 라 콘솔 창을 띄우지 않는다
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="lohasauto",
)
