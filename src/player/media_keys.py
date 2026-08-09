import sys
import time
import platform
import ctypes
from pynput.keyboard import Controller, Key

keyboard = Controller()

# Виртуальные коды мультимедийных клавиш Windows
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_PLAY_PAUSE = 0xB3

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002

def _send_win32_media_key(vk_code: int):
    """
    Отправка медиа-клавиши через вызов системной функции keybd_event.
    Передаёт сигнал с флагом KEYEVENTF_EXTENDEDKEY прямо в операционную систему.
    """
    try:
        # Нажатие клавиши
        ctypes.windll.user32.keybd_event(vk_code, 0, KEYEVENTF_EXTENDEDKEY, 0)
        time.sleep(0.05)
        # Отпускание клавиши
        ctypes.windll.user32.keybd_event(vk_code, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)
    except Exception as e:
        print(f"⚠️ Win32 API Error: {e}")

def press_next_track():
    """Симулирует нажатие клавиши 'Следующий трек'."""
    try:
        if platform.system() == "Windows":
            _send_win32_media_key(VK_MEDIA_NEXT_TRACK)
        
        keyboard.press(Key.media_next)
        keyboard.release(Key.media_next)
        print("⏭️ [Media Keys] Сигнал: Переключение на следующий трек")
    except Exception as e:
        print(f"❌ [Media Keys] Ошибка: {e}")


def press_previous_track():
    """Симулирует нажатие клавиши 'Предыдущий трек'."""
    try:
        if platform.system() == "Windows":
            _send_win32_media_key(VK_MEDIA_PREV_TRACK)
            
        keyboard.press(Key.media_previous)
        keyboard.release(Key.media_previous)
        print("⏮️ [Media Keys] Сигнал: Возврат на предыдущий трек")
    except Exception as e:
        print(f"❌ [Media Keys] Ошибка: {e}")


def press_play_pause():
    """Симулирует нажатие клавиши 'Play / Pause'."""
    try:
        if platform.system() == "Windows":
            _send_win32_media_key(VK_MEDIA_PLAY_PAUSE)
            
        keyboard.press(Key.media_play_pause)
        keyboard.release(Key.media_play_pause)
        print("⏯️ [Media Keys] Сигнал: Play / Pause")
    except Exception as e:
        print(f"❌ [Media Keys] Ошибка: {e}")

if __name__ == "__main__":
    print("🧪 [Тест] Проверяем прямую отправку медиа-клавиш...")
    print("Убедись, что запущен фоновый плеер или ПЛЕЙЛИСТ!\n")
    
    print("1️⃣ Пауза / Возобновление (через 2 секунды)...")
    time.sleep(2)
    press_play_pause()
    
    print("\n2️⃣ Повторный Play / Pause (через 2 секунды)...")
    time.sleep(2)
    press_play_pause()
    
    print("\n3️⃣ Переключение на следующий трек (через 2 секунды)...")
    time.sleep(2)
    press_next_track()