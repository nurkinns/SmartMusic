from PyQt6.QtCore import QObject, pyqtSignal

class GSISignals(QObject):
    """
    Сигналы для передачи событий из FastAPI в UI.
    """
    # Сигнал передает: (state, action_text, is_night, volume_factor)
    state_changed = pyqtSignal(str, str, bool, float)
    # Что DJ Brain поставил: (title, reason)
    #
    # Отдельный сигнал, потому что state_changed приходит на КАЖДЫЙ пакет
    # от игры, а трек меняется редко. Если бы трек ехал в том же сигнале,
    # подпись перерисовывалась бы десятки раз в секунду впустую.
    # title - пустая строка, если не играет ничего.
    track_changed = pyqtSignal(str, str)

# Единый экземпляр сигналов для всего приложения
gsi_signals = GSISignals()