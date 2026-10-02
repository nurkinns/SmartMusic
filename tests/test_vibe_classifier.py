"""
Тесты классификатора вайбов: энергия трека (темп + телесность), настроение
из названия, комбинация в единый вердикт.
"""

from tests.core import suite
from config import STATE_CALM, STATE_COMBAT, STATE_DEATH, STATE_VICTORY

S = suite("Классификатор вайбов — энергия и настроение")


# --------------------------------------------------------------------------- #
#  energy_score
# --------------------------------------------------------------------------- #


@S.add("energy_score: быстрый и ударный трек ≈ 100")
def _t_energy_high():
    from src.ai import vibe_classifier as vc

    e = vc.energy_score({"bpm": 180.0, "punch": 10.0})
    assert e is not None
    assert e >= 55, f"энергия быстрого трека слишком низкая: {e}"


@S.add("energy_score: медленный и мягкий трек ≈ 0")
def _t_energy_low():
    from src.ai import vibe_classifier as vc

    e = vc.energy_score({"bpm": 60.0, "punch": -30.0})
    assert e is not None
    assert e < 55, f"энергия медленного трека слишком высокая: {e}"


@S.add("energy_score: нет фич → None")
def _t_energy_none():
    from src.ai import vibe_classifier as vc

    assert vc.energy_score(None) is None
    assert vc.energy_score({}) is None


@S.add("energy_score: bpm == 0 → None")
def _t_energy_zero_bpm():
    from src.ai import vibe_classifier as vc

    assert vc.energy_score({"bpm": 0, "punch": 0}) is None


# --------------------------------------------------------------------------- #
#  mood_from_title
# --------------------------------------------------------------------------- #


@S.add("mood_from_title: слова печали → DEATH")
def _t_mood_death():
    from src.ai import vibe_classifier as vc

    r = vc.mood_from_title("Sad Song")
    assert r == STATE_DEATH, f"«Sad Song» → {r}"
    r = vc.mood_from_title("грустная мелодия")
    assert r == STATE_DEATH, f"«грустная мелодия» → {r}"
    r = vc.mood_from_title("Lonely Night")
    assert r == STATE_DEATH, f"«Lonely Night» → {r}"
    r = vc.mood_from_title("печальный вальс")
    assert r == STATE_DEATH, f"«печальный вальс» → {r}"


@S.add("mood_from_title: слова победы → VICTORY")
def _t_mood_victory():
    from src.ai import vibe_classifier as vc

    assert vc.mood_from_title("Epic Triumph") == STATE_VICTORY
    assert vc.mood_from_title("победный марш") == STATE_VICTORY
    assert vc.mood_from_title("Champion") == STATE_VICTORY


@S.add("mood_from_title: без характерных слов → None")
def _t_mood_none():
    from src.ai import vibe_classifier as vc

    assert vc.mood_from_title("Ordinary Track") is None
    assert vc.mood_from_title("") is None


@S.add("mood_from_title: DEATH важнее VICTORY, если оба слова в названии")
def _t_mood_death_over_victory():
    from src.ai import vibe_classifier as vc

    ast = vc.mood_from_title("Goodbye Victory")
    assert ast == STATE_DEATH, f"смерть должна перевесить: {ast}"


# --------------------------------------------------------------------------- #
#  classify_track
# --------------------------------------------------------------------------- #


@S.add("classify_track: высокоэнергичный трек → COMBAT")
def _t_classify_combat():
    from src.ai import vibe_classifier as vc

    state, reason = vc.classify_track("Fast Track", {"bpm": 170, "punch": 0})
    assert state == STATE_COMBAT, f"ожидали COMBAT: {state}"
    assert "энергия" in reason, reason


@S.add("classify_track: низкоэнергичный трек → CALM")
def _t_classify_calm():
    from src.ai import vibe_classifier as vc

    state, reason = vc.classify_track("Chill Out", {"bpm": 80, "punch": -25})
    assert state == STATE_CALM, f"ожидали CALM: {state}"
    assert "энергия" in reason, reason


@S.add("classify_track: грустное слово побеждает высокую энергию")
def _t_classify_mood_over_energy():
    from src.ai import vibe_classifier as vc

    state, _ = vc.classify_track("Sad and Fast", {"bpm": 170, "punch": 10})
    assert state == STATE_DEATH, f"настроение выше энергии: {state}"


@S.add("classify_track: боевое слово в названии без фич → COMBAT")
def _t_classify_word_combat():
    from src.ai import vibe_classifier as vc

    state, reason = vc.classify_track("Phonk Feeling", features=None)
    assert state == STATE_COMBAT, f"должен быть combat по слову: {state}"
    assert "бой по слову" in reason, reason


@S.add("classify_track: спокойное слово в названии без фич → CALM")
def _t_classify_word_calm():
    from src.ai import vibe_classifier as vc

    state, reason = vc.classify_track("Lo-Fi Dreams", features=None)
    assert state == STATE_CALM, f"должен быть calm: {state}"
    assert "спокойное слово" in reason, reason


@S.add("classify_track: нет ни фич, ни слов → None")
def _t_classify_unknown():
    from src.ai import vibe_classifier as vc

    state, reason = vc.classify_track("Unrelated Channel", features=None)
    assert state is None, f"должен быть None: {state}"
    assert "доказательств" in reason, reason


# --------------------------------------------------------------------------- #
#  Регрессии
# --------------------------------------------------------------------------- #


@S.add("слово slowed больше не отправляет треки в DEATH")
def _t_slowed_not_death():
    from src.ai import vibe_classifier as vc

    state, reason = vc.classify_track("Song (slowed + reverb)", features=None)
    assert state != STATE_DEATH, f"slowed не должно быть смертью: {state}"
    assert state is None, f"но и без фич slow не тянет на combat: {state}"