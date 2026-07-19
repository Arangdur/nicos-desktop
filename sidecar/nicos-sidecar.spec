# -*- mode: python ; coding: utf-8 -*-


# v0.2.1-rc7 -- hallado empaquetando el .dmg real: 'datas' vacío significaba
# que migrations/*.sql y provider_matrix.json NUNCA se incluían en el
# binario. db.py/ai_router.py los buscan con una ruta relativa a __file__,
# que en un binario 'frozen' de PyInstaller resuelve a sys._MEIPASS -- sin
# esto, el sidecar arrancaba pero la tabla 'tasks' nunca se creaba (las
# migraciones no encontraban ningún .sql) y provider_matrix.json caía
# siempre al default hardcodeado. Los destinos ('migrations', '.') son
# relativos a esa misma raíz, para que coincidan exactamente con lo que el
# código ya espera.
a = Analysis(
    ['server.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('migrations', 'migrations'),
        ('provider_matrix.json', '.'),
    ],
    hiddenimports=[],
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
    name='nicos-sidecar',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
