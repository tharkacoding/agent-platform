# PyInstaller spec — one-file console agentos.exe (CLI + Textual TUI + host bridge).
# Build: powershell -File build_exe.ps1   (or: pyinstaller --noconfirm --clean agentos.spec)
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hidden = (
    collect_submodules("cli")
    + collect_submodules("bridge")
    + collect_submodules("textual")
    + collect_submodules("rich")          # rich loads unicode tables / markdown lazily
    + collect_submodules("uvicorn")
    + collect_submodules("pywinauto")
    + collect_submodules("comtypes")
    + ["mss", "mss.windows", "pyautogui", "pygetwindow", "pymsgbox", "pyscreeze", "pytweening", "mouseinfo",
       "win32api", "win32con", "win32gui", "pywintypes"]
)
datas = collect_data_files("textual") + collect_data_files("rich")

a = Analysis(
    ["agentos_entry.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["aap", "runner", "browser_svc", "langgraph", "langchain", "langchain_core", "langchain_openai",
              "torch", "tensorflow", "matplotlib", "pandas", "numpy.tests", "tkinter", "PyQt5", "PySide6",
              "IPython", "jupyter", "notebook", "pytest", "chromadb"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="agentos",
    debug=False,
    strip=False,
    upx=False,
    console=True,
    icon=None,
)
