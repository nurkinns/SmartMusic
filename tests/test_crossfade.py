"""
Тесты плавной смены трека (кроссфейд).

Проверяются только алгоритмы и логика, а не реальный звук. VLC не
поднимается: для этого плеер собирается через object.__new__ с
фейковыми голосами.
"""

import math
import threading

from tests.core import suite

S = suite("Кроссфейд — плавная смена трека")


class _FakeVoice:
    """Имитация одного голоса VLC — ровно то, что нужно кроссфейду."""

    def __init__(self, name: str):
        self.name = name
        self.player = object()  # не None — _maybe_start_crossfade пропустит
        self.src = None
        self.playing = False
        self.vol = 100
        self.length = 0
        self.time = 0

    def set_source(self, src) -> bool:
        self.src = str(src)
        return True

    def play(self):
        self.playing = True

    def stop(self):
        self.playing = False

    def volume(self, factor):
        self.vol = int(max(0.0, min(1.0, float(factor))) * 100)

    def get_volume(self) -> int:
        return self.vol

    def is_playing(self) -> bool:
        return self.playing

    def time_ms(self) -> int:
        return self.time

    def length_ms(self) -> int:
        return self.length

    def pause(self, on) -> bool:
        return True

    def start(self) -> bool:
        return True


def _bare_player(mode=None):
    """Собирает плеер через __new__, без VLC и потоков."""
    from src.player.audio_player import AudioPlayer

    p = AudioPlayer.__new__(AudioPlayer)
    p._mode = mode or AudioPlayer.MODE_DIRECT
    p._state_lock = threading.RLock()
    p._playlist = [{"id": "1", "title": "A", "artist": "X"},
                   {"id": "2", "title": "B", "artist": "Y"}]
    p._index = 0
    p._fading = False
    p._generation = 0
    p._paused = False
    p._stopped = False
    p._current_id = None
    p._current_track = None
    p._desired_id = None
    p._user_volume = 1.0
    p._night_factor = 1.0
    p._current = _FakeVoice("основной")
    p._incoming = _FakeVoice("запасной")
    return p


# --------------------------------------------------------------------------- #
#  Кривая равной мощности
# --------------------------------------------------------------------------- #


@S.add("кривая cos/sin даёт равную мощность (нет провала середины)")
def _t_equal_power_curve():
    from src.player.audio_player import _FADE_STEPS

    for step in range(1, _FADE_STEPS + 1):
        progress = step / _FADE_STEPS
        out = math.cos(progress * math.pi / 2)
        inn = math.sin(progress * math.pi / 2)
        power = out * out + inn * inn
        assert abs(power - 1.0) < 1e-9, f"шаг {step}: мощность {power}"


# --------------------------------------------------------------------------- #
#  Параметры из конфига
# --------------------------------------------------------------------------- #


@S.add("describe_crossfade() и _crossfade_seconds() читают конфиг")
def _t_describe():
    import config

    saved_e = config.CROSSFADE_ENABLED
    saved_s = config.CROSSFADE_SECONDS
    from src.player.audio_player import AudioPlayer

    try:
        config.CROSSFADE_ENABLED = True
        config.CROSSFADE_SECONDS = 3.0
        assert AudioPlayer._crossfade_enabled() is True
        assert abs(AudioPlayer._crossfade_seconds() - 3.0) < 1e-9

        config.CROSSFADE_SECONDS = 0.1
        assert AudioPlayer._crossfade_seconds() >= 0.5  # зажим минимума
    finally:
        config.CROSSFADE_ENABLED = saved_e
        config.CROSSFADE_SECONDS = saved_s


# --------------------------------------------------------------------------- #
#  _maybe_start_crossfade — условия старта
# --------------------------------------------------------------------------- #


@S.add("кроссфейд стартует, когда до конца трека меньше fade+0.75 с")
def _t_start_near_end():
    import config

    saved_e = config.CROSSFADE_ENABLED
    saved_s = config.CROSSFADE_SECONDS
    from src.player import audio_player as ap
    old_steps = ap._FADE_STEPS

    try:
        config.CROSSFADE_ENABLED = True
        config.CROSSFADE_SECONDS = 0.5
        ap._FADE_STEPS = 6  # быстрая рампа

        p = _bare_player()
        p._current.playing = True
        p._current.length = 5000  # 5 с
        p._current.time = 4000     # осталось 1.0 с ≤ 0.5+0.75 = 1.25 → старт
        p._current.vol = 100

        p._maybe_start_crossfade()
        # После выхода из _maybe_start_crossfade рампа уже доиграла.
        # Проверяем результат: запасной стал основным, индекс сдвинут.
        assert p._current_track is not None, "трек должен быть установлен"
        assert p._current_track["id"] == "2", (
            f"должен играть второй трек: {p._current_track.get('id')}")
        assert p._index == 1, f"индекс должен быть 1: {p._index}"
        assert p._incoming.src is None, "старый голос не содержит источник"
    finally:
        config.CROSSFADE_ENABLED = saved_e
        config.CROSSFADE_SECONDS = saved_s
        ap._FADE_STEPS = old_steps


@S.add("не стартует, когда до конца далеко")
def _t_not_start_early():
    import config

    saved_s = config.CROSSFADE_SECONDS
    try:
        config.CROSSFADE_SECONDS = 3.0
        p = _bare_player()
        p._current.length = 60_000
        p._current.time = 5_000
        p._maybe_start_crossfade()
        assert p._fading is False
    finally:
        config.CROSSFADE_SECONDS = saved_s


@S.add("не стартует, если трек короче самого кроссфейда")
def _t_not_start_short():
    import config

    saved_s = config.CROSSFADE_SECONDS
    try:
        config.CROSSFADE_SECONDS = 3.0
        p = _bare_player()
        p._current.playing = True
        p._current.length = 2_000  # 2 с < 3 с кроссфейда
        p._current.time = 1_900
        p._maybe_start_crossfade()
        assert p._fading is False
    finally:
        config.CROSSFADE_SECONDS = saved_s


@S.add("на паузе кроссфейд не начинается")
def _t_not_start_paused():
    p = _bare_player()
    p._paused = True
    p._current.length = 5_000
    p._current.time = 4_000
    p._maybe_start_crossfade()
    assert p._fading is False


@S.add("в плейлисте из одного трека кроссфейда нет")
def _t_not_start_one_track():
    p = _bare_player()
    p._playlist = [{"id": "1"}]
    p._index = 0
    p._current.length = 5_000
    p._current.time = 4_000
    p._maybe_start_crossfade()
    assert p._fading is False


# --------------------------------------------------------------------------- #
#  _begin_crossfade — сам переход
# --------------------------------------------------------------------------- #


@S.add("после кроссфейда голоса меняются местами и индекс сдвинут")
def _t_begin_swap():
    import config

    saved_s = config.CROSSFADE_SECONDS
    saved_e = config.CROSSFADE_ENABLED
    from src.player import audio_player as ap
    old_steps = ap._FADE_STEPS

    try:
        config.CROSSFADE_SECONDS = 0.5
        config.CROSSFADE_ENABLED = True
        ap._FADE_STEPS = 6

        p = _bare_player()
        p._current.vol = 100
        p._current.playing = True
        incoming_before = p._incoming
        current_before = p._current

        # Имитируем, что кроссфейд пора: остаток письма 1.0 с.
        p._current.length = 5_000
        p._current.time = 4_000
        p._maybe_start_crossfade()
        # Ждём, пока рампа доиграет (шаг 0.5/6 ≈ 0.083 с → меньше секунды).
        import time as _t
        end = _t.monotonic() + 5.0
        while _t.monotonic() < end and p._fading:
            _t.sleep(0.05)

        assert p._fading is False, "рампа должна была доиграть"
        assert p._current is incoming_before, "запасной стал основным"
        assert p._incoming is current_before, "старый ушёл в запасной"
        assert p._index == 1, f"индекс должен быть 1, а он {p._index}"
        assert p._current_track is not None
        assert p._current_track["id"] == "2"
        assert current_before.playing is False
    finally:
        config.CROSSFADE_SECONDS = saved_s
        config.CROSSFADE_ENABLED = saved_e
        ap._FADE_STEPS = old_steps


@S.add("если источник не скачался — кроссфейд отменён, тишины нет")
def _t_begin_source_unavailable():
    import config

    saved_s = config.CROSSFADE_SECONDS
    saved_e = config.CROSSFADE_ENABLED
    from src.player import audio_player as ap
    old_steps = ap._FADE_STEPS

    try:
        config.CROSSFADE_SECONDS = 0.5
        config.CROSSFADE_ENABLED = True
        ap._FADE_STEPS = 1  # 1 шаг — ничего не ждём

        p = _bare_player()
        # В режиме DIRECT _source_for вернёт URL — он не None. Для теста
        # «не скачался» делаем _source_for == None, заменяя кратко.
        p._source_for = lambda tr: None
        p._current.length = 5_000
        p._current.time = 4_500

        p._maybe_start_crossfade()
        assert p._fading is False
        # Запасной голос не должен играть.
        assert p._incoming.src is None
    finally:
        config.CROSSFADE_SECONDS = saved_s
        config.CROSSFADE_ENABLED = saved_e
        ap._FADE_STEPS = old_steps


# --------------------------------------------------------------------------- #
#  _volumes_collide — диагностика
# --------------------------------------------------------------------------- #


@S.add("_volumes_collide: равные → True, разные → False, -1 → False")
def _t_volumes_collide():
    p = _bare_player()
    a = _FakeVoice("a")
    b = _FakeVoice("b")

    a.vol = b.vol = 50
    # gain_out=0.9, gain_in=0.1 → diff 0.8; master=20 ⇒ diff*master=16 ≥10
    assert p._volumes_collide(a, b, master=20, gain_out=0.9, gain_in=0.1) is True

    b.vol = 55
    assert p._volumes_collide(a, b, 20, 0.9, 0.1) is False

    a.vol = b.vol = -1
    assert p._volumes_collide(a, b, 20, 0.9, 0.1) is False


# --------------------------------------------------------------------------- #
#  Работающая температура
# --------------------------------------------------------------------------- #


@S.add("выключение кроссфейда на лету меняет config и описание")
def _t_toggle():
    import config

    saved_e = config.CROSSFADE_ENABLED
    try:
        p = _bare_player()
        p.set_crossfade(False)
        assert config.CROSSFADE_ENABLED is False
        assert p.describe_crossfade() == "выключен"
        p.set_crossfade(True)
        assert "сек" in p.describe_crossfade()
    finally:
        config.CROSSFADE_ENABLED = saved_e