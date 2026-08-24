"""Tests for startup database update connectivity probe backgrounding."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.controllers.main_content_controller import (
    MainContentController,
    _StartupConnectivityWorker,
)


def _make_controller(update_on_startup: bool = True) -> MainContentController:
    """构造 MainContentController stub,跳过重量级 __init__。"""
    instance = MainContentController.__new__(MainContentController)
    settings = MagicMock()
    settings.update_databases_on_startup = update_on_startup
    instance.settings = settings
    instance._startup_connectivity_worker = None
    return instance


class TestUpdateDatabasesOnStartupSilent:
    def test_disabled_does_not_start_worker(self) -> None:
        """设置关闭时不启动探测。"""
        controller = _make_controller(update_on_startup=False)
        with patch(
            "app.controllers.main_content_controller._StartupConnectivityWorker"
        ) as mock_worker_cls:
            controller._update_databases_on_startup_if_enabled_silent()
            mock_worker_cls.assert_not_called()

    def test_enabled_starts_background_worker(self) -> None:
        """设置开启时探测移入后台线程,online/offline 信号均已连接。"""
        controller = _make_controller(update_on_startup=True)
        with patch(
            "app.controllers.main_content_controller._StartupConnectivityWorker"
        ) as mock_worker_cls:
            worker = MagicMock()
            mock_worker_cls.return_value = worker
            controller._update_databases_on_startup_if_enabled_silent()
            mock_worker_cls.assert_called_once()
            worker.online.connect.assert_called_once()
            worker.offline.connect.assert_called_once()
            worker.start.assert_called_once()

    def test_offline_callback_shows_dialog(self) -> None:
        """离线回调在主线程补弹提示(保留原同步实现行为)。"""
        controller = _make_controller()
        with patch(
            "app.controllers.main_content_controller.show_internet_connection_error"
        ) as mock_dialog:
            controller._on_startup_connectivity_offline()
            mock_dialog.assert_called_once()


class TestStartupConnectivityWorker:
    def test_online_path(self) -> None:
        worker = _StartupConnectivityWorker()
        events: list[str] = []
        worker.online.connect(lambda: events.append("online"))
        worker.offline.connect(lambda: events.append("offline"))
        with patch(
            "app.controllers.main_content_controller.check_internet_connection",
            return_value=True,
        ) as mock_check:
            worker.run()
        assert events == ["online"]
        # 后台线程禁用弹窗
        mock_check.assert_called_once_with(show_error_dialog=False)

    def test_offline_path(self) -> None:
        worker = _StartupConnectivityWorker()
        events: list[str] = []
        worker.online.connect(lambda: events.append("online"))
        worker.offline.connect(lambda: events.append("offline"))
        with patch(
            "app.controllers.main_content_controller.check_internet_connection",
            return_value=False,
        ):
            worker.run()
        assert events == ["offline"]
