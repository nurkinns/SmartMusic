import sys
from pathlib import Path
import uvicorn
from fastapi import FastAPI, Request
from typing import Dict, Any
from src.ui.signals import gsi_signals

# Определяем путь к корню проекта (SmartMusic/)
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import (
    GSI_SERVER_HOST,
    GSI_SERVER_PORT,
    STATE_CALM,
    STATE_COMBAT,
    STATE_DEATH,
    STATE_VICTORY,
    STATE_DEFEAT,
    STATE_IDLE,
    DEFAULT_COOLDOWN_SECONDS,
)
from src.ai.dj_brain import DJBrain

app = FastAPI(title="SmartMusic Dota 2 GSI Listener")

# Мозг ИИ-Диджея. Кулдаун берём из config, а не пишем здесь число.
# Раньше тут стояло 15, а в config было 30, и настройка была в двух
# местах с разными значениями: поменять «кулдаун» можно было только
# угадав, какое из двух мест читает программа.
dj_brain = DJBrain(cooldown_seconds=DEFAULT_COOLDOWN_SECONDS)

last_hero_health: int = 100
last_game_state: str = STATE_IDLE
is_first_run: bool = True


def analyze_game_state(payload: Dict[str, Any]) -> str:
    """
    Анализирует сырой JSON-пакет от Dota 2 GSI
    и определит текущий 'вайб' игры.
    """
    global last_hero_health, is_first_run

    provider = payload.get("provider", {})
    map_data = payload.get("map", {})
    hero_data = payload.get("hero", {})

    if not provider or not map_data:
        return STATE_IDLE

    game_state = map_data.get("game_state", "")

    # Проверка завершения матча
    if game_state == "DOTA_GAMERULES_STATE_POST_GAME":
        win_team = map_data.get("win_team", "")
        player_team = payload.get("player", {}).get("team_name", "")
        if win_team and player_team and win_team == player_team:
            return STATE_VICTORY
        return STATE_DEFEAT

    # Если игра еще не идет (выбор героев / меню)
    if game_state != "DOTA_GAMERULES_STATE_GAME_IN_PROGRESS":
        return STATE_IDLE

    # Проверка смерти героя
    is_alive = hero_data.get("alive", True)
    if not is_alive:
        return STATE_DEATH

    current_health = hero_data.get("health", 100)
    max_health = hero_data.get("max_health", 100)
    health_percent = hero_data.get("health_percent", 100)

    # Первый запуск — запоминаем HP
    if is_first_run:
        last_hero_health = current_health
        is_first_run = False
        return STATE_CALM

    damage_taken = last_hero_health - current_health
    last_hero_health = current_health

    # Условия начала боя: потеря >15% HP за тик, лоу-хп (<50%) или любой входящий урон
    is_heavy_damage = max_health > 0 and damage_taken > (max_health * 0.15)
    is_low_health = health_percent < 50

    if is_low_health or is_heavy_damage or damage_taken > 0:
        return STATE_COMBAT

    return STATE_CALM


@app.post("/")
async def gsi_receiver(request: Request):
    global last_game_state

    try:
        data = await request.json()
        current_state = analyze_game_state(data)

        if current_state != last_game_state:
            print(f"\n⚡ [Событие Dota 2] Смена состояния: {last_game_state} ──> {current_state}")
            decision = dj_brain.evaluate_state(current_state)
            last_game_state = current_state

            # 📡 Отправляем событие в GUI
            action = decision.get("action", "NO_ACTION") if isinstance(decision, dict) else "NO_ACTION"
            is_night = dj_brain.is_night_time()
            vol = dj_brain.get_current_volume_factor()

            gsi_signals.state_changed.emit(current_state, action, is_night, vol)

            # Что именно заиграло - отдельным сигналом. Раньше этого не
            # было вовсе: в окне было видно состояние игры, но не было
            # видно музыки, и если трек не тот, разобраться было нечем.
            self._notify_track(action, decision, current_state)

            return {"status": "ok", "state": current_state, "dj_decision": decision}

        return {"status": "ok", "state": current_state, "dj_decision": "NO_CHANGE"}

    except Exception as error:
        return {"status": "error", "details": str(error)}


def _notify_track(action: str, decision: Dict[str, Any], state: str) -> None:
    """
    Отправляет в GUI, что сейчас играет.

    Отдельная функция, потому что у события три разных исхода, и в одном
    месте они выглядели бы неразборчиво:
      - трек сменился  -> показываем название;
      - ждём кулдаун   -> трек прежний, предупреждаем, что будет смена;
      - сбой/пусто    -> прямо говорим, что музыки нет.

    Молчать в последних двух случаях нельзя: выглядит так, будто всё в
    порядке, а на деле играет не то (или не играет ничего).
    """
    status = decision.get("status") if isinstance(decision, dict) else None
    track = decision.get("track") if isinstance(decision, dict) else None

    if action == "NO_TRACKS":
        gsi_signals.track_changed.emit("", "База треков пуста — нажми «Синхронизировать ИИ»")
        return

    if action == "PLAYBACK_FAILED":
        gsi_signals.track_changed.emit("", "Плеер не смог включить трек")
        return

    if status == "COOLDOWN_ACTIVE":
        wait = decision.get("remaining_seconds", 0)
        gsi_signals.track_changed.emit(
            "", f"Смена вайба через {wait} сек (защита от частых переключений)")
        return

    if track:
        title = track.get("title", "")
        artist = track.get("artist", "")
        label = f"{artist} — {title}" if artist else title
        gsi_signals.track_changed.emit(label, f"Вайб: {state}")


def start_gsi_server():
    """Запускает HTTP-сервер uvicorn."""
    print(f"🚀 [GSI Server] Запуск HTTP слушателя на http://{GSI_SERVER_HOST}:{GSI_SERVER_PORT}")
    uvicorn.run(app, host=GSI_SERVER_HOST, port=GSI_SERVER_PORT, log_level="warning")


if __name__ == "__main__":
    start_gsi_server()