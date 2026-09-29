"""
Центральный файл конфигурации приложения SmartMusic.
Здесь хранятся порты, константы состояний и базовые настройки.

ВАЖНО: секреты (ключи, токены) НЕ хранятся в коде.
Они читаются из переменных окружения или файла .env.
"""

import os
from pathlib import Path

# Загружаем переменные из файла .env, если он есть
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("⚠️ [Config] Не установлен python-dotenv. Чтение .env отключено.")


ROOT_DIR: Path = Path(__file__).resolve().parent

# --- Кодировка вывода в консоль ---
# На русской Windows консоль по умолчанию cp1251 (cp866), и обычный print()
# с эмодзи падает с UnicodeEncodeError. Принудительно переключаем на UTF-8
# с заменой непечатаемых символов, чтобы программа не падала.
import sys as _sys

for _stream in ("stdout", "stderr"):
    _obj = getattr(_sys, _stream, None)
    if _obj is not None and hasattr(_obj, "reconfigure"):
        try:
            _obj.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


# Настройки GSI Сервера
GSI_SERVER_HOST: str = "127.0.0.1"
GSI_SERVER_PORT: int = 4000

# Константы состояний игры (Вайб-Матрица)
STATE_CALM: str = "CALM"          # Спокойный фарм, лес, перебежки
STATE_COMBAT: str = "COMBAT"      # Резкая драка, падение HP
STATE_DEATH: str = "DEATH"        # Герой мертв (в таверне)
STATE_VICTORY: str = "VICTORY"    # Победа
STATE_DEFEAT: str = "DEFEAT"      # Поражение
STATE_IDLE: str = "IDLE"          # Вне матча / Главное меню

# Настройки защиты от спама (Гистерезис)
DEFAULT_COOLDOWN_SECONDS: int = 30

# Настройки Night Governor (Ночной режим)
NIGHT_MODE_ENABLED: bool = True     # Включен ли ночной лимитер
NIGHT_START_HOUR: int = 23           # Час начала ночи (23:00)
NIGHT_END_HOUR: int = 7             # Час окончания ночи (07:00)
NIGHT_VOLUME_FACTOR: float = 0.6    # Множитель громкости ночью (60% от нормальной)

# Настройки YouTube / YouTube Music OAuth2
# Файл для сохранения авторизационных токенов (секрет!)
YOUTUBE_TOKENS_FILE: str = os.getenv("YOUTUBE_TOKEN_FILE", "token.json")

# Файл с OAuth-данными приложения из Google Cloud Console (секрет!)
GOOGLE_CLIENT_SECRETS_FILE: str = os.getenv(
    "GOOGLE_CLIENT_SECRETS_FILE", "client_secret.json"
)

# Scope (права доступа): управление YouTube аккаунтом и плейлистами
YOUTUBE_SCOPES: list[str] = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl"
]

# Google OAuth2 Endpoints (официальные эндпоинты авторизации Google)
GOOGLE_AUTH_URL: str = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL: str = "https://oauth2.googleapis.com/token"

# --------------------------------------------------------------------------
# Настройки ИИ (локальная модель через Ollama)
# --------------------------------------------------------------------------

# Адрес демона Ollama
OLLAMA_HOST: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")

# Основная модель. Скачать один раз:  ollama pull qwen2.5:3b
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")

# Сколько лайкнутых треков забирать с YouTube за один раз
MAX_LIKED_TRACKS: int = 75