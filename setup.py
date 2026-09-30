"""
setup.py — ставит SmartMusic на машину. Единственное место, где перечислены
зависимости проекта, оттуда же их берёт проверка в main.py.

Запуск:
    python setup.py            поставить недостающие библиотеки
    python setup.py --check    только проверить, ничего не ставить
    python setup.py --list     показать список (без установки)
    python setup.py --force    переставить всё заново, включая уже установленное

Что делает по умолчанию:
    1) смотрит, какие библиотеки уже есть (через importlib.util.find_spec,
       без импорта - импорт тяжёлых пакетов ради проверки это лишние секунды);
    2) ставит ТОЛЬКО недостающие, одной командой pip;
    3) перепроверяет результат и печатает отчёт.

Почему ставим только недостающие, а не все сразу
------------------------------------------------
pip при установке пакета почти всегда трогает и его зависимости. На этой
машине numpy уже поднят до 2.4.6 и стоит рядом с aider-chat, который его
пинит. Если каждый запуск дёргать `pip install` по полному списку, pip будет
пересобирать окружение и в какой-то момент уронит чужой инструмент. Поэтому
установка адресная: «у тебя нет av - ставлю av», а не «ставлю 12 пакетов».

Настройка GSI здесь не живёт
----------------------------
Файл конфигурации Dota 2 для GSI ставит setup_gsi.py. Это разные задачи,
и смешивать их в одном файле не надо: setup.py должен работать на любой
машине, даже где Dota 2 не установлена.
"""

import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent

# --- Кодировка консоли -----------------------------------------------------
# Русская Windows печатает в cp1251, и любой русский текст с эмодзи здесь
# упал бы с UnicodeEncodeError. Конфиг приложения (config.py) делает то же
# самое, но импортировать его отсюда нельзя: он тянет за собой python-dotenv,
# которого на чистой машине ещё нет, и проверка упала бы раньше установки.
import sys as _sys

for _stream in ("stdout", "stderr"):
    _obj = getattr(_sys, _stream, None)
    if _obj is not None and hasattr(_obj, "reconfigure"):
        try:
            _obj.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


# --------------------------------------------------------------------------
# Список зависимостей. Формат тройки:
#     (имя для pip, имя модуля для проверки, зачем нужен)
# --------------------------------------------------------------------------

# Без этих программа не запустится или упадёт с ImportError.
REQUIRED: tuple = (
    ("PyQt6>=6.6.0", "PyQt6", "окно программы"),
    ("python-vlc>=3.0.20123", "vlc", "плеер музыки"),
    ("python-dotenv>=1.0.0", "dotenv", "чтение секретов из .env"),
    ("fastapi>=0.110.0", "fastapi", "GSI-сервер, который слушает Dota"),
    ("uvicorn[standard]>=0.28.0", "uvicorn", "запуск GSI-сервера"),
    ("pynput>=1.7.6", "pynput", "мультимедийные клавиши Windows"),
    ("numpy>=1.26.0", "numpy", "расчёты по звуку"),
    ("pydantic>=2.6.0", "pydantic", "модели FastAPI"),
    ("google-auth>=2.29.0", "google.auth", "авторизация Google"),
    ("google-auth-oauthlib>=1.2.0", "google_auth_oauthlib", "вход в аккаунт"),
    ("google-api-python-client>=2.110.0", "googleapiclient",
     "загрузка треков с YouTube"),
)

# Этих в коде нет, но без них соответствующая функция молча ничего не делает.
# Ставятся по умолчанию, но отсутствие не считается поломкой запуска.
OPTIONAL: tuple = (
    ("yt-dlp>=2024.1.0", "yt_dlp", "скачивание аудиодорожки с YouTube"),
    ("av>=12.0.0", "av", "декодирование звука (заменяет ffmpeg)"),
    ("librosa>=0.10.0", "librosa", "поиск такта; без неё темп считает "
                                 "запасной детектор в audio_analyzer"),
)

# --------------------------------------------------------------------------
# Проверка
# --------------------------------------------------------------------------

def module_installed(module_name: str) -> bool:
    """
    Стоит ли библиотека. Ничего не импортирует - только спрашивает у
    интерпретатора, есть ли такой модуль.

    Импортировать нельзя: librosa и PyQt6 при первом импорте тратят секунды
    на инициализацию, а numpy 2 может упасть с ошибкой, которую тут и лечить
    нечем. find_spec отвечает на тот же вопрос мгновенно.
    """
    import importlib.util
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        # Родительский пакет не найден (например google.auth без google) -
        # это тот же самый ответ: библиотеки нет.
        return False


def missing(required: bool = True) -> list:
    """
    Что не установлено.

    required=True  - только обязательные (то, без чего не запустится)
    required=False - все, включая дополнительные
    """
    wanted = REQUIRED if required else REQUIRED + OPTIONAL
    return [entry for entry in wanted if not module_installed(entry[1])]


def describe_packages() -> None:
    """Печатает весь список со статусом. Для глаза, не для pip."""
    print("Зависимости SmartMusic:")
    for title, group in (("обязательные", REQUIRED), ("дополнительные", OPTIONAL)):
        print(f"\n  {title}:")
        for pip_name, module_name, purpose in group:
            mark = "✅" if module_installed(module_name) else "❌ нет"
            print(f"    [{mark:^7}] {pip_name:<32} {purpose}")


# --------------------------------------------------------------------------
# Установка
# --------------------------------------------------------------------------

def install(entries: list) -> int:
    """
    Ставит переданные пакеты одной командой pip.

    Возвращает 0, если всё встало, и 1 если нет.
    """
    names = [entry[0] for entry in entries]

    print("\n" + "=" * 58)
    print("  Установка библиотек")
    print("=" * 58)
    for pip_name, _module, purpose in entries:
        print(f"  • {pip_name}  ({purpose})")

    command = [sys.executable, "-m", "pip", "install",
               "--disable-pip-version-check", *names]
    print(f"\nВыполняю:\n  {' '.join(command)}\n")
    print("Это может занять несколько минут (librosa и PyQt6 весят много).")
    print("-" * 58)

    try:
        result = subprocess.call(command)
    except KeyboardInterrupt:
        print("\n❌ Установка прервана.")
        return 1

    if result != 0:
        print(f"\n❌ pip вернул код {result}. Установка не завершилась.")
        return 1

    return 0


def run(force: bool = False, check_only: bool = False) -> int:
    """Основной сценарий. Возвращает код для sys.exit."""
    if check_only:
        absent = missing(required=False)
        if not absent:
            print("✅ [Setup] Все библиотеки на месте. Устанавливать нечего.")
            return 0
        print(f"❌ [Setup] Не хватает {len(absent)} библиотек:")
        for pip_name, _module, purpose in absent:
            print(f"    • {pip_name}  ({purpose})")
        print("\nПоставить:  python setup.py")
        return 1

    if force:
        absent = list(REQUIRED) + list(OPTIONAL)
        print("⚠️ [Setup] Режим --force: переустанавливаю всё, включая "
              "уже установленное.")
    else:
        absent = missing(required=False)

    if not absent:
        print("✅ [Setup] Все библиотеки уже установлены. Пропускаю.")
        return 0

    if install(absent) != 0:
        return 1

    # Перепроверяем после установки: pip молча пропускает пакет, если он
    # несовместим с уже стоящим, и программа потом падает на импорте.
    still_absent = missing(required=False)
    if still_absent:
        print("\n❌ [Setup] После установции всё ещё не хватает:")
        for pip_name, _module, purpose in still_absent:
            print(f"    • {pip_name}  ({purpose})")
        print("\nЧаще всего это конфликт версий. Посмотри вывод pip выше.")
        return 1

    print("\n" + "=" * 58)
    print("✅ [Setup] Все библиотеки на месте.")
    print("=" * 58)
    print("\nЧто дальше:")
    print("  1) Настройка Dota 2 (GSI-конфиг):   python setup_gsi.py")
    print("  2) Запуск программы:                python main.py")
    print("\nЕсли сортировка треков не работает, нужен локальный ИИ:")
    print("  ollama serve            - запустить демон")
    print("  ollama pull qwen2.5:3b  - скачать модель")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if "--help" in args or "-h" in args:
        print(__doc__)
        return 0
    if "--list" in args:
        describe_packages()
        return 0
    return run(force="--force" in args, check_only="--check" in args)


if __name__ == "__main__":
    sys.exit(main())
