import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# Автоматически определяем путь к корню проекта (SmartMusic/)
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Импортируем настройки и константы из корня проекта
from config import (
    DEFAULT_COOLDOWN_SECONDS,
    STATE_CALM,
    STATE_COMBAT,
    STATE_DEATH,
    STATE_VICTORY,
    STATE_DEFEAT,
    STATE_IDLE,
)

from src.player.media_keys import press_next_track, press_previous_track, press_play_pause


class DJBrain:
    """
    Класс 'Мозг ИИ-Диджея' принимает решения о смене музыки,
    учитывая кулдауны (гистерезис) и ночное время (Night Governor).
    """

    def __init__(self, cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS):
        self.cooldown_seconds = cooldown_seconds
        self.last_switch_timestamp: float = 0.0
        self.current_state: str = STATE_IDLE

    def is_night_time(self) -> bool:
        """
        Night Governor: проверяет, входит ли текущее время в ночной интервал (00:00 - 07:00).
        """
        current_hour = datetime.now().hour
        return 0 <= current_hour < 7

    def can_switch_track(self) -> bool:
        """
        Проверка гистерезиса (кулдауна): прошло ли достаточно времени с последнего переключения.
        """
        elapsed_time = time.time() - self.last_switch_timestamp
        return elapsed_time >= self.cooldown_seconds

    def evaluate_state(self, new_state: str) -> Optional[str]:
        """
        Анализирует новое состояние от GSI и принимает решение: нужно ли менять трек.
        """
        if new_state == self.current_state:
            return None

        print(f"\n🧠 [DJ Brain] Вайб-смена: {self.current_state} ──> {new_state}")
        self.current_state = new_state

        night_mode = self.is_night_time()
        if night_mode:
            print("🌙 [Night Governor] Активен ночной режим (00:00 - 07:00). Смягчаем переключения.")

        if not self.can_switch_track():
            remaining = int(self.cooldown_seconds - (time.time() - self.last_switch_timestamp))
            print(f"⏳ [Гистерезис] Кулдаун активен. Ждем еще {remaining} сек.")
            return "COOLDOWN_ACTIVE"

        action_taken = None

        if new_state == STATE_COMBAT:
            if night_mode:
                print("🌙 [Night Governor] Ночной бой: оставляем плавный трек.")
            else:
                print("⚔️ [DJ Brain] Жаркий файт! Инициируем переключение на эпичный трек.")
                press_next_track()
                action_taken = "SWITCH_COMBAT"
                self.last_switch_timestamp = time.time()

        elif new_state == STATE_DEATH:
            print("☠️ [DJ Brain] Герой в таверне. Переключаем на меланхоличный трек.")
            press_next_track()
            action_taken = "SWITCH_DEATH"
            self.last_switch_timestamp = time.time()

        elif new_state in (STATE_VICTORY, STATE_DEFEAT):
            print("🏆 [DJ Brain] Матч завершен!")
            press_next_track()
            action_taken = "MATCH_END"
            self.last_switch_timestamp = time.time()

        return action_taken


if __name__ == "__main__":
    print("🧪 [Тест DJ Brain] Проверяем логику...")
    dj = DJBrain(cooldown_seconds=5)
    dj.evaluate_state(STATE_COMBAT)