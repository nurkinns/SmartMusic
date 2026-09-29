import os
import re
import json
import sys
from pathlib import Path
from typing import List, Dict, Any
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Имена файлов с секретами берём из config.py (который читает .env)
from config import GOOGLE_CLIENT_SECRETS_FILE, YOUTUBE_TOKENS_FILE

CLIENT_SECRETS_FILE = ROOT_DIR / GOOGLE_CLIENT_SECRETS_FILE
TOKEN_FILE = ROOT_DIR / YOUTUBE_TOKENS_FILE

SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]

# Стоп-слова для фильтрации разговорного и мемного контента
NON_MUSIC_KEYWORDS = [
    "стартап", "будни", "за 1 минуту", "за 10 минут", "за 5 минут", "мем", 
    "шортс", "shorts", "реакция", "подкаст", "обзор", "гайд", "привычек", 
    "круиз", "заменил", "челлендж", "топ 10", "топ 5", "дотерские будни",
    "в реальной жизни", "как играть", "разбор", "разоблачение", "флешмоб"
]


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

    def fetch_liked_tracks(self, max_results: int = 75) -> List[Dict[str, str]]:
        """
        Выкачивает лайкнутые видео пользователя.
        Фильтрует ролики по длительности (ОТ 60 СЕКУНД ДО 4.5 МИНУТ) и стоп-словам.

        YouTube API отдаёт максимум 50 видео за один запрос, поэтому при
        max_results больше 50 делаем несколько запросов по очереди.
        """
        if not self.is_authenticated():
            print("⚠️ [YouTube Auth] Пользователь не авторизован.")
            return []

        try:
            youtube = build("youtube", "v3", credentials=self.creds)

            # --- Собираем страницы лайков ---------------------------------
            items: List[dict] = []
            page_token = None
            while len(items) < max_results:
                # за страницу просим не больше 50 - это лимит API
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

            valid_tracks = []
            for item in items:
                snippet = item.get("snippet", {})
                content_details = item.get("contentDetails", {})
                
                duration_iso = content_details.get("duration", "PT0S")
                duration_sec = parse_iso8601_duration(duration_iso)

                title = snippet.get("title", "")
                channel = snippet.get("channelTitle", "")
                video_id = item.get("id", "")
                title_lower = title.lower()

                # 🛑 ФИЛЬТР 1: длительность от 45 сек до 5 минут
                # Раньше было 60..270 - с лимитом в 50 треков это ещё
                # работало, но при 75 треках треков банально не хватало:
                # короткие перебивки и длинные лоуфаи отсеивались.
                if duration_sec < 45 or duration_sec > 300:
                    print(f"⏭️ Пропуск по длительности ({duration_sec}s): {title}")
                    continue

                # 🛑 ФИЛЬТР 2: Проверка на не-музыкальные стоп-слова
                if any(kw in title_lower for kw in NON_MUSIC_KEYWORDS):
                    print(f"⏭️ Пропуск (разговорное/мем): {title}")
                    continue

                valid_tracks.append({
                    "id": video_id,
                    "title": title,
                    "artist": channel,
                    "duration_sec": duration_sec
                })

            print(f"🎵 Отобрано {len(valid_tracks)} полноценных треков.")
            return valid_tracks

        except Exception as e:
            print(f"❌ [YouTube Auth] Ошибка при получении треков: {e}")
            return []