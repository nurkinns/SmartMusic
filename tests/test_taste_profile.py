"""
Тесты профиля вкусов — запасные цепочки вайбов, разбор ответа модели,
выбор треков.
"""

from tests.core import suite
from config import STATE_CALM, STATE_COMBAT, STATE_DEATH, STATE_VICTORY, STATE_DEFEAT

S = suite("Профиль вкусов — цепочки вайбов")


# --------------------------------------------------------------------------- #
#  _candidates_for_state
# --------------------------------------------------------------------------- #


@S.add("COMBAT ночью → первая запасная CALM")
def _t_night_combat_fallback():
    from src.ai.taste_profile import TasteProfileAI

    tp = TasteProfileAI()
    candidates = tp._candidates_for_state(STATE_COMBAT, is_night=True)
    assert candidates[0] == STATE_CALM, f"ночью первым должен быть CALM: {candidates}"


@S.add("DEFEAT → первая запасная VICTORY (как финал матча)")
def _t_defeat_fallback():
    from src.ai.taste_profile import TasteProfileAI

    tp = TasteProfileAI()
    candidates = tp._candidates_for_state(STATE_DEFEAT, is_night=False)
    assert candidates[0] == STATE_VICTORY


@S.add("VICTORY → первая VICTORY, потом COMBAT, потом CALM")
def _t_victory_order():
    from src.ai.taste_profile import TasteProfileAI

    tp = TasteProfileAI()
    c = tp._candidates_for_state(STATE_VICTORY, False)
    assert c == [STATE_VICTORY, STATE_COMBAT, STATE_CALM], c


@S.add("DEATH → первая DEATH, потом CALM, потом VICTORY")
def _t_death_order():
    from src.ai.taste_profile import TasteProfileAI

    tp = TasteProfileAI()
    c = tp._candidates_for_state(STATE_DEATH, False)
    assert c == [STATE_DEATH, STATE_CALM, STATE_VICTORY], c


@S.add("CALM → первая CALM, потом DEATH")
def _t_calm_order():
    from src.ai.taste_profile import TasteProfileAI

    tp = TasteProfileAI()
    c = tp._candidates_for_state(STATE_CALM, False)
    assert c == [STATE_CALM, STATE_DEATH], c


@S.add("COMBAT днём → первая COMBAT")
def _t_combat_order():
    from src.ai.taste_profile import TasteProfileAI

    tp = TasteProfileAI()
    c = tp._candidates_for_state(STATE_COMBAT, False)
    assert c[0] == STATE_COMBAT, c


# --------------------------------------------------------------------------- #
#  get_tracks_for_state — fallback между категориями
# --------------------------------------------------------------------------- #


@S.add("в VICTORY нет треков → откатывается на COMBAT")
def _t_get_victory_fallback():
    from src.ai.taste_profile import TasteProfileAI

    tp = TasteProfileAI()
    tp._load_vibe_db = lambda: {
        STATE_VICTORY: [],
        STATE_COMBAT: [{"id": "c1"}],
        STATE_CALM: [],
    }
    got = tp.get_tracks_for_state(STATE_VICTORY)
    assert len(got) >= 1 and got[0]["id"] == "c1", f"откат не сработал: {got}"


@S.add("все категории пусты → []")
def _t_get_all_empty():
    from src.ai.taste_profile import TasteProfileAI

    tp = TasteProfileAI()
    tp._load_vibe_db = lambda: {s: [] for s in
                                (STATE_CALM, STATE_COMBAT, STATE_DEATH,
                                 STATE_VICTORY, STATE_DEFEAT)}
    assert tp.get_tracks_for_state(STATE_COMBAT) == []


# --------------------------------------------------------------------------- #
#  get_track_for_state (один трек)
# --------------------------------------------------------------------------- #


@S.add("get_track_for_state: трек есть → возвращается")
def _t_get_one_ok():
    from src.ai.taste_profile import TasteProfileAI

    tp = TasteProfileAI()
    tp.vibe_db = {STATE_CALM: [{"id": "x", "title": "Test"}]}
    t = tp.get_track_for_state(STATE_CALM)
    assert t is not None and t["id"] == "x"


@S.add("get_track_for_state: пусто → None")
def _t_get_one_none():
    from src.ai.taste_profile import TasteProfileAI

    tp = TasteProfileAI()
    tp.vibe_db = {STATE_CALM: []}
    assert tp.get_track_for_state(STATE_CALM) is None


# --------------------------------------------------------------------------- #
#  _parse_assignment (разбор ответа модели)
# --------------------------------------------------------------------------- #


@S.add("_parse_assignment: JSON → категории, NOT_MUSIC исключаются")
def _t_parse():
    from src.ai.taste_profile import _parse_assignment

    batch = [
        {"id": "0", "title": "A"},
        {"id": "1", "title": "B"},
        {"id": "2", "title": "C"},
        {"id": "3", "title": "D"},
    ]
    text = (
        '```json\n'
        '{"CALM":[{"n":0}],"COMBAT":[{"n":1},{"n":9}],'
        '"NOT_MUSIC":[{"n":2,"reason":"meme"}]}\n'
        '```'
    )
    out, not_music = _parse_assignment(text, batch)
    assert {t["id"] for t in out[STATE_CALM]} == {"0"}
    assert {t["id"] for t in out[STATE_COMBAT]} == {"1"}
    assert not_music == {2}


@S.add("_parse_assignment: числа вместо объектов — тоже работают")
def _t_parse_ints():
    from src.ai.taste_profile import _parse_assignment

    batch = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    text = '{"CALM": [0, 2], "NOT_MUSIC": [1]}'
    out, not_music = _parse_assignment(text, batch)
    assert {t["id"] for t in out[STATE_CALM]} == {"a", "c"}
    assert not_music == {1}


@S.add("_parse_assignment: мусорный ответ → пусто")
def _t_parse_garbage():
    from src.ai.taste_profile import _parse_assignment

    out, not_music = _parse_assignment("это не JSON", [{"id": "0"}])
    assert out == {}
    assert not_music == set()