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
    COMBAT_DAMAGE_WINDOW_SECONDS,
    COMBAT_DAMAGE_THRESHOLD,
    COMBAT_LOW_HEALTH_PERCENT,
)
from src.ai.dj_brain import DJBrain
from src.triggers.damage_window import DamageWindow

app = FastAPI(title="SmartMusic Dota 2 GSI Listener")

# Мозг ИИ-Диджея. Кулдаун берём из config, а не пишем здесь число.
# Раньше тут стояло 15, а в config было 30, и настройка была в двух
# местах с разными значениями: поменять «кулдаун» можно было только
# угадав, какое из двух мест читает программа.
dj_brain = DJBrain(cooldown_seconds=DEFAULT_COOLDOWN_SECONDS)

# Окно урона на всё время работы программы. Живёт здесь, а не внутри
# analyze_game_state, потому что окно по смыслу длиннее одного вызова:
# если создавать его заново на каждый пакет, оно всегда оставалось бы
# пустым и считало ровно то же, что и раньше.
damage_window = DamageWindow(window_seconds=COMBAT_DAMAGE_WINDOW_SECONDS)

# Прошлое состояние. Сравнение идёт по нему, а не по памяти DJ Brain:
# если бы оно жило там, мы бы не узнали, что состояние сменилось, пока
# музыка не доехала до вайба, и событие потерялось бы.
last_game_state: str = STATE_IDLE

# Герой был мёртв на прошлом пакете. Нужно, чтобы на возрождении сбросить
# окно: см. analyze_game_state.
_was_alive: bool = True


def reset_damage_window() -> None:
    """Забыть всю историю урона. Выход из матча или возрождение."""
    damage_window.reset()


def analyze_game_state(payload: Dict[str, Any]) -> str:
    """
    Анализирует сырой JSON-пакет от Dota 2 GSI
    и определит текущий 'вайб' игры.
    """
    global _was_alive

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
        # История боя к матчу отношения не имеет: после перезапуска
        # программы она была бы засчитана как поединок прямо в первом
        # же пакете, и музыка поехала бы боевой с первой секунды.
        reset_damage_window()
        if win_team and player_team and win_team == player_team:
            return STATE_VICTORY
        return STATE_DEFEAT

    # Если игра еще не идет (выбор героев / меню)
    if game_state != "DOTA_GAMERULES_STATE_GAME_IN_PROGRESS":
        reset_damage_window()
        return STATE_IDLE

    # Проверка смерти героя
    is_alive = hero_data.get("alive", True)
    if not is_alive:
        _was_alive = False
        return STATE_DEATH

    # Воскрес: здоровье вернулось к максимуму, а в окне лежит всё, что
    # герой получил перед смертью. Если не сбросить, ещё десять секунд
    # после возрождения программа считала бы боем то, что уже кончилось.
    if not _was_alive:
        _was_alive = True
        reset_damage_window()

    max_health = hero_data.get("max_health", 100)
    current_health = hero_data.get("health", 100)
    health_percent = hero_data.get("health_percent", 100)

    # HP из пакета иногда приходит мусором: ноль у живого героя или
    # значение выше максимума в момент респауна. Без проверки такой пакет
    # либо выдал бы фантомный урон в 300 HP, либо обнулил бы окно.
    if max_health and max_health > 0:
        current_health = max(0, min(current_health, max_health))

    # Бой - это суммарная потеря HP за последние
    # COMBAT_DAMAGE_WINDOW_SECONDS секунд. Раньше здесь стояло падение
    # между двумя соседними пакетами, а GSI шлёт их десять раз в секунду:
    # один удар на фарме давал COMBAT, и на пробе героев выходило
    # 13 переходов за 7 минут.
    window_damage = damage_window.observe(current_health)

    # Порог - 15% от ТЕКУЩЕГО HP, как и сказано в задаче: «если он больше
    # чем 15% хп героя на данный момент». Следствие: у раненого порог
    # ниже, и короткая стычка считается боем. Это заказано поведение,
    # а не ошибка.
    is_recent_heavy_damage = window_damage > current_health * COMBAT_DAMAGE_THRESHOLD

    # Низкое HP - отдельная причина, к урону отношения не имеет: загнал
    # в таверну на фарме, и это уже драка.
    is_low_health = health_percent < COMBAT_LOW_HEALTH_PERCENT

    if is_low_health or is_recent_heavy_damage:
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
            #
            # Здесь стояло self._notify_track(...), хотя функция уровня
            # модуля и self в этой области не существует. Имя не
            # определялось -> NameError -> его съедал except ниже, и весь
            # track_changed из GSI не уходил никогда: плашка «Сейчас
            # играет» молчала, а Dota на каждую смену состояния
            # получала {"status":"error"} вместо {"status":"ok"}.
            # state_changed на строке выше успевал уйти, поэтому статус
            # игры на дашборде работал и баг не бросился в глаза.
            _notify_track(action, decision, current_state)

            return {"status": "ok", "state": current_state, "dj_decision": decision}

        return {"status": "ok", "state": current_state, "dj_decision": "NO_CHANGE"}

    except Exception as error:
        # Раньше здесь был голый `return {"status": "error"}`, и это была
        # ловушка: любая опечатка внутри обработчика выглядела для Dota
        # как «сервер ответил ошибкой» и для нас как «GSI молчит, наверное
        # игра не отправляет пакеты». Ошибку печатаем целиком - иначе
        # тот же NameError в _notify_track невозможно было заметить.
        import traceback
        traceback.print_exc()
        print(f"❌ [GSI] Ошибка разбора пакета: {type(error).__name__}: {error}")
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