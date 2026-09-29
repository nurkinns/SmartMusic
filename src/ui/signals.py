from PyQt6.QtCore import QObject, pyqtSignal

class GSISignals(QObject):
    """
    Сигналы для передачи событий из FastAPI в UI.
    """
    # Сигнал передает: (state, action_text, is_night, volume_factor)
    state_changed = pyqtSignal(str, str, bool, float)

# Единый экземпляр сигналов для всего приложения
gsi_signals = GSISignals()