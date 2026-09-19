"""
Build script for packaging client.py as ConnectX.app using py2app.

This must be run ON A MAC (py2app builds native app bundles and can't
cross-compile from another OS) — that's why I can't run or test this
build myself; everything up to this point in the project I ran and
verified directly, this is the first piece that genuinely needs your
actual hardware.

--- One-time setup ---
    pip3 install py2app

--- Build ---
    python3 setup.py py2app

The finished app appears at: dist/ConnectX.app

--- Clean rebuild (do this if a build ever behaves strangely) ---
    rm -rf build dist
    python3 setup.py py2app

--- First launch on any Mac ---
Since this isn't signed with a paid Apple Developer certificate, macOS
Gatekeeper will block the first launch with "cannot be opened because the
developer cannot be verified." Right-click the app -> Open (or allow it
under System Settings -> Privacy & Security) once per machine — after
that first approval, it opens normally like any other app.
"""

from setuptools import setup

APP = ["client.py"]
DATA_FILES = []

OPTIONS = {
    "argv_emulation": False,  # not needed — this app takes no command-line arguments
    "packages": [
        "PIL",           # Pillow — image resizing/encoding for the image-sharing feature
        "pillow_heif",   # HEIC/HEIF support (iPhone/Mac default photo format), bundled
                          # in so app users never need to touch pip themselves
        "sqlite3",       # stdlib, but explicitly listed since py2app's static import
                          # scanner can sometimes miss compiled stdlib extension modules
    ],
    "plist": {
        "CFBundleName": "ConnectX",
        "CFBundleDisplayName": "ConnectX",
        "CFBundleIdentifier": "com.connectx.app",
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleVersion": "1.0.0",
        # Without this, a bundled Tk app renders blurry/pixelated on Retina
        # displays — Tk doesn't declare Retina support on its own, macOS
        # has to be told explicitly via the app's own Info.plist.
        "NSHighResolutionCapable": True,
    },
}

setup(
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
