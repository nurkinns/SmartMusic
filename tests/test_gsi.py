"""
Тесты GSI-сервера: приём фейковых пакетов Dota 2, разбор состояний,
отклик через HTTP.

Перед импортом dota_gsi подменяем AudioPlayer на StubPlayer — иначе DJBrain
на уровне модуля создал бы настоящий VLC-плеер.
"""

import asyncio

from tests.core import suite, Skip

# Ставим заглушку плеера ДО импорта dota_gsi.
from tests.test_dj_brain import StubPlayer
import src.ai.dj_brain as djb

djb.AudioPlayer = StubPlayer

import config
from config import STATE_IDLE, STATE_CALM, STATE_COMBAT, STATE_DEATH
from src.triggers import dota_gsi as gsi

S = suite("GSI — приём пакетов Dota 2")


class _FakeRequest:
    """Подмена входящего HTTP-запроса FastAPI — только .json()."""

    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


def _send(payload):
    """Отправка фейкового GSI-пакета в обработчик сервера."""
    return asyncio.run(gsi.gsi_receiver(_FakeRequest(payload)))


def _payload(**kw):
    """Стандартный GSI-пакет с произвольными полями."""
    return {
        "provider": {"name": "Dota 2", "appid": 570, "steamid": "123"},
        "map": {
            "game_state": kw.get("game_state",
                                 "DOTA_GAMERULES_STATE_GAME_IN_PROGRESS"),
            "win_team": kw.get("win_team", ""),
        },
        "hero": {
            "alive": kw.get("alive", True),
            "health": kw.get("health", 1000),
            "max_health": kw.get("max_health", 1000),
            "health_percent": kw.get("health_percent", 100),
        },
        "player": {"team_name": kw.get("team", "radiant")},
    }


def _reset():
    """Сбрасываем модульное состояние GSI перед каждым сценарием."""
    gsi.last_game_state = STATE_IDLE
    gsi._was_alive = True
    gsi.reset_damage_window()
    gsi.dj_brain.cooldown_seconds = 0
    gsi.dj_brain.last_switch_timestamp = 0.0
    gsi.dj_brain.current_state = STATE_IDLE
    gsi.dj_brain.pending_state = None
    gsi.dj_brain.set_night_enabled(False)


# --------------------------------------------------------------------------- #
#  End-to-end отклик сервера
# --------------------------------------------------------------------------- #


@S.add("валидный пакет фарма → ответ ok, состояние CALM")
def _t_farm_ok():
    _reset()
    r = _send(_payload())
    assert r["status"] == "ok", r
    assert r["state"] == "CALM", r


@S.add("повторный тот же вайб → NO_CHANGE")
def _t_no_change():
    _reset()
    _send(_payload())
    r = _send(_payload())
    assert r["dj_decision"] == "NO_CHANGE", r


@S.add("урон в окне > 15% → COMBAT")
def _t_combat():
    _reset()
    r1 = _send(_payload(health=1000))
    r2 = _send(_payload(health=500, max_health=1000))
    assert r2["state"] == "COMBAT", r2


@S.add("герой мёртв → DEATH")
def _t_death():
    _reset()
    r = _send(_payload(alive=False, health=0))
    assert r["state"] == "DEATH", r


@S.add("конец матча → VICTORY при совпадении команды")
def _t_victory():
    _reset()
    r = _send(_payload(game_state="DOTA_GAMERULES_STATE_POST_GAME",
                       win_team="radiant", team="radiant"))
    assert r["state"] == "VICTORY", r


@S.add("конец матча → DEFEAT при другой команде")
def _t_defeat():
    _reset()
    r = _send(_payload(game_state="DOTA_GAMERULES_STATE_POST_GAME",
                       win_team="dire", team="radiant"))
    assert r["state"] == "DEFEAT", r


@S.add("вне матча → IDLE")
def _t_idle():
    _reset()
    r = _send(_payload(game_state="DOTA_GAMERULES_STATE_HERO_SELECTION"))
    assert r["state"] == "IDLE", r


# --------------------------------------------------------------------------- #
#  Анализ состояний напрямую
# --------------------------------------------------------------------------- #


@S.add("analyze_game_state: низкое HP → COMBAT")
def _t_analyze_low_hp():
    state = gsi.analyze_game_state(_payload(health=300, max_health=1000,
                                             health_percent=30))
    assert state == "COMBAT", f"низкое HP не распознано: {state}"


@S.add("analyze_game_state: здоровый герой + нет урона → CALM")
def _t_analyze_calm():
    _reset()
    state = gsi.analyze_game_state(_payload(health=1000))
    assert state == "CALM", f"должен быть CALM: {state}"


@S.add("analyze_game_state: мусорное HP зажимается в [0, max_health]")
def _t_analyze_clamp():
    _reset()
    state = gsi.analyze_game_state(_payload(health=1500, max_health=1000,
                                             health_percent=90))
    # health=1500 → clamped to 1000, damage=0, health_percent=90 → CALM
    assert state == "CALM", f"мусорный HP дал {state}"


@S.add("analyze_game_state: возрождение сбрасывает окно")
def _t_analyze_respawn():
    _reset()
    gsi._was_alive = True
    # Несколько пакетов с уроном.
    gsi.analyze_game_state(_payload(health=1000))
    gsi.analyze_game_state(_payload(health=500))  # COMBAT
    assert gsi.damage_window.damage() > 0, "урон должен быть зафиксирован"
    # Смерть.
    gsi.analyze_game_state(_payload(alive=False, health=0))
    gsi._was_alive = False
    # Воскрес.
    gsi.analyze_game_state(_payload(health=1000))
    assert gsi.damage_window.damage() == 0.0, (
        f"окно не сброшено: {gsi.damage_window.damage()}")


# --------------------------------------------------------------------------- #
#  Ошибки
# --------------------------------------------------------------------------- #


@S.add("битый пакет → ответ error с причиной в details")
def _t_error():
    _reset()
    saved = gsi.analyze_game_state

    def _boom(payload_arg):
        raise ValueError("тестовая ошибка разбора")

    gsi.analyze_game_state = _boom
    try:
        r = _send(_payload())
        assert r["status"] == "error", r
        assert "тестовая" in r.get("details", ""), r
    finally:
        gsi.analyze_game_state = saved


@S.add("NameError-регресс: _notify_track не вызывает self и не ломается")
def _t_nameerror():
    _reset()
    saved = gsi._notify_track

    def _check(action, decision, state):
        assert isinstance(decision, dict), f"decision не словарь: {decision}"

    gsi._notify_track = _check
    try:
        r = _send(_payload())
        assert r["status"] == "ok", r
    finally:
        gsi._notify_track = saved


# --------------------------------------------------------------------------- #
#  Работа с _notify_track
# --------------------------------------------------------------------------- #


@S.add("_notify_track не падает и принимает любые комбинации")
def _t_notify_call():
    _reset()
    # Нормальный трек.
    gsi._notify_track("SWITCH_COMBAT",
                      {"status": "SUCCESS",
                       "track": {"title": "T", "artist": "A", "id": "x"}},
                      "COMBAT")
    # Кулдаун.
    gsi._notify_track("NO_ACTION",
                      {"status": "COOLDOWN_ACTIVE", "remaining_seconds": 10,
                       "track": {"title": "Old"}},
                      "COMBAT")
    # Нет треков.
    gsi._notify_track("NO_TRACKS",
                      {"status": "SUCCESS", "track": None},
                      "CALM")
    # Провал воспроизведения.
    gsi._notify_track("PLAYBACK_FAILED",
                      {"status": "SUCCESS", "track": None},
                      "CALM")
    # Главное — не упасть.
    assert True