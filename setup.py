import os
import sys
import winreg
from pathlib import Path

GSI_CONFIG_CONTENT = """ "SmartMusic Integration v1.0"
{
    "uri"           "http://127.0.0.1:4000/"
    "timeout"       "5.0"
    "buffer"        "0.1"
    "throttle"      "0.1"
    "heartbeat"     "30.0"
    "data"
    {
        "provider"  "1"
        "map"       "1"
        "hero"      "1"
        "player"    "1"
    }
}
"""

def find_steam_path() -> Path | None:
    """Ищет путь к установке Steam через реестр Windows."""
    registry_keys = [
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
    ]
    
    for hkey, subkey in registry_keys:
        try:
            with winreg.OpenKey(hkey, subkey) as key:
                steam_path, _ = winreg.QueryValueEx(key, "SteamPath")
                if steam_path and os.path.exists(steam_path):
                    return Path(steam_path)
        except Exception:
            continue
    return None

def find_dota_cfg_folder() -> Path | None:
    """Находит папку cfg/gamestate_integration в директории Dota 2."""
    steam_path = find_steam_path()
    candidate_dota_paths = []

    if steam_path:
        # Основная библиотека Steam
        candidate_dota_paths.append(steam_path / "steamapps" / "common" / "dota 2 beta")
        
        # Сканируем libraryfolders.vdf на случай если Дота на диске D:, E: и т.д.
        vdf_path = steam_path / "steamapps" / "libraryfolders.vdf"
        if vdf_path.exists():
            try:
                with open(vdf_path, "r", encoding="utf-8") as f:
                    for line in f:
                        if "path" in line:
                            parts = line.split('"')
                            if len(parts) >= 4:
                                lib_path = Path(parts[3].replace("\\\\", "\\"))
                                dota_p = lib_path / "steamapps" / "common" / "dota 2 beta"
                                candidate_dota_paths.append(dota_p)
            except Exception:
                pass

    # Популярные базовые пути на разных дисках
    for drive in ["C", "D", "E", "F", "G"]:
        candidate_dota_paths.append(Path(f"{drive}:/Program Files (x86)/Steam/steamapps/common/dota 2 beta"))
        candidate_dota_paths.append(Path(f"{drive}:/SteamLibrary/steamapps/common/dota 2 beta"))
        candidate_dota_paths.append(Path(f"{drive}:/Games/SteamLibrary/steamapps/common/dota 2 beta"))

    # Проверяем реальное существование папки
    for dota_path in candidate_dota_paths:
        if dota_path.exists() and (dota_path / "game" / "dota").exists():
            return dota_path / "game" / "dota" / "cfg" / "gamestate_integration"

    return None

def main():
    """Ищет папку с установленной Dota 2, или предлагает пользователю найти её самостоятельно, создает конфигурационный файл"""
    print("🛠️  [SmartMusic Setup] Автоматическая настройка Dota 2 GSI...")
    
    cfg_dir = find_dota_cfg_folder()
    
    if not cfg_dir:
        print("\n❌ Не удалось автоматически найти папку с установленной Dota 2.")
        manual_path = input("Введи путь к папке 'dota 2 beta' вручную (или Enter для отмены): ").strip()
        if manual_path:
            cfg_dir = Path(manual_path) / "game" / "dota" / "cfg" / "gamestate_integration"
        else:
            print("Отмена установки.")
            sys.exit(1)

    try:
        # Создаем папку, если ее не существовало
        cfg_dir.mkdir(parents=True, exist_ok=True)
        
        file_path = cfg_dir / "gamestate_integration_smartmusic.cfg"
        
        # Записываем CFG файл
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(GSI_CONFIG_CONTENT)
            
        print("\n✅ УСПЕШНО! Конфигурационный файл GSI создан!")
        print(f"📍 Путь: {file_path}")
        print("\n🎮 Теперь Dota 2 умеет автоматически отправлять события в наш SmartMusic!")
        
    except Exception as error:
        print(f"\n❌ Ошибка создания файла: {error}")

if __name__ == "__main__":
    main()