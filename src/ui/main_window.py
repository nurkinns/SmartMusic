import sys
import json
from datetime import datetime
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
    QMessageBox,
    QSlider,
    QScrollArea
)
from PyQt6.QtCore import Qt, QThread, QTimer, QSignalBlocker, pyqtSignal
from PyQt6.QtGui import QAction, QShortcut, QKeySequence

# Определяем путь к корню проекта (SmartMusic/)
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.triggers.dota_gsi import start_gsi_server
from src.ui.signals import gsi_signals
from src.auth.youtube_auth import YouTubeAuthManager
from src.ai.taste_profile import TasteProfileAI
from src.ai import octave_tuning
from src.ai import vibe_classifier
import config
from config import MAX_LIKED_TRACKS
from config import PLAYER_MODE_DOWNLOAD, PLAYER_MODE_DIRECT

# Пояснение под каждый способ воспроизведения. Показывается в Настройках
# рядом с переключателем: разница между «скачать» и «ссылка» неочевидна,
# а полазить в код ради одного переключателя - перебор.
PLAYER_MODE_HELP = {
    PLAYER_MODE_DOWNLOAD:
        "Аудио скачивается один раз и играется с диска. Старт трека "
        "мгновенный, во время драки интернет не нужен.\n"
        "Цена: файлы на диске (около 3 МБ на трек) и первая синхронизация "
        "скачивает полноразмерное аудио вместо сжатого.",
    PLAYER_MODE_DIRECT:
        "VLC тянет звук прямо с YouTube, ничего не скачивая.\n"
        "Цена: старт трека зависит от сети (в бою это секунды тишины), "
        "а VLC не всегда умеет открывать YouTube-ссылки - тогда не "
        "заработает вовсе. Если основной способ не устроил - пробуйте этот.",
}


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
    Фоновая синхронизация: YouTube API + разбор треков по вайбам.

    Нужна потому, что и запрос к YouTube, и измерение звука занимают
    минуты. В главном потоке окно бы замерло.
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
            print(f"🔊 Измеряю {len(tracks)} треков по звуку...")
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

        # Панель плеера кладём НАД стеком, а не внутрь Дашборда.
        # Иначе кнопка исчезала бы при переходе в Настройки - ровно
        # тогда, когда человек переключает способ воспроизведения и
        # хочет сразу его послушать.
        content = QWidget()
        content.setStyleSheet("background-color: #0f121a;")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # Ставим ДО сборки панели: обработчик ползунка смотрит на этот
        # флаг, и он должен существовать уже на первом сигнале.
        self._player = None
        self._syncing_volume = False
        self._warned_no_vlc = False

        content_layout.addWidget(self.build_player_bar())

        self.stacked_widget = QStackedWidget()
        self.stacked_widget.setStyleSheet("background-color: #0f121a;")

        self.page_dashboard = self.create_dashboard_page()
        self.page_matrix = self.create_matrix_page()
        self.page_settings = self.create_settings_page()

        self.stacked_widget.addWidget(self.page_dashboard)
        self.stacked_widget.addWidget(self.page_matrix)
        self.stacked_widget.addWidget(self.page_settings)

        content_layout.addWidget(self.stacked_widget)

        main_layout.addWidget(sidebar)
        main_layout.addWidget(content)

        # Переключатель ночного режима рисуем после сборки страницы.
        self._refresh_night_toggle()

        # Плеер появляется позже окна: GSI-сервер поднимается в своём
        # потоке, и DJ Brain создаётся уже после отрисовки. Поэтому
        # сначала рисуем пустое состояние, а таймер ниже каждые полсекунды
        # подтягивает настоящее.
        self._refresh_player_bar()

        self.player_timer = QTimer(self)
        self.player_timer.timeout.connect(self._refresh_player_bar)
        self.player_timer.start(500)

    def build_player_bar(self) -> QWidget:
        """
        Верхняя панель: большая кнопка play/pause и ползунок громкости.

        Кнопка и ползунок живут здесь, а не в Настройках, потому что
        нужны постоянно: музыка играет сама, вайб меняет сам, и человек
        должен суметь её остановить и прибавить громкость, не заходя
        ни в какое меню.
        """
        bar = QFrame()
        bar.setFixedHeight(84)
        bar.setStyleSheet(
            "background-color: #11141d; border-bottom: 1px solid #1e2330;"
        )

        row = QHBoxLayout(bar)
        row.setContentsMargins(24, 12, 24, 12)
        row.setSpacing(18)
        row.addStretch(1)

        # ---------------- Кнопка play/pause ----------------
        self.btn_play = QPushButton("▶")
        self.btn_play.setFixedSize(64, 64)
        self.btn_play.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_play.setToolTip("Play / Pause (пробел)")
        self.btn_play.setStyleSheet("""
            QPushButton {
                background-color: #7d5fff;
                color: #ffffff;
                border: none;
                border-radius: 32px;
                font-size: 26px;
            }
            QPushButton:hover:disabled { background-color: #2a2145; }
            QPushButton:hover { background-color: #8f72ff; }
            QPushButton:pressed { background-color: #6a4ae0; }
            QPushButton:disabled { background-color: #1e2330; color: #4a5261; }
        """)
        self.btn_play.clicked.connect(self.toggle_play)
        row.addWidget(self.btn_play, 0, Qt.AlignmentFlag.AlignVCenter)

        # ---------------- Кнопки переключения трека ----------------
        # ⏮ ⏭ стоят ровно там, где раньше были подписи «0% / 50% / 100%».
        # Подписи были бесполезны: они не подписаны ни одной кнопкой,
        # стояли отдельно от ползунка и на них нельзя было нажать -
        # выглядело как три кнопки, а работало как три слова.
        self.btn_prev = QPushButton("⏮")
        self.btn_next = QPushButton("⏭")
        for btn, tip in ((self.btn_prev, "Предыдущий трек"),
                         (self.btn_next, "Следующий трек")):
            btn.setFixedSize(44, 44)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(tip)
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #1e2330; color: #ffffff; border: none;
                    border-radius: 22px; font-size: 16px;
                }
                QPushButton:hover:disabled { background-color: #161a24; color: #333a48; }
                QPushButton:hover { background-color: #2a3145; }
                QPushButton:pressed { background-color: #151a24; }
                QPushButton:disabled { background-color: #161a24; color: #333a48; }
            """)

        self.btn_prev.clicked.connect(self.skip_prev)
        self.btn_next.clicked.connect(self.skip_next)
        row.addWidget(self.btn_prev, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.btn_next, 0, Qt.AlignmentFlag.AlignVCenter)

        # ---------------- Ползунок громкости ----------------
        self.volume_label = QLabel("—")
        self.volume_label.setFixedWidth(48)
        self.volume_label.setAlignment(Qt.AlignmentFlag.AlignRight
                                       | Qt.AlignmentFlag.AlignVCenter)
        self.volume_label.setStyleSheet(
            "color: #a4b0be; font-size: 13px; font-weight: bold;")
        row.addWidget(self.volume_label, 0, Qt.AlignmentFlag.AlignVCenter)

        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setFixedWidth(180)
        self.volume_slider.setPageStep(5)
        self.volume_slider.setToolTip("Громкость музыки")
        self.volume_slider.valueChanged.connect(self.on_volume_changed)
        self.volume_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                height: 6px; background: #1e2330; border-radius: 3px;
            }
            QSlider::sub-page:horizontal {
                background: #7d5fff; border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #ffffff; width: 16px; margin: -6px 0;
                border-radius: 8px;
            }
            QSlider::handle:horizontal:hover { background: #c9baff; }
        """)
        row.addWidget(self.volume_slider, 0, Qt.AlignmentFlag.AlignVCenter)

        # ---------------- Ночной лимит ----------------
        # Отдельной подписью, потому что это не то же, что ползунок.
        # Ночью слышно меньше, чем выставлено, и без подписи человек
        # решит, что программа его не слушается, и начнёт ползунок
        # вверх впустую.
        self.night_cap_label = QLabel("")
        self.night_cap_label.setStyleSheet("color: #5a6270; font-size: 11px;")
        row.addWidget(self.night_cap_label, 0, Qt.AlignmentFlag.AlignVCenter)

        row.addStretch(1)

        # Пробел - самая естественная клавиша для play/pause. Прицел на
        # время матча: в бою до мыши не всегда удобно тянуться.
        self.space_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        self.space_shortcut.activated.connect(self.toggle_play)

        return bar

    def toggle_play(self):
        """
        Play/pause с кнопки и с пробела.

        Разводим три случая, иначе кнопка ведёт себя по-разному в
        зависимости от того, что было раньше:

          играет      -> пауза;
          на паузе    -> продолжить;
          остановлено -> включить музыку.
        """
        player = self._get_player()
        if player is None:
            return

        if player.is_playing() or player.is_paused():
            player.toggle_pause()
        else:
            brain = self._get_brain()
            if brain is None:
                return
            result = brain.resume()
            track = result.get("track") or {}
            if track:
                gsi_signals.track_changed.emit(
                    track.get("title", ""), "▶️ Запущено вручную")
            elif result.get("status") == "FAILED":
                print("🚫 [UI] Play нажат, а включить нечего: "
                      "база треков пуста.")
        self._refresh_player_bar()

    def skip_next(self):
        """Кнопка ⏭. Пропуск по плейлисту, а не смена вайба."""
        self._skip(forward=True)

    def skip_prev(self):
        """Кнопка ⏮."""
        self._skip(forward=False)

    def _skip(self, forward: bool):
        """
        Общая часть ⏮ и ⏭.

        Если музыка сейчас не идёт (остановлена или пауза), сначала
        включаем её и только потом переключаем. Иначе первое нажатие
        после остановки молча проглатывалось бы: плеер не играет,
        плейлист пуст, переключать не на что - и человек решил бы, что
        кнопка сломана. А он просто хотел начать со следующего трека.

        Вайб при этом не трогаем: пропуск вручную - это «мне не нравится
        этот трек», а не «в Dota сменилось состояние».
        """
        player = self._get_player()
        if player is None:
            return

        if not player.is_playing() and not player.is_paused():
            brain = self._get_brain()
            if brain is None:
                return
            result = brain.resume()
            if result.get("status") == "FAILED":
                print("🚫 [UI] Переключение трека нажато, а база треков пуста.")
                self._refresh_player_bar()
                return

        if forward:
            player.next_track()
        else:
            player.prev_track()
        self._refresh_player_bar()

    def on_volume_changed(self, value: int):
        """
        Ползунок поехал - сообщаем плееру.

        Направление тут важно: событие приходит и от настоящей смены
        громкости, и от нашей же подстройки ползунка под плеер. Чтобы
        не образовалась петля, стоит блокировщик на время подстройки -
        тогда значение от плеера до кнопки обратно не доходит.
        """
        if self._syncing_volume:
            return
        player = self._get_player()
        if player is not None:
            player.set_user_volume(value)
        self._refresh_player_bar()

    def _get_brain(self):
        """Живой DJ Brain. Создаёт его GSI-сервер, а не окно."""
        try:
            from src.triggers import dota_gsi
            return getattr(dota_gsi, "dj_brain", None)
        except Exception as e:
            print(f"⚠️ [UI] Не удалось достать DJ Brain: {e}")
            return None

    def _get_player(self):
        """Плеер живого DJ Brain, если он уже создан."""
        brain = self._get_brain()
        return getattr(brain, "player", None) if brain is not None else None

    def _refresh_player_bar(self):
        """
        Подтягивает кнопку и ползунок к настоящему состоянию плеера.

        Именно таймер, а не сигналы: плеер живёт в своём потоке и о своём
        состоянии окно не сообщает. Заодно он снимает вопрос «что там
        с кнопкой, пока плеера нет»: какое-то время после запуска
        нажимать просто не на что, и это честнее, чем серая кнопка с
        процентами - она выглядит как поломка.
        """
        player = self._get_player()
        if player is None:
            self.btn_play.setText("▶")
            self.btn_play.setEnabled(False)
            self.btn_play.setToolTip(
                "Плеер ещё создаётся. Обычно это пара секунд после запуска.")
            self.btn_prev.setEnabled(False)
            self.btn_next.setEnabled(False)
            self.volume_label.setText("—")
            return

        self._player = player

        # Плеер есть, а звукового движка нет - например, не установлен
        # VLC. Кнопка в этом случае нажимается впустую: плеер примет
        # команду и не сможет её выполнить. Лучше прямо сказать, что
        # не работает, чем оставить мёртвую кнопку.
        if not player.available():
            self.btn_play.setText("▶")
            self.btn_play.setEnabled(False)
            self.btn_play.setToolTip(
                "Не удалось запустить VLC. Запусти setup.py - он ставит "
                "python-vlc и проверяет сам проигрыватель.")
            self.volume_label.setText("—")
            self.btn_prev.setEnabled(False)
            self.btn_next.setEnabled(False)
            if not self._warned_no_vlc:
                self._warned_no_vlc = True
                print("⚠️ [UI] VLC не поднялся. Кнопка play отключена - "
                      "запусти setup.py, чтобы доставить проигрыватель.")
            return

        self._warned_no_vlc = False
        self.btn_play.setEnabled(True)
        self.btn_play.setToolTip("Play / Pause (пробел)")
        self.btn_play.setText("⏸" if player.is_playing() else "▶")

        # ⏮ ⏭ гасим, когда переключать нечего. Нажатая впустую кнопка
        # выглядит как поломка, а человек просто не знает, что плейлист
        # ещё не собран.
        can_skip = player.playlist_size() > 1
        self.btn_prev.setEnabled(can_skip)
        self.btn_next.setEnabled(can_skip)

        percent = player.user_volume()
        self.volume_label.setText(f"{percent}%")
        if self.volume_slider.value() != percent:
            # Ползунок догоняет плеер, а не наоборот: иначе он
            # показывал бы то, чего на самом деле не слышно.
            self._syncing_volume = True
            try:
                self.volume_slider.setValue(percent)
            finally:
                self._syncing_volume = False

        # Ночной лимит показываем фактическим, а не «сработало / нет».
        factor = player.night_factor()
        if factor < 0.999:
            self.night_cap_label.setText(
                f"🌙 ночью слышно {int(percent * factor)}%")
            self.night_cap_label.setStyleSheet(
                "color: #7d5fff; font-size: 11px;")
        else:
            self.night_cap_label.setText("☀️ дневной режим")
            self.night_cap_label.setStyleSheet(
                "color: #5a6270; font-size: 11px;")

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

        # --- Что играет сейчас --------------------------------------
        # Отдельная карточка: до неё было видно только состояние игры,
        # а что именно поставил DJ Brain - нигде. Если музыка не та,
        # не с чем было разобраться.
        now_card = QFrame()
        now_card.setStyleSheet("background-color: #161b26; border-radius: 12px; border: 1px solid #232a3b;")
        now_layout = QVBoxLayout(now_card)
        now_layout.setContentsMargins(20, 20, 20, 20)

        now_title = QLabel("СЕЙЧАС ИГРАЕТ")
        now_title.setStyleSheet("color: #747d8c; font-size: 12px; font-weight: bold;")
        now_layout.addWidget(now_title)

        self.now_playing = QLabel("🔇 Ничего не играет")
        self.now_playing.setWordWrap(True)
        self.now_playing.setStyleSheet("color: #f1f2f6; font-size: 15px; margin-top: 5px;")
        now_layout.addWidget(self.now_playing)

        self.now_reason = QLabel("Ожидаем первый вайб от Dota 2")
        self.now_reason.setWordWrap(True)
        self.now_reason.setStyleSheet("color: #747d8c; font-size: 12px; margin-top: 8px;")
        now_layout.addWidget(self.now_reason)

        layout.addSpacing(15)
        layout.addWidget(now_card)
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

        # --- Подстройка октавы под себя ------------------------------
        # Единственная ручка, которую программа не может подобрать сама.
        # Подробности в src/ai/octave_tuning.py.
        oct_card = QFrame()
        oct_card.setStyleSheet("background-color: #161b26; border-radius: 12px; border: 1px solid #232a3b;")
        oct_layout = QVBoxLayout(oct_card)
        oct_layout.setContentsMargins(20, 20, 20, 20)

        oct_title = QLabel("ПОДСТРОЙКА ОКТАВЫ")
        oct_title.setStyleSheet("color: #747d8c; font-size: 12px; font-weight: bold;")
        oct_layout.addWidget(oct_title)

        oct_help = QLabel(
            "Программа иногда считает трек вдвое быстрее, чем он есть: "
            "хип-хоп с хэтами на слабой доле выглядит как трек в 185 BPM, "
            "а на слух это 92.\n\n"
            "Слушай музыку и жми кнопку, когда слышишь ошибку. Каждое "
            "нажатие сдвигает порог всё меньше, поэтому он мягко "
            "подойдёт к нужному значению и не пролетит мимо.")
        oct_help.setWordWrap(True)
        oct_help.setStyleSheet("color: #a0a8b8; font-size: 13px;")
        oct_layout.addWidget(oct_help)
        oct_layout.addSpacing(10)

        self.octave_label = QLabel()
        self.octave_label.setStyleSheet(
            "color: #7d5fff; font-size: 15px; font-weight: bold;")
        oct_layout.addWidget(self.octave_label)
        self.refresh_octave_label()

        oct_btns = QHBoxLayout()
        oct_btns.setContentsMargins(0, 10, 0, 0)

        self.btn_octave_faster = QPushButton("⚡ Слишком быстро")
        self.btn_octave_faster.setStyleSheet("""
            QPushButton {
                background-color: #ff4757; color: #ffffff; border: none;
                padding: 10px 16px; border-radius: 6px; font-weight: bold;
            }
            QPushButton:hover { background-color: #ff6b81; }
        """)
        self.btn_octave_faster.clicked.connect(
            lambda: self.handle_octave_shift(octave_tuning.DIRECTION_FASTER))

        self.btn_octave_slower = QPushButton("🐌 Слишком медленно")
        self.btn_octave_slower.setStyleSheet("""
            QPushButton {
                background-color: #2ed573; color: #ffffff; border: none;
                padding: 10px 16px; border-radius: 6px; font-weight: bold;
            }
            QPushButton:hover { background-color: #4ee08a; }
        """)
        self.btn_octave_slower.clicked.connect(
            lambda: self.handle_octave_shift(octave_tuning.DIRECTION_SLOWER))

        self.btn_octave_reset = QPushButton("↩️ Сброс")
        self.btn_octave_reset.setStyleSheet("""
            QPushButton {
                background-color: #2f3542; color: #a0a8b8; border: none;
                padding: 10px 16px; border-radius: 6px;
            }
            QPushButton:hover { background-color: #3d4453; }
        """)
        self.btn_octave_reset.clicked.connect(self.handle_octave_reset)

        oct_btns.addWidget(self.btn_octave_faster)
        oct_btns.addWidget(self.btn_octave_slower)
        oct_btns.addWidget(self.btn_octave_reset)
        oct_layout.addLayout(oct_btns)

        self.octave_result = QLabel("")
        self.octave_result.setWordWrap(True)
        self.octave_result.setStyleSheet("color: #747d8c; font-size: 12px;")
        oct_layout.addWidget(self.octave_result)

        # --- Как играем музыку ---------------------------------------
        # Основной способ и запасной. Раньше выбора не было вовсе:
        # DJ Brain жал клавишу «следующий трек» и надеялся, что в
        # браузере уже открыт нужный плейлист.
        play_card = QFrame()
        play_card.setStyleSheet("background-color: #161b26; border-radius: 12px; border: 1px solid #232a3b;")
        play_layout = QVBoxLayout(play_card)
        play_layout.setContentsMargins(20, 20, 20, 20)

        play_title = QLabel("ВОСПРОИЗВЕДЕНИЕ")
        play_title.setStyleSheet("color: #747d8c; font-size: 12px; font-weight: bold;")
        play_layout.addWidget(play_title)

        play_help = QLabel(
            "Откуда плеер берёт звук для выбранного трека.")
        play_help.setWordWrap(True)
        play_help.setStyleSheet("color: #a0a8b8; font-size: 13px;")
        play_layout.addWidget(play_help)
        play_layout.addSpacing(10)

        self.btn_mode_download = QPushButton("📥 Скачивать и играть локально")
        self.btn_mode_direct = QPushButton("🔗 Ссылка напрямую в VLC")
        for btn, mode in ((self.btn_mode_download, PLAYER_MODE_DOWNLOAD),
                          (self.btn_mode_direct, PLAYER_MODE_DIRECT)):
            btn.setCheckable(True)
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #2f3542; color: #a0a8b8; border: none;
                    padding: 10px 16px; border-radius: 6px; text-align: left;
                }
                QPushButton:checked {
                    background-color: #7d5fff; color: #ffffff; font-weight: bold;
                }
            """)
            btn.clicked.connect(lambda _, m=mode: self.handle_player_mode(m))
            play_layout.addWidget(btn)
        play_layout.addSpacing(8)

        self.player_mode_help = QLabel("")
        self.player_mode_help.setWordWrap(True)
        self.player_mode_help.setStyleSheet("color: #747d8c; font-size: 12px;")
        play_layout.addWidget(self.player_mode_help)

        self.btn_cache_check = QPushButton("📦 Проверить кэш аудио")
        self.btn_cache_check.setStyleSheet("""
            QPushButton {
                background-color: #3d4453; color: #a4b0be; border: none;
                padding: 8px 16px; border-radius: 6px; margin-top: 8px;
            }
            QPushButton:hover { background-color: #4a5262; }
        """)
        self.btn_cache_check.clicked.connect(self.handle_cache_check)
        play_layout.addWidget(self.btn_cache_check)

        # Отмечаем текущий режим: иначе при запуске не видно, что выбрано.
        current_mode = getattr(config, "PLAYER_MODE", PLAYER_MODE_DOWNLOAD)
        self.btn_mode_download.setChecked(current_mode == PLAYER_MODE_DOWNLOAD)
        self.btn_mode_direct.setChecked(current_mode == PLAYER_MODE_DIRECT)
        self.player_mode_help.setText(PLAYER_MODE_HELP.get(current_mode, ""))

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

        layout.addSpacing(15)
        layout.addWidget(oct_card)

        layout.addSpacing(15)
        layout.addWidget(play_card)

        # --- Ночной режим --------------------------------------------
        # По умолчанию программа сама решает по часам: после 23:00
        # громкость падает до 60%. Но это не всегда удобно - ночью можно
        # играть с наушниками в полную, а днём кто-то спит рядом.
        # Поэтому переключатель в руках человека.
        night_card = QFrame()
        night_card.setStyleSheet(
            "background-color: #161b26; border-radius: 12px; border: 1px solid #232a3b;")
        night_layout = QVBoxLayout(night_card)
        night_layout.setContentsMargins(20, 20, 20, 20)

        night_title = QLabel("НОЧНОЙ РЕЖИМ")
        night_title.setStyleSheet(
            "color: #747d8c; font-size: 12px; font-weight: bold;")
        night_layout.addWidget(night_title)

        night_help = QLabel(
            f"Сейчас программа приглушает музыку до "
            f"{int(config.NIGHT_VOLUME_FACTOR * 100)}% с "
            f"{config.NIGHT_START_HOUR}:00 до {config.NIGHT_END_HOUR}:00.\n"
            "Выключи переключатель - и громкость останется как выставил "
            "ползунок. Ползунок при этом не меняется, так что вернуть "
            "обратно можно одним нажатием.")
        night_help.setWordWrap(True)
        night_help.setStyleSheet("color: #a0a8b8; font-size: 13px;")
        night_layout.addWidget(night_help)
        night_layout.addSpacing(10)

        self.night_toggle = QPushButton()
        self.night_toggle.setCheckable(True)
        self.night_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.night_toggle.setFixedHeight(44)
        self.night_toggle.clicked.connect(self.handle_night_mode)
        night_layout.addWidget(self.night_toggle)

        self.night_mode_result = QLabel("")
        self.night_mode_result.setWordWrap(True)
        self.night_mode_result.setStyleSheet("color: #747d8c; font-size: 12px;")
        night_layout.addWidget(self.night_mode_result)

        layout.addSpacing(15)
        layout.addWidget(night_card)

        # Растяжка в конце. Пока она была одна и вверху, карточки
        # прижимались к верху и не было видно, что страница кончается.
        layout.addStretch()

        # Прокрутка. Без неё Qt ужимает содержимое под 520 px и
        # настройки превращаются в нечитаемые полоски.
        return self._wrap_scrollable(page)

    def _wrap_scrollable(self, page: QWidget) -> QWidget:
        """
        Кладёт страницу в прокручиваемую область.

        setWidgetResizable(True) обязателен: без него вьюпорт
        получает размер страницы целиком, то есть она перестаёт
        помещаться и скроллбар не появляется.

        Горизонтальную прокрутку убираем: страница и так по ширине
        в окно, а ползунок снизу вбок только мешает.
        """
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("""
            QScrollArea { background-color: #0f121a; border: none; }
            QScrollBar:vertical {
                background: #0f121a; width: 10px; margin: 0;
            }
            QScrollBar::handle:vertical {
                background: #2a3145; border-radius: 5px; min-height: 30px;
            }
            QScrollBar::handle:vertical:hover { background: #3a435c; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px; width: 0px;
            }
        """)
        scroll.setWidget(page)
        return scroll

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

    def refresh_octave_label(self):
        """Показывает текущий порог и размер следующего шага."""
        self.octave_label.setText("Сейчас: " + octave_tuning.describe())

    def handle_octave_shift(self, direction: int):
        """
        Сдвигает порог октавы на один убывающий шаг.

        Сдвиг мгновенный: темпы пересчитываются из уже сохранённых
        замеров, звук заново не скачивается.
        """
        data = octave_tuning.adjust(direction)
        threshold = float(data["threshold"])

        stats = vibe_classifier.reapply_octave_threshold(threshold)
        self.refresh_octave_label()

        word = ("слышу слишком быстро"
                if direction == octave_tuning.DIRECTION_FASTER
                else "слышу слишком медленно")
        next_step = octave_tuning.next_step_percent(int(data["presses"]))
        self.octave_result.setText(
            f"{word}. Порог теперь {threshold:.2f}, "
            f"следующий шаг {next_step:.1f}%. "
            f"Темп изменился у {stats['changed']} из {stats['total']} треков. "
            f"Нажми «Синхронизировать ИИ», чтобы обновить вайб-матрицу.")

    def handle_octave_reset(self):
        """Возвращает порог к значению из кода."""
        octave_tuning.reset()
        stats = vibe_classifier.reapply_octave_threshold()
        self.refresh_octave_label()
        self.octave_result.setText(
            f"Сброшено к значению по умолчанию. "
            f"Темп изменился у {stats['changed']} из {stats['total']} треков. "
            f"Нажми «Синхронизировать ИИ», чтобы обновить вайб-матрицу.")

    def _refresh_night_toggle(self):
        """
        Приводит переключатель ночного режима к настоящему состоянию.

        Отдельным вызовом, а не внутри create_settings_page: там DJ Brain
        может быть ещё не создан, а здесь страница уже собрана.
        """
        brain = self._get_brain()
        enabled = brain.night_enabled() if brain is not None \
            else bool(config.NIGHT_MODE_ENABLED)

        self.night_toggle.blockSignals(True)
        try:
            self.night_toggle.setChecked(enabled)
        finally:
            self.night_toggle.blockSignals(False)

        self._render_night_toggle(enabled)

    def _render_night_toggle(self, enabled: bool):
        self.night_toggle.setText("🌙 Ночной режим: ВКЛ" if enabled
                                 else "☀️ Ночной режим: ВЫКЛ")
        self.night_toggle.setStyleSheet("""
            QPushButton {
                background-color: %s; color: #ffffff; border: none;
                padding: 10px 18px; border-radius: 6px; font-weight: bold;
                font-size: 14px; text-align: left;
            }
            QPushButton:hover { background-color: #8f72ff; }
        """ % ("#7d5fff" if enabled else "#2f3542"))

    def handle_night_mode(self):
        """
        Человек переключил ночной режим.

        Меняем у живого DJ Brain, а не в конфиге: конфиг читается при
        импорте, и запись в него подействовала бы только после
        перезапуска. А человек жмёт кнопку посреди игры и ждёт
        результат сейчас.
        """
        enabled = self.night_toggle.isChecked()
        self._render_night_toggle(enabled)

        brain = self._get_brain()
        if brain is None:
            self.night_mode_result.setText(
                "DJ Brain ещё создаётся. Переключатель подействует, "
                "как только он поднимется.")
            return

        brain.set_night_enabled(enabled)

        factor = brain.get_current_volume_factor()
        if enabled and brain.is_night_time():
            status = (f"Громкость ночью ограничена "
                      f"{int(factor * 100)}% - это решили часы.")
        elif enabled:
            status = (f"Ночной режим включён, но сейчас "
                      f"{datetime.now().hour:02d}:00 - это не ночное время, "
                      f"лимит пока не применяется.")
        else:
            status = "Ночной лимит выключен, громкость как на ползунке."
        self.night_mode_result.setText(status)
        self._refresh_player_bar()

    def handle_player_mode(self, mode: str):
        """
        Переключает способ воспроизведения.

        Меняем и в работающем плеере, и в config.py: первый работает до
        перезапуска, второй сохраняет выбор на будущее. Плеер нужен здесь
        потому, что GSI-сервер поднимает DJ Brain сам - он не связан с
        окном, и без явной передачи выбора в настройках не подействовали бы.
        """
        config.PLAYER_MODE = mode

        player = self._get_player()
        if player is not None:
            player.set_mode(mode)
            status = f"Плеер переключён: {player.describe_mode()}"
        else:
            status = "Плеер ещё не создан, выбор применится при запуске."

        self.player_mode_help.setText(PLAYER_MODE_HELP.get(mode, ""))
        QMessageBox.information(self, "Воспроизведение", status)

    def handle_cache_check(self):
        """Показывает, сколько треков уже лежит в кэше."""
        try:
            from src import audio_cache
            count = audio_cache.get_cache().cached_count()
        except Exception as e:
            QMessageBox.warning(self, "Кэш", f"Не удалось проверить кэш: {e}")
            return

        folder = audio_cache.CACHE_DIR
        total = 0
        try:
            total = sum(p.stat().st_size for p in folder.glob("*") if p.is_file())
        except OSError:
            pass

        QMessageBox.information(
            self, "Кэш аудио",
            f"Скачано треков: {count}\n"
            f"Занято места: {total / 1048576:.0f} МБ\n\n"
            f"Папка: {folder}\n\n"
            "Файлы можно удалить вручную - программа скачает нужное заново.")

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
        gsi_signals.track_changed.connect(self.update_now_playing)

    def update_now_playing(self, title: str, reason: str):
        """Показывает, что DJ Brain поставил, и почему именно это."""
        if not title:
            self.now_playing.setText("🔇 Ничего не играет")
            self.now_playing.setStyleSheet(
                "color: #747d8c; font-size: 15px; margin-top: 5px;")
            self.now_reason.setText(reason or "")
            return

        self.now_playing.setText(f"🎵 {title}")
        self.now_playing.setStyleSheet(
            "color: #7d5fff; font-size: 15px; font-weight: bold; margin-top: 5px;")
        self.now_reason.setText(reason or "")

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