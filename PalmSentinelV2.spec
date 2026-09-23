# -*- mode: python ; coding: utf-8 -*-
"""
PalmSentinel V2 -- PyInstaller spec for the standalone desktop build.

    pyinstaller --noconfirm PalmSentinelV2.spec

Produces ``dist/PalmSentinelV2/PalmSentinelV2.exe``: a real Windows GUI
application with its own Python interpreter, its own copy of Flask, OpenCV,
NumPy, Pillow and pywebview, and its own templates, stylesheets and demo
orthomosaic.  It needs no Python on the machine it runs on.

It deliberately does not repeat either of the previous version's packaging
faults:

* **The entry point is the desktop app, not the server.**  The previous spec
  packaged ``app.py`` -- the console Flask server -- with ``console=True``, so the
  "compiled distribution" opened a terminal rather than the application window.
  This packages ``desktop_app.py`` as a windowed GUI executable.
* **The imagery it ships is stated, exactly.**  The previous spec listed
  ``templates`` and ``static`` and no imagery at all, which is why its packaged
  build came up with an empty library.  This ships the one demo orthomosaic and
  *nothing else* from ``data/``: the rest of that directory is whoever is running
  the tool -- private flights, often hundreds of megabytes each -- and packaging
  it would both bloat the build and leak the operator's imagery into a
  distributable.  The library directory is writable state; see ``paths.py``.

Build notes:

* ``bottle`` is a hard requirement of the windowed build even though this project
  never imports it directly: pywebview serves the WSGI application through a
  Bottle server.  It is listed as a hidden import because no module here imports
  it by name, so static analysis would otherwise drop it.
* ``console=False`` makes stdout best-effort (PyInstaller leaves ``sys.stdout``
  as ``None``); ``desktop_app.py --selftest`` writes its report to a file for
  exactly that reason.
* UPX is off.  It buys little against OpenCV's DLLs and is a reliable way to
  attract false antivirus positives on a freshly built binary.
"""

import os

block_cipher = None

# The demo orthomosaic is named individually.  Do not replace this with
# ('data', 'data') -- that would package the operator's private imagery.
datas = [
    ('templates', 'templates'),
    ('static', 'static'),
    (os.path.join('data', 'demo_palm_estate.jpg'), 'data'),
]

hiddenimports = [
    # pywebview resolves its toolkit and HTTP server dynamically.
    'webview.platforms.edgechromium',
    'webview.platforms.winforms',
    'webview.http',
    'bottle',
    'proxy_tools',
    # pywebview drives the WebView2 control through .NET.
    'clr_loader',
    'pythonnet',
    'clr',
    # Imported lazily inside ImageStore.decode as a JPEG/TIFF fallback decoder.
    'PIL.Image',
    'PIL.ImageFile',
    # Flask pulls these in dynamically through its template loader.
    'jinja2.ext',
]

# Toolkits and scientific stacks that are never reached at runtime.  Excluding
# them keeps the build from carrying a second GUI framework.
excludes = [
    'tkinter',
    'matplotlib',
    'PyQt5',
    'PyQt6',
    'PySide2',
    'PySide6',
    'IPython',
    'pytest',
    'notebook',
    'pandas',
    'scipy',
    'unittest',
]

a = Analysis(
    ['desktop_app.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='PalmSentinelV2',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # a windowed application, not a console server
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='PalmSentinelV2',
)
