"""
Тесты окна урона (DamageWindow).

Кольцевой буфер потери HP за последние 10 секунд — именно он решает, драка
это или фарм на данный момент. Часы подменяются, поэтому тесты не ждут
настоящих секунд.
"""

from tests.core import suite
from src.triggers.damage_window import DamageWindow

S = suite("Окно урона — 10 секунд боя")


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


@S.add("первый отсчёт всегда даёт ноль урона")
def _t_first_zero():
    c = _Clock()
    w = DamageWindow(window_seconds=10.0, clock=c)
    d = w.observe(1000.0)
    assert d == 0.0, f"первый отсчёт не может быть уроном: {d}"


@S.add("сумма падений внутри окна считается верно")
def _t_sum_drops():
    c = _Clock()
    w = DamageWindow(window_seconds=10.0, clock=c)
    c.advance(0.01)
    w.observe(1000.0)
    c.advance(0.01)
    w.observe(800.0)  # -200
    c.advance(0.01)
    d = w.observe(700.0)  # -100 → всего 300
    assert abs(d - 300.0) < 1e-9, f"ждали 300, получили {d}"


@S.add("лечение внутри окна не отменяет урон")
def _t_healing_doesnt_cancel():
    c = _Clock()
    w = DamageWindow(window_seconds=10.0, clock=c)
    c.advance(0.01)
    w.observe(1000.0)
    c.advance(0.01)
    w.observe(700.0)  # -300
    c.advance(0.01)
    w.observe(900.0)  # +200 (лечение), урон не считается
    c.advance(0.01)
    d = w.observe(600.0)  # -300 → итог = 600
    assert abs(d - 600.0) < 1e-9, f"ждали 600, получили {d}"


@S.add("лечение ДО боя (первый отсчёт) не считается уроном")
def _t_healing_before_not_damage():
    c = _Clock()
    w = DamageWindow(window_seconds=10.0, clock=c)
    c.advance(0.01)
    d = w.observe(500.0)  # первый → 0
    assert d == 0.0
    c.advance(0.01)
    d = w.observe(800.0)  # реген, не падение → 0
    assert d == 0.0


@S.add("один отсчёт вне границы сохраняется как предшественник (урон из него тоже считается)")
def _t_one_older_kept():
    c = _Clock()
    w = DamageWindow(window_seconds=10.0, clock=c)
    c.advance(0.01)
    w.observe(1000.0)
    c.advance(9.0)  # 9.01 — ещё в окне
    w.observe(700.0)  # -300
    c.advance(1.1)  # > 10.0 — за окном, но предшественник нужен
    w.observe(700.0)  # 0, старый отсчёт вылетел, но предшественник остался
    # теперь снова урон — он будет считаться правильно.
    # В сумме остаётся также урон от предшественника (300 + 300 = 600).
    c.advance(0.01)
    d = w.observe(400.0)  # -300
    assert abs(d - 600.0) < 1e-9, f"ожидали 600, получили {d}"


@S.add("reset очищает всю историю")
def _t_reset():
    c = _Clock()
    w = DamageWindow(window_seconds=10.0, clock=c)
    c.advance(0.01)
    w.observe(1000.0)
    c.advance(0.01)
    w.observe(500.0)  # -500
    w.reset()
    assert w.damage() == 0.0
    assert len(w) == 0


@S.add("буфер не растёт бесконечно (2001 пакет ∼ 102 элемента)")
def _t_buffer_limited():
    c = _Clock()
    w = DamageWindow(window_seconds=10.0, clock=c)
    for _ in range(2001):
        c.advance(0.05)
        w.observe(1000.0)
    # В 10-секундном окне при тике 0.05 — 200 сэмплов; + один предшественник.
    assert len(w) <= 250, f"буфер разросся до {len(w)}"


@S.add("пустое окно после reset → damage() == 0")
def _t_empty_damage():
    c = _Clock()
    w = DamageWindow(window_seconds=10.0, clock=c)
    assert w.damage() == 0.0