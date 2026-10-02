"""
Тесты фильтра треков — жёсткая проверка «музыка или не музыка».
"""

from tests.core import suite

S = suite("Фильтр треков — музыка или нет")


def _snippet(title="", channel="", cat=None, duration=None, live="none"):
    """Собирает минимальный ответ YouTube-ролика для judge()."""
    return {
        "snippet": {
            "title": title,
            "channelTitle": channel,
            "categoryId": cat,
            "liveBroadcastContent": live,
        },
        "contentDetails": {
            "duration": duration,
        },
        "duration_sec": None,
    }


# --------------------------------------------------------------------------- #
#  judge()
# --------------------------------------------------------------------------- #


@S.add("категория Music (10) → это трек")
def _t_judge_music():
    from src.ai import track_filter as tf

    ok, reason = tf.judge(_snippet(title="Any",
                            channel="Any", cat=10))
    assert ok is True, reason
    assert "Music" in reason


@S.add("канал -Topic → это трек")
def _t_judge_topic():
    from src.ai import track_filter as tf

    ok, reason = tf.judge(_snippet(title="Any",
                            channel="Artist - Topic"))
    assert ok is True, reason
    assert "Topic" in reason


@S.add("слишком короткое → не трек")
def _t_judge_short():
    from src.ai import track_filter as tf

    ok, reason = tf.judge(_snippet(title="Any",
                            duration="PT30S"))
    assert ok is False, "короткое должно отбрасываться"
    assert "шортс" in reason.lower() or "коротк" in reason, reason


@S.add("слишком длинное → не трек")
def _t_judge_long():
    from src.ai import track_filter as tf

    ok, reason = tf.judge(_snippet(title="Any",
                            duration="PT1H30M"))
    assert ok is False
    assert "длинное" in reason, reason


@S.add("прямая трансляция → не трек")
def _t_judge_live():
    from src.ai import track_filter as tf

    ok, reason = tf.judge(_snippet(title="Any",
                            live="live"))
    assert ok is False, reason
    assert "трансляци" in reason, reason


@S.add("мусорное название → не трек")
def _t_judge_junk():
    from src.ai import track_filter as tf

    ok, reason = tf.judge(_snippet(title="МЕМ ПРО ДОТУ #shorts",
                            channel="Gamer", cat=20))
    assert ok is False, reason
    assert "мусор" in reason.lower() or "шортс" in reason.lower() or "gaming" in reason.lower(), reason


@S.add("сильные маркеры (Official Audio) → трек даже в серой категории")
def _t_judge_markers():
    from src.ai import track_filter as tf

    ok, reason = tf.judge(_snippet(title="Artist - Song (Official Audio)",
                            channel="Indie Channel", cat=24))
    assert ok is True, reason
    assert "признаки" in reason, reason


@S.add("серая категория без маркеров → не трек")
def _t_judge_gray_no_markers():
    from src.ai import track_filter as tf

    ok, reason = tf.judge(_snippet(title="Случайное видео",
                            channel="Unknown", cat=24))
    assert ok is False, reason


@S.add("мёртвая заглушка → не трек")
def _t_judge_dead():
    from src.ai import track_filter as tf

    ok, reason = tf.judge(_snippet(title="private video"))
    assert ok is False, reason


# --------------------------------------------------------------------------- #
#  looks_like_music
# --------------------------------------------------------------------------- #


@S.add("looks_like_music: Music → True")
def _t_looks_music():
    from src.ai import track_filter as tf

    assert tf.looks_like_music("Any", "", 10) is True


@S.add("looks_like_music: -Topic канал (в artist) → True")
def _t_looks_topic():
    from src.ai import track_filter as tf

    assert tf.looks_like_music("Title", "Artist - Topic") is True


@S.add("looks_like_music: мусорный текст → False")
def _t_looks_junk():
    from src.ai import track_filter as tf

    assert tf.looks_like_music("мем про доту", "") is False


@S.add("looks_like_music: есть сильный маркер → True")
def _t_looks_marker():
    from src.ai import track_filter as tf

    assert tf.looks_like_music("lo-fi beat", "") is True


@S.add("looks_like_music: ничего → False")
def _t_looks_nothing():
    from src.ai import track_filter as tf

    assert tf.looks_like_music("Как собрать пк", "") is False


# --------------------------------------------------------------------------- #
#  music_score
# --------------------------------------------------------------------------- #


@S.add("music_score: official video + remix → 4")
def _t_score():
    from src.ai import track_filter as tf

    assert tf.music_score("Official Audio Remix") >= 4


@S.add("music_score: без маркеров → 0")
def _t_score_zero():
    from src.ai import track_filter as tf

    assert tf.music_score("Обычный ролик") == 0


# --------------------------------------------------------------------------- #
#  Duration ISO
# --------------------------------------------------------------------------- #


@S.add("разбор длительности из ISO → секунды")
def _t_duration_iso():
    from src.ai.track_filter import _duration_of

    item = _snippet(duration="PT3M34S")
    assert _duration_of(item) == 214, f"PT3M34S → {_duration_of(item)}"

    item = _snippet(duration="PT1H2M5S")
    assert _duration_of(item) == 3725

    item = _snippet(duration="PT0S")
    assert _duration_of(item) == 0


# --------------------------------------------------------------------------- #
#  Вспомогательное
# --------------------------------------------------------------------------- #


@S.add("is_junk_text доопределяет дефолтный garbage")
def _t_junk_text():
    from src.ai import track_filter as tf

    assert tf.is_junk_text("shorts", "Gaming Channel") is True, "shorts — junk"
    assert tf.is_junk_text("Best Song Ever", "Music") is False


@S.add("is_topic_channel распознаёт каналы YouTube Music")
def _t_topic_channel():
    from src.ai import track_filter as tf

    assert tf.is_topic_channel("Artist - Topic") is True
    assert tf.is_topic_channel("Music Channel") is False