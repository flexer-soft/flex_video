# -*- coding: utf-8 -*-
"""
FLEX VIDEO Launcher
Запускает приложение и проверяет наличие обновлений через GitHub Releases.
"""

import sys
import os
import json
import threading
import zipfile
import shutil
import tempfile
import subprocess
import logging

# ─────────────────────────────────────────────────────────────
#  НАСТРОЙКИ ОБНОВЛЕНИЯ — заполнить перед раздачей
# ─────────────────────────────────────────────────────────────
import platform
_OS_SUFFIX = "macOS" if platform.system() == "Darwin" else "Windows"

GITHUB_USER   = "flexer-soft"
GITHUB_REPO   = "flex_video"
# URL к version.json в репозитории (main-ветка)
VERSION_URL   = f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/main/version.json"
# URL к архиву обновления (latest GitHub Release asset)
DOWNLOAD_URL  = f"https://github.com/{GITHUB_USER}/{GITHUB_REPO}/releases/latest/download/update_{_OS_SUFFIX}.zip"
# ─────────────────────────────────────────────────────────────

CHECK_TIMEOUT = 4   # секунд на запрос к серверу

def _get_app_support_dir() -> str:
    """Возвращает постоянную папку для данных приложения (всегда доступна для записи)."""
    if sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support/FLEX VIDEO")
    elif sys.platform == "win32":
        base = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "FLEX VIDEO")
    else:
        base = os.path.expanduser("~/.flex_video")
    os.makedirs(base, exist_ok=True)
    return base

APP_SUPPORT_DIR = _get_app_support_dir()
LOG_FILE        = os.path.join(APP_SUPPORT_DIR, "launcher.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [LAUNCHER] %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("launcher")

CURRENT_DIR    = os.path.dirname(os.path.abspath(__file__))
VERSION_LOCAL  = os.path.join(CURRENT_DIR, "version.json")


def _read_local_version() -> str:
    # Если обновление уже установлено, читаем версию из Application Support
    for base_dir in [APP_SUPPORT_DIR, CURRENT_DIR]:
        path = os.path.join(base_dir, "version.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                ver = json.load(f).get("version", "0.0.0")
                if ver != "0.0.0":
                    return ver
        except Exception:
            continue
    return "0.0.0"


def _fetch_remote_version() -> tuple[str | None, str | None]:
    """Возвращает (version, download_url) или (None, None) при ошибке."""
    try:
        import urllib.request
        with urllib.request.urlopen(VERSION_URL, timeout=CHECK_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        version = data.get("version")
        # Можно переопределить URL архива через поле download_url в version.json
        url = data.get("download_url", DOWNLOAD_URL)
        return version, url
    except Exception as e:
        log.warning("Не удалось получить информацию об обновлении: %s", e)
        return None, None


def _version_tuple(v: str) -> tuple:
    try:
        return tuple(int(x) for x in v.split("."))
    except Exception:
        return (0,)


def _show_update_dialog(cur_ver: str, new_ver: str) -> bool:
    """Показывает диалог обновления. Возвращает True если пользователь согласился."""
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance()
        if not app:
            app = QApplication(sys.argv)

        message = (
            f"⚡ Доступна новая версия FLEX VIDEO!\n\n"
            f"Ваша версия:     {cur_ver}\n"
            f"Новая версия:    {new_ver}\n\n"
            f"Обновиться прямо сейчас? Это займёт всего пару секунд.\n"
            f"Если откажетесь — можно обновить позже при следующем запуске."
        )
        reply = QMessageBox.question(
            None,
            "Обновление FLEX VIDEO",
            message,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        return reply == QMessageBox.Yes
    except Exception as e:
        log.warning("Диалог обновления не удалось показать: %s", e)
        return False


def _get_install_dir() -> str:
    """Возвращает директорию куда нужно распаковывать обновление (всегда доступна для записи)."""
    # При App Translocation на macOS путь к .app read-only — пишем в Application Support
    return APP_SUPPORT_DIR


def _download_and_apply(download_url: str) -> bool:
    """Скачивает ZIP обновления и распаковывает поверх текущего каталога."""
    try:
        import urllib.request

        log.info("Скачивание обновления: %s", download_url)

        # Прогресс через PySide6
        _qt_progress_start()

        tmp_zip = os.path.join(tempfile.gettempdir(), "flex_video_update.zip")
        urllib.request.urlretrieve(download_url, tmp_zip)

        install_dir = _get_install_dir()
        log.info("Распаковка обновления в %s", install_dir)
        with zipfile.ZipFile(tmp_zip, "r") as zf:
            zf.extractall(install_dir)

        os.remove(tmp_zip)
        _qt_progress_stop()
        log.info("Обновление успешно применено")
        return True

    except Exception as e:
        _qt_progress_stop()
        log.error("Ошибка при обновлении: %s", e)
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
            app = QApplication.instance()
            if not app:
                app = QApplication(sys.argv)
            QMessageBox.critical(
                None,
                "Ошибка обновления",
                f"Не удалось загрузить обновление:\n{e}\n\nЗапускаем текущую версию."
            )
        except Exception:
            pass
        return False


_progress_dialog = None


def _qt_progress_start():
    global _progress_dialog
    try:
        from PySide6.QtWidgets import QApplication, QProgressDialog
        from PySide6.QtCore import Qt
        app = QApplication.instance()
        if not app:
            app = QApplication(sys.argv)
        _progress_dialog = QProgressDialog("⏳ Загружаем обновление, подождите...", None, 0, 0, None)
        _progress_dialog.setWindowTitle("FLEX VIDEO — Обновление")
        _progress_dialog.setWindowModality(Qt.ApplicationModal)
        _progress_dialog.setCancelButton(None)
        _progress_dialog.setMinimumDuration(0)
        _progress_dialog.resize(340, 80)
        _progress_dialog.show()
        app.processEvents()
    except Exception:
        pass


def _qt_progress_stop():
    global _progress_dialog
    try:
        if _progress_dialog:
            _progress_dialog.close()
            _progress_dialog = None
    except Exception:
        pass


def _restart():
    """Перезапускает приложение после обновления."""
    log.info("Перезапуск приложения...")

    # Режим PyInstaller-сборки (frozen)
    if getattr(sys, "frozen", False):
        executable = sys.executable  # путь к бинарнику внутри .app

        # Независимо от платформы (включая Mac), просто подменяем текущий процесс
        # тем же бинарником. Это безотказно работает даже внутри App Translocation.
        os.execv(executable, [executable] + sys.argv[1:])

    # Режим разработки (обычный .py скрипт)
    python = sys.executable
    script = os.path.abspath(__file__)
    log.info("Перезапуск скрипта: %s %s", python, script)
    if sys.platform == "win32":
        subprocess.Popen([python, script], creationflags=subprocess.CREATE_NEW_CONSOLE)
        sys.exit(0)
    else:
        os.execv(python, [python, script])


def _check_for_updates():
    """Основная логика проверки обновлений. Вызывается синхронно перед запуском."""
    cur_ver = _read_local_version()
    log.info("Текущая версия: %s", cur_ver)

    new_ver, dl_url = _fetch_remote_version()
    if not new_ver:
        return  # нет связи — просто продолжаем

    log.info("Версия на сервере: %s", new_ver)

    if _version_tuple(new_ver) <= _version_tuple(cur_ver):
        log.info("Обновлений нет")
        return

    # Есть более новая версия
    if _show_update_dialog(cur_ver, new_ver):
        success = _download_and_apply(dl_url)
        if success:
            _restart()
            sys.exit(0)  # на случай если exec не сработал (непредвиденное)
    else:
        log.info("Пользователь отказался от обновления, запускаем v%s", cur_ver)


# ─────────────────────────────────────────────────────────────
#  BOOTSTRAP
# ─────────────────────────────────────────────────────────────

def _setup_paths():
    # В скомпилированном бинарнике (PyInstaller) весь код уже внутри бандла.
    # НЕ добавляем Application Support в sys.path — там могут быть открытые .py файлы.
    # Application Support используется ТОЛЬКО для пользовательских данных:
    # license.key, profiles/, projects/, логи.
    if getattr(sys, "frozen", False):
        return  # бандл самодостаточен, ничего извне не нужно

    # Режим разработки: запуск .py напрямую
    core_dir = os.path.join(CURRENT_DIR, "core")
    if os.path.exists(core_dir) and core_dir not in sys.path:
        sys.path.insert(0, core_dir)
    if CURRENT_DIR not in sys.path:
        sys.path.insert(0, CURRENT_DIR)


def _launch_app():
    try:
        from core.flex_video import main
    except ImportError:
        try:
            from flex_video import main
        except ImportError as e:
            log.critical("Не удалось импортировать main: %s", e)
            sys.exit(1)
    main()

def _check_license() -> bool:
    """Проверяет лицензию перед запуском приложения."""
    try:
        from core.license_manager import license_manager as _lic
    except ImportError as e:
        log.error("ОШИБКА: Менеджер лицензий не найден. %s", e)
        return False

    key_file = os.path.join(APP_SUPPORT_DIR, "license.key")
    saved_id = ""
    if os.path.exists(key_file):
        with open(key_file, "r", encoding="utf-8") as f:
            saved_id = f.read().strip()

    if saved_id:
        if _lic.verify_license(saved_id):
            return True
        log.warning("Сохраненая лицензия недействительна: %s", _lic.get_last_error())

    # Запрашиваем через PySide6
    try:
        from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox, QLineEdit
        app = QApplication.instance()
        if not app:
            app = QApplication(sys.argv)

        while True:
            license_key, ok = QInputDialog.getText(
                None,
                "FLEX VIDEO — Авторизация",
                "Введите ваш Лицензионный ключ для активации программы:",
                QLineEdit.Normal,
                ""
            )
            if not ok or not license_key:
                return False

            license_key = license_key.strip()
            if _lic.verify_license(license_key):
                with open(key_file, "w", encoding="utf-8") as f:
                    f.write(license_key)
                info = _lic.get_display_info()
                QMessageBox.information(
                    None,
                    "Успех",
                    f"Лицензия успешно активирована!\n\nТариф: {info['type']}\n{info['expires_str']}"
                )
                return True
            else:
                QMessageBox.critical(
                    None,
                    "Ошибка активации",
                    f"Не удалось проверить лицензию:\n\n{_lic.get_last_error()}"
                )
    except Exception as e:
        log.error("ОШИБКА UI: %s", e)
        return False


if __name__ == "__main__":
    _setup_paths()
    _check_for_updates()   # Загрузит обновления если есть и перезапустится
    
    # После обновлений запрашиваем/проверяем лицензию
    if _check_license():
        _launch_app()
    else:
        log.warning("Запуск отменён, лицензия не активирована.")
        sys.exit(0)

