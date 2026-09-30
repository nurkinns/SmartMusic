"""
app_log.py — запись того, что программа печатает, в файл.

Зачем
-----
Весь проект пишет через print(), и это правильно: сообщения видны в
консоли сразу, а разбирать их по ходу игры не нужно. Но в живой Dota
консоли не видно совсем - окно свёрнуто или закрыто, и всё, что
произошло, пропадает. А разбираться приходится именно после матча:
почему трек не сменился, почему плеер не включился, что прислала игра.

Поэтому тот же самый вывод дублируется в файл, но уже с временем.

Почему не logging
-----------------
Через logging пришлось бы переписывать сотни print() по всему проекту.
Здесь ловится вывод на выходе, поэтому работает сразу и целиком: любая
новая строчка print() попадёт в лог сама, без участия автора.

Файлы
-----
logs/smartmusic.log        текущий
logs/smartmusic.log.1      предыдущий, и так далее (по кругу)
logs/ в .gitignore.
"""

import sys
import time
from pathlib import Path

import config

LOG_DIR: Path = config.ROOT_DIR / "logs"
LOG_FILE: Path = LOG_DIR / "smartmusic.log"

# Сколько логов храним. Больше двух не нужно: за матч лог весит
# копейки, а три файла по 5 МБ - это уже мусор на диске.
KEEP_LOGS = 3

# После этого размера текущий лог отодвигается в сторону и начинается
# новый. 5 МБ - примерно полгода игр.
MAX_BYTES = 5 * 1024 * 1024

# Время в начале каждой строки файла. В консоли его нет: там важно
# видеть сообщение, а не код секунд.
LINE_PREFIX = "%H:%M:%S"


class _Tee:
    """
    Пишет в консоль и в файл одновременно.

    В файл строка идёт с временем, в консоль - как есть. Дублирование
    нужно только для файла, поэтому если файл недоступен (например,
    папка read-only), консольный вывод продолжает работать как обычно.
    """

    def __init__(self, stream, path: Path, add_time: bool):
        self._stream = stream
        self._path = path
        self._add_time = add_time
        self._pending = ""      # недописанная строка без перевода строки
        self._broken = False    # файл перестал писаться - больше не пробуем

    # ------------------------------------------------------------------ #
    #  Запись в файл
    # ------------------------------------------------------------------ #

    def _rotate_if_needed(self) -> None:
        """Отодвигает текущий лог, если он вырос до потолка."""
        try:
            if not self._path.exists() or self._path.stat().st_size < MAX_BYTES:
                return
        except OSError:
            return

        try:
            # .1 становится .2, .2 становится .3, и так по кругу
            for index in range(KEEP_LOGS - 1, 0, -1):
                older = self._path.with_name(self._path.name + f".{index}")
                newer = self._path.with_name(self._path.name + f".{index + 1}")
                if older.exists():
                    older.replace(newer)
            self._path.replace(self._path.with_name(self._path.name + ".1"))
        except OSError:
            # Не смогли переложить - пишем дальше в тот же файл.
            pass

    def _write_line(self, text: str) -> None:
        if self._broken or not text:
            return
        try:
            self._rotate_if_needed()
            with open(self._path, "a", encoding="utf-8", errors="replace") as f:
                stamp = time.strftime(LINE_PREFIX) if self._add_time else ""
                f.write(f"{stamp} {text}\n" if stamp else f"{text}\n")
        except OSError:
            # Лог недоступен. Молча отключаем файл и продолжаем: программа
            # обязана работать даже тогда, когда писать логи некуда.
            self._broken = True

    # ------------------------------------------------------------------ #
    #  Методы, которые ждёт print()
    # ------------------------------------------------------------------ #

    def write(self, text: str) -> int:
        """Пишет в консоль и копит текст для файла до перевода строки."""
        try:
            self._stream.write(text)
        except (ValueError, OSError):
            # Консоль закрыли, а программа продолжает работать (так
            # бывает при перезапуске из трея). Молча проглатываем.
            pass

        if self._broken:
            return len(text)

        self._pending += text
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            self._write_line(line.rstrip("\r"))
        return len(text)

    def flush(self) -> None:
        try:
            self._stream.flush()
        except (ValueError, OSError):
            pass

    def isatty(self) -> bool:
        # Некоторые библиотеки (цветной вывод в консоль) спрашивают это.
        # Отвечаем честно: файл логом не является.
        return False

    def fileno(self):
        return self._stream.fileno()

    # ------------------------------------------------------------------ #
    #  Досрочный сброс
    # ------------------------------------------------------------------ #

    def close(self) -> None:
        """Дописывает недописанную строку. Вызывается при выходе."""
        if not self._broken and self._pending.strip():
            self._write_line(self._pending.rstrip())
        self._pending = ""

    # Код, который иногда проверяют наличие fileno у stderr.
    @property
    def encoding(self) -> str:
        return getattr(self._stream, "encoding", "utf-8")


def setup_logging() -> bool:
    """
    Включает запись в файл. Вызывается самым первым в main.py.

    Возвращает True, если лог ведётся, и False, если нет - тогда
    вызывающий может один раз об этом сказать и больше не думать.
    """
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False

    try:
        # Пробуем создать файл заранее: если не выйдет сейчас, то
        # не выйдет и позже, а узнаем об этом лучше сейчас.
        with open(LOG_FILE, "a", encoding="utf-8"):
            pass
    except OSError:
        return False

    if sys.stdout is not None and not isinstance(sys.stdout, _Tee):
        sys.stdout = _Tee(sys.stdout, LOG_FILE, add_time=True)
    if sys.stderr is not None and not isinstance(sys.stderr, _Tee):
        # Время печатаем и в ошибках тоже: по журналу потом ищут, почему
        # программа упала, и там без времени не поймёшь ничего.
        sys.stderr = _Tee(sys.stderr, LOG_FILE, add_time=True)

    return True


def write_header(title: str = "SmartMusic") -> bool:
    """
    Отделяет запуск нового сеанса от предыдущего.

    Без этого в логе не видно, где кончился прошлый запуск и где
    начался этот, а падение при запуске и падение через два часа
    игры выглядят одинаково.
    """
    if sys.stdout is not None and isinstance(sys.stdout, _Tee):
        sys.stdout._write_line("=" * 58)
        sys.stdout._write_line(f"{title} - запуск")
    return True


def log_path() -> Path:
    """Путь к логу. GUI показывает его, чтобы его можно было открыть."""
    return LOG_FILE


def flush() -> None:
    """Дописывает недописанную строку в оба потока. При закрытии программы."""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, _Tee):
            stream.close()
