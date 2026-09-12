# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

dnd_datas, dnd_binaries, dnd_imports = collect_all('tkinterdnd2')


a = Analysis(
    ['iqplus_batch_print.py'],
    pathex=[],
    binaries=dnd_binaries,
    datas=[('assets', 'assets')] + dnd_datas,
    hiddenimports=['win32timezone'] + dnd_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Qualitrol-Batch-PDF',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets\\q.ico'],
)
