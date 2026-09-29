"""
taste_profile.py — ИИ-разбор понравившихся треков по вайб-категориям.

Работает на локальной модели через Ollama (см. src/ai/llm_providers.py).
Облачные ИИ не используются: недоступны из этого региона, ключей нет.
"""

import sys
import json
import random
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import (
    STATE_CALM,
    STATE_COMBAT,
    STATE_DEATH,
    STATE_VICTORY,
    STATE_DEFEAT,
)
from src.ai import track_filter
from src.ai import vibe_classifier

VIBE_DB_PATH = ROOT_DIR / "user_vibe_db.json"


def _parse_assignment(
    text: str,
    batch: List[Dict[str, Any]],
) -> Tuple[Dict[str, List[Dict[str, Any]]], set]:
    """
    Разбирает ответ модели.

    Возвращает (категория -> треки, номера не-музыки).

    Две защиты:
    1. От галлюцинаций: берём трек ТОЛЬКО из исходного пакета по
       номеру 'n'. Модель не может 'придумать' несуществующий трек.
    2. От не-музыки: всё, что модель отметила в NOT_MUSIC, вообще
       не участвует в раскладке - эти номера исключаются, даже если
       модель по глупости указала их ещё и в какой-то категории.
    """
    out: Dict[str, List[Dict[str, Any]]] = {}
    if not text:
        return out, set()
    clean = text.strip()
    if clean.startswith("```"):
        clean = clean.split("\n", 1)[1] if "\n" in clean else clean
        if clean.endswith("```"):
            clean = clean.rsplit("\n", 1)[0]
    # иногда модель оборачивает JSON в текст - вытащим самый длинный блок
    start, end = clean.find("{"), clean.rfind("}")
    if start == -1 or end == -1:
        return out, set()
    try:
        data = json.loads(clean[start:end + 1])
    except Exception:
        return out, set()

    def read_indices(key: str) -> List[int]:
        result = []
        for item in data.get(key, []) or []:
            if not isinstance(item, dict):
                # модель может вернуть просто числа вместо объектов
                if isinstance(item, int):
                    result.append(item)
                continue
            idx = item.get("n")
            if idx is None:
                idx = item.get("index")
            try:
                result.append(int(idx))
            except (TypeError, ValueError):
                continue
        return result

    # номера, которые модель назвала не-музыкой
    not_music = {i for i in read_indices("NOT_MUSIC")
                 if 0 <= i < len(batch)}

    for state in (STATE_CALM, STATE_COMBAT, STATE_DEATH, STATE_VICTORY):
        bucket = out.setdefault(state, [])
        for idx in read_indices(state):
            # жёсткая валидация: номер обязан существовать в пакете
            # и не должен быть помечен как не-музыка
            if 0 <= idx < len(batch) and idx not in not_music:
                bucket.append(batch[idx])
    return out, not_music


class TasteProfileAI:
    def __init__(self):
        self.vibe_db: Dict[str, List[Dict[str, str]]] = self._load_vibe_db()

    def _load_vibe_db(self) -> Dict[str, List[Dict[str, str]]]:
        if VIBE_DB_PATH.exists():
            try:
                with open(VIBE_DB_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"⚠️ [Taste Profile AI] Ошибка чтения базы треков: {e}")

        return {
            STATE_CALM: [],
            STATE_COMBAT: [],
            STATE_DEATH: [],
            STATE_VICTORY: [],
            STATE_DEFEAT: []
        }

    def _save_vibe_db(self) -> None:
        try:
            with open(VIBE_DB_PATH, "w", encoding="utf-8") as f:
                json.dump(self.vibe_db, f, ensure_ascii=False, indent=4)
            print(f"💾 [Taste Profile AI] База сохранена в {VIBE_DB_PATH.name}")
        except Exception as e:
            print(f"❌ [Taste Profile AI] Ошибка сохранения базы: {e}")

    def categorize_tracks(self, raw_tracks: List[Dict[str, str]]) -> Dict[str, Any]:
        """
        Сортирует треки по вайб-категориям.

        Раньше это делала нейросеть по названию, а потом результат ещё и
        выравнивался, чтобы категории выглядели одинаково. Оба шага
        давали неверную раскладку: DARUDE - SANDSTORM оказывался в CALM,
        а тихий трек - в COMBAT.

        Теперь вайб определяется по измеренному звуку (см.
        vibe_classifier.py): темп и телесность решают, бой это или фон,
        а смысл из названия решает, грустный трек или победный.

        Нейросеть осталась только на крайний случай - когда трек не
        удалось скачать и измерить. Сортировка по названию как раз её
        слабое место, поэтому тратить на неё основной заход бессмысленно.
        """
        if not raw_tracks:
            print("⚠️ [Taste Profile AI] Нет треков для сортировки.")
            return self.vibe_db

        print(f"🎵 [Taste Profile AI] Треков на сортировку: {len(raw_tracks)}")

        # --- 1. Главный проход: энергия измеряется по звуку -----------
        categorized = vibe_classifier.classify_all(raw_tracks)

        # --- 2. Нейросеть только для того, что не измерилось ---------
        # Треки без замеров уже разложены по названию - но это запасной
        # вариант, и он хуже. Вынимаем их из списков и пересортировываем
        # моделью, чтобы они не занимали место наугад.
        unmeasured = [t for items in categorized.values() for t in items
                      if not t.get("audio")]
        if unmeasured:
            for items in categorized.values():
                items[:] = [t for t in items if t.get("audio")]
            print(f"\n🤖 [Taste Profile AI] Без звука: {len(unmeasured)}, "
                  f"подключаю модель")
            self._sort_unmeasured_by_ai(unmeasured, categorized)

        # --- 3. Складываем результат -------------------------------
        self.vibe_db = {
            STATE_CALM: categorized.get(STATE_CALM, []),
            STATE_COMBAT: categorized.get(STATE_COMBAT, []),
            STATE_DEATH: categorized.get(STATE_DEATH, []),
            STATE_VICTORY: categorized.get(STATE_VICTORY, []),
            STATE_DEFEAT: [],
        }

        # --- 4. Последний рубеж: чистим не-музыку ---------------------
        self._filter_junk()

        self._save_vibe_db()

        print("\n✨ [Taste Profile AI] Готово! Раскладка по вайбам:")
        for state, items in self.vibe_db.items():
            print(f"   {state:<9} {len(items):>3}")
        return self.vibe_db

    def _sort_unmeasured_by_ai(
        self,
        pool: List[Dict[str, Any]],
        categorized: Dict[str, List[Dict[str, Any]]],
    ) -> None:
        """
        Запасная сортировка для треков, которые не удалось измерить.

        Скачать трек не удалось - у ролика нет аудиодорожки, он удалён
        или недоступен из региона. Тут нейросеть всё же полезнее, чем
        наугад: угадать настроение по названию она может.

        Результат кладём в те же списки, и трек сразу проходит общую
        чистку на не-музыку.
        """
        from src.ai.llm_providers import get_fallback_provider

        try:
            provider = get_fallback_provider()
        except Exception as e:
            print(f"⚠️ [Taste Profile AI] Резервная модель недоступна: {e}")
            provider = None

        if provider is None:
            # Модели нет - раскладываем по названию теми же словами,
            # что и в основном проходе. Раньше здесь треки просто
            # сваливались в CALM, и категория разрасталась наугад.
            print("⚠️ [Taste Profile AI] Модели нет, раскладываю по названиям")
            for t in pool:
                state, reason = vibe_classifier.classify_track(
                    t.get("title", ""), None)
                t["vibe_reason"] = reason
                categorized.setdefault(state, []).append(t)
            return

        print(f"🤖 [Taste Profile AI] Резервная модель: {provider.describe()}")
        SMALL = 5
        for i in range(0, len(pool), SMALL):
            chunk = pool[i:i + SMALL]
            listing = [{"n": j, "title": t.get("title", ""),
                        "artist": t.get("artist", "")}
                       for j, t in enumerate(chunk)]
            prompt = f"""Разбери эти треки по настроению.

CALM = спокойно, чил, лоуфай, фон
COMBAT = энергично, драйв, агрессия, для боя
DEATH = грустно, медленно, sad, поражение
VICTORY = эпично, героически, победа, триумф

Если это НЕ музыка (мем, разговор, геймплей, shorts) - впиши номер
в "NOT_MUSIC" и не раскладывай его никуда.

Треки: {json.dumps(listing, ensure_ascii=False)}

Ответ - ТОЛЬКО JSON, ключ "n" это номер трека:
{{"CALM":[{{"n":0}}],"COMBAT":[{{"n":1}}],"DEATH":[{{"n":2}}],"VICTORY":[{{"n":3}}],"NOT_MUSIC":[{{"n":4}}]}}"""
            try:
                text = provider.generate(prompt)
            except Exception as e:
                print(f"  ⚠️ модель не ответила: {str(e)[:80]}")
                text = None

            parsed, _ = _parse_assignment(text or "", chunk)
            assigned = {t.get("id") for items in parsed.values()
                        for t in items}

            for state, items in parsed.items():
                for t in items:
                    t["vibe_reason"] = "настроение по названию (звук недоступен)"
                    categorized.setdefault(state, []).append(t)

            # Модель ответила криво и кого-то пропустила. Раскладываем
            # пропущенное по названию, как если бы модели не было.
            # Если и по названию нечего сказать - трек не идёт в
            # матрицу: доказательств, что это музыка, нет.
            for t in chunk:
                if t.get("id") in assigned:
                    continue
                state, reason = vibe_classifier.classify_track(
                    t.get("title", ""), None)
                if state is None:
                    print(f"  🚫 без доказательств, не взяли: "
                          f"{t.get('title', '')[:48]}")
                    continue
                t["vibe_reason"] = reason
                categorized.setdefault(state, []).append(t)


    @staticmethod
    def _is_junk(title: str, artist: str = "", category_id: Any = None) -> bool:
        """
        Отсекает заведомо не-музыкальные ролики.

        Раньше этот список жил тут отдельно от общего фильтра и со временем
        с ним разошёлся. Теперь источник один - src/ai/track_filter.py,
        чтобы правила отсева не расходились в разных местах проекта.
        """
        return not track_filter.looks_like_music(title, artist, category_id)

    def _filter_junk(self) -> int:
        """
        Последний рубеж перед сохранением: вычищает всё, что не музыка.

        Стоит именно здесь, а не в начале, потому что это единственная
        точка, через которую проходит ЛЮБОЙ трек, попадающий в базу.
        Даже если нейросеть ошиблась или фильтр выше пропустил ролик,
        здесь он будет выкинут.
        """
        removed = 0
        for state, tracks in list(self.vibe_db.items()):
            if not tracks:
                continue
            keep = [t for t in tracks
                    if not self._is_junk(t.get("title", ""), t.get("artist", ""),
                                         t.get("category_id"))]
            for t in tracks:
                if t not in keep:
                    print(f"  ⛔ вычищен из {state}: {t.get('title','')[:52]}")
            removed += len(tracks) - len(keep)
            self.vibe_db[state] = keep
        if removed:
            print(f"🧹 [Taste Profile AI] Убрано не-музыкальных роликов: {removed}")
        return removed

    def get_track_for_state(self, state: str) -> Optional[Dict[str, str]]:
        tracks = self.vibe_db.get(state, [])
        if tracks:
            return random.choice(tracks)
        return None

    def get_playlist_for_state(self, state: str, is_night: bool = False) -> Optional[Dict[str, str]]:
        """
        Выбирает конкретный трек под текущее состояние игры.

        Этот метод вызывается из DJBrain.evaluate_state().
        Раньше он отсутствовал, и программа падала с AttributeError
        на первом же событии из Dota 2.

        Логика выбора:
          - ночью для боя берём более спокойный трек;
          - если в нужной категории ничего нет, пробуем осмысленные
            запасные (см. список кандидатов ниже);
          - если база пуста — возвращаем None (вызывающий код это обработает).
        """
        # Перечитываем базу с диска, чтобы подхватить результаты новой синхронизации
        self.vibe_db = self._load_vibe_db()

        # Порядок кандидатов: сначала точный вайб, потом осмысленные запасные.
        #
        # Запасные варианты важны, потому что сортировка честная: если в
        # библиотеке нет эпичных треков, VICTORY останется пустой. И
        # тогда важно, что поставится вместо: на победе должен играть
        # энергичный трек, а не чил-бит из фарма.
        if is_night and state == STATE_COMBAT:
            candidates = [STATE_CALM, STATE_DEATH, STATE_COMBAT]
        elif state == STATE_DEFEAT:
            # Проигрыш - тот же финальный момент матча, что и победа.
            # Отдельной категории DEFEAT в сортировке нет.
            candidates = [STATE_VICTORY, STATE_DEATH, STATE_COMBAT]
        elif state == STATE_VICTORY:
            # Победа без эпичной музыки: лучше энергичный трек из боя,
            # чем спокойный из фарма.
            candidates = [STATE_VICTORY, STATE_COMBAT, STATE_CALM]
        elif state == STATE_DEATH:
            candidates = [STATE_DEATH, STATE_CALM, STATE_VICTORY]
        elif state == STATE_CALM:
            # CALM - фон. Если его нет, лучше грустный трек, чем боевой.
            candidates = [STATE_CALM, STATE_DEATH]
        else:
            candidates = [state, STATE_CALM]

        for candidate in candidates:
            track = self.get_track_for_state(candidate)
            if track:
                return track

        print(f"⚠️ [Taste Profile AI] Нет треков для состояния {state} (ночь={is_night})")
        return None


TasteProfile = TasteProfileAI