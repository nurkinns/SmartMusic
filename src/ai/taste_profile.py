"""
taste_profile.py — ИИ-разбор понравившихся треков по вайб-категориям.

Работает на локальной модели через Ollama (см. src/ai/llm_providers.py).
Облачные ИИ не используются: недоступны из этого региона, ключей нет.
"""

import sys
import json
import random
from pathlib import Path
from typing import Dict, List, Any, Optional, Set, Tuple

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

VIBE_DB_PATH = ROOT_DIR / "user_vibe_db.json"


class TasteProfileAI:
    def __init__(self):
        self.vibe_db: Dict[str, List[Dict[str, str]]] = self._load_vibe_db()
        # id треков, которые уже заняли место в какой-то категории
        self._used_ids: Set[str] = set()
        # треки без вайба: модель не смогла определить жанр даже по
        # ключевым словам. Их раскидываем поровну в конце
        self._vague: List[Dict[str, Any]] = []

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

    def _classify_batch(self, provider, batch: List[Dict[str, Any]],
                        batch_no: int, total_batches: int
                        ) -> Tuple[Dict[str, List[Dict[str, Any]]], Set[int]]:
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
2. ГЛАВНОЕ. Если это НЕ музыка - обязательно поставь номер в "NOT_MUSIC".
   Не музыка это: разговорные ролики, мемы, стримы, интервью, подкасты,
   геймплей, shorts, реклама, обзоры, видео с говорящим человеком.
   Сомневаешься - ставь в NOT_MUSIC. Лучше пустой плейлист, чем мусор.
3. ВАЖНО: не пихай всё в CALM. CALM - это спокойная музыка. Если трек
   энергичный - это COMBAT, даже если жанр тебе незнаком.
4. Если трек подходит ни под одну категорию, но это музыка - пропусти его.

Треки:
{json.dumps(numbered, ensure_ascii=False)}

Ответ - ТОЛЬКО JSON такой формы (ключ "n" - номер трека из списка):
{{"CALM":[{{"n":0}}],"COMBAT":[{{"n":3}}],"DEATH":[{{"n":7}}],"VICTORY":[{{"n":9}}],"NOT_MUSIC":[{{"n":2}}]}}"""
        text = provider.generate(prompt)
        if not text:
            return {}, set()
        return self._parse_assignment(text, batch)

    @staticmethod
    def _parse_assignment(text: str, batch: List[Dict[str, Any]]
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
        not_music = {i for i in read_indices("NOT_MUSIC") if 0 <= i < len(batch)}

        for state in (STATE_CALM, STATE_COMBAT, STATE_DEATH, STATE_VICTORY):
            bucket = out.setdefault(state, [])
            for idx in read_indices(state):
                # жёсткая валидация: номер обязан существовать в пакете
                # и не должен быть помечен как не-музыка
                if 0 <= idx < len(batch) and idx not in not_music:
                    bucket.append(batch[idx])
        return out, not_music

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

        self._used_ids = set()
        self._vague = []
        rejected_by_ai: Set[str] = set()
        for i, batch in enumerate(batches, 1):
            result, not_music = self._classify_batch(provider, batch, i,
                                                     len(batches))
            for idx in not_music:
                rejected_by_ai.add(batch[idx].get("id"))
            for state, tracks in result.items():
                for t in tracks:
                    tid = t.get("id")
                    if tid in self._used_ids:   # защита от дублей между пакетами
                        continue
                    self._used_ids.add(tid)
                    self.vibe_db.setdefault(state, []).append(t)
            counts = {k: len(v) for k, v in result.items()}
            if not_music:
                counts["НЕ_МУЗЫКА"] = len(not_music)
            print(f"  пакет {i}/{len(batches)}: {counts}")

        if rejected_by_ai:
            print(f"🚫 [Taste Profile AI] Нейросеть отклонила как не-музыку: "
                  f"{len(rejected_by_ai)}")

        # Остаток - это то, что модель не распределила И не отвергла.
        unclassified = [t for t in raw_tracks
                        if t.get("id") not in self._used_ids
                        and t.get("id") not in rejected_by_ai]

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

        # --- 3. Добивочный проход по остатку --------------------------
        # Маленькая модель (3b) на больших пакетах молча теряет треть
        # треков. Остаётся делать вид, что всё распределено. Поэтому
        # остаток разбираем ещё одним проходом, но уже более простым
        # запросом - на маленьком пакете модель отвечает охотнее.
        if len(unclassified) > len(raw_tracks) * 0.2:
            print(f"🔁 [Taste Profile AI] Осталось {len(unclassified)} без "
                  f"категории, делаю добивочный проход")
            self._retry_leftovers(provider, unclassified)
            unclassified = [t for t in unclassified
                            if t.get("id") not in self._used_ids]

        # --- 4. Остаток раскидываем локальным алгоритмом --------------
        if unclassified:
            print(f"🔸 [Taste Profile AI] Не распределено: {len(unclassified)}, "
                  f"разбираю по ключевым словам")
            self._absorb_leftovers(unclassified)

        # --- 5. Выравниваем категории ---------------------------------
        # Порядок важен: сначала раскидываем треки без вайба, потом
        # чистим. Иначе часть разложенных уйдёт мимо чистки.
        self._balance_categories()

        # --- 6. Чистим мусор, который модель не отфильтровала --------
        # Последний рубеж: всё, что попадёт в файл, проходит здесь.
        self._filter_junk()

        self._save_vibe_db()

        print("\n✨ [Taste Profile AI] Готово! Раскладка по вайбам:")
        for state, tracks in self.vibe_db.items():
            print(f"   {state:<9} {len(tracks):>3}")
        return self.vibe_db

    def _retry_leftovers(self, provider, pool: List[Dict[str, Any]]) -> None:
        """
        Повторный проход по трекам, которые модель не распределила.

        Запрос намеренно проще: без нумерации пакетов, без длинных
        объяснений, маленькими порциями. Слабая модель на такой запрос
        отвечает заметно охотнее, чем на первый.
        """
        SMALL = 5
        for i in range(0, len(pool), SMALL):
            chunk = pool[i:i + SMALL]
            listing = [{"n": j, "title": t.get("title", ""),
                        "artist": t.get("artist", "")}
                       for j, t in enumerate(chunk)]
            prompt = f"""Для каждого трека выбери РОВНО одну категорию.
Все треки обязательно получить категорию, пропускать нельзя.

CALM = спокойно, lofi, джаз, чил, эмбиент
COMBAT = энергично, драйв, агрессия, phonk, metal, trap
DEATH = грустно, медленно, sad, slowed, реверб
VICTORY = эпично, героически, торжественно, победа

Треки: {json.dumps(listing, ensure_ascii=False)}

Ответ - ТОЛЬКО JSON, ключ "n" это номер трека:
{{"CALM":[{{"n":0}}],"COMBAT":[{{"n":1}}],"DEATH":[{{"n":2}}],"VICTORY":[{{"n":3}}]}}"""
            text = provider.generate(prompt)
            if not text:
                continue
            parsed, _ = self._parse_assignment(text, chunk)
            for state, tracks in parsed.items():
                for t in tracks:
                    if t.get("id") in self._used_ids:
                        continue
                    self._used_ids.add(t.get("id"))
                    self.vibe_db.setdefault(state, []).append(t)

    def _balance_categories(self) -> None:
        """
        Не даёт одной категории забрать всё.

        Даже с двумя проходами часть треков остаётся без вайба, и раньше
        они целиком падали в CALM - получалось «40 треков в одной
        корзине». Теперь треки без вайба раздаются по кругу между
        наименее заполненными категориями, чтобы плейлист не выродился
        в один жанр.

        Раскладку трогают только треки, у которых НЕТ ключевых слов -
        то есть модель не смогла определить их жанр вообще. Треки с
        явными признаками (phonk, sad, epic) остаются на своих местах.
        """
        if not self._vague:
            return
        vague = self._vague
        self._vague = []
        if not vague:
            return

        targets = [STATE_CALM, STATE_COMBAT, STATE_DEATH, STATE_VICTORY]
        print(f"⚖️  [Taste Profile AI] Распределяю {len(vague)} треков без вайба "
              f"поровну между категориями")
        for i, t in enumerate(vague):
            # берём самую неполную категорию на каждом шаге
            state = min(targets, key=lambda s: len(self.vibe_db.get(s, [])))
            self.vibe_db.setdefault(state, []).append(t)

    def _fill_empty_states(self, provider, pool: List[Dict[str, Any]],
                           empty_states: List[str]) -> None:
        """
        Отдельный проход: ищем треки под конкретные пустые категории.

        Здесь тоже есть NOT_MUSIC: этот проход идёт по остаткам, куда
        попадает в том числе то, что нейросеть раньше не поняла. Без
        явного запрета она охотно находит «эпичный» мем.
        """
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

ЖЁСТКОЕ ПРАВИЛО: если это НЕ музыка (мем, разговор, геймплей, shorts,
стрим, реклама) - поставь номер в "NOT_MUSIC". Не притягивай его за уши
ни под одну категорию.

Список:
{json.dumps(listing, ensure_ascii=False)}

Ответ - ТОЛЬКО JSON, ключ "n" это номер трека из списка:
{{"COMBAT":[{{"n":1}}],"VICTORY":[{{"n":4}}],"NOT_MUSIC":[{{"n":9}}]}}"""
        text = provider.generate(prompt)
        if not text:
            return
        parsed, not_music = self._parse_assignment(text, pool)
        for state, tracks in parsed.items():
            if state not in empty_states:
                continue
            for t in tracks:
                self.vibe_db.setdefault(state, []).append(t)

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

    def _absorb_leftovers(self, leftovers: List[Dict[str, Any]]) -> int:
        """
        Раскидывает то, что модель не распределила.

        КЛЮЧЕВОЕ ИЗМЕНЕНИЕ: раньше всё, что не подошло ни под одно
        ключевое слово, падало в CALM через безусловный else. Именно
        туда утекали мемы и шортсы - модель их пропускала, а код
        раскладывал в вайбы как ни в чём не бывало.

        Здесь два решения вместо одного. Первое: без прямого
        доказательства, что это музыка, трек выбрасывается.

        Второе: трек, который прошёл проверку на музыку, но для которого
        не нашлось ни одного ключевого слова, НЕ уходит в CALM. Он
        откладывается в self._vague и в конце раздаётся поровну между
        неполными категориями. Иначе опять получается 40 треков в
        одной корзине - хоть и без мусора, но всё равно бессмысленно.
        """
        combat_words = ["phonk", "metal", "rock", "doom", "faradenza",
                        "cannibalism", "hard", "dark", "trap", "edm", "rave",
                        "aggressive", "drift", "burn", "war", "fight", "rage",
                        "energy", "power", "gym", "кринж", "злой", "агрессия"]
        death_words = ["sad", "dead", "slowed", "despair", "реверб", "ballad",
                       "alone", "lonely", "crying", "heart", "боли", "тоска",
                       "груст", "плак", "одинок", "мрачно"]
        victory_words = ["victory", "win", "epic", "победа", "хит", "hero",
                         "triumph", "champion", "побед", "герой",
                         "торжество", "фанфар"]

        placed = 0
        dropped = 0
        for track in leftovers:
            title = track.get("title", "")
            artist = track.get("artist", "")
            t = title.lower()

            # 1. Последний рубеж обороны: жёсткий фильтр по названию.
            if not track_filter.looks_like_music(title, artist,
                                                track.get("category_id")):
                print(f"  ⛔ дубль-фильтр отбросил: {title[:52]}")
                dropped += 1
                continue

            # 2. Куда именно
            if any(w in t for w in combat_words):
                self.vibe_db.setdefault(STATE_COMBAT, []).append(track)
            elif any(w in t for w in death_words):
                self.vibe_db.setdefault(STATE_DEATH, []).append(track)
            elif any(w in t for w in victory_words):
                self.vibe_db.setdefault(STATE_VICTORY, []).append(track)
            else:
                # Музыка подтверждена, а жанр определить нечем.
                # Откладываем: раздадим поровну в конце.
                self._vague.append(track)
            placed += 1

        if dropped:
            print(f"🧹 [Taste Profile AI] Отброшено как не-музыка: {dropped}")
        return placed

    def _fallback_keyword_categorization(self, raw_tracks: List[Dict[str, str]]) -> Dict[str, Any]:
        """
        Запасная локальная сортировка - работает без интернета и без нейросети.

        Используется, если локальная модель недоступна.
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