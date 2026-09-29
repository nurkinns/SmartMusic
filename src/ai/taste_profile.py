"""
taste_profile.py — ИИ-разбор понравившихся треков по вайб-категориям.

Работает на локальной модели через Ollama (см. src/ai/llm_providers.py).
Облачные ИИ не используются: Gemini заблокирован по региону, ключей нет.
"""

import sys
import json
import random
from pathlib import Path
from typing import Dict, List, Any, Optional

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

VIBE_DB_PATH = ROOT_DIR / "user_vibe_db.json"


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

    def _classify_batch(self, provider, batch: List[Dict[str, str]],
                        batch_no: int, total_batches: int) -> Dict[str, List[Dict[str, str]]]:
        """
        Классифицирует ОДИН пакет треков.

        Почему пакетами, а не всё сразу: при 50 треках модель отвечает
        ерундой - сыплет 40 треков в первую категорию, а в остальные
        кладёт по нолю. На пакетах по 8-10 штук точность заметно выше.
        """
        numbered = [
            {"n": i, "title": t.get("title", ""), "artist": t.get("artist", "")}
            for i, t in enumerate(batch)
        ]
        prompt = f"""Ты — музыкальный эксперт и ИИ-диджей для Dota 2.
Пакет {batch_no} из {total_batches}. Раздай {len(batch)} треков по 4 категориям.

КАТЕГОРИИ:
- "CALM": спокойный фон для фарма (lofi, jazz, chill, soft indie, pop, ambient)
- "COMBAT": драйв и агрессия (phonk, metal, hard rock, aggressive edm, trap, dark)
- "DEATH": грусть и замедленность (sad, slowed + reverb, melancholy, acoustic ballad)
- "VICTORY": эпик и подъём (epic, triumphant, victory, heroic, anthem)

ЖЁСТКИЕ ПРАВИЛА:
1. Один трек получает РОВНО одну категорию. Не дублируй.
2. ПРОПУСКАЙ не-музыку (мемы, обзоры, подкасты, разговорные ролики) -
   такие просто не упоминай в ответе.
3. ВАЖНО: не пихай всё в CALM. CALM - это спокойная музыка. Если трек
   энергичный - это COMBAT, даже если жанр тебе незнаком.
4. Если трек не подходит ни под одну категорию - пропусти его.

Треки:
{json.dumps(numbered, ensure_ascii=False)}

Ответ - ТОЛЬКО JSON такой формы (ключ "n" - номер трека из списка):
{{"CALM":[{{"n":0}}],"COMBAT":[{{"n":3}}],"DEATH":[{{"n":7}}],"VICTORY":[{{"n":9}}]}}"""
        text = provider.generate(prompt)
        if not text:
            return {}
        return self._parse_assignment(text, batch)

    @staticmethod
    def _parse_assignment(text: str, batch: List[Dict[str, str]]
                          ) -> Dict[str, List[Dict[str, str]]]:
        """
        Разбирает ответ модели и возвращает реальные треки.

        Защита от галлюцинаций: берём трек ТОЛЬКО из исходного пакета
        по номеру 'n'. Модель не может 'придумать' несуществующий трек -
        максимум может ошибиться номером, и тогда трек просто уйдёт
        в unclassified и будет обработан дальше.
        """
        out: Dict[str, List[Dict[str, str]]] = {}
        if not text:
            return out
        clean = text.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1] if "\n" in clean else clean
            if clean.endswith("```"):
                clean = clean.rsplit("\n", 1)[0]
        # иногда модель оборачивает JSON в текст - вытащим самый длинный блок
        start, end = clean.find("{"), clean.rfind("}")
        if start == -1 or end == -1:
            return out
        try:
            data = json.loads(clean[start:end + 1])
        except Exception:
            return out

        for state in (STATE_CALM, STATE_COMBAT, STATE_DEATH, STATE_VICTORY):
            bucket = out.setdefault(state, [])
            for item in data.get(state, []) or []:
                if not isinstance(item, dict):
                    continue
                idx = item.get("n")
                if idx is None:
                    idx = item.get("index")
                try:
                    idx = int(idx)
                except (TypeError, ValueError):
                    continue
                # жёсткая валидация: номер обязан существовать в пакете
                if 0 <= idx < len(batch):
                    bucket.append(batch[idx])
        return out

    def categorize_tracks(self, raw_tracks: List[Dict[str, str]]) -> Dict[str, Any]:
        """
        Сортирует треки по вайб-категориям через локальную модель.

        Старое поведение (один запрос на 50 треков) давало 40 в CALM и 0
        в VICTORY/DEFEAT. Теперь: пакетами + валидация + добираем
        пустые категории отдельным проходом.
        """
        from src.ai.llm_providers import get_provider

        if not raw_tracks:
            print("⚠️ [Taste Profile AI] Нет треков для сортировки.")
            return self.vibe_db

        provider = get_provider()
        print(f"🤖 [Taste Profile AI] Провайдер: {provider.describe()}")
        print(f"🎵 [Taste Profile AI] Треков на сортировку: {len(raw_tracks)}")

        # --- 1. Основная сортировка пакетами -------------------------
        BATCH = 10
        batches = [raw_tracks[i:i + BATCH]
                   for i in range(0, len(raw_tracks), BATCH)]

        for state in (STATE_CALM, STATE_COMBAT, STATE_DEATH, STATE_VICTORY,
                      STATE_DEFEAT):
            self.vibe_db[state] = []

        used_ids = set()
        for i, batch in enumerate(batches, 1):
            result = self._classify_batch(provider, batch, i, len(batches))
            for state, tracks in result.items():
                for t in tracks:
                    tid = t.get("id")
                    if tid in used_ids:      # защита от дублей между пакетами
                        continue
                    used_ids.add(tid)
                    self.vibe_db.setdefault(state, []).append(t)
            counts = {k: len(v) for k, v in result.items()}
            print(f"  пакет {i}/{len(batches)}: {counts}")

        unclassified = [t for t in raw_tracks if t.get("id") not in used_ids]

        # --- 2. Добор пустых категорий -------------------------------
        # Модель часто игнорирует VICTORY/DEFEAT. Отдельный проход
        # с явным требованием найти треки именно под них.
        empty = [s for s in (STATE_COMBAT, STATE_DEATH, STATE_VICTORY)
                 if len(self.vibe_db.get(s, [])) < 3]
        if empty and unclassified:
            print(f"⚠️ [Taste Profile AI] Пустые категории: {empty}, добираю...")
            self._fill_empty_states(provider, unclassified, empty)
            unclassified = [t for t in unclassified
                            if t.get("id") not in
                            {x.get("id") for s in empty
                             for x in self.vibe_db.get(s, [])}]

        # --- 3. Остаток раскидываем локальным алгоритмом --------------
        if unclassified:
            print(f"🔸 [Taste Profile AI] Не распределено: {len(unclassified)}, "
                  f"разбираю по ключевым словам")
            self._absorb_leftovers(unclassified)

        # --- 4. Чистим мусор, который модель не отфильтровала --------
        self._filter_junk()

        self._save_vibe_db()

        print("\n✨ [Taste Profile AI] Готово! Раскладка по вайбам:")
        for state, tracks in self.vibe_db.items():
            print(f"   {state:<9} {len(tracks):>3}")
        return self.vibe_db

    def _fill_empty_states(self, provider, pool: List[Dict[str, str]],
                           empty_states: List[str]) -> None:
        """Отдельный проход: ищем треки под конкретные пустые категории."""
        listing = [
            {"n": i, "title": t.get("title", ""), "artist": t.get("artist", "")}
            for i, t in enumerate(pool)
        ]
        wanted = ", ".join(f'"{s}"' for s in empty_states)
        prompt = f"""Найди среди этих треков те, что подходят
под категории: {wanted}.

- COMBAT: энергичный, агрессивный, драйвовый трек
- DEATH: грустный, медленный, депрессивный, slowed
- VICTORY: эпичный, героический, торжественный, поднимающий

Список:
{json.dumps(listing, ensure_ascii=False)}

Ответ - ТОЛЬКО JSON, ключ "n" это номер трека из списка:
{{"COMBAT":[{{"n":1}}],"VICTORY":[{{"n":4}}]}}"""
        text = provider.generate(prompt)
        if not text:
            return
        parsed = self._parse_assignment(text, pool)
        for state, tracks in parsed.items():
            if state not in empty_states:
                continue
            for t in tracks:
                self.vibe_db.setdefault(state, []).append(t)

    @staticmethod
    def _is_junk(title: str) -> bool:
        """
        Отсекает заведомо не-музыкальные ролики.

        Нужно потому, что даже хорошая модель периодически кладёт
        'обзор стартапа' в VICTORY вместо того, чтобы пропустить его.
        """
        t = (title or "").lower()
        junk = [
            "обзор", "разбор", "гайд", "подкаст", "мем", "челлендж",
            "стартап", "будни", "флешмоб", "улыбайся", "привычек",
            "круиз", "заменил", "в реальности жизни", "как играть",
            "разоблачение", "интервью", "влог", "let's play", "летсплей",
            "shorts", "шортс", "реакция", "топ-", "подборка", "что если",
        ]
        return any(w in t for w in junk)

    def _filter_junk(self) -> int:
        """Убирает мусор, который модель всё же пропустила в вайбы."""
        removed = 0
        for state, tracks in list(self.vibe_db.items()):
            if not tracks:
                continue
            keep = [t for t in tracks if not self._is_junk(t.get("title", ""))]
            removed += len(tracks) - len(keep)
            self.vibe_db[state] = keep
        if removed:
            print(f"🧹 [Taste Profile AI] Убрано не-музыкальных роликов: {removed}")
        return removed

    def _absorb_leftovers(self, leftovers: List[Dict[str, str]]) -> None:
        """
        Раскидывает нераспределённое по ключевым словам.

        Важно: сначала жёсткая проверка на 'энергичность', и только потом
        всё остальное уходит в CALM. Раньше всё, что не попало под
        keywords, падало в CALM - отсюда и 40 треков в одной корзине.
        """
        combat_words = ["phonk", "metal", "rock", "doom", "faradenza",
                        "cannibalism", "hard", "dark", "trap", "edm", "rave",
                        "aggressive", "drift", "burn", "war", "fight", "rage",
                        "energy", "power", "gym", "кринж", "злой", "агрессия"]
        death_words = ["sad", "dead", "slowed", "despair", "реверб", "ballad",
                       "alone", "lonely", "crying", "heart", "боли", "тоска",
                       "груст", "плак", "одинок", "мрачно"]
        victory_words = ["victory", "win", "epic", "победа", "хит", "hero",
                         "triumph", "champion", "win", "побед", "герой",
                         "торжество", "фанфар"]
        junk_words = ["блог", "обзор", "привычек", "круиз", "заменил",
                      "стартап", "будни", "улыбайся", "флешмоб", "мем",
                      "подкаст", "гайд", "челлендж", "разбор"]

        for track in leftovers:
            title = track.get("title", "").lower()
            if any(w in title for w in junk_words):
                continue
            if any(w in title for w in combat_words):
                self.vibe_db.setdefault(STATE_COMBAT, []).append(track)
            elif any(w in title for w in death_words):
                self.vibe_db.setdefault(STATE_DEATH, []).append(track)
            elif any(w in title for w in victory_words):
                self.vibe_db.setdefault(STATE_VICTORY, []).append(track)
            else:
                self.vibe_db.setdefault(STATE_CALM, []).append(track)

    def _fallback_keyword_categorization(self, raw_tracks: List[Dict[str, str]]) -> Dict[str, Any]:
        """
        Запасная локальная сортировка - работает без интернета и без нейросети.

        Используется, если ни Ollama, ни Gemini недоступны.
        """
        print("🛠️ [Taste Profile AI] Локальная сортировка по ключевым словам")
        for state in (STATE_CALM, STATE_COMBAT, STATE_DEATH, STATE_VICTORY,
                      STATE_DEFEAT):
            self.vibe_db[state] = []
        self._absorb_leftovers(raw_tracks)
        self._save_vibe_db()
        return self.vibe_db

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
          - ночью для боя берём более спокойный трек (приоритет DEATH/calm-подобные);
          - если в нужной категории ничего нет, пробуем соседние;
          - если база пуста — возвращаем None (вызывающий код это обработает).
        """
        # Перечитываем базу с диска, чтобы подхватить результаты новой синхронизации
        self.vibe_db = self._load_vibe_db()

        # Порядок кандидатов: сначала точный вайб, потом осмысленные запасные.
        if is_night and state == STATE_COMBAT:
            candidates = [STATE_CALM, STATE_DEATH, STATE_COMBAT]
        elif state == STATE_DEFEAT:
            # Проигрыш - тот же финальный момент матча, что и победа.
            # Отдельной категории DEFEAT в сортировке нет, берём VICTORY.
            candidates = [STATE_VICTORY, STATE_DEATH, STATE_CALM]
        elif state == STATE_CALM:
            # CALM - самая большая категория, но если её нет,
            # лучше DEATH, чем эпичный трек в фарме.
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