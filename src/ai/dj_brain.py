import random
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
from src.player.audio_player import AudioPlayer


class DJBrain:
    """
    Класс 'Мозг ИИ-Диджея'.
    Принимает решения о смене музыки, выбирает плейлисты через TasteProfile,
    фильтрует треки в ночное время (Night Governor) и предотвращает спам (гистерезис).

    Раньше здесь вместо плеера стоял press_next_track() - мультимедийная
    клавиша «дальше». Это не было проигрыванием: она переключала то, что
    уже играло в чём-то постороннем, и вайб на выбор музыки не влиял.
    Теперь трек выбирается здесь же и передаётся настоящему плееру.
    """

    def __init__(self, cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
                 player: Optional[AudioPlayer] = None):
        self.cooldown_seconds = cooldown_seconds
        self.last_switch_timestamp: float = 0.0
        self.current_state: str = STATE_IDLE
        self.is_night_active: bool = False
        # Ночной лимит по умолчанию включён (как в config). Дальше его
        # можно переключить из окна, поэтому хранится здесь, а не берётся
        # из конфига напрямую.
        self._night_enabled: bool = bool(NIGHT_MODE_ENABLED)
        # Что играет прямо сейчас. Нужно GUI и отладке: без этого
        # не видно, что именно поставил DJ Brain.
        self.current_track: Optional[Dict[str, Any]] = None
        # Вайб, который хотели включить, но кулдаун не дал. Игра шла
        # дальше, а переключение ждало. Без этого поля переключение
        # терялось бы до следующей смены состояния.
        self.pending_state: Optional[str] = None

        # Подключаем Вайб-Матрицу плейлистов
        self.taste_profile = TasteProfile()
        # Плеер поднимается один раз и живёт всё время работы программы:
        # у него свой поток VLC, и создавать его на каждый вызов было бы
        # и долго, и неправильно. Снаружи можно подставить свой, это
        # нужно тестам.
        self.player = player if player is not None else AudioPlayer()

    def set_night_enabled(self, enabled: bool) -> None:
        """
        Включает или выключает ночной лимит по воле человека.

        Отдельное поле, а не запись в config.NIGHT_MODE_ENABLED: конфиг
        читается при импорте и в dj_brain уже лежит своей копией,
        поэтому запись туда ничего бы не изменила до перезапуска.
        Переключатель в окне должен работать сразу.

        После переключения громкость применяется немедленно: иначе
        человек снял галочку, а музыка осталась приглушённой до
        следующего события от игры.
        """
        enabled = bool(enabled)
        if enabled == self._night_enabled:
            return
        self._night_enabled = enabled
        print(f"{'🌙' if enabled else '☀️'} [Night Governor] "
              f"Ночной режим {'включён' if enabled else 'выключен'} вручную.")
        self.apply_night_limit()

    def night_enabled(self) -> bool:
        """Включён ли ночной лимит. Для окна, чтобы показать переключатель."""
        return self._night_enabled

    def is_night_time(self) -> bool:
        """Проверяет текущее системное время ПК (через datetime.now())."""
        if not self._night_enabled:
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

    def apply_night_limit(self) -> None:
        """
        Проталкивает текущий лимит громкости в плеер.

        Отдельный метод, потому что вызывать его надо в двух местах: на
        каждом пакете от игры и сразу после ручного переключения.
        Плеер повторяющиеся значения игнорирует, так что звать его
        часто и недорого.
        """
        try:
            self.player.set_volume(self.get_current_volume_factor())
        except Exception as e:
            print(f"⚠️ [Night Governor] Не удалось применить лимит: {e}")

    def can_switch_track(self) -> bool:
        """Проверка гистерезиса: прошло ли достаточно времени с последнего переключения."""
        elapsed_time = time.time() - self.last_switch_timestamp
        return elapsed_time >= self.cooldown_seconds

    def resume(self) -> Dict[str, Any]:
        """
        Включает музыку по воле человека, а не по событию игры.

        Нужна для кнопки play в окне. Без неё кнопка умела бы только
        снимать и возвращать паузу, а если музыка ещё не запускалась -
        делала бы вид, что нажата, и ничего. Человек нажал «играть» и
        ждёт музыку, а не пустую кнопку.

        Здесь намеренно не ждём смены состояния и не смотрим на
        кулдаун: событие от Dota может и не прийти (программа только
        что запустили, вкладка с игрой молчит), а человек нажал
        кнопку сознательно. Не сработает только одно - если брать не
        из чего: база треков пуста.
        """
        # Вне игры состояние IDLE, а музыка для него не заведена.
        # Берём фарм: это самый спокойный вайб из имеющихся.
        state = self.current_state
        if not state or state == STATE_IDLE:
            state = STATE_CALM

        night_mode = self.is_night_time()
        self.is_night_active = night_mode
        self.player.set_volume(self.get_current_volume_factor())

        action = self._play_for_state(state, night_mode)
        if action in ("PLAYBACK_FAILED", "NO_TRACKS"):
            return {"status": "FAILED", "action": action, "state": state}

        # Считаем это полноценной сменой: состояние зафиксировано,
        # кулдаун запущен. Иначе следующий пакет от игры увидит
        # «вайб уже такой» и ничего не сделает, хотя человек только что
        # попросил музыку - а это молчание выглядело бы как поломка.
        self.current_state = state
        self.pending_state = None
        self.last_switch_timestamp = time.time()

        return {"status": "SUCCESS", "action": action, "state": state,
                "track": self.current_track, "night_mode": night_mode}

    def evaluate_state(self, new_state: str) -> Dict[str, Any]:
        """
        Главный метод принятия решений на основе события от GSI Dota 2.

        Вызывается на КАЖДЫЙ пакет от игры, то есть много раз в секунду.
        Поэтому здесь важно не сломаться о кулдаун и не потерять переключение.
        """
        # Отложенный вайб важнее всего остального. Если прошлый раз
        # переключение заблокировал кулдаун, вайб мог не успеть смениться,
        # а игра продолжилась в новом состоянии. Тогда событий «новая
        # смена» уже не будет - оно будет приходить как NO_CHANGE.
        # Здесь такое отложенное состояние и доигрывается.
        if self.pending_state and self.pending_state != new_state:
            print(f"\n🧠 [DJ Brain] Отложенный вайб {self.pending_state} "
                  f"меняется на {new_state} (ждал кулдаун)")
            self.pending_state = None

        night_mode = self.is_night_time()
        self.is_night_active = night_mode

        # Громкость Night Governor - ДО раннего выхода, и это не
        # перестановка для красоты.
        #
        # Стояло после проверки NO_CHANGE, и обещание в комментарии не
        # выполнялось: ночь наступает между сменами вайба, а смена может
        # не наступить десять минут. Всё это время человек спал бы рядом
        # с колонками на полной громкости, и ползунок в окне показывал бы
        # 100% при том, что ночной лимит якобы включён.
        #
        # Теперь ограничение едет на КАЖДЫЙ пакет от игры, но плеер
        # повторяющиеся значения игнорирует, поэтому лишней работы нет.
        self.player.set_volume(self.get_current_volume_factor())

        if new_state == self.current_state and not self.pending_state:
            return {"status": "NO_CHANGE", "state": self.current_state}

        if night_mode:
            print(f"🌙 [Night Governor] Активен ночной режим ({NIGHT_START_HOUR}:00 - {NIGHT_END_HOUR}:00).")
            print(f"🔊 [Night Governor] Ограничение громкости: {int(NIGHT_VOLUME_FACTOR * 100)}%")

        if not self.can_switch_track():
            remaining = int(self.cooldown_seconds - (time.time() - self.last_switch_timestamp))
            # Запоминаем вайб, но НЕ трогаем current_state.
            #
            # Раньше здесь стояло self.current_state = new_state до проверки
            # кулдауна, и это была ловушка: состояние менялось, музыка -
            # нет. Следующее событие с тем же вайбом возвращало NO_CHANGE,
            # потому что «вайб уже такой». Переключение терялось навсегда
            # до следующей смены состояния - то есть на весь бой.
            self.pending_state = new_state
            print(f"⏳ [Гистерезис] Ждём ещё {remaining} сек. "
                  f"Вайб {new_state} запомнен, включим как освободимся.")
            return {
                "status": "COOLDOWN_ACTIVE",
                "remaining_seconds": remaining,
                "pending_state": new_state,
                "track": self.current_track,
                "night_mode": night_mode,
                "volume_factor": self.get_current_volume_factor()
            }

        print(f"\n🧠 [DJ Brain] Вайб-смена: {self.current_state} ──> {new_state}")

        # CALM раньше вообще ничего не делал: ветки под него не было,
        # программа молчала на фарме. Это ошибка была не в том, что
        # фарм - не повод для музыки, а в том, что про него забыли.
        action_taken = "NO_ACTION"
        if new_state in (STATE_COMBAT, STATE_DEATH,
                         STATE_VICTORY, STATE_DEFEAT, STATE_CALM):
            action_taken = self._play_for_state(new_state, night_mode)

        # Состояние фиксируем только после того, как музыка реально
        # поставлена. Если плеер не смог - вайб остаётся прежним, чтобы
        # следующий пакет от игры снова попробовал.
        if action_taken not in ("PLAYBACK_FAILED", "NO_TRACKS"):
            self.current_state = new_state
            self.pending_state = None

        return {
            "status": "SUCCESS",
            "action": action_taken,
            "track": self.current_track,
            "night_mode": night_mode,
            "volume_factor": self.get_current_volume_factor()
        }

    def _play_for_state(self, state: str, night_mode: bool) -> str:
        """
        Ставит трек, подходящий под состояние игры.

        Возвращает название действия для логов и GUI.
        """
        tracks = self.taste_profile.get_tracks_for_state(state, is_night=night_mode)

        if not tracks:
            # База пуста - синхронизация ещё не проходила. Молчание
            # здесь вводит в заблуждение: кажется, что музыка есть, но
            # её нет. Поэтому говорим прямо.
            print("🚫 [DJ Brain] Нечего играть: база треков пуста. "
                  "Нажми «Синхронизировать ИИ» в Настройках.")
            self.current_track = None
            return "NO_TRACKS"

        track = random.choice(tracks)
        label = f"{track.get('artist', '—')} — {track.get('title', 'без названия')}"

        if state == STATE_COMBAT:
            action = "SWITCH_NIGHT_COMBAT" if night_mode else "SWITCH_COMBAT"
            print(f"{'🌙 Ночной бой: расслабляющий вайб' if night_mode else '⚔️ Драка: драйвовый трек'}")
        elif state == STATE_DEATH:
            action = "SWITCH_DEATH"
            print("☠️ Герой мёртв: чилл / грустный трек")
        elif state in (STATE_VICTORY, STATE_DEFEAT):
            action = "MATCH_END"
            print("🏆 Конец матча")
        else:
            action = "SWITCH_CALM"
            print("🌾 Фарм: спокойный фон")

        print(f"   Треков в вайбе: {len(tracks)}")

        # Отдаём не один трек, а весь список: плеер крутит их по кругу,
        # пока держится вайб. Один трек на весь фарм означал бы, что
        # один и тот же кусок повторяется каждые три минуты.
        if self.player.play_list(tracks):
            self.current_track = track
            self.last_switch_timestamp = time.time()
            return action

        # Плеер взял треки, но включить не смог (нет звука, файл не
        # скачался). Не считаем это успехом и не щёлкаем кулдаун:
        # иначе следующая подходящая смена пропадёт.
        print("❌ [DJ Brain] Плеер не смог включить трек.")
        self.current_track = None
        return "PLAYBACK_FAILED"


if __name__ == "__main__":
    # Пробный прогон без Dota: гоняем состояния по кругу и смотрим,
    # что музыка меняется. Кулдаун крошечный, иначе проверка шла бы
    # дольше, чем сам матч.
    import time as _time

    print("🧪 [Тест DJ Brain] Гоняем вайбы без Dota, музыка пойдёт в колонки.\n")
    dj = DJBrain(cooldown_seconds=2)

    for state in (STATE_CALM, STATE_COMBAT, STATE_DEATH, STATE_CALM, STATE_COMBAT):
        res = dj.evaluate_state(state)
        track = res.get("track") or {}
        title = track.get("title", "—")
        print(f"   → {state:<8} действие: {res.get('action')}, трек: {title[:44]}\n")
        _time.sleep(0.5)

    dj.player.shutdown()
    print("Готово.")