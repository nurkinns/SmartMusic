import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

# Определяем путь к корню проекта (SmartMusic/)
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Импортируем настройки из центрального config.py
from config import (
    DEFAULT_COOLDOWN_SECONDS,
    NIGHT_MODE_ENABLED,
    NIGHT_START_HOUR,
    NIGHT_END_HOUR,
    NIGHT_VOLUME_FACTOR,
    STATE_CALM,
    STATE_COMBAT,
    STATE_DEATH,
    STATE_VICTORY,
    STATE_DEFEAT,
    STATE_IDLE,
)

# Импортируем Вайб-Матрицу плейлистов
from src.ai.taste_profile import TasteProfile
from src.player.media_keys import press_next_track, press_previous_track, press_play_pause


class DJBrain:
    """
    Класс 'Мозг ИИ-Диджея'.
    Принимает решения о смене музыки, выбирает плейлисты через TasteProfile,
    фильтрует треки в ночное время (Night Governor) и предотвращает спам (гистерезис).
    """

    def __init__(self, cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS):
        self.cooldown_seconds = cooldown_seconds
        self.last_switch_timestamp: float = 0.0
        self.current_state: str = STATE_IDLE
        self.is_night_active: bool = False
        
        # Подключаем Вайб-Матрицу плейлистов
        self.taste_profile = TasteProfile()

    def is_night_time(self) -> bool:
        """Проверяет текущее системное время ПК (через datetime.now())."""
        if not NIGHT_MODE_ENABLED:
            return False

        current_hour = datetime.now().hour

        if NIGHT_START_HOUR <= NIGHT_END_HOUR:
            return NIGHT_START_HOUR <= current_hour < NIGHT_END_HOUR
        else:
            return current_hour >= NIGHT_START_HOUR or current_hour < NIGHT_END_HOUR

    def get_current_volume_factor(self) -> float:
        """Возвращает множитель громкости в зависимости от времени суток."""
        if self.is_night_time():
            return NIGHT_VOLUME_FACTOR
        return 1.0

    def can_switch_track(self) -> bool:
        """Проверка гистерезиса: прошло ли достаточно времени с последнего переключения."""
        elapsed_time = time.time() - self.last_switch_timestamp
        return elapsed_time >= self.cooldown_seconds

    def evaluate_state(self, new_state: str) -> Dict[str, Any]:
        """Главный метод принятия решений на основе события от GSI Dota 2."""
        if new_state == self.current_state:
            return {"status": "NO_CHANGE", "state": self.current_state}

        print(f"\n🧠 [DJ Brain] Вайб-смена: {self.current_state} ──> {new_state}")
        self.current_state = new_state

        night_mode = self.is_night_time()
        self.is_night_active = night_mode

        # Получаем целевую ссылку/плейлист из Вайб-Матрицы
        target_playlist = self.taste_profile.get_playlist_for_state(new_state, is_night=night_mode)

        if night_mode:
            print(f"🌙 [Night Governor] Активен ночной режим ({NIGHT_START_HOUR}:00 - {NIGHT_END_HOUR}:00).")
            print(f"🔊 [Night Governor] Ограничение громкости: {int(NIGHT_VOLUME_FACTOR * 100)}%")

        if not self.can_switch_track():
            remaining = int(self.cooldown_seconds - (time.time() - self.last_switch_timestamp))
            print(f"⏳ [Гистерезис] Защита от спама активна. Ждём ещё {remaining} сек.")
            return {
                "status": "COOLDOWN_ACTIVE", 
                "remaining_seconds": remaining,
                "target_playlist": target_playlist
            }

        action_taken = "NO_ACTION"

        if new_state == STATE_COMBAT:
            if night_mode:
                print("🌙 [Night Governor] Ночной бой: включаем расслабляющий Synthwave вайб.")
                action_taken = "SWITCH_NIGHT_COMBAT"
            else:
                print("⚔️ [DJ Brain] Драка! Включаем драйвовый трек.")
                action_taken = "SWITCH_COMBAT"
            
            press_next_track()
            self.last_switch_timestamp = time.time()

        elif new_state == STATE_DEATH:
            print("☠️ [DJ Brain] Герой мертв. Включаем чилл / грустный трек.")
            press_next_track()
            action_taken = "SWITCH_DEATH"
            self.last_switch_timestamp = time.time()

        elif new_state in (STATE_VICTORY, STATE_DEFEAT):
            print("🏆 [DJ Brain] Конец матча!")
            press_next_track()
            action_taken = "MATCH_END"
            self.last_switch_timestamp = time.time()

        return {
            "status": "SUCCESS",
            "action": action_taken,
            "target_playlist": target_playlist,
            "night_mode": night_mode,
            "volume_factor": self.get_current_volume_factor()
        }


if __name__ == "__main__":
    print("🧪 [Тест обновленного DJ Brain c TasteProfile]...")
    dj = DJBrain(cooldown_seconds=1)
    res = dj.evaluate_state(STATE_COMBAT)
    print(f"Результат: {res}")