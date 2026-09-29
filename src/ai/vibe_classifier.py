"""
vibe_classifier.py — раскладывает треки по вайбам по-настоящему.

Что было не так
--------------
Раньше вайб определяла нейросеть по названию. Модель на 3 миллиарда
параметров не знает, что «DARUDE - SANDSTORM» это агрессивная техно, а
«Holidays» - тихая задумчивость. Она наугад раскидывала треки по спискам,
а потом мы выравнивали результат, чтобы категории выглядели одинаково.
Получалась ровная, но бессмысленная раскладка: в COMBAT лежал тихий трек,
в CALM - легендарный хардстайл.

Как теперь
----------
Главный источник правды - сам звук. audio_analyzer.py измеряет темп и
энергию, и распределение идёт по числам, а не по догадкам.

Но одного темпа мало, и вот почему. Темп отвечает на вопрос «быстро или
медленно», а вайб отвечает на вопрос «какое настроение». Медленный
грустный трек и медленный чил-бит - оба низкотемповые, но попасть они
должны в разные категории. Поэтому работают две оси:

  ОСЬ ЭНЕРГИИ (из звука) - решает COMBAT или CALM.
      Быстрый и телесный трек - это бой. Медленный и мягкий - фон.
      Голоса нейросети тут нет вообще, только числа.

  ОСЬ НАСТРОЕНИЯ (из названия) - решает DEATH и VICTORY.
      Грустный трек попадает в DEATH независимо от темпа: замедленная
      баллада в 140 BPM всё равно про прощание, а весёлый трек в 100 BPM
      про победу. Эти два вайба - про смысл, и смысл видно только в
      названии.

И главное: ничего не выравнивается. Если энергичных треков 25, значит
в COMBAT их будет 25, а не 15 «ради красивой таблички». Лучше перекос
в пользу правды, чем ровная картинка из неверных категорий.
"""

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import STATE_CALM, STATE_COMBAT, STATE_DEATH, STATE_VICTORY
from src.ai import audio_analyzer

# --------------------------------------------------------------------------
# Пороги по энергии
# --------------------------------------------------------------------------
# Энергия пересчитывается в шкалу 0..100, где 0 - это еле слышно,
# а 100 - полный разгон. Границы подобраны по замерам твоей же
# библиотеки, а не на глаз.
#
# TEMPO_LOW/TEMPO_HIGH - границы темпа в BPM, которые пересчитываются
# в 0 и 100. Ниже 90 BPM - вяло, 180 - верхняя граница разгона.
# Широкие границы здесь специальные: весь разброс библиотеки укладывается
# внутрь (86..172 BPM), и шкала нигде не упирается в потолок.
TEMPO_LOW = 90.0
TEMPO_HIGH = 180.0

# «Телесность» трека: сколько в нём средних частот (ударные, бас-гитар,
# синты) относительно низких. Ударный трек - это COMBAT, даже если
# темп невысокий: ровно для этого есть класс музыки вроде nu-metal.
PUNCH_LOW = -25.0     # почти нет середины: мягкий фон
PUNCH_HIGH = -10.0    # много середины: телесный, ударный трек

# Граница энергии, выше которой трек считается боевым.
#
# Порог перепроверен после починки октавы в audio_analyzer. Починка
# сдвинула четыре трека вниз (Kendrick, Uknowhowwedu, Pete Rock, Paper
# Planes перестали считаться вдвое быстрее), так что границу надо было
# проверить заново. Проверял не «красиво ли число», а попадает ли она
# в разрыв между энергиями.
#
# Сразу над порогом: Darude - Sandstorm (57), GORILLA GLU phonk (56).
# Сразу под порогом: Culture Beat - Mr. Vain (52), GALA (51).
#
# То есть 55 попадает в пустоту между «явно боевыми» и «явно спокойными»,
# а не режет середину списка пополам. Двигать можно, но тогда придётся
# решить, куда отправлять eurodance вроде Mr. Vain - 129 BPM, а энергии
# уже не хватает.
COMBAT_THRESHOLD = 55.0

# --------------------------------------------------------------------------
# Настроение из названия
# --------------------------------------------------------------------------
# Эти слова решают, грустный трек или победный. Темп тут не поможет:
# замедленная баллада бывает быстрой, а марш бывает медленным.

# Замечание про «slowed». Раньше это слово стояло здесь, и это была ошибка.
# «slowed» - не настроение, а продакшн-тег: замедленный трек может быть
# любым, включая злой (slowed phonk, slowed trap - огромный поджанр).
# Из-за этого слова КАЖДЫЙ трек с пометкой (Slowed) попадал в DEATH, даже
# если по звуку он был самым боевым в библиотеке. Замедление и так учтено
# при замере звука - трек физически звучит медленнее, и энергия отражает
# это сама. Слово здесь было лишним и вредило.
DEATH_WORDS = [
    "sad", "sadness", "death", "dead", "die", "dying",
    "despair", "alone", "lonely", "loneliness", "crying", "cry",
    "heartbreak", "goodbye", "farewell", "lost", "loss", "pain",
    "sorry", "tears", "tear", "broken", "grief", "funeral", "grave",
    "ballad", "melancholy", "melancholic", "sorrow", "tragic",
    "мрачн", "груст", "тоск", "одинок", "плак", "страдан", "уныл",
    "печал", "слез", "слезы", "скорб", "погиб",
]

VICTORY_WORDS = [
    "victory", "win", "winner", "wins", "champion", "champions",
    "triumph", "epic", "hero", "heroes", "glory", "conquer", "legend",
    "immortal", "victorious", "championship", "triumphant", "anthem",
    "fanfare", "boss fight", "final boss", "level up",
    "orchestral", "orchestra", "soundtrack", "cinematic", "main theme",
    "победа", "побед", "побежда", "победитель", "герой", "героизм",
    "торжеств", "триумф", "чемпион", "легенд", "слава", "финальн",
    "босс", "гимн", "марш", "оркестр",
]

# Явное боевое слово. Например phonk и drift - это всегда бой, даже если
# трек не самый быстрый.
COMBAT_WORDS = [
    "phonk", "drift", "aggressive", "rage", "fight", "war", "battle",
    "gym", "workout", "intense", "brutal", "violence", "riot", "hardstyle",
    "hardcore", "raw", "no mercy", "kill", "attack", "chaos", "demon",
    "злой", "зло", "агрессия", "злобы", "война", "бой", "драка", "свиреп",
]

CALM_WORDS = [
    "lofi", "lo-fi", "chill", "chillhop", "ambient", "sleep", "study",
    "relax", "relaxing", "calm", "soft", "dreamy", "jazz", "acoustic",
    "piano", "instrumental", "спокойн", "уют", "чилл", "релакс", "лоуфай",
    "сон", "дождь", "дождя", "ночь", "ночи", "мечта", "мечты",
]

# Слова ищем аккуратно, а не как куски текста. Иначе «Deadly Mix»
# считается словом «dead», а «боли» срабатывает на половину названия.
#
# Тут есть тонкость. Английские слова в списках - целые слова, их надо
# искать целиком с обеих сторон. А русские записаны основами («груст»,
# «одинок», «плак»), потому что в русском куча окончаний: «грустный»,
# «грустно», «грустить». Если потребовать границу после основы, ничего
# не найдётся. Поэтому для кириллицы проверяем только начало слова.
_CYRILLIC_RE = re.compile(r"[а-яё]")
_WORD_RE_CACHE: Dict[str, "re.Pattern"] = {}


def _matches_word(text: str, word: str) -> bool:
    """Ищет слово: английское - целиком, русскую основу - с начала."""
    pattern = _WORD_RE_CACHE.get(word)
    if pattern is None:
        # «ё» и «е» в названиях пишут как попало, поэтому считаем их
        # одной буквой.
        normalized = word.lower().replace("ё", "е")
        escaped = re.escape(normalized)
        if _CYRILLIC_RE.search(normalized):
            pattern = re.compile(rf"\b{escaped}")
        else:
            pattern = re.compile(rf"\b{escaped}\b")
        _WORD_RE_CACHE[word] = pattern
    return pattern.search(text) is not None


def _any_word(text: str, words: List[str]) -> bool:
    """Есть ли в тексте хоть одно из слов."""
    return any(_matches_word(text, w) for w in words)


def _scale(value: float, low: float, high: float) -> float:
    """Переводит число в шкалу 0..100 по границам low..high."""
    if high <= low:
        return 50.0
    ratio = (value - low) / (high - low)
    return max(0.0, min(100.0, ratio * 100.0))


def energy_score(features: Optional[Dict[str, float]]) -> Optional[float]:
    """
    Считает энергию трека в шкале 0..100.

    Складывает два независимых признака, взятых из самого звука:

      темп  - 75% веса. Это главный признак энергии: 90 BPM это вяло,
              180 BPM это полный разгон. Проверено на замерах всей
              библиотеки - темп там различает жанры надёжнее всего.
      телесность - 25% веса. Показывает, есть ли в треке средние
              частоты (ударные бочки, бас-гитара, синты). Нужен для
              музыки вроде nu-metal: темп невысокий (100 BPM), но трек
              физически тяжёлый и в драке звучит правильно.
              Вес намеренно небольшой. Проверка показала: у бас-тяжёлой
              музыки (phonk, dubstep) почти вся энергия уходит в самый
              низ, поэтому «телесность» у неё выходит низкой, хотя трек
              злой. Если дать этому признаку много веса, агрессивный
              phonk уезжает в CALM. Темп надёжнее, он и главный.

    Оба признака нормализуются по границам, подобранным по реальной
    музыке, а не выдуманным. Возвращает None, если трек не удалось
    измерить - тогда решение принимает нейросеть.
    """
    if not features:
        return None
    bpm = features.get("bpm", 0)
    punch = features.get("punch", PUNCH_LOW)
    if bpm <= 0:
        return None
    tempo_part = _scale(bpm, TEMPO_LOW, TEMPO_HIGH)
    punch_part = _scale(punch, PUNCH_LOW, PUNCH_HIGH)
    return round(tempo_part * 0.75 + punch_part * 0.25, 1)


def mood_from_title(title: str) -> Optional[str]:
    """
    Определяет настроение трека по названию.

    Возвращает STATE_DEATH, STATE_VICTORY или None, если непонятно.
    Порядок важен: сначала проверяем смерть, потом победу. Потому что
    трек про поражение - это всё-таки смерть, даже если в названии есть
    слово «win».
    """
    text = (title or "").lower().replace("ё", "е")

    if _any_word(text, DEATH_WORDS):
        return STATE_DEATH
    if _any_word(text, VICTORY_WORDS):
        return STATE_VICTORY
    return None


def classify_track(
    title: str,
    features: Optional[Dict[str, float]] = None,
) -> Tuple[Optional[str], str]:
    """
    Определяет вайб одного трека.

    Возвращает (категория, объяснение). Объяснение печатается при
    синхронизации, чтобы было видно, ПОЧЕМУ трек попал туда.

    Категория может быть None. Это значит «я не могу сказать, что это
    за трек»: звук измерить не вышло и в названии нет ни одного слова,
    за которое можно ухватиться. Такой трек в матрицу не попадает
    вообще.

    Почему не по умолчанию кидать в CALM
    ------------------------------------
    Так было раньше, и это была дыра. Трек, который не удалось ни
    скачать, ни опознать, молча уезжал в фон. Но «не смог определить»
    и «это музыка для фона» - разные вещи. Ролик из чужого плейлиста
    может оказаться хоть записью разговора. Если у нас нет ни одного
    доказательства, что это музыка, то заносить его в вайб-матрицу
    нельзя: это ровно тот случай, где в игре заиграет не трек.
    Пустую категорию лучше видно и починить, чем спрятанный мусор.

    Логика:
      1. Сначала смотрим настроение. Грустный трек - это DEATH, победный -
         VICTORY, и это важнее энергии.
      2. Если настроения нет, решает энергия из звука. Энергичный -
         COMBAT, спокойный - CALM.
      3. Если звук измерить не удалось, решает название. Есть явное
         боевое слово - COMBAT, спокойное - CALM.
      4. Иначе - не знаю (None).
    """
    mood = mood_from_title(title)
    if mood:
        reason = "настроение из названия"
        return mood, reason

    energy = energy_score(features)
    if energy is not None:
        if energy >= COMBAT_THRESHOLD:
            bpm = features.get("bpm", 0)
            punch = features.get("punch", 0)
            return (STATE_COMBAT,
                    f"энергия {energy:.0f} (темп {bpm:.0f} BPM, "
                    f"телесность {punch:.0f} дБ)")
        bpm = features.get("bpm", 0)
        punch = features.get("punch", 0)
        return (STATE_CALM,
                f"энергия {energy:.0f} (темп {bpm:.0f} BPM, "
                f"телесность {punch:.0f} дБ)")

    # звук измерить не вышло - падаем назад на название
    text = (title or "").lower().replace("ё", "е")
    if _any_word(text, COMBAT_WORDS):
        return STATE_COMBAT, "бой по слову в названии (звук не измерен)"
    if _any_word(text, CALM_WORDS):
        return STATE_CALM, "спокойное слово в названии (звук не измерен)"
    return None, "нет доказательств, что это вообще музыка"


# --------------------------------------------------------------------------
# Кэш замеров
# --------------------------------------------------------------------------
# Измерение стоит 2-3 секунды на трек (скачать, разобрать, посчитать).
# На 70 треках это несколько минут и куча запросов к YouTube, который
# начинает отдавать 403. Но замеры не меняются от запуска к запуску:
# тот же трек всегда даёт те же числа. Поэтому держим их в отдельном
# файле и переиспользуем.
CACHE_PATH = ROOT_DIR / "user_vibe_db_audio.json"

_CACHE: Dict[str, Dict[str, float]] = {}


def _load_cache() -> Dict[str, Dict[str, float]]:
    """
    Читает замеры с диска. Ошибки не страшны - просто меряем заново.

    Заодно выкидывает замеры, сделанные старой версией алгоритма: если
    способ расчёта поменялся, старые числа больше не годятся, и держать
    их - значит молча показывать неверную раскладку.
    """
    try:
        if CACHE_PATH.exists():
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {}

            fresh = {vid: feat for vid, feat in data.items()
                     if isinstance(feat, dict)
                     and feat.get("v") == audio_analyzer.ANALYZER_VERSION}
            stale = len(data) - len(fresh)
            if stale:
                print(f"♻️  [Vibe] {stale} замеров устарело "
                      f"(сменился алгоритм), меряем заново")

            # Темп в записи мог остаться от другого порога октавы: файл
            # переживает перезапуск, а человек мог подкрутить порог
            # кнопками и закрыть программу. Поэтому готовый темп
            # пересчитывается заново из сырых окон при каждой загрузке.
            # Иначе кнопки работали бы только до перезапуска, а потом
            # программа молча возвращалась бы к чужому порогу.
            _rederive(fresh, audio_analyzer._current_threshold())
            return fresh
    except Exception as e:
        print(f"⚠️ [Vibe] Не удалось прочитать кэш замеров: {e}")
    return {}


def _rederive(cache: Dict[str, Dict[str, float]], threshold: float) -> int:
    """
    Пересчитывает готовый темп в записях кэша под указанный порог октавы.

    Сырые данные окон от порога не зависят, поэтому пересчёт мгновенный и
    не требует звука. Возвращает, сколько записей поменяло темп.
    """
    changed = 0
    for entry in cache.values():
        if not isinstance(entry, dict):
            continue
        new_bpm = audio_analyzer.tempo_from_cache(entry, threshold)
        if round(float(entry.get("bpm", 0.0)), 1) != new_bpm:
            changed += 1
        entry["bpm"] = new_bpm
    return changed


def _save_cache() -> None:
    """Сохраняет замеры. Не смогли - не беда, просто в следующий раз
    придётся скачать заново."""
    try:
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(_CACHE, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ [Vibe] Не удалось сохранить кэш замеров: {e}")


_CACHE.update(_load_cache())


def reapply_octave_threshold(threshold: Optional[float] = None) -> Dict[str, int]:
    """
    Пересчитывает темп всех замеров под новый порог октавы.

    Звук при этом не скачивается заново: в кэше лежат сырые темпы всех
    окон и «доказательства» октавы, а готовый темп получается из них
    применением порога. Поэтому нажатие кнопки в интерфейсе срабатывает
    мгновенно, а не через десять минут повторной закачки.

    Возвращает, сколько треков поменяло темп.
    """
    if threshold is None:
        threshold = audio_analyzer._current_threshold()
    changed = _rederive(_CACHE, threshold)
    _save_cache()
    return {"total": len(_CACHE), "changed": changed}


def enrich_with_audio(
    tracks: List[Dict[str, Any]],
    on_progress=None,
) -> List[Dict[str, Any]]:
    """
    Измеряет звук для каждого трека и кладёт результат внутрь.

    Измерение медленное: трек скачивается и разбирается, это примерно
    2-3 секунды на один. Для 70 треков это пара минут, поэтому
    показываем прогресс.

    Треки, которые не удалось скачать, остаются работать дальше без
    признаков - classify_track сам это учтёт.
    """
    total = len(tracks)
    measured = 0
    for i, track in enumerate(tracks, 1):
        if track.get("audio"):
            measured += 1
            continue

        video_id = track.get("id", "")
        # 1. Сначала смотрим кэш: трек могли измерить в прошлый раз
        cached = _CACHE.get(video_id)
        if cached:
            track["audio"] = cached
            measured += 1
            continue

        # 2. Не получилось - качаем и меряем заново
        features = audio_analyzer.analyze(
            video_id, int(track.get("duration_sec") or 0))
        if features:
            track["audio"] = features
            _CACHE[video_id] = features
            measured += 1
        if on_progress and (i % 5 == 0 or i == total):
            on_progress(i, total, measured)

    _save_cache()
    return tracks


def classify_all(
    tracks: List[Dict[str, Any]],
    on_progress=None,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Раздаёт треки по четырём категориям.

    Здесь нет нейросети и нет выравнивания. Каждый трек попадает туда,
    куда его ведут измерения. Если треков для боя больше, чем для фона,
    значит так и должно быть: у тебя энергичная библиотека.

    Возвращает словарь категория -> список треков.
    """
    if on_progress:
        on_progress(0, len(tracks), 0)

    print("🔊 [Vibe] Измеряю энергию треков по звуку...")
    enrich_with_audio(tracks, on_progress)

    result: Dict[str, List[Dict[str, Any]]] = {
        STATE_CALM: [],
        STATE_COMBAT: [],
        STATE_DEATH: [],
        STATE_VICTORY: [],
    }

    no_audio = 0
    unknown = 0
    for track in tracks:
        features = track.get("audio")
        if not features:
            no_audio += 1
        state, reason = classify_track(track.get("title", ""), features)
        if state is None:
            # Ни звука, ни слова в названии. Доказательств, что это
            # музыка, нет вообще - в матрицу такой трек не идёт.
            unknown += 1
            print(f"  🚫 без доказательств, не взяли: "
                  f"{track.get('title', '')[:48]}")
            continue
        track["vibe_reason"] = reason
        result[state].append(track)

    measured = len(tracks) - no_audio
    print(f"🔊 [Vibe] Звук измерен у {measured} из {len(tracks)} треков")
    if no_audio:
        print(f"   ⚠️ {no_audio} не удалось измерить, решали по названию")
    if unknown:
        print(f"   🚫 {unknown} треков без доказательств, что это музыка, "
              f"в матрицу не попали")

    for state, items in result.items():
        print(f"   {state:<9} {len(items):>3}")

    return result
