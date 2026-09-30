"""
audio_cache.py — общий кэш аудиодорожек.

Зачем он общий
--------------
Одни и те же треки нужны в двух местах: анализатору (измерить темп и
энергию) и плееру (воспроизвести). Раньше каждый качал сам, причём
разное:

  анализатор - bestaudio[abr<=64], потому что для измерения качество не
                нужно, и сразу удалял файл;
  плеер      - то же самое, но в хорошем качестве и навсегда.

Итог был один и тот же трек скачивался дважды, а на диске лежал только
один экземпляр. Теперь качает только этот модуль, один раз, в хорошем
качестве, и результат служит обоим.

Почему качество важно для измерения
-----------------------------------
Раньше бралось намеренно дёшево (64 кбит/с). Проверено: на решение это не
влияет. Энергия считается как темп (вес 0.75) и punch (0.25), а punch -
это отношение полос 400-3500 Гц к 30-400 Гц. Даже битрейт 64 кбит режет
частоты примерно выше 11 кГц, то есть полосу punch не трогает.

Что всё-таки пострадало бы
--------------------------
Признак bright (энергия верхних частот) от смены битрейта смещается
заметно: при 64 кбит он врёт. Но в решении он не участвует вообще -
он лежит в замерах справочно. Поэтому менять битрейт можно, не
поднимая ANALYZER_VERSION и не заставляя перезамерывать всю библиотеку.
Стоит знать, когда вернёмся к bright для классификации: тогда либо
остаёмся на высоком битрайте, либо помечаем версии замеров.

Где лежат файлы
---------------
audio_cache/ рядом с проектом, в .gitignore. Файлы не удаляются: кэш,
переживающий перезапуск, - это весь смысл. Папка на 66 треков - около
200 МБ. Удалить можно руками в любой момент, программа от этого
рассердится только перекачает при следующей синхронизации.
"""

import threading
import time
from collections import deque
from pathlib import Path
from typing import Optional

import config

# Куда складываем. Рядом с проектом, а не в temp: temp очищается сам, и
# весь толк от кэша тогда исчезает.
CACHE_DIR: Path = config.ROOT_DIR / "audio_cache"

# Формат для скачивания. M4A первым - он есть почти у всех роликов и
# естся VLC без конвертации. Конвертировать нечем: ffmpeg в системе нет,
# а ставить его ради второго формата смысла нет.
DOWNLOAD_FORMAT = "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio"

# Потолок на размер файла. Ролик на полчаса в нормальном битрайте - около
# 30 МБ, но в базе лежат треки по 2-6 минут, то есть до 6 МБ.
MAX_BYTES = 25 * 1024 * 1024

# Сколько ждать готовности. Скачивание трека занимает секунды; если за
# это время файл не появился - лучше честно сказать об этом и пропустить
# трек, чем молчать.
DEFAULT_TIMEOUT = 45.0

# Сколько записей о результатах держим в памяти сверх того, что лежит
# на диске. Записи нужны только ожидающим; тот, кто не забрал ответ,
# найдёт файл на диске и в следующий раз. Держать бесконечно смысла нет.
_MAX_REMEMBERED = 512


class AudioCache:
    """
    Скачивает аудио в фоне, по одному треку за раз, и отдаёт файлом.

    Один экземпляр на процесс - см. get_cache() внизу. Общий на двоих
    гарантирует, что анализатор и плеер не начнут качать один и тот же
    трек одновременно и не будут писать в один файл.

    Вызывать ensure() можно из любого потока: метод блокирует вызывающего,
    а не загрузчик.
    """

    def __init__(self, cache_dir: Path = CACHE_DIR):
        self.cache_dir = cache_dir
        self._lock = threading.Condition()
        self._done: dict = {}          # video_id -> путь или None
        self._pending: set = set()     # video_id, который сейчас качается или в очереди
        self._queue: deque = deque()   # порядок запросов
        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="smartmusic-audio-cache")
        self._thread.start()

    # ------------------------------------------------------------------ #
    #  Точка входа
    # ------------------------------------------------------------------ #

    def ensure(self, video_id: str, timeout: float = DEFAULT_TIMEOUT) -> Optional[Path]:
        """
        Возвращает путь к файлу трека, скачивая его при необходимости.

        None означает «не получилось»: id пустой, файл не скачался или
        не дождались за timeout секунд. Повторный вызов для того же
        трека ничего не качает заново.
        """
        if not video_id:
            return None

        found = self.find(video_id)
        if found is not None:
            return found

        with self._lock:
            # Пока файл качается, повторные запросы просто ждут тот же
            # результат, а не ставят второе задание.
            if video_id not in self._pending:
                self._pending.add(video_id)
                self._queue.append(video_id)
            self._lock.notify_all()

            deadline = time.monotonic() + timeout
            while video_id not in self._done:
                left = deadline - time.monotonic()
                if left <= 0:
                    return None
                self._lock.wait(left)

            return self._done.get(video_id)

    def find(self, video_id: str) -> Optional[Path]:
        """Файл уже на диске. Ничего не скачивает."""
        if not video_id:
            return None
        try:
            if not self.cache_dir.exists():
                return None
            for path in self.cache_dir.glob(f"{video_id}.*"):
                # .part - незаконченный файл, его брать нельзя
                if (path.is_file() and path.suffix != ".part"
                        and path.stat().st_size > 0):
                    return path
        except OSError:
            return None
        return None

    def cached_count(self) -> int:
        """Сколько треков уже лежит на диске. Для диагностики."""
        try:
            return len([p for p in self.cache_dir.glob("*")
                        if p.is_file() and p.suffix != ".part"])
        except OSError:
            return 0

    def shutdown(self) -> None:
        """Останавливает загрузчик. Вызывается при закрытии программы."""
        with self._lock:
            self._stop = True
            self._lock.notify_all()

    # ------------------------------------------------------------------ #
    #  Поток загрузки
    # ------------------------------------------------------------------ #

    def _run(self) -> None:
        while True:
            with self._lock:
                while not self._queue and not self._stop:
                    self._lock.wait(0.5)
                if self._stop and not self._queue:
                    return
                if not self._queue:
                    continue
                video_id = self._queue.popleft()

            path = self._download(video_id)

            with self._lock:
                self._pending.discard(video_id)
                self._done[video_id] = path
                self._lock.notify_all()
                self._trim_memory_locked()

    def _trim_memory_locked(self) -> None:
        """Подрезает память, вызывается под замком."""
        if len(self._done) <= _MAX_REMEMBERED:
            return
        # Записи о файлах на диске и так бесполезны: find() найдёт их сам.
        # Вот те, что запомнили неудачу, стоит забыть - вдруг сеть ожила.
        on_disk = {vid for vid in self._done if self.find(vid) is not None}
        for video_id in on_disk:
            self._done.pop(video_id, None)

    def _download(self, video_id: str) -> Optional[Path]:
        try:
            import yt_dlp
        except ImportError:
            print("❌ [Audio Cache] Не установлен yt-dlp.")
            return None

        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            print(f"❌ [Audio Cache] Не удалось создать {self.cache_dir}: {e}")
            return None

        options = {
            "outtmpl": str(self.cache_dir / f"{video_id}.%(ext)s"),
            "format": DOWNLOAD_FORMAT,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "noplaylist": True,
            "max_filesize": MAX_BYTES,
            # Ключевое: если файл уже есть, не качать заново. Именно это
            # превращает папку в кэш, а не в одноразовую загрузку.
            "overwrites": False,
            "continuedl": True,
        }

        try:
            import logging
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                logging.getLogger("yt_dlp").setLevel(logging.CRITICAL)
                with yt_dlp.YoutubeDL(options) as ydl:
                    ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
        except Exception as e:
            print(f"❌ [Audio Cache] Не скачался {video_id}: "
                  f"{type(e).__name__}: {str(e)[:70]}")
            return None

        path = self.find(video_id)
        if path is None:
            # Скачалось, но файла нет. Обычно это ролик без аудиодорожки.
            print(f"⚠️ [Audio Cache] У {video_id} нет аудиодорожки.")
            return None
        return path


_cache: Optional[AudioCache] = None
_cache_lock = threading.Lock()


def get_cache() -> AudioCache:
    """
    Единственный кэш на процесс.

    Ленивый: создаётся при первом обращении, чтобы модуль можно было
    импортировать там, где качать всё равно не нужно.
    """
    global _cache
    with _cache_lock:
        if _cache is None:
            _cache = AudioCache()
        return _cache
