"""Find Steam game folders (Steam path from the registry plus every library in libraryfolders.vdf)."""
from __future__ import annotations

import re
from pathlib import Path


def library_dirs() -> list[Path]:
    roots: list[Path] = []
    try:
        import winreg
        for hive, key, value in [(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                                 (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath")]:
            try:
                with winreg.OpenKey(hive, key) as k:
                    roots.append(Path(winreg.QueryValueEx(k, value)[0]))
            except OSError:
                pass
    except ImportError:  # not Windows
        pass
    roots.append(Path(r"C:\Program Files (x86)\Steam"))
    libs: list[Path] = []
    for r in roots:
        libs.append(r)
        vdf = r / "steamapps" / "libraryfolders.vdf"
        if vdf.exists():
            text = vdf.read_text(encoding="utf-8", errors="replace")
            libs += [Path(p.replace("\\\\", "\\")) for p in re.findall(r'"path"\s+"([^"]+)"', text)]
    seen, out = set(), []
    for p in libs:
        k = str(p).lower()
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


def game_dir(folder: str, must_contain: str) -> Path | None:
    """steamapps/common/<folder> in the first library where <must_contain> exists inside it."""
    for lib in library_dirs():
        d = lib / "steamapps" / "common" / folder
        if (d / must_contain).exists():
            return d
    return None
