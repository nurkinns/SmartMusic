"""
octave_tuning.py — подстройка октавы под вкус конкретного человека.

Зачем это нужно
---------------
Порог октавы в audio_analyzer пришлось подбирать вручную по 12 трекам
одной библиотеки. Программа считает, что трек насчитан вдвое быстрее,
чем он есть, если его половинный темп «похож» на настоящий. Где проходит
эта граница похожести - вкусовое решение, а не математическое, и
универсального значения не существует. У одного человека правильный
порог 2.5, у другого с библиотекой хардстайла и днб тот же порог
порежет настоящие быстрые треки пополам.

Две кнопки решают это без всякой магии: человек слушает музыку и говорит,
как она ощущается. Порог ползёт в его сторону, пока ошибок не останется.

Почему шаг уменьшается
----------------------
Если бы шаг был всегда одинаковый, порог уехал бы в ноль или в
бесконечность, и рано или поздно всё сломалось бы. Поэтому каждый
следующий шаг меньше предыдущего:

    1-е нажатие: -50%   порога
    2-е нажатие: -35%   порога   (50 * 0.7)
    3-е нажатие: -24.5% порога   (50 * 0.7^2)
    4-е нажатие: -17.2% порога   (50 * 0.7^3)

Так порог мягко подходит к нужному значению и останавливается рядом с ним,
а не пролетает мимо. Сумма всех шагов сходится к 166% от текущего порога,
поэтому порог стремится к нулю, а не уходит в минус.

Почему шаг умножается на текущий порог, а не вычитается из него
-----------------------------------------------------------------
При вычитании «50% порога» от 2.5 это 1.25, от 1.25 - 0.62, дальше
девятый шаг уже ушёл бы в минус и порог перестал бы иметь смысл.
Умножение на (1 - шаг) даёт сходящуюся последовательность:
2.5 -> 1.25 -> 0.81 -> 0.61 -> 0.51 ... и всегда остаётся положительным.
"""

import json
import sys
from pathlib import Path
from typing import Dict

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.ai import audio_analyzer

# Файл с настройками. Не в git: это личная настройка конкретного человека.
TUNING_PATH = ROOT_DIR / "user_octave_tuning.json"

# Первый шаг - половина порога. Дальше каждый шаг в 0.7 раза меньше.
STEP_START_PERCENT = 50.0
STEP_DECAY = 0.7

# Ниже этого порог опускаться не будет: слишком маленькое значение
# означало бы «делить пополам вообще всё», и быстрые треки
# превратились бы в чил-биты.
MIN_THRESHOLD = 0.01

# Выше этого - симметрично. Больше уже не про октаву, а про мусор в
# расчётах: там разумнее чинить код, чем крутить ручку.
MAX_THRESHOLD = 1000.0

# «Слишком быстро» и «слишком медленно» - это про то, как трек ЗВУЧИТ
# на слух, а не про то, что там написано в названии.
DIRECTION_FASTER = -1     # слышим быстрее, чем есть -> надо делить сильнее
DIRECTION_SLOWER = 1      # слышим медленнее, чем есть -> делим слишком сильно


def _default() -> Dict[str, float]:
    return {
        "threshold": audio_analyzer.OCTAVE_RATIO,
        "base": audio_analyzer.OCTAVE_RATIO,
        "presses": 0,
    }


def load() -> Dict[str, float]:
    """Читает настройку. Любая ошибка - просто настройки по умолчанию."""
    data = _default()
    try:
        if TUNING_PATH.exists():
            stored = json.loads(TUNING_PATH.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                for key in data:
                    if key in stored:
                        data[key] = stored[key]
    except Exception:
        pass
    return data


def save(data: Dict[str, float]) -> None:
    try:
        TUNING_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"⚠️ [Octave] Не удалось сохранить настройку: {e}")


def current_threshold() -> float:
    """Нынешний порог для audio_analyzer."""
    return float(load()["threshold"])


def next_step_percent(presses: int) -> float:
    """
    На сколько процентов изменится порог при следующем нажатии.

    presses - сколько нажатий уже было. Первое нажатие (presses=0)
    даёт шаг 50%, второе (presses=1) - 35%, и так далее.
    """
    return STEP_START_PERCENT * (STEP_DECAY ** max(0, presses))


def adjust(direction: int) -> Dict[str, float]:
    """
    Сдвигает порог на один шаг в указанную сторону.

    direction - DIRECTION_FASTER или DIRECTION_SLOWER.

    Возвращает состояние после сдвига, чтобы вызывающий показал
    пользователю, что именно произошло.
    """
    data = load()
    step = next_step_percent(int(data["presses"]))
    data["threshold"] = _clamp(
        float(data["threshold"]) * (1.0 + direction * step / 100.0))
    data["presses"] = int(data["presses"]) + 1
    save(data)
    return data


def reset() -> Dict[str, float]:
    """Возвращает порог к значению, зашитому в коде."""
    data = _default()
    save(data)
    return data


def _clamp(value: float) -> float:
    return max(MIN_THRESHOLD, min(MAX_THRESHOLD, value))


def describe() -> str:
    """Человекочитаемая сводка для интерфейса."""
    data = load()
    presses = int(data["presses"])
    step = next_step_percent(presses)
    return (f"порог октавы {float(data['threshold']):.2f} "
            f"(нажатий: {presses}, след. шаг {step:.1f}%)")
