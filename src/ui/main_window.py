import sys
import json
from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QStackedWidget,
    QSystemTrayIcon,
    QMenu,
    QListWidget,
    QStyle,
    QMessageBox
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QAction

# Определяем путь к корню проекта (SmartMusic/)
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.triggers.dota_gsi import start_gsi_server
from src.ui.signals import gsi_signals
from src.auth.youtube_auth import YouTubeAuthManager
from src.ai.taste_profile import TasteProfileAI
from config import MAX_LIKED_TRACKS


class GSIServerWorker(QThread):
    """Фоновый поток для запуска HTTP-сервера GSI."""
    def run(self):
        print("🧵 [QThread] Запуск фонового потока GSI Сервера...")
        try:
            start_gsi_server()
        except Exception as e:
            # Раньше исключение просто глоталось и UI вечно показывал
            # "Ожидание подключения Dota 2..." без объяснений.
            print(f"❌ [GSI Server] Не удалось запустить: {e}")
            print("   Возможно, порт 4000 уже занят другим программами.")


class SyncWorker(QThread):
    """
    Фоновая синхронизация: YouTube API + разбор треков нейросетью.

    Нужна потому, что и запрос к YouTube, и ответ модели занимают
    десятки секунд. В главном потоке окно бы замерло.
    """
    finished_ok = pyqtSignal(int)
    failed = pyqtSignal(str)

    def __init__(self, yt_auth, ai_analyzer, max_results: int = MAX_LIKED_TRACKS):
        super().__init__()
        self.yt_auth = yt_auth
        self.ai_analyzer = ai_analyzer
        self.max_results = max_results

    def run(self):
        try:
            print("🔄 Собираю треки с YouTube...")
            # Сначала плейлисты (они чище), лайки - только как добавка.
            tracks = self.yt_auth.collect_tracks(max_total=self.max_results)
            if not tracks:
                self.failed.emit("Не удалось получить ни одного трека.\n"
                                 "Проверь, что плейлист не пуст и треки прошли фильтр.")
                return
            print(f"🤖 Разбираю {len(tracks)} треков по вайбам...")
            self.ai_analyzer.categorize_tracks(tracks)
            self.finished_ok.emit(len(tracks))
        except Exception as e:
            self.failed.emit(f"{type(e).__name__}: {str(e)[:300]}")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.yt_auth = YouTubeAuthManager()
        self.ai_analyzer = TasteProfileAI()
        self._sync_thread = None

        self.init_ui()
        self.init_tray()
        self.connect_signals()
        self.start_gsi_thread()

    def init_ui(self):
        self.setWindowTitle("SmartMusic — Dota 2 Cyberwave")
        self.resize(850, 520)
        self.setMinimumSize(750, 480)

        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)

        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ---------------- 1. ЛЕВЫЙ САЙДБАР ----------------
        sidebar = QFrame()
        sidebar.setFixedWidth(210)
        sidebar.setStyleSheet("background-color: #11141d; border-right: 1px solid #1e2330;")
        
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(15, 20, 15, 20)

        title_label = QLabel("SmartMusic 🎧")
        title_label.setStyleSheet("color: #7d5fff; font-size: 20px; font-weight: bold;")
        sidebar_layout.addWidget(title_label)
        sidebar_layout.addSpacing(25)

        self.btn_dashboard = QPushButton("🎮 Дашборд")
        self.btn_playlists = QPushButton("🎵 Вайб-Матрица")
        self.btn_settings = QPushButton("⚙️ Настройки")

        self.nav_buttons = [self.btn_dashboard, self.btn_playlists, self.btn_settings]

        for idx, btn in enumerate(self.nav_buttons):
            btn.setCheckable(True)
            btn.setStyleSheet("""
                QPushButton {
                    color: #a4b0be;
                    background-color: transparent;
                    border: none;
                    text-align: left;
                    padding: 12px;
                    font-size: 14px;
                    font-weight: 500;
                    border-radius: 8px;
                }
                QPushButton:hover {
                    background-color: #1e2330;
                    color: #ffffff;
                }
                QPushButton:checked {
                    background-color: #7d5fff;
                    color: #ffffff;
                    font-weight: bold;
                }
            """)
            btn.clicked.connect(lambda _, i=idx: self.switch_page(i))
            sidebar_layout.addWidget(btn)

        self.btn_dashboard.setChecked(True)
        sidebar_layout.addStretch()

        # ---------------- 2. ЦЕНТРАЛЬНЫЙ СТЭК СТРАНИЦ ----------------
        self.stacked_widget = QStackedWidget()
        self.stacked_widget.setStyleSheet("background-color: #0f121a;")

        self.page_dashboard = self.create_dashboard_page()
        self.page_matrix = self.create_matrix_page()
        self.page_settings = self.create_settings_page()

        self.stacked_widget.addWidget(self.page_dashboard)
        self.stacked_widget.addWidget(self.page_matrix)
        self.stacked_widget.addWidget(self.page_settings)

        main_layout.addWidget(sidebar)
        main_layout.addWidget(self.stacked_widget)

    def create_dashboard_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(35, 30, 35, 30)

        dash_title = QLabel("Монитор игры")
        dash_title.setStyleSheet("color: #ffffff; font-size: 22px; font-weight: bold;")
        layout.addWidget(dash_title)
        layout.addSpacing(15)

        self.status_card = QFrame()
        self.status_card.setStyleSheet("background-color: #161b26; border-radius: 12px; border: 1px solid #232a3b;")
        card_layout = QVBoxLayout(self.status_card)
        card_layout.setContentsMargins(20, 20, 20, 20)

        self.status_title = QLabel("СТАТУС ИГРЫ")
        self.status_title.setStyleSheet("color: #747d8c; font-size: 12px; font-weight: bold;")
        
        self.status_val = QLabel("🟢 Ожидание подключения Dota 2...")
        self.status_val.setStyleSheet("color: #2ed573; font-size: 18px; font-weight: bold; margin-top: 5px;")

        self.night_val = QLabel("🌙 Night Governor: Дневной режим (100% громкость)")
        self.night_val.setStyleSheet("color: #a4b0be; font-size: 13px; margin-top: 10px;")

        card_layout.addWidget(self.status_title)
        card_layout.addWidget(self.status_val)
        card_layout.addWidget(self.night_val)

        layout.addWidget(self.status_card)
        layout.addStretch()
        return page

    def create_matrix_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(35, 30, 35, 30)

        title = QLabel("ИИ Вайб-Матрица")
        title.setStyleSheet("color: #ffffff; font-size: 22px; font-weight: bold;")
        layout.addWidget(title)

        subtitle = QLabel("Автоматически отсортированные треки из вашей медиатеки")
        subtitle.setStyleSheet("color: #a4b0be; font-size: 13px; margin-bottom: 10px;")
        layout.addWidget(subtitle)

        self.track_list_widget = QListWidget()
        self.track_list_widget.setStyleSheet("""
            QListWidget {
                background-color: #161b26;
                border: 1px solid #232a3b;
                border-radius: 10px;
                color: #f1f2f6;
                padding: 10px;
                font-size: 14px;
            }
            QListWidget::item {
                padding: 8px;
                border-bottom: 1px solid #1e2330;
            }
        """)
        layout.addWidget(self.track_list_widget)

        self.reload_vibe_db_view()
        return page

    def create_settings_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(35, 30, 35, 30)

        title = QLabel("Настройки SmartMusic")
        title.setStyleSheet("color: #ffffff; font-size: 22px; font-weight: bold;")
        layout.addWidget(title)
        layout.addSpacing(15)

        # Карточка YouTube Аккаунта
        yt_card = QFrame()
        yt_card.setStyleSheet("background-color: #161b26; border-radius: 12px; border: 1px solid #232a3b;")
        yt_layout = QVBoxLayout(yt_card)
        yt_layout.setContentsMargins(20, 20, 20, 20)

        card_title = QLabel("АККАУНТ YOUTUBE")
        card_title.setStyleSheet("color: #747d8c; font-size: 12px; font-weight: bold;")
        yt_layout.addWidget(card_title)

        self.yt_status_label = QLabel()
        self.update_yt_status_label()
        yt_layout.addWidget(self.yt_status_label)

        btn_box = QHBoxLayout()
        btn_box.setContentsMargins(0, 10, 0, 0)

        self.btn_yt_auth = QPushButton("🔑 Подключить YouTube")
        self.btn_yt_auth.setStyleSheet("""
            QPushButton {
                background-color: #7d5fff;
                color: #ffffff;
                border: none;
                padding: 10px 18px;
                border-radius: 6px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #6c47ff;
            }
        """)
        self.btn_yt_auth.clicked.connect(self.handle_yt_auth)

        self.btn_sync = QPushButton("🔄 Синхронизировать ИИ")
        self.btn_sync.setStyleSheet("""
            QPushButton {
                background-color: #2ed573;
                color: #ffffff;
                border: none;
                padding: 10px 18px;
                border-radius: 6px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #26af5f;
            }
        """)
        self.btn_sync.clicked.connect(self.handle_sync_tracks)

        btn_box.addWidget(self.btn_yt_auth)
        btn_box.addWidget(self.btn_sync)
        btn_box.addStretch()

        yt_layout.addLayout(btn_box)
        layout.addWidget(yt_card)

        layout.addStretch()
        return page

    def update_yt_status_label(self):
        if self.yt_auth.is_authenticated():
            self.yt_status_label.setText("🟢 Статус: Аккаунт YouTube успешно подключен!")
            self.yt_status_label.setStyleSheet("color: #2ed573; font-size: 15px; font-weight: bold; margin-top: 5px;")
        else:
            self.yt_status_label.setText("🔴 Статус: Аккаунт не подключен (требуется вход)")
            self.yt_status_label.setStyleSheet("color: #ff4757; font-size: 15px; font-weight: bold; margin-top: 5px;")

    def handle_yt_auth(self):
        """Обработчик нажатия кнопки авторизации."""
        success = self.yt_auth.authenticate()
        self.update_yt_status_label()

        if success:
            QMessageBox.information(self, "Успех", "Вы успешно вошли в аккаунт YouTube!")
            self.handle_sync_tracks()
        else:
            QMessageBox.warning(self, "Ошибка", "Не удалось войти в аккаунт. Проверьте файл client_secret.json.")

    def handle_sync_tracks(self):
        """
        Скачивает лайкнутые треки и разложила по вайбам.

        Вся работа уходит в фоновый поток: локальная модель думает
        20-60 секунд, а раньше окно на это время полностью замирало.
        """
        if self._sync_thread is not None and self._sync_thread.isRunning():
            QMessageBox.information(self, "Уже выполняется",
                                    "Предыдущая синхронизация ещё идёт, подожди.")
            return

        if not self.yt_auth.is_authenticated():
            QMessageBox.warning(self, "Внимание", "Сначала подключите YouTube аккаунт!")
            return

        self.btn_sync.setEnabled(False)
        self.btn_sync.setText("⏳ Обработка...")

        self._sync_thread = SyncWorker(self.yt_auth, self.ai_analyzer)
        self._sync_thread.finished_ok.connect(self.on_sync_finished)
        self._sync_thread.failed.connect(self.on_sync_failed)
        self._sync_thread.finished.connect(self._reset_sync_button)
        self._sync_thread.start()

    def _reset_sync_button(self):
        self.btn_sync.setEnabled(True)
        self.btn_sync.setText("🔄 Синхронизировать ИИ")

    def on_sync_finished(self, count: int):
        self.reload_vibe_db_view()
        QMessageBox.information(
            self, "Готово!",
            f"ИИ разложил {count} треков по Вайб-Матрице."
        )

    def on_sync_failed(self, message: str):
        QMessageBox.warning(self, "Ошибка синхронизации", message)

    def reload_vibe_db_view(self):
        self.track_list_widget.clear()

        # Раньше был относительный путь - при запуске из другой папки
        # база не находилась и показывалось "база пуста".
        db_file = ROOT_DIR / "user_vibe_db.json"
        if not db_file.exists():
            self.track_list_widget.addItem("⚠️ База треков пока пуста. Запустите синхронизацию в Настройках.")
            return

        try:
            with open(db_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            for state, tracks in data.items():
                if tracks:
                    self.track_list_widget.addItem(f"=== 📁 Категория: {state} ({len(tracks)} треков) ===")
                    for track in tracks:
                        title = track.get("title", "Без названия")
                        artist = track.get("artist", "Неизвестен")
                        self.track_list_widget.addItem(f"  • {title} — {artist}")
        except Exception as e:
            self.track_list_widget.addItem(f"❌ Ошибка загрузки базы: {e}")

    def switch_page(self, index: int):
        for idx, btn in enumerate(self.nav_buttons):
            btn.setChecked(idx == index)
        self.stacked_widget.setCurrentIndex(index)
        if index == 1:
            self.reload_vibe_db_view()

    def init_tray(self):
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))

        tray_menu = QMenu()
        show_action = QAction("Открыть SmartMusic", self)
        show_action.triggered.connect(self.showNormal)
        
        quit_action = QAction("Выйти из приложения", self)
        quit_action.triggered.connect(QApplication.instance().quit)

        tray_menu.addAction(show_action)
        tray_menu.addSeparator()
        tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.show()

    def closeEvent(self, event):
        if self.tray_icon.isVisible():
            self.hide()
            event.ignore()

    def connect_signals(self):
        gsi_signals.state_changed.connect(self.update_game_status)

    def update_game_status(self, state: str, action: str, is_night: bool, volume: float):
        state_map = {
            "CALM": ("🌾 Спокойный фарм / Лес", "#2ed573"),
            "COMBAT": ("⚔️ Идет драка!", "#ff4757"),
            "DEATH": ("☠️ Герой в таверне", "#ffa502"),
            "VICTORY": ("🏆 Победа!", "#eccc68"),
            "DEFEAT": ("💔 Поражение", "#ff6b81"),
            "IDLE": ("💤 Вне игры / Меню", "#a4b0be")
        }

        text, color = state_map.get(state, (f"Состояние: {state}", "#ffffff"))
        self.status_val.setText(text)
        self.status_val.setStyleSheet(f"color: {color}; font-size: 18px; font-weight: bold; margin-top: 5px;")

        if is_night:
            self.night_val.setText(f"🌙 Night Governor: АКТИВЕН ({int(volume * 100)}% громкости)")
            self.night_val.setStyleSheet("color: #7d5fff; font-size: 13px; font-weight: bold; margin-top: 10px;")
        else:
            self.night_val.setText("☀️ Night Governor: Дневной режим (100% громкость)")
            self.night_val.setStyleSheet("color: #a4b0be; font-size: 13px; margin-top: 10px;")

    def start_gsi_thread(self):
        self.gsi_thread = GSIServerWorker()
        self.gsi_thread.start()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())