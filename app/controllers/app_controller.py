import os
import sys
import threading

from loguru import logger
from PySide6.QtCore import QCoreApplication, QLibraryInfo, QObject, QTranslator
from PySide6.QtWidgets import QApplication

import app.utils.globals as app_globals
from app.controllers.main_window_controller import MainWindowController
from app.controllers.metadata_controller import MetadataController
from app.controllers.metadata_db_controller import AuxMetadataController
from app.controllers.settings_controller import SettingsController
from app.controllers.theme_controller import ThemeController
from app.models.settings import Settings
from app.services.instance_service import InstanceService
from app.utils.app_info import AppInfo
from app.utils.dds_utility import DDSUtility
from app.utils.gui_info import GUIInfo
from app.utils.perf_timing import log_stage
from app.utils.steam.steamcmd.wrapper import SteamcmdInterface
from app.views.main_window import MainWindow
from app.views.settings_dialog import SettingsDialog

app_translator = QTranslator()
qt_translator = QTranslator()


class AppController(QObject):
    def __init__(self) -> None:
        super().__init__()

        with log_stage("QApplication.construct"):
            self.app = QApplication(sys.argv)
            self.app.setDesktopFileName("io.github.rimsort.RimSort")
            self.app.setWindowIcon(GUIInfo().app_icon)

        # Initialize the application settings.
        with log_stage("initialize_settings"):
            self.initialize_settings()
        # set the language of the application.
        with log_stage("set_language"):
            self.set_language()
        # Initialize the theme controller
        with log_stage("initialize_theme_controller"):
            self.initialize_theme_controller()
        # Set the theme of the application.
        with log_stage("set_theme"):
            self.set_theme()
        # Initialize the Steamcmd interface
        with log_stage("initialize_steamcmd_interface"):
            self.initialize_steamcmd_interface()
        # Perform cleanup of orphaned DDS files if the setting is enabled
        with log_stage("do_dds_cleanup"):
            self.do_dds_cleanup()
        # Initialize the new MetadataController
        with log_stage("initialize_metadata_controller"):
            self.initialize_metadata_controller()
        # Initialize the instance service (self-subscribes to EventBus)
        with log_stage("initialize_instance_service"):
            self.initialize_instance_service()
        # Initialize the main window controller
        with log_stage("initialize_main_window"):
            self.initialize_main_window()

    def set_language(self) -> None:
        """Sets the language of the application on initial setup."""
        available_languages = self.settings_controller.language_controller.languages
        os_language = os.getenv("LANG", "").split(".")[0]
        is_inital = self.settings_controller.active_instance.initial_setup
        if is_inital and os_language in available_languages:
            self.settings_controller.settings.language = os_language
            self.settings_controller.settings.save()
            self.initialize_settings()

    def set_theme(self) -> None:
        """Sets the theme for the application."""
        self.app.setStyle("Fusion")
        self.theme_controller.set_font(
            self.settings.font_family,
            self.settings.font_size,
        )
        self.theme_controller.apply_selected_theme(
            self.settings.enable_themes,
            self.settings.theme_name,
        )

    def initialize_settings(self) -> None:
        """Initializes the settings model, view, and controller."""
        self.settings = Settings()
        self.settings.load()
        self.initialize_translator(self.settings.language)
        self.settings_dialog = SettingsDialog()
        self.settings_controller = SettingsController(
            model=self.settings, view=self.settings_dialog
        )
        app_globals.SETTINGS = self.settings

    def initialize_theme_controller(self) -> None:
        """Initializes the ThemeController."""
        self.theme_controller = ThemeController()

    def initialize_translator(self, language: str) -> None:
        """Initializes the translator with the specified language."""
        path = AppInfo().language_data_folder / f"{language}.qm"
        if app_translator.load(str(path)):
            QCoreApplication.installTranslator(app_translator)
        else:
            print(f"Translation file {path} not found.")

        qt_translations_path = QLibraryInfo.path(
            QLibraryInfo.LibraryPath.TranslationsPath
        )

        qt_file_path = os.path.join(qt_translations_path, f"qtbase_{language}.qm")
        if qt_translator.load(qt_file_path):
            QCoreApplication.installTranslator(qt_translator)
        else:
            print(f"Qt translation file {qt_file_path} not found.")

    def initialize_steamcmd_interface(self) -> None:
        """Initializes the SteamcmdInterface."""
        self.steamcmd_wrapper = SteamcmdInterface.instance(
            self.settings_controller.settings.instances[
                self.settings_controller.settings.current_instance
            ].steamcmd_install_path,
            self.settings_controller.settings.steamcmd_validate_downloads,
        )

    def do_dds_cleanup(self) -> None:
        """Performs cleanup of orphaned DDS files if the setting is enabled.

        优化:原实现同步执行 ``rglob("*.dds")`` 扫描 local + workshop 目录,
        会阻塞启动。改为后台 daemon 线程执行,启动不等待。
        DDS 清理是纯 I/O 操作且幂等(下次启动会继续清理未完成的),
        daemon 线程在应用退出时被强制终止不会留下不一致状态。
        """
        if not self.settings.auto_delete_orphaned_dds:
            return
        # 复制 settings 引用,避免后台线程与主线程竞争 controller 状态
        settings = self.settings_controller.settings
        thread = threading.Thread(
            target=self._dds_cleanup_worker,
            args=(settings,),
            name="dds_cleanup",
            daemon=True,
        )
        thread.start()

    @staticmethod
    def _dds_cleanup_worker(settings: Settings) -> None:
        """DDS 清理后台工作线程入口。"""
        try:
            dds_utility = DDSUtility(settings)
            dds_utility.delete_dds_files_without_png()
        except Exception:
            # daemon 线程内异常不能冒泡到主线程,记录日志即可
            logger.exception("DDS cleanup background thread failed")

    def initialize_metadata_controller(self) -> None:
        """Initializes the MetadataController."""
        aux_db_controller = AuxMetadataController.get_or_create_cached_instance(
            self.settings_controller.settings.aux_db_path
        )
        self.metadata_controller = MetadataController.instance(
            settings=self.settings_controller.settings,
            get_active_instance=lambda: self.settings_controller.active_instance,
            metadata_db_controller=aux_db_controller,
        )

    def initialize_instance_service(self) -> None:
        """Initializes the instance service."""
        InstanceService(
            settings=self.settings_controller.settings,
            steamcmd_wrapper=self.steamcmd_wrapper,
        )

    def initialize_main_window(self) -> None:
        """Initializes the main window and its controller."""
        self.main_window = MainWindow(
            settings=self.settings,
            get_active_instance=lambda: self.settings_controller.active_instance,
            set_instance=self.settings_controller.set_instance,
            show_settings_dialog=self.settings_controller.show_settings_dialog,
            metadata_controller=self.metadata_controller,
        )
        self.main_window_controller = MainWindowController(self.main_window)

    def run(self) -> int:
        """Runs the main application loop after initializing the main window."""
        self.main_window.show()
        self.main_window.initialize_content(is_initial=True)
        # If the window was closed during initialization (e.g. user closed during
        # mod scanning), skip the main event loop — Qt resets the quit flag in exec()
        # so a prior quit() from quitOnLastWindowClosed would have no effect and the
        # event loop would block forever with no visible windows.
        if not self.main_window.isVisible():
            return 0
        return self.app.exec()

    def shutdown_watchdog(self) -> None:
        """Initiates the shutdown procedure for the watchdog."""
        self.main_window.shutdown_watchdog()

    def quit(self) -> None:
        """Exits the application."""
        self.app.quit()
