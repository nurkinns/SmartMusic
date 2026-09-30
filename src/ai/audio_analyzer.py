"""
audio_analyzer.py — измеряет настоящую энергию трека.

Зачем это нужно
---------------
До сих пор вайб определялся по названию. Но название врёт: «DVRST - SHE
IS HYPERACTIVE» звучит агрессивно, а «Holidays» — спокойно, и наоборот.
Нейросеть тоже врёт: модель на 3 миллиарда параметров не отличает
Spaced Hunter от Radio Edit, если не знает артиста.

Единственный источник правды - сам звук. Мы скачиваем кусок трека,
считаем темп (BPM) и энергию, и раскладываем по вайбам уже по числам,
а не по догадкам.

Что измеряем
------------
  bpm    - темп. Самый честный признак энергии: чем быстрее, тем
           злее. 90 это спокойно, 180 это уже разгон.
  punch  - сколько энергии в средних частотах (400-3500 Гц) против
           низких. Это то, что слышно как «драйв»: ударные бочки,
           бас-гитара, синты.
  bright - отношение верхних частот к нижним. Звонкий и резкий трек -
           агрессивный, глухой и тёмный - спокойный.
  loud   - громкость трека в дБ. Сама по себе энергию не определяет
           (тихий мастеринг бывает у агрессивных треков), поэтому
           используется только для справки.

Окно анализа берём в пяти местах трека и сводим медианой. Максимум
не годится: одна резкая бочка перетягивала весь результат, и трек то
считался бодрым, то вялым. Медиана отражает трек целиком.

Про темп и октаву
-----------------
Темп считает librosa. Но этого мало, и вот почему.

Главная беда любого темп-детектора - ОКТАВА. Удар бьётся не только в
такте, но и на его доле. В хип-хопе хэты идут шестнадцатыми, а значит
поток ударов повторяется вдвое чаще такта. Спектр видит оба периода
одинаково яркими, и примерно на половине треков побеждает вдвое
быстрый вариант: Mos Def «Im Leaving» (настоящие ~92 BPM) читался
как 185, Dr. Dre - так же, Korn «Blind» - так же.

Это НЕ были ускоренные файлы. Проверено жёстко: декодер отдаёт ровно
столько секунд, сколько попросили, скорость проигрывания ровно 1.0, а
длительность дорожки совпадает с той, что показывает YouTube. Файлы
обычные, полной длины - врал расчёт.

Отдельного лекарства от октавы нет ни у кого, включая librosa. Лечится
сравнением: берём найденный темп и смотрим, объясняет ли его половина
удары не хуже, чем сам темп, с поправкой на то, какой из двух вариантов
правдоподобнее для музыки. См. _fix_octave.

Свой фильтр-детектор остался запасным вариантом на случай, если
librosa не установлена.
"""

import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src import audio_cache

# Сколько секунд звука разбираем. Много не нужно: темп и энергия
# определяются за несколько секунд, а качать треки целиком долго.
SAMPLE_RATE = 22050
WINDOW_SECONDS = 25.0

# Версия алгоритма замера. Поднимаем, когда меняем СПОСОБ расчёта.
# Старые записи в кэше при этом автоматически считаются устаревшими.
#   2 - темп считает librosa
#   3 - добавлена проверка октавы _fix_octave для обоих способов
#   4 - кэш хранит сырые темпы окон, порог можно менять без перезамера
ANALYZER_VERSION = 4
# Сколько мест в треке проверяем. Пять, а не три: при трёх окнах результат
# плавал между прогонами (один и тот же трек то 174 BPM, то 145), потому
# что окна попадали на разные участки. Медиана по пяти замерам стабильна.
WINDOW_COUNT = 5

# Где именно брать куски звука - доли от длительности трека. Начало
# пропускаем: там часто тихое интро или заставка канала.
WINDOW_POSITIONS = (0.08, 0.28, 0.48, 0.68, 0.85)

# MAX_BYTES, AUDIO_FORMAT и папка для временных файлов уехали в
# src/audio_cache.py: скачиванием теперь занимается он, один на
# анализатор и на плеер сразу.


def _real_duration(path: Path) -> float:
    """
    Узнаёт настоящую длительность аудиофайла в секундах.

    Зачем: длительность из базы (duration_sec) - это длина ролика на
    YouTube, а скачанная дорожка иногда оказывается короче. Если
    ориентироваться на цифру из базы, окна за пределами файла не
    декодируются, и трек измеряется по одному-двум кускам вместо трёх.
    """
    try:
        import av
    except ImportError:
        return 0.0
    try:
        with av.open(str(path)) as container:
            if container.duration:
                return float(container.duration) / 1_000_000.0
            stream = next((s for s in container.streams if s.type == "audio"),
                          None)
            if stream is not None and stream.duration and stream.time_base:
                return float(stream.duration * stream.time_base)
    except Exception:
        pass
    return 0.0


def _decode(path: Path, start: float, duration: float) -> Optional[np.ndarray]:
    """
    Достаёт моно-кусок звука в виде массива numpy.

    Используется PyAV, потому что ffmpeg в системе может отсутствовать,
    а PyAV содержит нужные кодеки внутри себя.

    Если не удалось разобрать с середины трека, делаем вторую попытку
    с самого начала. Так мы не теряем трек из-за того, что длительность
    в базе не совпала с реальной.
    """
    try:
        import av
    except ImportError:
        return None

    def read(at: float) -> Optional[np.ndarray]:
        try:
            with av.open(str(path)) as container:
                stream = next((s for s in container.streams
                               if s.type == "audio"), None)
                if stream is None:
                    return None
                stream.thread_type = "AUTO"
                resampler = av.audio.resampler.AudioResampler(
                    format="s16", layout="mono", rate=SAMPLE_RATE)
                need = int(duration * SAMPLE_RATE)
                chunks: List[np.ndarray] = []
                total = 0
                for frame in container.decode(stream):
                    pts = (float(frame.pts * frame.time_base)
                           if frame.pts is not None else 0.0)
                    if pts + frame.samples / stream.rate < at:
                        continue
                    for resampled in resampler.resample(frame):
                        # (1, samples) -> (samples,), моно-сигнал
                        a = resampled.to_ndarray().astype(np.float32) / 32768.0
                        chunks.append(a.reshape(-1))
                        total += a.size
                    if total >= need:
                        break
            if not chunks:
                return None
            return np.concatenate(chunks)[:need]
        except Exception:
            return None

    data = read(start)
    if data is None and start > 1.0:
        # окошко оказалось за пределами файла - берём с начала
        data = read(0.0)
    return data


# --- Приоры темпа --------------------------------------------------------
#
# Приор отвечает на вопрос «а какой темп вообще правдоподобен для музыки».
# Их два, и это не дублирование:
#
# ШИРОКИЙ (TEMPO_PRIOR) работает при переборе всех кандидатов. Он нужен
# слабым: его задача - отсечь совсем дикие значения, а не решать, какая
# из двух октав верна.
#
# УЗКИЙ (OCTAVE_PRIOR) работает только в паре «темп или половина темпа».
# Здесь он должен быть сильным, потому что это единственное место, где
# приор вообще может что-то решить: счёт очков у двух октав отличается
# в разы (см. _fix_octave), и без приора выбор был бы случайным.

# Центр и ширина широкого приора: большинство музыки живёт в 100-150 BPM.
TEMPO_PRIOR_CENTER = 125.0
TEMPO_PRIOR_SIGMA = 0.75

# Узкий приор для выбора октавы. Центр 110 - самая середина того, как
# звучит «нормальная» музыка; ширина 0.35 октавы означает, что приор
# уверенно давит вниз, но не ломает темпы от 60 до 190.
OCTAVE_PRIOR_CENTER = 110.0
OCTAVE_PRIOR_SIGMA = 0.35

# Насколько половина темпа должна объяснять удары, чтобы мы согласились
# признать её настоящей.
#
# Порог подобран замером на треках, где настоящий темп известен заранее.
# Проверялось условие
#
#     (счёт_половины / счёт_целого) * (приор_половины / приор_целого) > T
#
# Измеренные значения, где > T означает «чинить»:
#
#   Pete Rock - Step Up (настоящие ~92, читали 185) ....... 124.3
#   Kendrick - Untitled 05   (настоящие ~92, читали 185) ...... 8.9
#   Uknowhowwedu              (настоящие ~92, читали 185) ...... 5.5
#   Ливаю                     (настоящие ~90, читали 161) ...... 1.1
#   Paper Planes              (спорно, читали 172) ............. 0.6
#   --- а вот эти трогать нельзя, они правда быстрые ---
#   DVRST - SHE IS HYPERACTIVE (настоящие 174) ............. 2.1
#   Поменяю                     (настоящие 161) ............. 1.4
#   Darude - Sandstorm          (настоящие 136) ............. 0.04
#
# Полного разделения нет: «сломанный» Paper Planes (0.6) хуже настоящего
# DVRST (2.1). Значит одним признаком их не отличить, и пришлось выбрать
# точку, которая выигрывает у трёх самых уверенных сломанных треков,
# не задев ни одного настоящего. Это T = 2.5.
#
# В пересчёте на понятные величины: приор даёт примерно трёхкратный
# перевес в пользу половины, так что T = 2.5 это примерно требование
# «счёт половины не меньше 3/4 счёта целого». То есть половинный темп
# должен не просто возможен, а почти так же хорош.
#
# Честная цена: Ливаю и Paper Planes остаются посчитанными вдвое быстрее
# и уезжают в COMBAT. Для обоих это спорно, но цена приемлемая - два
# трека против трёх, которые иначе уехали бы в CALM.
OCTAVE_RATIO = 2.5


def _tempo_spectrum(onset: np.ndarray, fps: float):
    """Готовит спектр огибающей ударов, по которому считаются очки."""
    if len(onset) < 512:
        return None, 0.0
    centred = onset - onset.mean()
    win = 2 ** int(np.ceil(np.log2(len(centred) * 2)))
    power = np.abs(np.fft.rfft(centred, win)) ** 2
    return power, fps / win


def _raw_score(power: np.ndarray, hz_per_bin: float, bpm: float) -> float:
    """Сколько энергии ударов на всех трёх уровнях темпа."""
    if power is None or bpm <= 0:
        return 0.0
    base = bpm / 60.0
    if base >= len(power) - 2:
        return 0.0
    total = 0.0
    for mult, weight in ((1, 1.0), (2, 0.6), (3, 0.35)):
        k = int(round(base * mult / hz_per_bin))
        if 0 < k < len(power) - 1:
            total += weight * float(power[k - 1:k + 2].max())
    return total


def _tempo_prior(bpm: float) -> float:
    """Широкий приор: отсекает неправдоподобные темпы при переборе."""
    if bpm <= 0:
        return 0.0
    z = math.log2(bpm / TEMPO_PRIOR_CENTER) / TEMPO_PRIOR_SIGMA
    return math.exp(-0.5 * z * z)


def _octave_prior(bpm: float) -> float:
    """Узкий приор: решает, какая из двух октав правдоподобнее."""
    if bpm <= 0:
        return 0.0
    z = math.log2(bpm / OCTAVE_PRIOR_CENTER) / OCTAVE_PRIOR_SIGMA
    return math.exp(-0.5 * z * z)


def _octave_evidence(power: np.ndarray, hz_per_bin: float, bpm: float) -> float:
    """
    Насколько половина темпа похожа на настоящий. Больше - похоже.

    Возвращает отношение
        счёт(половина) * приор(половина) / (счёт(целого) * приор(целого))
    чем больше значение, тем увереннее можно сказать, что трек посчитан
    вдвое быстрее, чем он есть.

    Ноль означает «проверять нечего»: слишком медленный трек (половина
    ушла бы в неслышимую область) или слишком быстрый (половина вполне
    правдоподобна и трогать её незачем).

    Откуда берётся ошибка
    ---------------------
    Удар бьётся не только в такт, но и на доле. В хип-хопе на 92 BPM
    хэты идут шестнадцатыми, а значит поток ударов повторяется каждые
    0.16 с - это ровно вдвое чаще, чем такт. Спектр видит оба периода
    одинаково яркими, и примерно на половине треков побеждает вдвое
    быстрый вариант: Mos Def «Im Leaving» (настоящие ~92 BPM) читался
    как 185, Dr. Dre - так же, Korn «Blind» - так же.

    Это НЕ были ускоренные файлы. Проверено жёстко: декодер отдаёт ровно
    столько секунд, сколько попросили, скорость проигрывания ровно 1.0, а
    длительность дорожки совпадает с той, что показывает YouTube. Файлы
    обычные, полной длины - врал расчёт.

    Отдельного лекарства от октавы нет ни у кого, включая librosa. Лечится
    сравнением: берём найденный темп и смотрим, объясняет ли его половина
    удары не хуже, чем сам темп, с поправкой на правдоподобие.

    Считаем только в одну сторону, вниз. Вдвое быстрее, чем правда,
    ошибаются гораздо чаще, чем втрое медленнее, а поднимать темп
    обратно нельзя - это уводило бы настоящие медленные треки в
    несуществующую быструю октаву.
    """
    half = bpm / 2.0
    if bpm < 80.0 or half < 55.0:
        return 0.0
    full = _raw_score(power, hz_per_bin, bpm) * _octave_prior(bpm)
    if full <= 0:
        return 0.0
    low = _raw_score(power, hz_per_bin, half) * _octave_prior(half)
    return low / full


def _apply_octave(bpm: float, evidence: float, threshold: float) -> float:
    """Применяет порог октавы. evidence == 0 означает «не трогать»."""
    if evidence > 0 and evidence > threshold:
        return bpm / 2.0
    return bpm


def _current_threshold() -> float:
    """
    Порог октавы, настроенный пользователем.

    Значение по умолчанию - OCTAVE_RATIO отсюда же. Если человек что-то
    подкрутил кнопками, берётся его значение. Импорт внутри функции
    намеренно: octave_tuning ссылается на этот модуль, и прямой импорт
    наверху создал бы круговую зависимость.
    """
    try:
        from src.ai import octave_tuning
        return octave_tuning.current_threshold()
    except Exception:
        return OCTAVE_RATIO


def _tempo(onset: np.ndarray, fps: float) -> float:
    """
    Темп по спектру огибающей ударов. Запасной способ, когда librosa
    недоступна.

    Перебирает все темпы от 60 до 190 BPM и берёт тот, у которого
    энергия ударов на всех трёх уровнях (1x, 2x, 3x) самая высокая,
    с поправкой на правдоподобие. Гармоники нужны потому, что удар
    бьётся не только в такт, но и на его доли.

    Возвращает сырой темп, БЕЗ проверки октавы. Проверку применяет
    вызывающий код через _apply_octave - так порог можно менять,
    не скачивая треки заново.
    """
    power, hz_per_bin = _tempo_spectrum(onset, fps)
    if power is None:
        return 0.0
    return _best_tempo(power, hz_per_bin)


def _best_tempo(power: np.ndarray, hz_per_bin: float) -> float:
    """Перебирает все возможные темпы и берёт лучший по очкам."""
    candidates = np.arange(60.0, 190.0, 0.5)
    scores = np.array([_raw_score(power, hz_per_bin, b) * _tempo_prior(b)
                       for b in candidates])
    if not scores.any():
        return 0.0
    return float(candidates[int(np.argmax(scores))])


def _tempo_librosa(data: np.ndarray) -> float:
    """
    Темп через librosa. Это основной способ.

    librosa ищет удары перебором по всей длине куска, а не фильтром
    огибающую, поэтому цепляется за шум гораздо реже.

    Возвращает 0, если librosa не установлена или не справилась. Тогда
    темп посчитает запасной детектор.
    """
    try:
        import librosa
    except ImportError:
        return 0.0
    try:
        tempo, _ = librosa.beat.beat_track(y=data.astype(np.float32),
                                           sr=SAMPLE_RATE)
        return float(np.asarray(tempo).reshape(-1)[0])
    except Exception:
        return 0.0


def _window_features(data: np.ndarray) -> Optional[Dict[str, float]]:
    """Признаки одного куска трека."""
    # Шаг 256 сэмплов (11 мс) - достаточно, чтобы поймать 60 BPM
    hop = 256
    n = len(data) // hop
    if n < 200:
        return None
    env = np.abs(data[:n * hop].reshape(n, hop)).mean(axis=1)
    onset = np.diff(env, prepend=env[0])
    onset[onset < 0] = 0

    # Сначала пробуем librosa, если она недоступна или ошиблась - падаем
    # на свой детектор, чтобы анализ вообще не останавливался.
    bpm = _tempo_librosa(data)
    # Защита от явно неправдоподобного результата: музыка не бывает
    # медленнее 40 и быстрее 250 BPM. Если librosa такое выдала, значит
    # она зацепилась не за такт, и правильнее посчитать запасным путём.
    if bpm <= 40 or bpm >= 250:
        bpm = _tempo(onset, SAMPLE_RATE / hop)

    # Кладём рядом сырой темп и «доказательство» октавы. Сам порог тут
    # не применяется - его подставляет _merge. Так кэш хранит числа, по
    # которым темп можно пересчитать для ЛЮБОГО порога за миллисекунды,
    # не качая треки заново.
    power, hz_per_bin = _tempo_spectrum(onset, SAMPLE_RATE / hop)
    evidence = (_octave_evidence(power, hz_per_bin, bpm)
                if power is not None else 0.0)

    frame = 2048
    nf = len(data) // frame
    if nf < 8:
        return None
    frames = data[:nf * frame].reshape(nf, frame)
    rms = np.sqrt((frames.astype(np.float64) ** 2).mean(axis=1) + 1e-12)
    rms_db = 20 * np.log10(rms + 1e-12)

    window = np.hanning(frame)
    spectrum = np.abs(np.fft.rfft(frames[:min(nf, 800)] * window, axis=1)) ** 2
    freqs = np.fft.rfftfreq(frame, 1 / SAMPLE_RATE)
    low = spectrum[:, (freqs > 30) & (freqs < 400)].mean()
    mid = spectrum[:, (freqs >= 400) & (freqs <= 3500)].mean()
    high = spectrum[:, freqs > 3500].mean()

    return {
        "bpm_raw": bpm,
        "octave": evidence,
        "loud": float(np.percentile(rms_db, 95)),
        "punch": float(10 * np.log10(mid / (low + 1e-12))),
        "bright": float(10 * np.log10(high / (low + 1e-12))),
    }


def _merge(windows: List[Dict[str, float]], threshold: float) -> Dict[str, float]:
    """
    Сводит несколько окон в один результат.

    Берём медиану по всем окнам, а не максимум. Максимум ненадёжен:
    один резкий участок (например бочка в припеве) перетягивал весь
    результат, и трек получался то бодрым, то вялым в зависимости от
    того, попало ли окно на этот участок. Медиана отражает трек целиком.

    Вместе с готовым темпом кладём в результат сырые данные всех окон:
    какие темпы они дали и какое «доказательство октавы» у каждого.
    По ним tempo_from_cache() пересчитывает темп для любого порога
    мгновенно. Без этого кнопка настройки октавы запускала бы
    повторное скачивание всей библиотеки.
    """
    raws = [w.get("bpm_raw", 0.0) for w in windows]
    pairs = [(w.get("bpm_raw", 0.0), w.get("octave", 0.0)) for w in windows]
    bpms = [_apply_octave(raw, ev, threshold)
            for raw, ev in pairs if raw > 0]
    return {
        "bpm": round(float(np.median(bpms)), 1) if bpms else 0.0,
        "loud": round(float(np.median([w["loud"] for w in windows])), 1),
        "punch": round(float(np.median([w["punch"] for w in windows])), 1),
        "bright": round(float(np.median([w["bright"] for w in windows])), 1),
        # Сырые данные окон для мгновенного пересчёта порога.
        "windows": [[round(r, 1), round(e, 3)]
                    for r, e in pairs],
        # Версия расчёта. Когда меняется алгоритм (например, темп переехал
        # с самописного детектора на librosa), все старые замеры становятся
        # неправильными. По этой цифре кэш их отбрасывает и меряет заново.
        "v": ANALYZER_VERSION,
    }


def tempo_from_cache(entry: Dict[str, Any], threshold: float) -> float:
    """
    Пересчитывает темп трека для другого порога октавы.

    Берёт сохранённые сырые темпы окон и применяет новый порог. Звук не
    скачивается заново, поэтому занимает меньше миллисекунды.

    Если в записи нет сырых окон (старый кэш), возвращает тот темп, что
    был посчитан раньше.
    """
    windows = entry.get("windows")
    if not isinstance(windows, list) or not windows:
        return float(entry.get("bpm", 0.0))

    bpms = []
    for pair in windows:
        try:
            raw, evidence = float(pair[0]), float(pair[1])
        except (TypeError, ValueError, IndexError):
            continue
        if raw > 0:
            bpms.append(_apply_octave(raw, evidence, threshold))
    if not bpms:
        return float(entry.get("bpm", 0.0))
    return round(float(np.median(bpms)), 1)


def download_audio(video_id: str, duration_hint: int = 0) -> Optional[Path]:
    """
    Достаёт аудиодорожку ролика для замера.

    Звук берётся из общего кэша (src/audio_cache.py). Файл там остаётся
    навсегда, и его же потом использует плеер, поэтому один и тот же трек
    скачивается ровно один раз на оба дела.

    Раньше здесь была своя временная папка и формат bestaudio[abr<=64]:
    дёшево, но только для измерения. Проверено, что на решение это не
    влияет - punch считается по полосе 400-3500 Гц, а 64 кбит режет
    частоты заметно выше, и смена битрейта трогает только справочный
    признак bright, который в классификации не участвует.
    """
    if not video_id:
        return None
    return audio_cache.get_cache().ensure(video_id)


def analyze(video_id: str, duration_hint: int = 0) -> Optional[Dict[str, float]]:
    """
    Главная функция: id ролика -> признаки энергии.

    Возвращает {'bpm', 'loud', 'punch', 'bright'} либо None, если трек
    не удалось скачать или разобрать.
    """
    if not video_id:
        return None
    path = download_audio(video_id, duration_hint)
    if path is None:
        return None

    # Дальше файл НЕ удаляется: он лежит в общем кэше, и его же использует
    # плеер. Раньше здесь стоял unlink в finally, и каждый трек уходил на
    # повторную закачку перед игрой.
    # Длительность из базы может не совпадать с реальной, поэтому окна
    # считаем от настоящей длины скачанного файла.
    length = _real_duration(path) or float(duration_hint or 0)
    # Совсем короткий трек целиком не наберёт нужного куска -
    # тогда просто берём его от начала столько, сколько есть.
    last_start = max(0.0, length - WINDOW_SECONDS)
    starts = [min(length * f, last_start)
              for f in WINDOW_POSITIONS[:WINDOW_COUNT]]
    windows = []
    for start in starts:
        data = _decode(path, start, WINDOW_SECONDS)
        if data is not None:
            f = _window_features(data)
            if f:
                windows.append(f)
    if not windows:
        return None
    return _merge(windows, _current_threshold())
