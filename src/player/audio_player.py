"""
audio_player.py — плеер, которого в проекте не было.

Что было вместо него
--------------------
До этого файл src/player/spotify_control.py был пустым, а DJBrain во всех
ветках жал клавишу «следующий трек» (см. media_keys.py). Это не плеер:
клавиша просто переключает то, что уже играет в чём-то ещё - в браузере,
в Spotify, где угодно. Вайб-матрица к этому отношения не имела: на драку
и на фарм ставился один и тот же плейлист.

Что здесь
---------
Настоящий локальный плеер на VLC. Принимает трек из Вайб-Матрицы и играет
его, потому что DJ Brain выбрал его под текущее состояние игры.

Два способа достать звук (PLAYER_* в config.py и выбор в Настройках):

  download (основной)
      Аудиодорожка скачивается через yt-dlp один раз и лежит в кэше
      (audio_cache/). Дальше играется локальный файл.
      Почему так: старт трека мгновенный и не зависит от интернета.
      В драку нельзя ждать, пока плеер докачает ролик с YouTube.

  direct (запасной)
      VLC получает ссылку на ролик и тянет сам.
      Почему оставил: не нужно ничего качать и не занимать место, но
      старт трека зависит от сети - на слабом интернете это секунды
      тишины в самый неподходящий момент.

Кэш и место
-----------
Файлы лежат в audio_cache/ рядом с проектом и переиспользуются: один и тот
же трек второй раз не качается. Трек длиной 3 минуты весит около 3 МБ,
вся библиотека на 66 треков - примерно 200 МБ. Папка в .gitignore.

Почему не mpv
-------------
mpv умел бы всё то же и чуть лучше вёл бы себя с YouTube, но на этой
машине его нет, а VLC стоит. Лишняя зависимость в виде отдельной
установки того, что уже есть на компьютере, того не стоит.


Плавная смена трека
===================
Зачем: трек кончается, и следующий начинается с чистого листа. На дропе
это звучит как обрыв - доля секунды тишины, потом резкий удар. В бою,
где музыка переключается чаще всего, слышно особенно резко.

Как сделано
----------
Два голоса (_Voice), у каждого СВОЙ экземпляр VLC. За пару секунд до
конца трека следующий голос стартует с нулевой громкостью, и пока идёт
CROSSFADE_SECONDS, громкость одного падает, а другого растёт. Трек
никогда не перестаёт звучать, поэтому провала в тишину нет вовсе.

Почему два ЭКЗЕМПЛЯРА, а не два плеера в одном
----------------------------------------------
Это главная тонкость, на которой кроссфейд и сломался в первый раз.
Громкость в VLC живёт не на плеере, а на аудиовыходе. Два медиаплеера
в одном экземпляре делят один выход, и audio_set_volume на втором
стирает то, что поставили первому. Рампа 1->0 и 0->1 в таком случае
невозможна: последняя запись попросту затирает предыдущую, и на выходе
вместо плавного перехода остаётся обрыв - ровно то, что мы чиним.

Отдельные экземпляры у каждого свой выход, и громкости не мешают друг
другу. Проверено на VLC 3.0.6: в одном экземпляре значения совпадают,
в двух - независимы (100/0 и 100/70 держатся раздельно).

Почему не ffmpeg
----------------
ffmpeg на этой машине нет, а ставить его ради одной склейки смысла
нет: в проекте уже есть PyAV, который умеет то же. Но склейка означала
бы готовить новый файл перед каждым переходом, а это и диск, и
процессорное время прямо посреди боя. Два голоса делают то же самое
в памяти, мгновенно и без лишних файлов.

Встроенного кроссфейда у VLC тоже нет: опции --audio-crossfade,
--crossfade и --audio-fade в версии 3.0.6 не существуют, проверено.

Почему громкость идёт по кривой, а не по линейке
------------------------------------------------
Линейное затухание даёт провал в середине: оба трека играют на 0.5,
и суммарно звук тише примерно на 3 дБ. На переходе это слышно как
«провал». Косинус и синус держат громкость постоянной - в середине
каждый играет на 0.7, а вместе звучат так же, как один трек на полной.


Потоки
------
VLC живёт в своём фоновом потоке, а скачивание - в другом. Слежение за
концом трека - в третьем: кроссфейд держит проход по громкости несколько
секунд, и если бы он выполнялся в потоке команд, всё остальное встало бы
на это время.
"""

import math
import threading
from typing import Any, Callable, Dict, List, Optional

import config
from src import audio_cache

# Как часто следить за концом трека. Треть секунды - достаточно часто,
# чтобы поймать начало окна кроссфейда, и редко, чтобы не будить
# процессор зя: проверка делает два лёгких вызова VLC.
_MONITOR_INTERVAL = 0.25

# Шагов в рампе громкости. 30 шагов на 3 секунды - это 10 раз в секунду:
# глаз и ухо не различают, а нагрузка на CPU нулевая.
_FADE_STEPS = 30


class _Voice:
    """
    Один голос: свой экземпляр VLC, свой плеер, своя громкость.

    Отдельный класс, а не два поля в AudioPlayer, потому что при смене
    трека голоса меняются местами. Если бы экземпляры VLC хранились
    отдельно от плееров, пришлось бы вручную следить, какой экземпляр
    чей, и при перестановке забыть было бы легко - а ошибка выглядела бы
    как «громкость едет куда-то» без всяких подсказок. Вместе они
    меняются местами одной строкой.
    """

    def __init__(self, name: str, on_end=None):
        self.name = name
        self.instance = None
        self.player = None
        # Обработчик конца трека. Принимает голос, который замолчал:
        # голосов два, и молчать может не тот, который сейчас играет,
        # - уходящий во время кроссфейда. Слышавший об этом забывает
        # переключать трек.
        self._on_end = on_end

    def start(self) -> bool:
        try:
            import vlc
        except ImportError:
            return False
        try:
            # --aout обязателен и не переключается без нужды: при
            # родном для Windows wasapi громкость одна на весь
            # процесс, и два голоса затирали бы друг друга. Подробности
            # и замеры - в config.PLAYER_AUDIO_OUTPUT.
            aout = getattr(config, "PLAYER_AUDIO_OUTPUT", "directsound")
            self.instance = vlc.Instance("--no-video", "--quiet",
                                         "--no-video-title-show",
                                         f"--aout={aout}")
            self.player = self.instance.media_player_new()

            # Подписка ровно одна на весь срок жизни голоса. Раньше её
            # вешали при каждой загрузке трека, и к десятому треку VLC
            # звал нас десять раз на одно окончание: команда «дальше»
            # уходила десять раз, и музыка перепрыгивала через треки.
            # Отписаться в python-vlc нельзя, поэтому вешаем один раз
            # и больше не трогаем.
            if self._on_end is not None:
                self.player.event_manager().event_attach(
                    vlc.EventType.MediaPlayerEndReached, self._wrap_end)
            return True
        except Exception as e:
            print(f"❌ [Player] Голос '{self.name}' не поднялся: "
                  f"{type(e).__name__}: {e}")
            self.instance = None
            self.player = None
            return False

    def _wrap_end(self, _event) -> None:
        """Подкладывает в обработчик имя голоса, который замолчал."""
        if self._on_end is not None:
            self._on_end(self)

    def set_source(self, source: str) -> bool:
        """Заводит источник. Подписка на конец уже висит с момента start()."""
        if self.player is None:
            return False
        try:
            media = self.instance.media_new(source)
            self.player.set_media(media)
            return True
        except Exception as e:
            print(f"❌ [Player] Голос '{self.name}': не завести источник: {e}")
            return False

    def play(self) -> None:
        if self.player is not None:
            try:
                self.player.play()
            except Exception:
                pass

    def stop(self) -> None:
        if self.player is not None:
            try:
                self.player.stop()
            except Exception:
                pass

    def volume(self, factor: float) -> None:
        """Громкость голоса, 0.0-1.0."""
        if self.player is None:
            return
        try:
            self.player.audio_set_volume(int(max(0.0, min(1.0, factor)) * 100))
        except Exception:
            pass

    def get_volume(self) -> int:
        if self.player is None:
            return -1
        try:
            return int(self.player.audio_get_volume())
        except Exception:
            return -1

    def is_playing(self) -> bool:
        if self.player is None:
            return False
        try:
            return bool(self.player.is_playing())
        except Exception:
            return False

    def time_ms(self) -> int:
        if self.player is None:
            return -1
        try:
            value = self.player.get_time()
            return int(value) if value is not None else -1
        except Exception:
            return -1

    def length_ms(self) -> int:
        if self.player is None:
            return -1
        try:
            value = self.player.get_length()
            return int(value) if value is not None else -1
        except Exception:
            return -1

    def pause(self, on: bool) -> bool:
        """Пауза. on=True ставит, on=False снимает."""
        if self.player is None:
            return False
        try:
            self.player.set_pause(1 if on else 0)
            return True
        except Exception:
            return False


class AudioPlayer:
    """
    Публичный класс. Живёт в главном потоке приложения, а всю работу с VLC
    делает в своих фоновых потоках.

    Использование:
        player = AudioPlayer()
        player.play(track)              # один трек
        player.play_list(tracks)        # список, крутится по кругу
        player.set_user_volume(70)      # ползунок в GUI, проценты
        player.set_night_factor(0.6)    # Night Governor
        player.toggle_pause()
        player.stop()
    """

    # Основной и запасной способы. Имена совпадают с config.PLAYER_MODE.
    MODE_DOWNLOAD = "download"
    MODE_DIRECT = "direct"

    def __init__(self, mode: Optional[str] = None):
        self._mode = mode or getattr(config, "PLAYER_MODE", self.MODE_DOWNLOAD)
        self._cache = audio_cache.get_cache()

        self._cmd_lock = threading.Condition()
        self._commands: List[tuple] = []

        # Текущий плейлист. Хранится здесь, а не в VLC: решение о том,
        # что играть после конца трека, принимаем мы, а не плеер.
        self._playlist: List[Dict[str, Any]] = []
        self._index = -1
        self._current_id: Optional[str] = None
        self._current_track: Optional[Dict[str, Any]] = None
        # Какой трек человек считает нужным сейчас. Ставится в том
        # потоке, откуда пришла команда, - см. _want().
        self._desired_id: Optional[str] = None

        # Громкость разделена на две части намеренно.
        #   _user_volume   - то, что выставил человек ползунком;
        #   _night_factor  - ограничение ночного режима.
        # Перемножаются при применении. Если бы было одно число, то
        # ночной лимитер и ползунок затирали бы друг друга, и винтик
        # перестал бы что-либо делать.
        self._user_volume = _default_user_volume()
        self._night_factor = 1.0

        # Кто хочет знать, чем кончилась попытка включить трек.
        #
        # Заведено потому, что play_list() возвращает True сразу после
        # постановки команды в очередь, а не после того, как трек реально
        # зазвучал. Между этими двумя событиями проходят секунды скачивания,
        # и всё это время DJ Brain считал смену вайба удавшейся, щёлкал
        # кулдаун и записывал вайб в current_state. Реальная ошибка
        # всплывала через эти секунды и была уже никому не нужна: состояние
        # считалось применённым, повторного события не приходило.
        #
        # Обработчик зовётся из потока команд, то есть не из того потока,
        # который просил включить трек. Поэтому он обязан быть быстрым и
        # не бросать исключений - оба проверены в _report_play_result.
        self._play_result_cb: Optional[Callable[[Dict[str, Any], bool, str], None]] = None

        # Два голоса. Основной играет сейчас, второй готовится.
        self._current = _Voice("основной", self._on_voice_end)
        self._incoming = _Voice("запасной", self._on_voice_end)
        self._player_ready = threading.Event()

        # Состояние кроссфейда. Всё под _state_lock.
        self._state_lock = threading.RLock()
        self._fading = False
        self._generation = 0          # растёт на каждой смене трека
        self._paused = False
        self._stopped = True

        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="smartmusic-player")
        self._thread.start()
        self._monitor = threading.Thread(target=self._monitor_loop, daemon=True,
                                          name="smartmusic-monitor")
        self._monitor_started = False

        # Ждём готовности VLC только если им сразу пользуются: без этого
        # первый play() ушёл бы в пустоту.
        if not self._player_ready.wait(timeout=8.0):
            print("⚠️ [Player] VLC не поднялся за 8 секунд. Проверь установку.")

    # ------------------------------------------------------------------ #
    #  Управление (вызывается из главного потока)
    # ------------------------------------------------------------------ #

    @property
    def mode(self) -> str:
        return self._mode

    def available(self) -> bool:
        """Готов ли плеер к работе."""
        return self._current.player is not None

    def set_play_result_callback(
            self, callback: Optional[Callable[[Dict[str, Any], bool, str], None]],
    ) -> None:
        """
        Подписка на итог попытки включить трек: (трек, получилось, причина).

        Нужна потому, что play_list() отвечает сразу - «команда принята»,
        а не «трек играет». Пока трек качается (это секунды), вызывающий
        считает смену вайба удавшейся: щёлкает кулдаун и фиксирует
        состояние. Если скачивание провалилось, узнать об этом уже
        некому - состояние записано, событие больше не повторится.

        Обработчик вызывается из потока команд плеера, а не из потока
        вызова. Исключения внутри проглатываются с записью в лог: иначе
        ошибка в подписчике убила бы поток плеера, и музыка встала бы до
        перезапуска программы.

        None отписывает.
        """
        self._play_result_cb = callback

    def _report_play_result(self, track: Dict[str, Any],
                            ok: bool, reason: str) -> None:
        """Отдать подписчику итог попытки. Вызывается под _state_lock."""
        callback = self._play_result_cb
        if callback is None:
            return
        try:
            callback(track, ok, reason)
        except Exception as e:
            print(f"⚠️ [Player] Подписчик на итог воспроизведения сломался: "
                  f"{type(e).__name__}: {str(e)[:70]}")

    def play(self, track: Optional[Dict[str, Any]]) -> bool:
        """
        Играет один трек. Список при этом не трогаем: следующим после конца
        пойдёт последний заданный play_list, если он был.

        Возвращает False, если играть нечего (нет id, VLC не поднялся).
        """
        if not track or not track.get("id"):
            return False
        self._playlist = [track]
        self._index = 0
        self._want(track)
        return self._send(("play", track))

    def play_list(self, tracks: List[Dict[str, Any]],
                  start_index: int = 0) -> bool:
        """
        Играет список по кругу. Трек заканчивается - берётся следующий.
        Это то, чего не хватало при переключении клавишей: там после
        последнего трека плейлиста наступала тишина.
        """
        tracks = [t for t in (tracks or []) if t and t.get("id")]
        if not tracks:
            return False
        self._playlist = tracks
        self._index = max(0, min(start_index, len(tracks) - 1))
        self._want(tracks[self._index])
        return self._send(("play", tracks[self._index]))

    def _want(self, track: Dict[str, Any]) -> None:
        """
        Запоминает, какой трек человек считает нужным прямо сейчас.

        Записано в том потоке, откуда пришла команда, а не в потоке
        VLC. Разница принципиальная: пока трек качается (это секунды),
        может прийти новая команда. Скажем, вышли из матча и музыку
        остановили. Пока качался трек для боя, пришёл Idle, и при
        включении надо пропустить боевой трек и не играть ничего.

        Важно, что это именно «чего хочет человек», а не «что сейчас
        играет». Раньше здесь стояла проверка против текущего трека,
        и из-за неё после остановки музыка не возвращалась: команда
        «играй» сравнивалась с тем, что осталось играть с прошлого раза,
        не совпадала, и молча уходила в никуда.
        """
        with self._state_lock:
            self._desired_id = str(track.get("id") or "")

    # ------------------------------------------------------------------ #
    #  Громкость
    # ------------------------------------------------------------------ #

    def set_user_volume(self, percent: int) -> None:
        """
        Громкость от человека, в процентах 0-100. То, что двигает ползунок.

        Отдельно от ночного лимитера, см. комментарий в __init__.
        """
        self._user_volume = max(0.0, min(100.0, float(percent))) / 100.0
        self._send(("volume", None))

    def user_volume(self) -> int:
        """Что сейчас выставлено ползунком, в процентах."""
        return int(round(self._user_volume * 100))

    def set_night_factor(self, factor: float) -> None:
        """
        Ночной лимитер, множитель 0.0-1.0. Ставит его Night Governor.

        Отдельное имя от set_user_volume специально: раньше это был один и
        тот же set_volume, и ползунок с ночным режимом дрались.

        Повторное то же значение игнорируем. Night Governor зовёт это на
        КАЖДЫЙ пакет от игры, то есть десятки раз в секунду, и без
        проверки в очередь шла бы лавина одинаковых команд на применение
        громкости. А менять здесь нечего: то же значение - то же
        состояние.
        """
        factor = max(0.0, min(1.0, float(factor)))
        if abs(factor - self._night_factor) < 0.001:
            return
        self._night_factor = factor
        self._send(("volume", None))

    def night_factor(self) -> float:
        """
        Текущий ночной множитель, 0.0-1.0.

        Нужен окну, чтобы показать честное «ночью слышно столько-то»,
        а не «ночной режим включён». Человек смотрит на ползунок в 100%
        и слышит 60 - без пояснения это выглядит как поломка.
        """
        return self._night_factor

    # Старый вызов оставлен: DJ Brain зовёт его по имени, и переименование
    # сломало бы вызов из чужого кода. Смысл тот же - ночной множитель.
    set_volume = set_night_factor

    def master_volume(self) -> float:
        """Итоговая громкость 0.0-1.0: ползунок, умноженный на ночной лимит."""
        return max(0.0, min(1.0, self._user_volume * self._night_factor))

    # ------------------------------------------------------------------ #
    #  Пауза и остановка
    # ------------------------------------------------------------------ #

    def toggle_pause(self) -> bool:
        """
        Пауза / продолжение. Возвращает True, если сейчас играет.

        Именно то, что делает большая кнопка в окне.
        """
        with self._state_lock:
            if self._paused:
                if not self._current.pause(False):
                    return False
                self._paused = False
            else:
                if not self._current.pause(True):
                    return False
                self._paused = True
            return not self._paused

    def is_playing(self) -> bool:
        """Играет ли сейчас музыка (не на паузе). Для кнопки в окне."""
        with self._state_lock:
            if self._paused or self._stopped:
                return False
        return self._current.is_playing()

    def is_paused(self) -> bool:
        with self._state_lock:
            return self._paused

    def current_track(self) -> Optional[Dict[str, Any]]:
        with self._state_lock:
            return self._current_track

    def next_track(self) -> None:
        """
        Переключает на следующий трек вручную, как кнопка «дальше».

        Следующий трек выбираем здесь, в потоке нажатия, и сразу
        записываем как нужный. Если бы мы просто сказали «дальше» и
        оставили выбор потоку VLC, то кнопка, нажатая посреди
        скачивания, переключила бы не тот трек - тот, что скачался
        первым, а не тот, что человек ожидал.
        """
        with self._state_lock:
            if len(self._playlist) <= 1:
                return
            self._index = (self._index + 1) % len(self._playlist)
            nxt = self._playlist[self._index]
        self._want(nxt)
        self._send(("play", nxt))

    def prev_track(self) -> None:
        """
        Переключает на предыдущий трек вручную, как кнопка «назад».

        Отдельно от next_track, а не через знак минус: DJ Brain играет
        не по кругу, а человек жмёт «назад» и ждёт тот самый трек,
        который только что закончился. По кругу от первого он сразу
        прыгнул бы на последний - не то, что человек ожидал.

        Но если мы в самом начале плейлиста, идущего трека негде взять,
        идём по кругу - как в любом плеере.
        """
        with self._state_lock:
            if len(self._playlist) <= 1:
                return
            idx = self._index - 1
            if idx < 0:
                idx = len(self._playlist) - 1
            self._index = idx
            prev = self._playlist[idx]
        self._want(prev)
        self._send(("play", prev))

    def playlist_size(self) -> int:
        """
        Сколько треков в текущем плейлисте.

        Нужно окну, чтобы гасить кнопки «вперёд» и «назад», когда
        переключать нечего: плейлист не собран или в нём один трек.
        Нажатая впустую кнопка выглядит как поломка.
        """
        with self._state_lock:
            return len(self._playlist)

    def stop(self) -> None:
        # Сбрасываем желание: пока музыка останавливается, может
        # прийти команда играть, и она обязана исполниться, а не быть
        # отброшенной как устаревшая.
        with self._state_lock:
            self._desired_id = None
        self._send(("stop", None))

    def set_mode(self, mode: str) -> None:
        """
        Меняет способ добычи звука на лету. Переключение безопасно
        посреди матча: применяется к следующему включённому треку.
        """
        if mode == self._mode:
            return
        self._mode = mode
        print(f"🔀 [Player] Способ воспроизведения: {self.describe_mode()}")

    def describe_mode(self) -> str:
        return ("скачивание + VLC (рекомендуется)" if self._mode == self.MODE_DOWNLOAD
                else "ссылка напрямую в VLC")

    def describe_crossfade(self) -> str:
        """Человеческое описание кроссфейда - показывается в Настройках."""
        if not self._crossfade_enabled():
            return "выключен"
        return f"{self._crossfade_seconds():g} сек"

    def shutdown(self) -> None:
        """Останавливает всё. Вызывается при закрытии программы."""
        self._send(("quit", None))
        self._cache.shutdown()
        self._thread.join(timeout=5.0)

    # ------------------------------------------------------------------ #
    #  Настройки кроссфейда
    # ------------------------------------------------------------------ #

    @staticmethod
    def _crossfade_enabled() -> bool:
        return bool(getattr(config, "CROSSFADE_ENABLED", True))

    @staticmethod
    def _crossfade_seconds() -> float:
        return max(0.5, float(getattr(config, "CROSSFADE_SECONDS", 3.0)))

    def set_crossfade(self, enabled: bool) -> None:
        """
        Включает или выключает плавную смену прямо во время игры.

        Если кроссфейд шёл в момент выключения - он отменяется, и
        текущий трек доигрывает как обычно. Иначе получилось бы, что
        выключили, а музыка всё равно наложилась.
        """
        config.CROSSFADE_ENABLED = bool(enabled)
        if not enabled:
            with self._state_lock:
                if self._fading:
                    self._generation += 1     # рампа увидит и прервётся
                    self._fading = False
                    spare = self._incoming
                else:
                    spare = None
            # Голос, который успел завестись, мог уже стать основным.
            # Останавливать его в таком случае нельзя - это музыка.
            if spare is not None and spare is not self._current:
                spare.stop()
                spare.volume(0.0)
        print(f"🎚️ [Player] Плавная смена: {self.describe_crossfade()}")

    # ------------------------------------------------------------------ #
    #  Очередь команд
    # ------------------------------------------------------------------ #

    def _send(self, command: tuple) -> bool:
        with self._cmd_lock:
            self._commands.append(command)
            self._cmd_lock.notify_all()
        return True

    def _ensure_monitor(self) -> None:
        """Слежение за концом трека нужно только во время игры."""
        if not self._monitor_started:
            self._monitor_started = True
            self._monitor.start()

    # ------------------------------------------------------------------ #
    #  Поток команд
    # ------------------------------------------------------------------ #

    def _run(self) -> None:
        if not self._start_vlc():
            self._player_ready.set()
            return
        self._player_ready.set()

        while True:
            with self._cmd_lock:
                while not self._commands:
                    self._cmd_lock.wait(0.5)
                command = self._commands.pop(0)

            name, payload = command
            if name == "quit":
                break
            try:
                if name == "play":
                    self._do_play(payload)
                elif name == "volume":
                    self._apply_volume()
                elif name == "stop":
                    self._do_stop()
                elif name == "end_of_track":
                    # Голос обязателен: по нему видно, чей это конец.
                    self._on_end_event(payload)
            except Exception as e:
                # Поток VLC не должен умирать из-за одного трека:
                # тогда бы программа молча перестала играть до перезапуска.
                print(f"❌ [Player] Ошибка в команде '{name}': "
                      f"{type(e).__name__}: {str(e)[:90]}")

        self._do_stop()

    def _start_vlc(self) -> bool:
        try:
            import vlc  # noqa: F401  - нужна только для проверки установки
        except ImportError:
            print("❌ [Player] Не установлен python-vlc.")
            print("   Выполни:  python setup.py")
            return False

        if not self._current.start():
            print("❌ [Player] VLC не запустился. Проверь, что он установлен.")
            return False

        # Второй голос нужен для кроссфейда. Поднимаем сразу, при старте:
        # если поднять его в момент перехода, пока он грузится, музыка
        # получила бы провал - ровно тот, ради которого всё затевалось.
        if not self._incoming.start():
            print("⚠️ [Player] Второй голос не поднялся. "
                  "Плавная смена выключена.")

        self._apply_volume()
        self._incoming.volume(0.0)
        print(f"🎵 [Player] VLC готов. Способ: {self.describe_mode()}. "
              f"Плавная смена: {self.describe_crossfade()}")
        return True

    def _on_voice_end(self, voice) -> None:
        """
        Колбэк VLC: голос дослушал. Держим его предельно коротким.

        Прилетает из потока VLC, поэтому здесь только команда в очередь.
        Сам голос передаём обязательно: во время кроссфейда уходящий
        голос тоже рано или поздно замолчит, и если бы мы на это
        реагировали, трек переключился бы второй раз подряд.
        """
        self._send(("end_of_track", voice))

    # ------------------------------------------------------------------ #
    #  Источник звука
    # ------------------------------------------------------------------ #

    def _source_for(self, track: Dict[str, Any]) -> Optional[str]:
        """Где брать звук для этого режима."""
        video_id = str(track.get("id") or "").strip()
        if not video_id:
            return None

        if self._mode == self.MODE_DIRECT:
            return f"https://www.youtube.com/watch?v={video_id}"

        path = self._cache.ensure(video_id)
        if path is None:
            print(f"⚠️ [Player] Не удалось получить звук: "
                  f"{track.get('title', video_id)[:50]}")
        return str(path) if path else None

    def _peek_next_track(self) -> Optional[Dict[str, Any]]:
        """Следующий трек по кругу, ничего не начиная."""
        if len(self._playlist) <= 1:
            return None
        return self._playlist[(self._index + 1) % len(self._playlist)]

    def _prefetch_next(self) -> None:
        """
        Качает следующий трек заранее, пока играет текущий.

        Без этого кроссфейд опаздывал бы: файл скачивается секунды, и
        начало наложения сдвинулось бы - музыка на дропе рвалась бы
        ровно в том месте, ради которого всё и затевалось.
        """
        nxt = self._peek_next_track()
        if not nxt or self._mode != self.MODE_DOWNLOAD:
            return
        video_id = nxt.get("id")
        if not video_id or self._cache.find(video_id) is not None:
            return  # уже лежит - качать нечего

        def worker():
            try:
                self._cache.ensure(video_id, timeout=120.0)
            except Exception:
                pass  # не скачался - в момент перехода разберёмся

        threading.Thread(target=worker, daemon=True,
                         name="smartmusic-prefetch").start()

    # ------------------------------------------------------------------ #
    #  Воспроизведение
    # ------------------------------------------------------------------ #

    def _do_play(self, track: Dict[str, Any]) -> None:
        if self._current.player is None:
            # Раньше тут был молчаливый return: ни строки в лог, ни
            # попытки взять следующий трек. Со стороны это выглядело так,
            # будто музыку выключили, и разобраться, почему, было нечем.
            print("❌ [Player] Голос не поднялся, играть нечем.")
            self._report_play_result(track, False, "VLC не запустился")
            self._send(("end_of_track", self._current))
            return

        title = track.get("title", "")
        artist = track.get("artist", "")
        label = f"{artist} — {title}" if artist else title

        source = self._source_for(track)
        if not source:
            print(f"⚠️ [Player] {label[:60]}: источник недоступен, пропускаю")
            # Сразу берём следующий, иначе музыка просто встанет.
            self._report_play_result(track, False, "источник недоступен")
            self._send(("end_of_track", self._current))
            return

        # Пока трек качался (это секунды), могла прийти новая команда -
        # например, остановка или смена вайба. Если такому треку больше
        # не нужен, играть его нельзя: включилась бы музыка, которую
        # уже отменили.
        with self._state_lock:
            if self._desired_id and self._desired_id != str(track.get("id")):
                print(f"⏭️  [Player] {label[:50]}: пока качался, трек сменили - "
                      f"пропускаю")
                # Не ошибка: трек отменили намеренно. Отчёт о неудаче здесь
                # соврал бы - подписчик решил бы, что запрос провалился, и
                # начал бы жаловаться на смену вайба.
                return

            # Смена трека отменяет всякий начатый кроссфейд.
            self._generation += 1
            self._fading = False
            self._paused = False
            self._stopped = False

        self._incoming.stop()
        self._incoming.volume(0.0)
        print(f"▶️  [Player] {label[:70]}")

        if not self._current.set_source(source):
            self._report_play_result(track, False, "VLC не открыл файл")
            self._send(("end_of_track", self._current))
            return

        # _current_track и _current_id ставятся только после того, как
        # источник принят. Раньше они заполнялись выше по тексту, до
        # проверки, и current_track() возвращал трек, который так и не
        # начал играть: плашка в окне показывала то, чего не слышно.
        with self._state_lock:
            self._current_id = str(track.get("id"))
            self._current_track = track

        self._apply_volume(force=True)
        self._current.play()
        self._report_play_result(track, True, "")
        self._ensure_monitor()
        self._prefetch_next()

    def _on_end_event(self, voice) -> None:
        """
        Трек кончился по событию VLC.

        Тут важно, ЧЕЙ голос замолчал:

        Обычно это основной, и мы переключаем трек. Но во время
        кроссфейда уходящий голос дослушивает позже пришедшего, и его
        событие прилетает уже после перестановки. Если бы мы на него
        среагировали, получилось бы два переключения подряд: первым мы
        поставили следующий трек, вторым - тот, что и так уже играет.
        Проверка голоса отсекает лишнее.

        Голоса может не быть вовсе (например, команда пришла из
        запасного пути). Тогда считаем, что кончился тот, кого мы
        считали играющим: иначе потеряли бы переключение, и музыка
        просто встанет.
        """
        if voice is None:
            voice = self._current

        with self._state_lock:
            if self._fading or voice is not self._current:
                return

        self._do_next()

    def _do_next(self) -> None:
        """Трек кончился: берём следующий из плейлиста по кругу."""
        if len(self._playlist) <= 1:
            with self._state_lock:
                self._current_id = None
                self._current_track = None
                self._desired_id = None
            self._do_stop()
            return

        with self._state_lock:
            self._index = (self._index + 1) % len(self._playlist)
            self._current_id = None
            nxt = self._playlist[self._index]
            # Смена по кругу - тоже «чего хочет человек», иначе
            # следующая команда play() во время скачивания отбросила бы
            # трек, который сам только что поставили в очередь.
            self._desired_id = str(nxt.get("id") or "")
        self._do_play(nxt)

    # ------------------------------------------------------------------ #
    #  Громкость
    # ------------------------------------------------------------------ #

    def _apply_volume(self, force: bool = False) -> None:
        """
        Применяет громкость к основному голосу.

        Во время кроссфейда громкостью обоих голосов занимается рампа,
        и руками её трогать нельзя - трек начал бы играть в полную
        посреди наложения. Исключение - force: его ставит сам _do_play,
        когда трек включается заново.
        """
        with self._state_lock:
            if self._fading and not force:
                return
        self._current.volume(self.master_volume())

    # ------------------------------------------------------------------ #
    #  Остановка
    # ------------------------------------------------------------------ #

    def _do_stop(self) -> None:
        with self._state_lock:
            self._generation += 1
            self._fading = False
            self._stopped = True
            self._paused = False
        self._incoming.stop()
        self._incoming.volume(0.0)
        self._current.stop()

    # ------------------------------------------------------------------ #
    #  Кроссфейд: слежение за концом трека
    # ------------------------------------------------------------------ #

    def _monitor_loop(self) -> None:
        while True:
            try:
                self._maybe_start_crossfade()
            except Exception as e:
                print(f"❌ [Player] Сбой слежения за треком: "
                      f"{type(e).__name__}: {str(e)[:70]}")
            threading.Event().wait(_MONITOR_INTERVAL)

    def _maybe_start_crossfade(self) -> None:
        """
        Начинает наложение, если трек подходит к концу.

        Проверка идёт по остатку времени: длина известна у локального
        файла, а у сетевой ссылки VLC её не знает заранее. В этом случае
        длина равна -1, кроссфейд не начинается, и смена трека остаётся
        прежней - не хуже, чем было.
        """
        if not self._crossfade_enabled() or self._incoming.player is None:
            return

        with self._state_lock:
            if self._fading or self._paused or self._stopped:
                return
            nxt = self._peek_next_track()

        if not nxt:
            return
        if not self._current.is_playing():
            return

        length_ms = self._current.length_ms()
        position_ms = self._current.time_ms()

        if length_ms <= 0:
            return          # длину не знаем - оставляем как было
        if position_ms < 0:
            return

        remaining = (length_ms - position_ms) / 1000.0
        fade_seconds = self._crossfade_seconds()

        # Небольшой запас сверху: лучше начать чуть раньше, чем опоздать
        # и поймать обрыв. Плюс не начинаем кроссфейд на самом старте,
        # когда VLC ещё не успел определить длительность.
        if remaining > fade_seconds + 0.75:
            return
        if length_ms < fade_seconds * 1000:
            return          # трек короче самого кроссфейда - накладывать не на что

        self._begin_crossfade(nxt)

    def _volumes_collide(self, outgoing, incoming, master: float,
                          gain_out: float, gain_in: float) -> bool:
        """
        Громкости голосов слиплись - значит, она общая.

        Проверяем не «совпали ли числа», а «совпали ли они там, где
        обязаны разойтись». На первом шаге рампа уходящий голос
        держит почти на нуле, а приходящий - почти на полной, так что
        разница должна быть заметной. Если вместо этого оба
        отчитываются об одном числе, громкость у них одна.

        Молчание (минус один) - это не слипание: голос ещё не успел
        получить свой выход, и догадываться тут нечем.
        """
        if master <= 0.01 or abs(gain_out - gain_in) * master < 10.0:
            return False

        left = outgoing.get_volume()
        right = incoming.get_volume()
        if left < 0 or right < 0:
            return False
        return left == right

    def _begin_crossfade(self, track: Dict[str, Any]) -> None:
        """Запускает следующий трек со сдвигом и гоняет громкость."""
        with self._state_lock:
            if self._fading:
                return
            self._fading = True
            generation = self._generation
            outgoing = self._current
            incoming = self._incoming
            index = (self._index + 1) % len(self._playlist)
            next_track = track

        title = next_track.get("title", "")
        artist = next_track.get("artist", "")
        label = f"{artist} — {title}" if artist else title

        source = self._source_for(next_track)
        if not source:
            # Не скачался следующий трек. Кроссфейд отменяем целиком и
            # ждём обычного окончания: наложение на тишину хуже, чем
            # обычная смена.
            with self._state_lock:
                self._fading = False
            return

        if not incoming.set_source(source):
            with self._state_lock:
                self._fading = False
            incoming.stop()
            incoming.volume(0.0)
            return

        # С нуля: иначе следующий трек ворвётся в уходящий.
        incoming.volume(0.0)
        incoming.play()

        seconds = self._crossfade_seconds()
        print(f"🔀 [Player] Плавный переход -> {label[:60]}")

        step_seconds = seconds / _FADE_STEPS
        for step in range(1, _FADE_STEPS + 1):
            # Условие отмены. Если за время перехода пришла команда
            # сменить трек или выключили кроссфейд - прерываемся и
            # оставляем то, что уже играет.
            #
            # Останавливаем именно incoming, а не «текущий запасной»:
            # за время перехода голоса уже могли поменяться местами,
            # и тогда self._incoming - это как раз голос, который мы
            # только что запустили и который уже играет. Убив его, мы
            # выключили бы музыку вместо того, чтобы её оставить.
            with self._state_lock:
                if self._generation != generation or not self._fading:
                    if incoming is not self._current:
                        incoming.stop()
                        incoming.volume(0.0)
                    return

            progress = step / _FADE_STEPS
            # Косинус и синус вместо линейки: см. объяснение в шапке
            # файла про провал громкости в середине перехода.
            gain_out = math.cos(progress * math.pi / 2)
            gain_in = math.sin(progress * math.pi / 2)

            # Громкость читаем на каждом шаге, а не берём один раз в
            # начале. Иначе движение ползунка или наступление ночи
            # посреди перехода потерялось бы молча: переход идёт
            # несколько секунд, и всё это время ползунок был бы мёртв.
            master = self.master_volume()
            outgoing.volume(master * gain_out)
            incoming.volume(master * gain_in)

            # Страховка на первом же шаге: проверяем, что голоса
            # действительно слышат разное. Если модуль вывода вдруг
            # вернёт общую громкость (об этом писано в
            # config.PLAYER_AUDIO_OUTPUT), то наложение невозможно -
            # вторая запись затрёт первую. Лучше отключить переход и
            # дать треку доиграть, чем три секунды гонять рампу,
            # которая портит музыку сильнее, чем её чинит.
            if step == 1 and self._volumes_collide(outgoing, incoming, master,
                                                   gain_out, gain_in):
                print("⚠️ [Player] Громкость у голосов общая, плавная смена "
                      "невозможна. Отключаю её.")
                config.CROSSFADE_ENABLED = False
                with self._state_lock:
                    self._fading = False
                if incoming is not self._current:
                    incoming.stop()
                    incoming.volume(0.0)
                return

            threading.Event().wait(step_seconds)

        # Переход доигран. Голоса меняются местами.
        with self._state_lock:
            if self._generation != generation:
                # Поколение сменилось в последнем шаге, уже после проверки
                # в цикле. Перестановки не было, incoming всё ещё не
                # основной, и его надо убрать, иначе он так и будет
                # играть поверх.
                if incoming is not self._current:
                    incoming.stop()
                    incoming.volume(0.0)
                return
            self._fading = False
            self._current, self._incoming = incoming, outgoing
            self._index = index
            self._current_id = str(next_track.get("id"))
            self._current_track = next_track

        # Ушедший голос больше не нужен - в этом и смысл, что их два.
        outgoing.stop()
        outgoing.volume(0.0)
        self._prefetch_next()


def _default_user_volume() -> float:
    """Стартовая громкость из config, в долях единицы."""
    percent = float(getattr(config, "DEFAULT_VOLUME_PERCENT", 70))
    return max(0.0, min(100.0, percent)) / 100.0
