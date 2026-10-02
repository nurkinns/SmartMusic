"""
Тесты DJ Brain — мозга ИИ-диджея.

Проверяем решения о смене вайба: все состояния игры, кулдауны (гистерезис),
ночной режим, отложенные вайбы, resume() и обработку реальных итогов
воспроизведения.

Звука здесь нет: вместо настоящего AudioPlayer подставляется StubPlayer,
который ничего не играет и не поднимает VLC.
"""

from tests.core import suite
from config import (
    STATE_CALM,
    STATE_COMBAT,
    STATE_DEATH,
    STATE_VICTORY,
    STATE_DEFEAT,
    STATE_IDLE,
    NIGHT_VOLUME_FACTOR,
)

S = suite("DJ Brain — мозг диджея")


class StubPlayer:
    """Плеер, который ничего не играет, а только запоминает вызовы."""

    def __init__(self):
        self._cb = None
        self.play_lists = []
        self.volumes = []
        self.play_list_result = True

    def set_play_result_callback(self, cb):
        self._cb = cb

    def play_list(self, tracks, start_index=0):
        self.play_lists.append(list(tracks))
        return self.play_list_result

    def set_volume(self, factor):
        self.volumes.append(factor)

    def shutdown(self):
        pass

    def report(self, track, ok, reason=""):
        """Имитируем итог из потока команд плеера."""
        if self._cb:
            self._cb(track, ok, reason)


def brain(cooldown=0, player=None):
    """Собирает DJ Brain на заглушке плеера."""
    from src.ai.dj_brain import DJBrain

    p = player or StubPlayer()
    return DJBrain(cooldown_seconds=cooldown, player=p), p


# --------------------------------------------------------------------------- #
#  Состояния вайбов
# --------------------------------------------------------------------------- #


@S.add("создаётся и подписывается на итог воспроизведения")
def _t_construct():
    b, p = brain()
    assert b.current_state == STATE_IDLE
    assert p._cb is not None, "плеер обязан подписать DJ Brain на итог"


@S.add("CALM запускает спокойный плейлист")
def _t_calm():
    b, p = brain(cooldown=0)
    b.set_night_enabled(False)
    r = b.evaluate_state(STATE_CALM)
    assert r["status"] == "SUCCESS", r
    assert r["action"] == "SWITCH_CALM", r
    assert b.current_state == STATE_CALM
    assert p.play_lists and len(p.play_lists[-1]) >= 1


@S.add("COMBAT запускает боевой плейлист")
def _t_combat():
    b, p = brain(cooldown=0)
    b.set_night_enabled(False)
    r = b.evaluate_state(STATE_COMBAT)
    assert r["action"] == "SWITCH_COMBAT", r
    assert b.current_state == STATE_COMBAT


@S.add("DEATH запускает плейлист смерти/грусти")
def _t_death():
    b, p = brain(cooldown=0)
    b.set_night_enabled(False)
    r = b.evaluate_state(STATE_DEATH)
    assert r["action"] == "SWITCH_DEATH", r
    assert b.current_state == STATE_DEATH


@S.add("VICTORY и DEFEAT ведут к MATCH_END")
def _t_end():
    b, p = brain(cooldown=0)
    b.set_night_enabled(False)
    r = b.evaluate_state(STATE_VICTORY)
    assert r["action"] == "MATCH_END", r
    r = b.evaluate_state(STATE_DEFEAT)
    assert r["action"] == "MATCH_END", r


@S.add("повторное то же состояние → NO_CHANGE, плейлист не перезапускается")
def _t_no_change():
    b, p = brain(cooldown=0)
    b.set_night_enabled(False)
    b.evaluate_state(STATE_CALM)
    calls_before = len(p.play_lists)
    r = b.evaluate_state(STATE_CALM)
    assert r["status"] == "NO_CHANGE", r
    assert len(p.play_lists) == calls_before


# --------------------------------------------------------------------------- #
#  Кулдаун (гистерезис) и отложенный вайб
# --------------------------------------------------------------------------- #


@S.add("кулдаун блокирует смену и запоминает вайб, не трогая current_state")
def _t_cooldown():
    import time

    b, p = brain(cooldown=30)  # кулдаун огромный
    b.set_night_enabled(False)
    b.evaluate_state(STATE_CALM)
    b.last_switch_timestamp = time.time()  # свежий переход → кулдаун активен
    r = b.evaluate_state(STATE_COMBAT)
    assert r["status"] == "COOLDOWN_ACTIVE", r
    assert b.current_state == STATE_CALM, "состояние не должно смениться"
    assert b.pending_state == STATE_COMBAT, "вайб обязан запомниться"


@S.add("когда кулдаун истёк, отложенный вайб включается")
def _t_cooldown_retry():
    import time

    b, p = brain(cooldown=30)
    b.set_night_enabled(False)
    b.evaluate_state(STATE_CALM)
    b.last_switch_timestamp = time.time()  # только что переключились → кулдаун активен
    first = b.evaluate_state(STATE_COMBAT)
    assert first["status"] == "COOLDOWN_ACTIVE", first
    # Кулдаун «истёк»: 0 секунд и старый таймстемп.
    b.cooldown_seconds = 0
    b.last_switch_timestamp = 0.0
    r = b.evaluate_state(STATE_COMBAT)
    assert r["status"] == "SUCCESS", r
    assert b.current_state == STATE_COMBAT
    assert b.pending_state is None


@S.add("во время кулдауна новый вайб перетирает старый в слоте")
def _t_cooldown_overwrite():
    import time

    b, p = brain(cooldown=30)
    b.set_night_enabled(False)
    b.evaluate_state(STATE_CALM)
    b.last_switch_timestamp = time.time()
    b.evaluate_state(STATE_COMBAT)  # заблокировано → слот = COMBAT
    assert b.pending_state == STATE_COMBAT
    r = b.evaluate_state(STATE_DEATH)  # пришёл более свежий вайб
    assert r["status"] == "COOLDOWN_ACTIVE"
    assert b.pending_state == STATE_DEATH


@S.add("IDLE не считается вайбом для музыки: NO_ACTION, состояние фиксируется")
def _t_idle():
    b, p = brain(cooldown=0)
    b.set_night_enabled(False)
    b.evaluate_state(STATE_CALM)
    r = b.evaluate_state(STATE_IDLE)
    assert r["status"] == "SUCCESS", r
    assert r["action"] == "NO_ACTION", r
    assert b.current_state == STATE_IDLE


# --------------------------------------------------------------------------- #
#  Ночной режим
# --------------------------------------------------------------------------- #


class _FakeDateTime:
    """datetime.now() с фиксированным часом (инстанс, можно менять на ходу)."""

    def __init__(self, hour=12):
        self.hour = hour

    def now(self):
        from datetime import datetime as real

        return real(2026, 1, 1, self.hour, 0, 0)


@S.add("ночью громкость снижается до NIGHT_VOLUME_FACTOR")
def _t_night_volume():
    from src.ai import dj_brain as djb

    saved_dt = djb.datetime
    saved_enabled = djb.NIGHT_MODE_ENABLED
    try:
        omega = _FakeDateTime()
        djb.datetime = omega

        # Первый прогон — «день».
        b, p = brain(cooldown=0)
        b.set_night_enabled(True)
        omega.hour = 12
        assert b.is_night_time() is False, f"час {omega.hour}: ночь не ожидается"
        assert b.get_current_volume_factor() == 1.0, (
            f"днём громкость 1.0, а не {b.get_current_volume_factor()}")

        # Ночь наступила.
        omega.hour = 1
        assert b.is_night_time() is True, f"час {omega.hour}: должна быть ночь"
        assert b.get_current_volume_factor() == NIGHT_VOLUME_FACTOR, (
            f"ночью {NIGHT_VOLUME_FACTOR}, а не {b.get_current_volume_factor()}")
        assert b.get_current_volume_factor() < 1.0

        b.apply_night_limit()
        assert p.volumes and abs(p.volumes[-1] - NIGHT_VOLUME_FACTOR) < 1e-6
    finally:
        djb.datetime = saved_dt
        djb.NIGHT_MODE_ENABLED = saved_enabled


@S.add("переключатель ночного режима работает сразу")
def _t_night_toggle():
    from src.ai import dj_brain as djb

    saved_dt = djb.datetime
    try:
        omega = _FakeDateTime()
        djb.datetime = omega
        omega.hour = 1  # ночь

        b, p = brain(cooldown=0)
        b.set_night_enabled(True)
        assert b.night_enabled() is True
        assert b.is_night_time() is True, f"должна быть ночь (час {omega.hour})"

        b.set_night_enabled(False)
        assert b.night_enabled() is False
        assert b.is_night_time() is False, "после выключения ночи быть не должно"
        assert b.get_current_volume_factor() == 1.0
    finally:
        djb.datetime = saved_dt


# --------------------------------------------------------------------------- #
#  resume() — ручное «играть»
# --------------------------------------------------------------------------- #


@S.add("resume() вне матча включает CALM, как и задумано")
def _t_resume():
    b, p = brain(cooldown=0)
    b.set_night_enabled(False)
    b.current_state = STATE_IDLE
    r = b.resume()
    assert r["status"] == "SUCCESS", r
    assert r["state"] == STATE_CALM, r
    assert b.current_state == STATE_CALM
    assert r["action"] == "SWITCH_CALM", r


# --------------------------------------------------------------------------- #
#  Итог воспроизведения (playback_result)
# --------------------------------------------------------------------------- #


@S.add("успешное воспроизведение заполняет current_track")
def _t_play_ok():
    b, p = brain()
    b._requested_id = "abc"
    p.report({"id": "abc", "artist": "X", "title": "Song"}, True, "")
    assert b.current_track is not None
    assert b.current_track["id"] == "abc"
    assert b._requested_id is None
    assert b.last_failure() == ""


@S.add("провал трека, который просили, — last_failure, current_track сброшен")
def _t_play_fail():
    b, p = brain()
    b._requested_id = "abc"
    b.current_track = {"id": "abc", "artist": "X", "title": "Song"}
    p.report({"id": "abc", "artist": "X", "title": "Song"}, False,
             "источник недоступен")
    assert b.last_failure() == "источник недоступен"
    assert b.current_track is None
    assert b._requested_id is None


@S.add("провал последующего трека плейлиста не сбрасывает current_track")
def _t_play_fail_other():
    b, p = brain()
    b._requested_id = "zzz"
    b.current_track = {"id": "keep", "artist": "X", "title": "Song"}
    p.report({"id": "other", "artist": "Y", "title": "Other"}, False, "сеть")
    assert b.current_track["id"] == "keep", "чужой провал не трогает наш трек"
    assert b._requested_id == "zzz", "чужой запрос не трогаем"


# --------------------------------------------------------------------------- #
#  Ошибки выбора треков
# --------------------------------------------------------------------------- #


class _EmptyTaste:
    """Профиль вкусов, в котором вообще нет треков."""

    def get_tracks_for_state(self, state, is_night=False):
        return []


@S.add("пустая библиотека → честный NO_TRACKS, вайб не засчитывается")
def _t_no_tracks():
    b, p = brain(cooldown=0)
    b.taste_profile = _EmptyTaste()
    b.set_night_enabled(False)
    r = b.evaluate_state(STATE_COMBAT)
    assert r["action"] == "NO_TRACKS", r
    assert b.current_state == STATE_IDLE, "пустая база — не повод верить, что играет"


@S.add("плеер не смог включить → PLAYBACK_FAILED, кулдаун не щёлкается")
def _t_playback_failed():
    b, p = brain(cooldown=0)
    b.set_night_enabled(False)
    p.play_list_result = False
    r = b.evaluate_state(STATE_COMBAT)
    assert r["action"] == "PLAYBACK_FAILED", r
    # Состояние не фиксируется: следующий пакет попробует снова.
    assert b.current_state == STATE_IDLE