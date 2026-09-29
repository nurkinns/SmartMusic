"""
track_filter.py — жёсткий фильтр «трек или не трек».

Зачем он нужен
--------------
Нейросеть (даже хорошая) периодически пропускает в вайб-матрицу не музыку:
ролики из TikTok, стримы, мемы, разборы, геймплей. Причина в том, что у
модели есть только название и канал, а «на вид это похоже на песню».

Мы даём модели больше данных и, главное, НИЖЕ неё ставим жёсткую
проверку по признакам, которые YouTube отдаёт сам и подделать нельзя:

  * categoryId  - категория ролика. У настоящей музыки она 10 (Music).
  * channelTitle - у автоматических музыкальных каналов всегда
                   оканчивается на " - Topic". Это 100% музыка.
  * duration    - настоящий трек живёт 1..6 минут. Короткое = шортс или
                   мем, длинное = стрим или лекция.
  * liveBroadcastContent - прямые трансляции отбрасываем целиком.

Логика построена так, что НЕПОДТВЕРЖДЁННЫЙ ролик отбрасывается.
Лучше пустая категория, чем мем в плейлисте во время драки.
"""

import re
from typing import Any, Dict, Optional, Tuple

# --------------------------------------------------------------------------
# Категории YouTube
# --------------------------------------------------------------------------
# 10 = Music. Единственная категория, где музыка гарантирована.
CATEGORY_MUSIC = 10

# Категории, где трека практически не бывает.
# Ролики из них отбрасываем, если нет явных признаков музыки в названии.
BLOCKED_CATEGORIES = {
    1,    # Film & Animation
    2,    # Autos & Vehicles
    3,    # Comedy (старая)
    15,   # Pets & Animals
    17,   # Sports
    19,   # Travel & Events
    20,   # Gaming - сюда попадают ролики про кс2, раст, доту
    21,   # Videoblogging
    22,   # People & Blogs
    23,   # Comedy
    25,   # News & Politics
    26,   # Howto & Style
    27,   # Education
    28,   # Science & Technology
    29,   # Nonprofits & Activism
    34,   # Comedy
    35,   # Documentary
    42,   # Shorts
    43,   # Shows
    44,   # Trailers
}

# Категории 24 (Entertainment) и 30-41 (кино, сериалы) - серая зона.
# Пропускаем только если в названии есть явные признаки музыки.
GRAY_CATEGORIES = {24, 30, 31, 32, 33, 36, 37, 38, 39, 40, 41}

# --------------------------------------------------------------------------
# Длительность
# --------------------------------------------------------------------------
# Пользователь просил максимально избегать длинных видео.
MIN_DURATION = 60       # короче минуты - шортс, мем, заставка
MAX_DURATION = 360      # дольше 6 минут - стрим, лекция, подборка

# --------------------------------------------------------------------------
# Признаки того, что это НЕ трек
# --------------------------------------------------------------------------
# Проверяются по названию и каналу. Пишем через регулярки, чтобы ловить
# варианты написания (#shorts / #шортс / shorts).
JUNK_PATTERNS = [
    r"#?\s*shorts?\b", r"#?\s*шортс", r"reels?\b",
    r"мем", r"meme", r"реакци[яи]", r"reaction", r"\bvlog\b", r"влог",
    r"подкаст|podcast", r"интервью|interview", r"стрим|livestream|\blive\b",
    r"24\s*/\s*7", r"continuous", r" nonstop", r"radio stream",
    r"обзор", r"разбор\b", r"гайд", r"руководств", r"туториал|tutorial",
    r"челлендж|challenge", r"соревнани[ея]", r"турнир",
    r"топ[\s\-–—]*\d+", r"подборка", r"лучшее за", r"что если|what if",
    r"угадай|отгадай|guess", r"проверь", r"факт[ыа]?",
    r"разоблачен", r"тайна|secret", r"легенд|миф",
    r"до и после|before.*after", r"как я ", r"как мы ", r"сделал", r"повторил",
    r"испытан|survived",
    r"gaming|gameplay|let's play|летсплей|walkthrough|прохождени",
    r"кс2|кс\s*2|cs2|мк\s*1|роблокс|майнкрафт|minecraft|фортнайт|fortnite",
    r"gta|варфейс|валор|valorant|апекс|apex|overwatch",
    r"дота\s*2|dota\s*2|дотерск",
    r"смешарик", r"мультфильм|cartoon|анимация",
    r"смешались",
    r"пранк|prank|розыгрыш",
    r"смешн|funny|прикол",
    r"инструкция|инструктаж|как установить|установка",
    r"сравнил|сравнение",
]

JUNK_RE = [re.compile(p, re.IGNORECASE) for p in JUNK_PATTERNS]

# Технические заглушки, которые YouTube отдаёт вместо удалённых роликов
DEAD_TITLES = {"private video", "deleted video", "video unavailable", ""}

# --------------------------------------------------------------------------
# Признаки того, что это музыка
# --------------------------------------------------------------------------
# Маркеры взвешенные. Сильные доказывают музыку сами по себе
# ("Official Audio" = точно трек). Слабые - только жанр, их одного мало.
#
# Зачем веса: независимые артисты часто ставят категорию "People & Blogs"
# вместо "Music", и тогда спасает только название. Но если в названии
# случайно есть слово "pop", трек не должен проходить.
STRONG_MARKERS = [
    "official music video", "official video", "official audio", "official lyric",
    "official visualizer", "official instrumental", "official hd", "official 4k",
    "lyric video", "lyrics", "audio only", "full album", "visualizer",
    "sped up", "slowed", "reverb", "remix", "mashup", "bootleg",
    "montagem", "type beat", "prod by", "prod.", "instrumental",
    "feat.", "ft.", "discography", "soundtrack", "ost", "m/v",
]

WEAK_MARKERS = [
    "official", "edit", "single", "ep ", "album", "live at", "demo", "acoustic",
    "pop", "rock", "metal", "jazz", "trap", "edm", "house music", "techno",
    "dubstep", "lofi", "lo-fi", "orchestral", "synthwave", "hardstyle",
    "ambient", "dnb", "drumcore", "phonk", "drift", " mv",
]

# Каналы YouTube Music — 100% настоящая музыка
TOPIC_CHANNEL_SUFFIX = "- topic"

# Порог для роликов из сомнительных категорий
MARKER_THRESHOLD = 2


def _norm(text: str) -> str:
    return (text or "").strip().lower()


def is_topic_channel(channel: str) -> bool:
    """Канал автоматически созданный YouTube Music. Это всегда трек."""
    return _norm(channel).endswith(TOPIC_CHANNEL_SUFFIX)


def music_score(title: str) -> int:
    """
    Вес доказательств, что ролик всё-таки про музыку.

    Сильный маркер даёт 2 очка, слабый 1. Нужно набрать MARKER_THRESHOLD.
    """
    t = _norm(title)
    return (2 * sum(1 for m in STRONG_MARKERS if m in t)
            + sum(1 for m in WEAK_MARKERS if m in t))


def is_junk_text(title: str, channel: str = "") -> bool:
    """Есть ли в названии или канале явные признаки не-музыки."""
    blob = f"{title or ''} {channel or ''}"
    return any(rx.search(blob) for rx in JUNK_RE)


def category_of(item: Dict[str, Any]) -> int:
    """Достаёт categoryId из разных мест ответа YouTube."""
    snippet = item.get("snippet", {}) or {}
    raw = snippet.get("categoryId", snippet.get("videoCategoryId"))
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def is_live(item: Dict[str, Any]) -> bool:
    """Прямая трансляция или премьера. Не трек."""
    snippet = item.get("snippet", {}) or {}
    return snippet.get("liveBroadcastContent", "none") != "none"


def _duration_of(item: Dict[str, Any]) -> Optional[int]:
    """
    Достаёт длительность в секундах из ролика YouTube.

    Формат входных данных меняется в зависимости от того, откуда ролик
    пришёл: у liked-видео длительность лежит в contentDetails.duration
    строкой ISO ("PT3M34S"), у элемента плейлиста её нет вообще, а
    посчитанные нами значения мы кладём в duration_sec. Поэтому ищем
    сразу во всех трёх местах и не падаем, если ничего не нашли.
    """
    for value in (item.get("duration_sec"),
                  (item.get("contentDetails") or {}).get("duration_sec")):
        if isinstance(value, (int, float)):
            return int(value)

    iso = (item.get("contentDetails") or {}).get("duration")
    if isinstance(iso, str) and iso:
        import re as _re
        m = _re.match(
            r"PT(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?", iso)
        if m:
            g = m.groupdict()
            return (int(g["h"] or 0) * 3600 + int(g["m"] or 0) * 60
                    + int(g["s"] or 0))
    return None


def judge(item: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Главный вердикт по одному ролику YouTube.

    Возвращает (это_трек, причина). Правила проверяются по порядку,
    срабатывает первое совпадение - так видно, ПОЧЕМУ отбросили.
    """
    snippet = item.get("snippet", {}) or {}
    details = item.get("contentDetails", {}) or {}

    title = snippet.get("title", "")
    channel = snippet.get("channelTitle", "")
    duration = _duration_of(item)

    # 1. Мёртвые заглушки
    if _norm(title) in DEAD_TITLES:
        return False, "пустое/удалённое видео"

    # 2. Длительность: короткое - шортс, длинное - стрим
    if duration is not None:
        if duration < MIN_DURATION:
            return False, f"слишком короткое ({duration}с) - шортс или мем"
        if duration > MAX_DURATION:
            return False, f"слишком длинное ({duration}с) - не трек"

    # 3. Прямые трансляции
    if is_live(item):
        return False, "прямая трансляция"

    # 4. Явный мусор в названии или канале
    if is_junk_text(title, channel):
        return False, "мусорное название/канал"

    # 5. Категория Music - гарантия
    cat = category_of(item)
    if cat == CATEGORY_MUSIC:
        return True, "категория Music"

    # 6. Канал YouTube Music - гарантия
    if is_topic_channel(channel):
        return True, "канал -Topic"

    # 7. Всё остальное. Категория Music и канал -Topic уже разобраны выше.
    #    Здесь любой другой ролик допускается ТОЛЬКО если в названии
    #    достаточно весомых признаков музыки.
    #
    #    Так треки независимых артистов из категории "People & Blogs"
    #    вида "MF DOOM - Gas Drawls (Official Audio)" проходят, а ролики
    #    из той же категории вроде "Можно ли проехать 100 км на велосипеде"
    #    - нет.
    score = music_score(title)
    if score >= MARKER_THRESHOLD:
        return True, f"признаки музыки в названии (вес {score})"

    # 8. Ничего не подтвердило, что это музыка. Отбрасываем.
    where = f"категория {cat}" if cat else "категория неизвестна"
    return False, f"{where}, вес признаков {score} < {MARKER_THRESHOLD}"


def looks_like_music(title: str, artist: str = "",
                     category_id: Any = None) -> bool:
    """
    Проверка для уже собранных треков (без всей простыни ответа YouTube).

    Используется как добивочный фильтр поверх всего, что попало в базу.
    Согласован с judge(): если трек прошёл строгую проверку по категории
    Music или по каналу -Topic, мягкая проверка обязана его пропустить,
    иначе настоящий трек выкинет уже на этапе сохранения.
    """
    try:
        cat = int(category_id)
    except (TypeError, ValueError):
        cat = None
    if cat == CATEGORY_MUSIC or is_topic_channel(artist):
        return True
    if is_junk_text(title, artist):
        return False
    return music_score(title) >= 1
