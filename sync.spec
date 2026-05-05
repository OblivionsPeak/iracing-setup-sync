# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['setup_sync.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'discord',
        'discord.ext.commands',
        'aiohttp',
        'asyncio',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='iRacingSetupSync',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    icon=None,
)
