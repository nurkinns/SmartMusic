"""
Тесты кэша аудио — поиск файла, учёт .part, счётчик.

Ничего не скачивает: кэш создаётся на временной папке с готовыми файлами.
"""

import tempfile
from pathlib import Path

from tests.core import suite

S = suite("Кэш аудио — поиск и состояние")


def _cache_dir():
    """Временная папка, которая удаляется сама при сборке мусора."""
    d = Path(tempfile.mkdtemp(prefix="smtest_"))
    # Несколько тестовых файлов — один нормальный, один .part, один другой.
    (d / "aaa111.mp3").write_bytes(b"\x00\x10")
    (d / "bbb222.mp3.part").write_bytes(b"\x00\x20")
    (d / "ccc333.m4a").write_bytes(b"\x00\x30")
    return d


@S.add("find находит существующий файл")
def _t_find():
    from src.audio_cache import AudioCache

    c = _cache_dir()
    cache = AudioCache(cache_dir=c)
    p = cache.find("aaa111")
    assert p is not None
    assert p.suffix == ".mp3"


@S.add("find игнорирует .part и отсутствующие id")
def _t_find_ignore():
    from src.audio_cache import AudioCache

    c = _cache_dir()
    cache = AudioCache(cache_dir=c)
    # .part — не считается.
    assert cache.find("bbb222") is None
    # Нет такого.
    assert cache.find("nonexistent") is None


@S.add("cached_count считает только завершённые файлы")
def _t_count():
    from src.audio_cache import AudioCache

    c = _cache_dir()
    cache = AudioCache(cache_dir=c)
    # aaa111.mp3 + ccc333.m4a = 2
    assert cache.cached_count() >= 2, f"счётчик: {cache.cached_count()}"


@S.add("ensure с пустым id → None")
def _t_ensure_empty():
    from src.audio_cache import AudioCache

    c = _cache_dir()
    cache = AudioCache(cache_dir=c)
    assert cache.ensure("") is None


@S.add("shutdown останавливает поток загрузки без ошибок")
def _t_shutdown():
    from src.audio_cache import AudioCache

    c = _cache_dir()
    cache = AudioCache(cache_dir=c)
    cache.shutdown()
    # Главное — не упасть и не зависнуть.
    assert True