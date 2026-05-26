# -*- mode: python ; coding: utf-8 -*-
# V3 카카오 매니저 — onedir (자동 업데이트 zip 교체용)

hiddenimports = [
    'win32timezone',
    'win32gui',
    'win32api',
    'win32con',
    'win32ui',
    'pyperclip',
    'requests',
    'packaging',
    'PyQt6.QtCore',
    'PyQt6.QtGui',
    'PyQt6.QtWidgets',
    'updater',
    'advanced_updater',
    'auto_update_runner',
    'v3_workspace_store',
    'chat_context_reader',
    'kakao_multi_instance',
]

excludes = [
    'PyQt5',
    'matplotlib',
    'IPython',
    'jupyter',
    'pytest',
    'black',
    'torch',
    'tensorflow',
    'sklearn',
    'django',
    'langchain',
    'openai',
]

a = Analysis(
    ['entry_v3_workspace.py'],
    pathex=['tools\\v3_spike'],
    binaries=[],
    datas=[
        ('Pretendard-Regular.ttf', '.'),
        ('version.py', '.'),
        ('updater.py', '.'),
        ('advanced_updater.py', '.'),
        ('auto_update_runner.py', '.'),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='KakaoManager-V3',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
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
    upx=True,
    upx_exclude=[],
    name='KakaoManager-V3',
)
