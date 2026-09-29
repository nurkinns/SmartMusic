import os
import re
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Имена файлов с секретами берём из config.py (который читает .env)
from config import GOOGLE_CLIENT_SECRETS_FILE, YOUTUBE_TOKENS_FILE
from src.ai import track_filter

CLIENT_SECRETS_FILE = ROOT_DIR / GOOGLE_CLIENT_SECRETS_FILE
TOKEN_FILE = ROOT_DIR / YOUTUBE_TOKENS_FILE

SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]

# --------------------------------------------------------------------------
# Старый список стоп-слов удалён намеренно.
# Он проверял только название ролика и поэтому пропускал половину мусора.
# Теперь фильтр живёт в src/ai/track_filter.py и смотрит ещё на категорию
# ролика, длительность и признаки прямых трансляций.
# --------------------------------------------------------------------------


def parse_iso8601_duration(duration_str: str) -> int:
    """Преобразует строку длительности YouTube (напр. PT4M30S, PT1H2M) в секунды."""
    pattern = re.compile(r'PT(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?')
    match = pattern.match(duration_str)
    if not match:
        return 0
    parts = match.groupdict()
    hours = int(parts['hours'] or 0)
    minutes = int(parts['minutes'] or 0)
    seconds = int(parts['seconds'] or 0)
    return hours * 3600 + minutes * 60 + seconds


class YouTubeAuthManager:
    def __init__(self):
        self.creds = None
        self._load_credentials()

    def _load_credentials(self):
        if TOKEN_FILE.exists():
            try:
                self.creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
            except Exception as e:
                print(f"⚠️ [YouTube Auth] Ошибка загрузки сохранённого токена: {e}")
                self.creds = None

    def is_authenticated(self) -> bool:
        if not self.creds:
            return False
        if self.creds.valid:
            return True
        if self.creds.expired and self.creds.refresh_token:
            try:
                self.creds.refresh(Request())
                with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                    f.write(self.creds.to_json())
                return True
            except Exception as e:
                print(f"⚠️ [YouTube Auth] Не удалось обновить токен: {e}")
                return False
        return False

    def authenticate(self) -> bool:
        """Запускает OAuth 2.0 вход через браузер."""
        if self.is_authenticated():
            print("✅ [YouTube Auth] Успешная авторизация по существующему токену.")
            return True

        if not CLIENT_SECRETS_FILE.exists():
            print(f"❌ [YouTube Auth] Файл {CLIENT_SECRETS_FILE.name} не найден!")
            return False

        try:
            flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS_FILE), SCOPES)
            self.creds = flow.run_local_server(port=0)
            with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                f.write(self.creds.to_json())
            print("🎉 [YouTube Auth] Успешный вход! Токен сохранён.")
            return True
        except Exception as e:
            print(f"❌ [YouTube Auth] Ошибка во время авторизации: {e}")
            return False

    # ------------------------------------------------------------------ #
    #  Сбор треков
    # ------------------------------------------------------------------ #
    def _keep(self, item: Dict[str, Any], source: str) -> Optional[Dict[str, Any]]:
        """
        Прогоняет ролик через жёсткий фильтр и превращает в трек.

        Возвращает None, если это точно не музыка. Причина отказа
        печатается - так видно, что именно система считает мусором.
        """
        ok, why = track_filter.judge(item)
        snippet = item.get("snippet", {}) or {}
        title = snippet.get("title", "")
        if not ok:
            print(f"  ⛔ [{source}] отброшен: {title[:52]} — {why}")
            return None

        return {
            "id": item.get("id", ""),
            "title": title,
            "artist": snippet.get("channelTitle", ""),
            "duration_sec": track_filter._duration_of(item) or 0,
            "category_id": track_filter.category_of(item),
            "source": source,
        }

    def fetch_liked_tracks(self, max_results: int = 75) -> List[Dict[str, Any]]:
        """
        Лайкнутые видео, прошедшие жёсткий фильтр.

        Лайки - самый грязный источник: на реальных данных из 50 лайков
        доходит около 15. Используется только как дополнение к плейлистам.
        """
        if not self.is_authenticated():
            print("⚠️ [YouTube Auth] Пользователь не авторизован.")
            return []
        try:
            youtube = build("youtube", "v3", credentials=self.creds)
            items: List[dict] = []
            page_token = None
            while len(items) < max_results:
                page_size = min(50, max_results - len(items))
                request = youtube.videos().list(
                    part="snippet,contentDetails",
                    myRating="like",
                    maxResults=page_size,
                    pageToken=page_token
                )
                response = request.execute()
                items.extend(response.get("items", []))
                page_token = response.get("nextPageToken")
                if not page_token:
                    break

            print(f"❤️  [YouTube] Лайков получено: {len(items)}, проверяю...")
            out = []
            for it in items:
                track = self._keep(it, "лайки")
                if track:
                    out.append(track)
            print(f"❤️  [YouTube] Из лайков осталось треков: {len(out)}")
            return out
        except Exception as e:
            print(f"❌ [YouTube Auth] Ошибка при получении лайков: {e}")
            return []

    def list_my_playlists(self) -> List[Dict[str, Any]]:
        """Список плейлистов, созданных пользователем."""
        if not self.is_authenticated():
            return []
        try:
            youtube = build("youtube", "v3", credentials=self.creds)
            r = youtube.playlists().list(part="snippet,contentDetails",
                                        mine=True, maxResults=50).execute()
            return r.get("items", [])
        except Exception as e:
            print(f"❌ [YouTube Auth] Ошибка при получении плейлистов: {e}")
            return []

    def fetch_playlist_tracks(self, per_playlist: int = 50) -> List[Dict[str, Any]]:
        """
        Треки из собственных плейлистов YouTube.

        Это ЛУЧШИЙ источник вкуса из доступных. Человек, который создал
        плейлист "+вайб", положил туда именно то, что хочет слушать.
        Лайки такого смысла не имеют - там половина это шортсы и мемы.

        На реальных данных: плейлист даёт 95% чистых треков, лайки 30%.
        """
        if not self.is_authenticated():
            print("⚠️ [YouTube Auth] Пользователь не авторизован.")
            return []

        try:
            youtube = build("youtube", "v3", credentials=self.creds)
            playlists = self.list_my_playlists()
            if not playlists:
                print("ℹ️  [YouTube] Своих плейлистов нет - берём лайки.")
                return []

            out: List[Dict[str, Any]] = []
            for pl in playlists:
                title = pl.get("snippet", {}).get("title", "?")
                pid = pl["id"]
                try:
                    items = youtube.playlistItems().list(
                        part="snippet,contentDetails",
                        playlistId=pid, maxResults=per_playlist
                    ).execute().get("items", [])
                except Exception as e:
                    print(f"⚠️ [YouTube] Не прочитался плейлист «{title}»: {e}")
                    continue

                # Элемент плейлиста содержит только videoId. Реальные
                # метаданные (длительность, категория) лежат у самого
                # видео, поэтому нужен второй запрос пачками по 50.
                video_ids = [
                    i["contentDetails"]["videoId"] for i in items
                    if i.get("contentDetails", {}).get("videoId")
                ]
                print(f"🎼 [YouTube] Плейлист «{title}»: {len(video_ids)} видео, "
                      f"проверяю...")

                for i in range(0, len(video_ids), 50):
                    batch = video_ids[i:i + 50]
                    try:
                        detail = youtube.videos().list(
                            part="snippet,contentDetails",
                            id=",".join(batch)).execute().get("items", [])
                    except Exception as e:
                        print(f"⚠️ [YouTube] Ошибка запроса видео: {e}")
                        continue
                    for it in detail:
                        track = self._keep(it, f"плейлист «{title}»")
                        if track:
                            out.append(track)

            print(f"🎼 [YouTube] Из плейлистов набрано треков: {len(out)}")
            return out
        except Exception as e:
            print(f"❌ [YouTube Auth] Ошибка при сборе плейлистов: {e}")
            return []

    def collect_tracks(self, max_total: int = 75,
                       use_playlists: bool = True,
                       use_likes: bool = True) -> List[Dict[str, Any]]:
        """
        Собирает треки из всех источников и убирает дубли.

        Порядок источников неслучаен: сначала плейлисты (они чистые и
        осознанно собраны), лайки идут вторыми и только дополняют.
        Дубликаты по id видео отбрасываются - один и тот же трек может
        лежать и в плейлисте, и среди лайков.
        """
        collected: List[Dict[str, Any]] = []
        seen = set()

        def add(new: List[Dict[str, Any]]) -> None:
            for t in new:
                vid = t.get("id")
                if not vid or vid in seen:
                    continue
                seen.add(vid)
                collected.append(t)

        if use_playlists:
            add(self.fetch_playlist_tracks())
        if use_likes and len(collected) < max_total:
            add(self.fetch_liked_tracks(max_results=max_total))
        elif use_likes:
            print("ℹ️  [YouTube] Плейлистов хватило, лайки не нужны.")

        if len(collected) > max_total:
            print(f"✂️ [YouTube] Обрезаю {len(collected)} → {max_total}")
            collected = collected[:max_total]

        print(f"\n🎵 [YouTube] ИТОГО треков собрано: {len(collected)}")
        return collected
