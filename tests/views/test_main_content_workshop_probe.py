"""Workshop 更新检查连通性探测后台化的行为测试。

原实现 `check_internet_connection()` 在主线程同步执行,离线时每次
刷新冻结约 3s;现改为 QThread 后台探测,结果经信号回主线程。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from app.views import main_content_panel
from app.views.main_content_panel import MainContent


def _make_panel() -> MainContent:
    """裸构造 MainContent(不走重量级 __init__),并保护单例不被污染。"""
    prev_instance = MainContent._instance
    MainContent._instance = None
    try:
        panel = MainContent.__new__(MainContent)
        QObject.__init__(panel)
    finally:
        MainContent._instance = prev_instance
    panel._workshop_connectivity_worker = None
    panel.metadata_controller = MagicMock(name="metadata_controller")
    return panel


class TestDoCheckForWorkshopUpdates:
    def test_starts_background_probe_and_connects_signals(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """触发检查时启动后台探测 worker 并连接 online/offline 信号。"""
        worker_cls = MagicMock(name="_WorkshopConnectivityWorker")
        monkeypatch.setattr(
            main_content_panel, "_WorkshopConnectivityWorker", worker_cls
        )
        panel = _make_panel()

        panel._do_check_for_workshop_updates()

        worker_cls.assert_called_once_with()
        worker = worker_cls.return_value
        worker.online.connect.assert_called_once_with(panel._on_workshop_probe_online)
        worker.offline.connect.assert_called_once_with(panel._on_workshop_probe_offline)
        worker.start.assert_called_once()
        assert panel._workshop_connectivity_worker is worker

    def test_skips_when_probe_already_running(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """已有探测在跑时不重复启动(避免重复动画流程)。"""
        worker_cls = MagicMock(name="_WorkshopConnectivityWorker")
        monkeypatch.setattr(
            main_content_panel, "_WorkshopConnectivityWorker", worker_cls
        )
        panel = _make_panel()
        running = MagicMock(name="running_worker")
        running.isRunning.return_value = True
        panel._workshop_connectivity_worker = running

        panel._do_check_for_workshop_updates()

        worker_cls.assert_not_called()
        running.start.assert_not_called()


class TestProbeCallbacks:
    def test_offline_callback_shows_connection_error(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """离线回调在主线程补齐连接错误弹窗(探测线程禁止弹控件)。"""
        mock_show = MagicMock()
        monkeypatch.setattr(
            "app.views.main_content_panel.dialogue.show_internet_connection_error",
            mock_show,
        )
        panel = _make_panel()

        panel._on_workshop_probe_offline()

        mock_show.assert_called_once()

    def test_online_callback_no_workshop_mods_emits_status(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """在线回调执行查询动画;无可查 mod 时发状态信号并提前返回。"""
        fake_result = MagicMock(name="result")
        fake_result.status = "no_workshop_mods"
        monkeypatch.setattr(
            MainContent,
            "do_threaded_loading_animation",
            lambda self, **kwargs: fake_result,
        )
        panel = _make_panel()
        emitted: list[str] = []
        panel.status_signal.connect(emitted.append)

        panel._on_workshop_probe_online()

        assert len(emitted) == 1


class TestWorkshopConnectivityWorker:
    def test_run_emits_online_when_connected(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """探测成功时发 online 信号。"""
        monkeypatch.setattr(
            main_content_panel, "check_internet_connection", lambda **kw: True
        )
        worker = main_content_panel._WorkshopConnectivityWorker()
        online: list[bool] = []
        offline: list[bool] = []
        worker.online.connect(lambda: online.append(True))
        worker.offline.connect(lambda: offline.append(True))

        worker.run()

        assert online == [True]
        assert offline == []

    def test_run_emits_offline_when_disconnected(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """探测失败时发 offline 信号(不弹窗,由主线程回调补齐)。"""
        monkeypatch.setattr(
            main_content_panel, "check_internet_connection", lambda **kw: False
        )
        worker = main_content_panel._WorkshopConnectivityWorker()
        online: list[bool] = []
        offline: list[bool] = []
        worker.online.connect(lambda: online.append(True))
        worker.offline.connect(lambda: offline.append(True))

        worker.run()

        assert online == []
        assert offline == [True]
