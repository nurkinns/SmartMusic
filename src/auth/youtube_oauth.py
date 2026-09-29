import sys
import json
import webbrowser
from pathlib import Path
from typing import Optional, Dict, Any
import requests

# Автоматически определяем корень проекта (SmartMusic/)
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import (
    YOUTUBE_TOKENS_FILE,
    YOUTUBE_SCOPES,
    GOOGLE_AUTH_URL,
    GOOGLE_TOKEN_URL,
)

# Путь к файлу сохранения токенов в корне проекта
TOKENS_PATH = ROOT_DIR / YOUTUBE_TOKENS_FILE


class YouTubeOAuth:
    """
    Класс для авторизации пользователя в YouTube API через OAuth 2.0.
    Управляет получением, сохранением и авто-обновлением (refresh) токенов.
    """

    def __init__(self, client_id: str = "", client_secret: str = "", redirect_uri: str = "http://127.0.0.1:8080"):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.tokens: Dict[str, Any] = self._load_tokens()

    def _load_tokens(self) -> Dict[str, Any]:
        """Загружает сохраненные токены из локального JSON-файла, если он существует."""
        if TOKENS_PATH.exists():
            try:
                with open(TOKENS_PATH, "r", encoding="utf-8") as f:
                    print("🔑 [YouTube OAuth] Найден сохраненный токен авторизации.")
                    return json.load(f)
            except Exception as e:
                print(f"⚠️ [YouTube OAuth] Ошибка чтения токенов: {e}")
        return {}

    def _save_tokens(self, tokens_data: Dict[str, Any]) -> None:
        """Сохраняет полученные токены локально в JSON-файл."""
        self.tokens.update(tokens_data)
        try:
            with open(TOKENS_PATH, "w", encoding="utf-8") as f:
                json.dump(self.tokens, f, ensure_ascii=False, indent=4)
            print(f"💾 [YouTube OAuth] Токены успешно сохранены в {YOUTUBE_TOKENS_FILE}")
        except Exception as e:
            print(f"❌ [YouTube OAuth] Ошибка сохранения токенов: {e}")

    def get_auth_url(self) -> str:
        """Генерирует ссылку для входа пользователя через браузер."""
        scopes_str = " ".join(YOUTUBE_SCOPES)
        auth_url = (
            f"{GOOGLE_AUTH_URL}?"
            f"client_id={self.client_id}&"
            f"redirect_uri={self.redirect_uri}&"
            f"response_type=code&"
            f"scope={scopes_str}&"
            f"access_type=offline&"      # Требуется для получения refresh_token
            f"prompt=consent"            # Принудительное согласие для гарантированного refresh_token
        )
        return auth_url

    def open_browser_for_auth(self) -> None:
        """Автоматически открывает ссылку авторизации в браузере пользователя."""
        if not self.client_id:
            print("❌ [YouTube OAuth] Ошибка: Client ID не указан!")
            return

        url = self.get_auth_url()
        print("🌐 [YouTube OAuth] Переход в браузер для входа в Google/YouTube...")
        webbrowser.open(url)

    def exchange_code_for_tokens(self, auth_code: str) -> Optional[Dict[str, Any]]:
        """
        Обменивает полученный Authorization Code от Google на Access Token и Refresh Token.
        """
        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": auth_code,
            "grant_type": "authorization_code",
            "redirect_uri": self.redirect_uri,
        }

        try:
            response = requests.post(GOOGLE_TOKEN_URL, data=payload)
            if response.status_code == 200:
                data = response.json()
                self._save_tokens(data)
                print("✅ [YouTube OAuth] Авторизация успешно завершена!")
                return data
            else:
                print(f"❌ [YouTube OAuth] Ошибка обмена кода: {response.text}")
                return None
        except Exception as e:
            print(f"❌ [YouTube OAuth] Сетевая ошибка: {e}")
            return None

    def refresh_access_token(self) -> Optional[str]:
        """
        Обновляет просроченный Access Token с помощью Refresh Token без повторного входа в браузер.
        """
        refresh_token = self.tokens.get("refresh_token")
        if not refresh_token:
            print("⚠️ [YouTube OAuth] Refresh token отсутствует. Требуется повторная авторизация.")
            return None

        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }

        try:
            response = requests.post(GOOGLE_TOKEN_URL, data=payload)
            if response.status_code == 200:
                data = response.json()
                self._save_tokens(data)
                print("🔄 [YouTube OAuth] Access Token успешно обновлен!")
                return data.get("access_token")
            else:
                print(f"❌ [YouTube OAuth] Ошибка обновления токена: {response.text}")
                return None
        except Exception as e:
            print(f"❌ [YouTube OAuth] Сетевая ошибка при обновлении токена: {e}")
            return None

    def get_valid_access_token(self) -> Optional[str]:
        """Возвращает действующий Access Token или обновляет его, если нужно."""
        access_token = self.tokens.get("access_token")
        if access_token:
            return access_token
        return self.refresh_access_token()


# --- БЛОК ЛОКАЛЬНОГО ТЕСТИРОВАНИЯ МОДУЛЯ ---
if __name__ == "__main__":
    print("🧪 [Тест YouTube OAuth Модуля]...")
    
    # Фейковые данные для проверки структуры
    auth_manager = YouTubeOAuth(
        client_id="YOUR_GOOGLE_CLIENT_ID.apps.googleusercontent.com",
        client_secret="YOUR_GOOGLE_CLIENT_SECRET",
        redirect_uri="http://127.0.0.1:8080"
    )

    print(f"🔗 Сгенерированная ссылка входа:\n{auth_manager.get_auth_url()}\n")
    print(f"📁 Файл токенов существует? {'ДА' if TOKENS_PATH.exists() else 'НЕТ'}")