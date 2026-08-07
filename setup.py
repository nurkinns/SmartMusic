import os

# Список всех папок
folders = [
    "assets/styles",
    "assets/icons",
    "src/ui/components",
    "src/auth",
    "src/ai",
    "src/triggers",
    "src/player",
]

# Список пустых файлов для создания
files = [
    "src/__init__.py",
    "src/ui/__init__.py",
    "src/ui/main_window.py",
    "src/auth/spotify_auth.py",
    "src/auth/youtube_oauth.py",
    "src/ai/taste_profile.py",
    "src/ai/dj_brain.py",
    "src/triggers/dota_gsi.py",
    "src/triggers/voice_listener.py",
    "src/player/spotify_control.py",
    "src/player/media_keys.py",
    "main.py",
    "config.py",
    "requirements.txt",
    ".gitignore"
]

# Создаем папки
for folder in folders:
    os.makedirs(folder, exist_ok=True)

# Создаем пустые файлы
for file in files:
    if not os.path.exists(file):
        with open(file, "w", encoding="utf-8") as f:
            pass

print("🚀 Каркас проекта dota2_cyberwave успешно создан!")