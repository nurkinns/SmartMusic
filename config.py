"""
Центральный файл конфигурации приложения SmartMusic.
Здесь хранятся порты, константы состояний и базовые настройки.
"""

# --- Настройки GSI Сервера ---
# Dota 2 отправляет данные на этот локальный адрес
GSI_SERVER_HOST: str = "127.0.0.1"
GSI_SERVER_PORT: int = 4000

# --- Константы состояний игры (Вайб-Матрица) ---
STATE_CALM: str = "CALM"          # Спокойный фарм, лес, перебежки
STATE_COMBAT: str = "COMBAT"      # Резкая драка, падение HP
STATE_DEATH: str = "DEATH"        # Герой мертв (в таверне)
STATE_VICTORY: str = "VICTORY"    # Победа
STATE_DEFEAT: str = "DEFEAT"      # Поражение
STATE_IDLE: str = "IDLE"          # Вне матча / Главное меню

# --- Настройки защиты от спама (Гистерезис) ---
DEFAULT_COOLDOWN_SECONDS: int = 15