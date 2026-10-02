"""
Тесты подстройки октавы — математика кнопок.
"""

import tempfile
import os
from pathlib import Path

from tests.core import suite

S = suite("Октава — подстройка порога")


def _tmp_path(name="octave_test.json"):
    return Path(tempfile.gettempdir()) / name


@S.add("next_step_percent: первое нажатие 50%, второе 35%, третье 24.5%")
def _t_step():
    from src.ai import octave_tuning as ot

    assert abs(ot.next_step_percent(0) - 50.0) < 1e-6
    assert abs(ot.next_step_percent(1) - 35.0) < 1e-6
    assert abs(ot.next_step_percent(2) - 24.5) < 1e-6


@S.add("_clamp не даёт порогу уйти в ноль или бесконечность")
def _t_clamp():
    from src.ai import octave_tuning as ot

    assert ot._clamp(-1.0) == ot.MIN_THRESHOLD
    assert ot._clamp(0.0) == ot.MIN_THRESHOLD
    assert ot._clamp(9999.0) == ot.MAX_THRESHOLD
    assert ot._clamp(2.5) == 2.5


@S.add("adjust в сторону быстрее уменьшает порог и увеличивает press-счётчик")
def _t_adjust_faster():
    from src.ai import octave_tuning as ot
    from src.ai import audio_analyzer as aa

    old_path = ot.TUNING_PATH
    tmp = _tmp_path("adjust_faster.json")
    try:
        ot.TUNING_PATH = tmp
        if tmp.exists():
            tmp.unlink()

        data = ot.adjust(ot.DIRECTION_FASTER)
        assert data["presses"] == 1, f"presses: {data['presses']}"
        ratio = aa.OCTAVE_RATIO
        assert data["threshold"] < ratio, f"{data['threshold']} >= {ratio}"
    finally:
        ot.TUNING_PATH = old_path
        if tmp.exists():
            tmp.unlink()


@S.add("adjust в сторону медленнее увеличивает порог")
def _t_adjust_slower():
    from src.ai import octave_tuning as ot
    from src.ai import audio_analyzer as aa

    old_path = ot.TUNING_PATH
    tmp = _tmp_path("adjust_slower.json")
    try:
        ot.TUNING_PATH = tmp
        if tmp.exists():
            tmp.unlink()

        data = ot.adjust(ot.DIRECTION_SLOWER)
        assert data["presses"] == 1, f"presses: {data['presses']}"
        ratio = aa.OCTAVE_RATIO
        assert data["threshold"] > ratio, f"{data['threshold']} <= {ratio}"
    finally:
        ot.TUNING_PATH = old_path
        if tmp.exists():
            tmp.unlink()


@S.add("save/load roundtrip на временном файле")
def _t_save_load():
    from src.ai import octave_tuning as ot

    old_path = ot.TUNING_PATH
    tmp = _tmp_path("octave_roundtrip.json")
    try:
        ot.TUNING_PATH = tmp
        if tmp.exists():
            tmp.unlink()

        ot.save({"threshold": 1.0, "base": 2.5, "presses": 3})
        data = ot.load()
        assert data["threshold"] == 1.0, f"порог: {data['threshold']}"
        assert data["presses"] == 3, f"presses: {data['presses']}"
    finally:
        ot.TUNING_PATH = old_path
        if tmp.exists():
            tmp.unlink()


@S.add("reset возвращает порог к значению по умолчанию")
def _t_reset():
    from src.ai import octave_tuning as ot
    from src.ai import audio_analyzer as aa

    old_path = ot.TUNING_PATH
    tmp = _tmp_path("octave_reset.json")
    try:
        ot.TUNING_PATH = tmp
        if tmp.exists():
            tmp.unlink()

        ot.adjust(ot.DIRECTION_FASTER)
        data = ot.reset()
        ratio = aa.OCTAVE_RATIO
        assert abs(data["threshold"] - ratio) < 1e-6, (
            f"{data['threshold']} != {ratio}")
        assert data["presses"] == 0, f"presses: {data['presses']}"
    finally:
        ot.TUNING_PATH = old_path
        if tmp.exists():
            tmp.unlink()


@S.add("describe возвращает строку с текущим порогом")
def _t_describe():
    from src.ai import octave_tuning as ot

    desc = ot.describe()
    assert "порог" in desc, f"нет слова «порог»: {desc[:60]}"
    assert "нажатий" in desc, f"нет «нажатий»: {desc[:60]}"