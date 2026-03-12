# -*- coding: utf-8 -*-
"""
FLEX VIDEO — Build Script
Полная сборка: Cython-компиляция, PyInstaller, update.zip, GitHub Release.
Запускать из папки flex_video/ (или её родителя — скрипт определит сам).

Поддерживаемые платформы:
  macOS  → .app + update_macOS.zip
  Windows → .exe + update_Windows.zip
"""

import sys
import os
import json
import glob
import stat
import shutil
import subprocess
import platform
import traceback
import tempfile
import zipfile
import time
from pathlib import Path
from typing import Optional, List

# Принудительно ставим UTF-8 для вывода в консоль (Windows fix)
try:
    if sys.stdout and sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.stderr and sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ─────────────────────────────────────────────────────────────
#  КОНФИГУРАЦИЯ — ЗАПОЛНИТЕ ПЕРЕД ПЕРВЫМ ЗАПУСКОМ
# ─────────────────────────────────────────────────────────────

APP_NAME     = "FLEX VIDEO"
ENTRY_SCRIPT = "FLEX_VIDEO.py"       # лаунчер в корне flex_video/
CORE_ENTRY   = "core/flex_video.py"  # точка входа PyInstaller

GITHUB_USER = "flexer-soft"
GITHUB_REPO = "flex_video"

# ── Иконки (относительно flex_video/) ──────────────────────
ICON_MAC      = "core/resources/logo.icns"
ICON_WIN      = "core/resources/logo.ico"
ICON_FALLBACK = "core/resources/logo.png"

# ── Исключения из сборки и ZIP ─────────────────────────────
EXCLUDE_DIRS  = {
    "__pycache__", "projects", "_build_temp", "dist", "build",
    "release", ".git", ".vscode", ".DS_Store", "supabase", "profiles",
}
EXCLUDE_FILES = {
    "gui_settings.ini", "license.key", "build.py",
    "FLEX_VIDEO.spec", "FLEX VIDEO.spec", "setup_cython.py",
}
EXCLUDE_EXTS  = {".pyc", ".pyo", ".log", ".db"}

# ── Исключения из Cython-компиляции (оставляем .py) ────────
CYTHON_EXCLUDE_FILES = {ENTRY_SCRIPT}
CYTHON_EXCLUDE_DIRS  = {"core/ui", "core/resources", "profiles"}

# ─────────────────────────────────────────────────────────────

IS_WINDOWS = platform.system() == "Windows"
IS_MACOS   = platform.system() == "Darwin"
SCRIPT_DIR = Path(__file__).parent.resolve()  # flex_video/


# ─────────────────────────────── helpers ────────────────────

def _sep() -> None:
    print("─" * 60)


def _run(cmd: List[str] | str, cwd=None, shell: bool = False,
         check: bool = True) -> subprocess.CompletedProcess:
    """Выполняет команду и печатает хвост её вывода."""
    cmd_str = " ".join(str(c) for c in cmd) if isinstance(cmd, list) else cmd
    print(f"\n▶ {cmd_str}")
    result = subprocess.run(
        cmd, cwd=cwd or str(SCRIPT_DIR),
        shell=shell, check=check,
        capture_output=True, text=True, errors="replace",
    )
    if result.stdout:
        lines = result.stdout.strip().split("\n")
        print("\n".join(lines[-15:] if len(lines) > 15 else lines))
    if result.stderr and result.returncode != 0:
        print("STDERR:", result.stderr.strip()[-2000:])
    return result


def _read_version() -> str:
    vf = SCRIPT_DIR / "version.json"
    try:
        return json.loads(vf.read_text("utf-8")).get("version", "1.0.0")
    except Exception:
        return "1.0.0"


def _write_version(version: str) -> None:
    version_data = json.dumps(
        {"version": version, "build_date": time.strftime("%Y-%m-%d")},
        ensure_ascii=False, indent=2,
    )
    
    # Запись только в корень проекта — Application Support не трогаем.
    # Там нет кода, туда ничего писать не нужно.
    vf = SCRIPT_DIR / "version.json"
    vf.write_text(version_data, "utf-8")
    print(f"  ✓ version.json -> [Project Root] {version}")

def _find_gh() -> str:
    """Находит gh.exe в системе или возвращает 'gh' по умолчанию."""
    candidates = [
        r"C:\Program Files\GitHub CLI\gh.exe",
        r"C:\Program Files (x86)\GitHub CLI\gh.exe",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return "gh"


def _on_rm_error(func, path: str, excinfo) -> None:
    """Обработчик ошибок для shutil.rmtree — снимает флаг read-only и повторяет."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass


def _rmtree(path: Path) -> None:
    """Удаляет папку с несколькими попытками (устойчива к блокировкам на Windows)."""
    if not path.exists():
        return
    for i in range(5):
        try:
            if not path.exists():
                return
            # Переименовываем перед удалением, чтобы освободить путь
            tmp = path.parent / f"{path.name}_del_{int(time.time())}_{i}"
            try:
                os.rename(str(path), str(tmp))
                target = tmp
            except Exception:
                target = path
            shutil.rmtree(target, onerror=_on_rm_error)
            return
        except Exception as e:
            if i == 4:
                print(f"  ⚠ Не удалось очистить {path}: {e}")
                return
            time.sleep(1)


def _find_icon() -> Optional[str]:
    """Возвращает подходящую иконку для текущей платформы."""
    candidates = (
        [ICON_MAC, ICON_WIN, ICON_FALLBACK] if IS_MACOS
        else [ICON_WIN, ICON_MAC, ICON_FALLBACK]
    )
    for c in candidates:
        p = SCRIPT_DIR / c
        if p.exists():
            return str(p)
    return None


def _increment_version(version: str) -> str:
    """Увеличивает патч-версию: 1.0.9 → 1.1.0."""
    try:
        parts = version.split(".")
        if len(parts) != 3:
            return version
        major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
        patch += 1
        if patch > 9:
            patch = 0
            minor += 1
            if minor > 9:
                minor = 0
                major += 1
        return f"{major}.{minor}.{patch}"
    except Exception:
        return version


# ──────────────────────── Step 1: copy to _build_temp ───────

def copy_to_temp(temp_dir: Path) -> None:
    print(f"\n[1] Копирование исходников в {temp_dir.name} ...")
    _rmtree(temp_dir)

    def _ignore(src, names):
        bad = set()
        for n in names:
            if n in EXCLUDE_DIRS or n in EXCLUDE_FILES:
                bad.add(n)
            elif any(n.endswith(e) for e in EXCLUDE_EXTS):
                bad.add(n)
        return bad

    shutil.copytree(str(SCRIPT_DIR), str(temp_dir), ignore=_ignore, dirs_exist_ok=True)
    print(f"  ✓ Скопировано в {temp_dir}")


# ──────────────────────── Step 2: Cython ────────────────────

def _should_compile(rel_path: str) -> bool:
    """True если файл нужно компилировать через Cython."""
    p = Path(rel_path)
    if p.name in CYTHON_EXCLUDE_FILES or p.name == "__init__.py":
        return False
    parts_lower = {part.lower() for part in p.parts}
    for excl in CYTHON_EXCLUDE_DIRS:
        if set(excl.split("/")).issubset(parts_lower):
            return False
    return True


def _write_setup_cython(build_dir: Path, files: List[str]) -> Path:
    content = f"""
# AUTO-GENERATED by build.py
from setuptools import setup, Extension
from Cython.Build import cythonize
import os

files_to_compile = {repr(files)}
files_to_compile = [f for f in files_to_compile if os.path.exists(f)]

if __name__ == "__main__":
    extensions = []
    for f in files_to_compile:
        mod_name = f.replace(".py", "").replace("/", ".").replace("\\\\", ".")
        extensions.append(Extension(mod_name, [f]))

    setup(
        ext_modules=cythonize(
            extensions,
            compiler_directives={{"language_level": "3", "boundscheck": False}},
            annotate=False,
            nthreads=1,
        )
    )
"""
    setup_file = build_dir / "setup_cython.py"
    setup_file.write_text(content, "utf-8")
    return setup_file


def _find_vcvarsall() -> Optional[str]:
    search = [
        r"D:\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvarsall.bat",
        r"D:\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvarsall.bat",
        r"D:\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvarsall.bat",
        r"C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvarsall.bat",
        r"C:\Program Files\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvarsall.bat",
        r"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvarsall.bat",
        r"C:\Program Files (x86)\Microsoft Visual Studio\2019\BuildTools\VC\Auxiliary\Build\vcvarsall.bat",
    ]
    for p in search:
        if os.path.exists(p):
            return p
    try:
        vswhere = r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"
        if os.path.exists(vswhere):
            out = subprocess.check_output(
                [vswhere, "-latest", "-property", "installationPath"], text=True
            ).strip()
            if out:
                candidate = os.path.join(out, r"VC\Auxiliary\Build\vcvarsall.bat")
                if os.path.exists(candidate):
                    return candidate
    except Exception:
        pass
    return None


def _cython_cleanup(build_dir: Path, compiled: List[str]) -> None:
    """Удаляет .py-исходники, .c-файлы и временную папку build/ после Cython."""
    removed = 0
    for rel in compiled:
        py_path = build_dir / rel
        compiled_exts = (
            list(py_path.parent.glob(f"{py_path.stem}*.so"))
            + list(py_path.parent.glob(f"{py_path.stem}*.pyd"))
        )
        if compiled_exts and py_path.exists():
            py_path.unlink()
            removed += 1
    print(f"  ✓ Удалено {removed} скомпилированных .py исходников")

    for c_file in build_dir.rglob("*.c"):
        c_file.unlink()

    cython_build = build_dir / "build"
    if cython_build.exists():
        _rmtree(cython_build)


def compile_with_cython(build_dir: Path) -> None:
    print("\n[2] Компиляция через Cython ...")

    all_py = glob.glob(str(build_dir / "**" / "*.py"), recursive=True)
    to_compile = [
        os.path.relpath(f, str(build_dir))
        for f in all_py
        if _should_compile(os.path.relpath(f, str(build_dir)))
    ]

    if not to_compile:
        print("  ! Нет файлов для компиляции.")
        return

    print(f"  Файлов для компиляции: {len(to_compile)}")
    _write_setup_cython(build_dir, to_compile)

    cmd = [sys.executable, "setup_cython.py", "build_ext", "--inplace"]

    if IS_WINDOWS:
        vcvars = _find_vcvarsall()
        if vcvars:
            print(f"  Найдено MSVC: {vcvars}")
            shell_cmd = (
                f'cmd /c ""{vcvars}" x64 && '
                f'cd /d "{build_dir}" && '
                f'"{sys.executable}" setup_cython.py build_ext --inplace"'
            )
            _run(shell_cmd, cwd=build_dir, shell=True)
        else:
            print("  ⚠ Visual Studio не найдена — пробуем без vcvarsall...")
            _run(cmd, cwd=build_dir)
    else:
        _run(cmd, cwd=build_dir)

    _cython_cleanup(build_dir, to_compile)


# ──────────────────────── Step 3: PyInstaller ───────────────

def build_with_pyinstaller(build_dir: Path, version: str) -> Path:
    print(f"\n[3] Сборка {'EXE' if IS_WINDOWS else 'APP'} через PyInstaller ...")

    icon_path = _find_icon()
    dist_dir  = build_dir / "dist"
    work_dir  = build_dir / "_pyibuild"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--windowed", "--onefile",
        "--name", APP_NAME,
        "--distpath", str(dist_dir),
        "--workpath", str(work_dir),
        "--specpath", str(build_dir),
    ]

    if icon_path and IS_WINDOWS:
        cmd += ["--icon", icon_path]
    elif icon_path and IS_MACOS:
        if icon_path.endswith(".icns"):
            cmd += ["--icon", icon_path]
        else:
            cmd += ["--icon", icon_path]

    # hidden-imports
    ui_modules = [
        f"core.ui.{f.stem}"
        for f in (build_dir / "core" / "ui").glob("*.py")
        if not f.name.startswith("__")
    ] if (build_dir / "core" / "ui").exists() else []

    hidden_imports = [
        "PySide6", "PySide6.QtWidgets", "PySide6.QtCore", "PySide6.QtGui",
        "requests", "openai", "cv2", "numpy", "PIL", "aiohttp",
        "edge_tts", "certifi", "psutil", "platform",
        "json", "logging", "threading", "pathlib", "subprocess",
        "concurrent.futures", "shutil", "glob", "re", "time",
        "core.flex_video", "core.utils",
        "core.modules.pipeline_runner",
        "core.modules.worker", "core.modules.worker_adapter",
        "core.modules.concurrency", "core.modules.api_manager",
        "core.modules.profile_manager", "core.modules.profile_registry",
        "core.modules.project_manager", "core.modules.text_processor",
    ] + ui_modules

    for m in hidden_imports:
        cmd += ["--hidden-import", m]

    # add-data
    sep = ";" if IS_WINDOWS else ":"
    data_dirs = [
        (build_dir / "core" / "ui",        "core/ui"),
        (build_dir / "core" / "resources", "core/resources"),
        (build_dir / "profiles",            "profiles"),
    ]
    for src, dst in data_dirs:
        if src.exists():
            cmd += ["--add-data", f"{src}{sep}{dst}"]

    entry = CORE_ENTRY if (build_dir / CORE_ENTRY).exists() else ENTRY_SCRIPT
    cmd.append(entry)
    _run(cmd, cwd=build_dir)

    # Определяем путь к результату
    if IS_WINDOWS:
        result = dist_dir / f"{APP_NAME}.exe"
    else:
        app_bundle = dist_dir / f"{APP_NAME}.app"
        result = app_bundle if app_bundle.exists() else dist_dir / APP_NAME

    if result.exists():
        print(f"  ✓ Готово: {result}")
    return result


# ──────────────────────── Step 4: update.zip ────────────────

def pack_update_zip(version: str, binary_path: Path, out_dir: Path) -> Path:
    """Упаковывает ТОЛЬКО скомпилированный бинарник + version.json.
    Никаких .py файлов — исходный код не распространяется.
    """
    zip_name = "update_macOS.zip" if IS_MACOS else "update_Windows.zip"
    print(f"\n[4] Упаковка {zip_name} (v{version}) ...")

    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / zip_name

    version_data = json.dumps(
        {"version": version, "build_date": time.strftime("%Y-%m-%d")},
        ensure_ascii=False, indent=2,
    ).encode("utf-8")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # Только бинарник
        if binary_path.is_dir():
            # macOS .app — упаковываем директорию целиком
            for file in binary_path.rglob("*"):
                if file.is_file():
                    arc_name = binary_path.parent.name + "/" + str(file.relative_to(binary_path.parent))
                    zf.write(str(file), arc_name.replace("\\", "/"))
        elif binary_path.exists():
            # Windows .exe
            zf.write(str(binary_path), binary_path.name)

        # version.json
        zf.writestr("version.json", version_data)

    size_kb = zip_path.stat().st_size // 1024
    print(f"  ✓ {zip_name} → {zip_path} ({size_kb} KB)")
    print(f"  ✓ Содержимое: только бинарник + version.json (без .py исходников)")
    return zip_path


# ──────────────────────── Step 5: GitHub Release ────────────

def _gh_available() -> bool:
    try:
        gh = _find_gh()
        r = subprocess.run([gh, "auth", "status"], capture_output=True, text=True)
        return r.returncode == 0
    except FileNotFoundError:
        return False


def _zip_app_if_needed(files: List[Path]) -> List[Path]:
    """Упаковывает папку .app в ZIP (gh CLI не принимает директории)."""
    result = []
    for f in files:
        if f.is_dir() and f.suffix == ".app":
            safe_stem = f.stem.replace(" ", "_")
            zip_base  = f.parent / f"{safe_stem}_macOS"
            print(f"  Упаковка {f.name} → {zip_base.name}.zip...")
            shutil.make_archive(str(zip_base), "zip", root_dir=str(f.parent), base_dir=f.name)
            result.append(Path(str(zip_base) + ".zip"))
        else:
            result.append(f)
    return result


def publish_to_github(version: str, files_to_upload: List[Path]) -> None:
    print(f"\n[5] Публикация GitHub Release v{version} ...")
    tag = f"v{version}"

    if not _gh_available():
        print("  ⚠ GitHub CLI (gh) не найден или не авторизован.")
        print("    Установить: https://cli.github.com/")
        print("    Авторизоваться: gh auth login")
        _create_manual_instructions(version, files_to_upload)
        return

    _update_repo_version_json(version)

    upload_files = _zip_app_if_needed([f for f in files_to_upload if f.exists()])
    str_files    = [str(f) for f in upload_files]

    gh       = _find_gh()
    check_r  = subprocess.run(
        [gh, "release", "view", tag, "--repo", f"{GITHUB_USER}/{GITHUB_REPO}"],
        capture_output=True,
    )

    if check_r.returncode == 0:
        print(f"  ℹ Релиз {tag} уже существует. Добавляем файлы...")
        rel_cmd = [
            gh, "release", "upload", tag,
            "--repo", f"{GITHUB_USER}/{GITHUB_REPO}", "--clobber",
        ] + str_files
    else:
        rel_cmd = [
            gh, "release", "create", tag,
            "--repo", f"{GITHUB_USER}/{GITHUB_REPO}",
            "--title", f"FLEX VIDEO {tag}",
            "--notes", f"Автоматический релиз v{version} от {time.strftime('%Y-%m-%d')}",
        ] + str_files

    try:
        _run(rel_cmd)
        print(f"  ✓ Файлы загружены в релиз {tag}!")
        print(f"    https://github.com/{GITHUB_USER}/{GITHUB_REPO}/releases/tag/{tag}")
    except subprocess.CalledProcessError as e:
        print(f"  ✗ Ошибка публикации: {e}")
        _create_manual_instructions(version, files_to_upload)


def _update_repo_version_json(version: str) -> None:
    """Обновляет version.json и пушит его в репозиторий."""
    try:
        _write_version(version)
        r = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=str(SCRIPT_DIR), capture_output=True,
        )
        if r.returncode == 0:
            subprocess.run(["git", "add", "version.json"], cwd=str(SCRIPT_DIR), check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", f"chore: bump version to {version}"], cwd=str(SCRIPT_DIR), check=False, capture_output=True)
            subprocess.run(["git", "push", "origin", "main"], cwd=str(SCRIPT_DIR), check=True, capture_output=True)
            print(f"  ✓ version.json ({version}) запушен в GitHub!")
        else:
            print("  ⚠ Это не git-репозиторий. version.json не запушен.")
    except Exception as e:
        print(f"  ⚠ Не удалось запушить version.json: {e}")


def _create_manual_instructions(version: str, files: List[Path]) -> None:
    rel_dir = SCRIPT_DIR / "release"
    rel_dir.mkdir(exist_ok=True)

    notes_path = rel_dir / "RELEASE_NOTES.txt"
    notes_path.write_text(
        f"FLEX VIDEO v{version} — Инструкция по ручному релизу\n"
        f"{'=' * 50}\n\n"
        f"1. Откройте: https://github.com/{GITHUB_USER}/{GITHUB_REPO}/releases/new\n\n"
        f"2. Тег: v{version}\n\n"
        f"3. Название: FLEX VIDEO v{version}\n\n"
        f"4. Загрузите эти файлы как Release Assets:\n"
        + "".join(f"   - {f.name}\n" for f in files if f.exists())
        + f"\n5. Скопируйте файл version.json в корень ветки main.\n\n"
        f"6. Нажмите 'Publish release'\n",
        encoding="utf-8",
    )

    for f in files:
        if not f.exists():
            continue
        dst = rel_dir / f.name
        if dst.resolve() == f.resolve():
            continue
        if f.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(str(f), str(dst))
        else:
            shutil.copy2(str(f), str(dst))

    print(f"\n  📁 Файлы для ручной отправки скопированы в: {rel_dir}")
    print(f"  📋 Инструкция: {notes_path}")


# ─────────────────────────── VERSION PROMPT ─────────────────

def _detect_version() -> str:
    """Определяет версию для сборки автоматически (без запроса ввода)."""
    cur_ver  = _read_version()
    next_ver = _increment_version(cur_ver)

    _sep()
    print(f"  FLEX VIDEO — BUILD SCRIPT")
    print(f"  Текущая версия : {cur_ver}")
    print(f"  Платформа      : {'Windows' if IS_WINDOWS else 'macOS'}")
    _sep()

    # Проверяем наличие сборки для текущей ОС в последнем релизе
    zip_name = "update_macOS.zip" if IS_MACOS else "update_Windows.zip"
    proposed = next_ver

    try:
        gh = _find_gh()
        r  = subprocess.run(
            [gh, "release", "view", f"v{cur_ver}", "--json", "assets",
             "--repo", f"{GITHUB_USER}/{GITHUB_REPO}"],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            assets = [a["name"] for a in json.loads(r.stdout).get("assets", [])]
            if zip_name not in assets:
                print(f"  ℹ В релизе v{cur_ver} нет сборки для текущей ОС ({zip_name}).")
                print(f"    Дособираем в существующий релиз v{cur_ver}.")
                proposed = cur_ver
            else:
                print(f"  ℹ В релизе v{cur_ver} сборка {zip_name} уже есть.")
        else:
            print(f"  ℹ Релиз v{cur_ver} не найден на GitHub. Используем текущую версию.")
            proposed = cur_ver
    except Exception as e:
        print(f"  ⚠ Ошибка при проверке GitHub: {e}")

    print(f"  Версия для сборки: {proposed}")
    return proposed


# ─────────────────────────────── MAIN ───────────────────────

def main() -> None:
    version = _detect_version()

    _sep()
    print(f"\n  Версия {version}, запуск полной сборки...\n")
    _sep()

    try:
        release_files: List[Path] = []
        temp_dir    = SCRIPT_DIR / "_build_temp"
        release_dir = SCRIPT_DIR / "release"

        # Step 1 — копируем исходники во временную папку
        copy_to_temp(temp_dir)

        # Step 2 — компилируем через Cython
        compile_with_cython(temp_dir)

        # Step 3 — собираем бинарь через PyInstaller
        binary = build_with_pyinstaller(temp_dir, version)

        # Перемещаем результат в release/ до удаления temp
        if binary.exists():
            release_dir.mkdir(exist_ok=True)
            dst = release_dir / binary.name
            if dst.exists():
                shutil.rmtree(dst) if dst.is_dir() else dst.unlink()
            shutil.move(str(binary), str(dst))
            release_files.append(dst)

        # Удаляем временные файлы
        _rmtree(temp_dir)
        print("  ✓ Временные файлы удалены")

        # Step 4 — пакуем update.zip только из бинарника (без .py исходников)
        _write_version(version)
        zip_path = pack_update_zip(version, dst, release_dir)
        release_files.append(zip_path)

        # Step 5 — публикуем GitHub Release
        publish_to_github(version, release_files)

        _sep()
        print(f"\n  ✅ ГОТОВО! Версия: {version}")
        for f in release_files:
            if f.exists():
                print(f"  📦 {f}")
        _sep()

    except KeyboardInterrupt:
        print("\n  ❌ Прервано пользователем")
        sys.exit(0)
    except Exception as e:
        print(f"\n  ❌ ОШИБКА: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
