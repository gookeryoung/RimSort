"""Tests for MainWindow FileSearch/Troubleshooting lazy construction."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tests.views.conftest import make_stub_main_window


def _make_lazy_window() -> object:
    """构造带懒加载所需最小属性的 stub MainWindow。

    tab_widget.widget(index) 按索引返回对应 tab,模拟 QTabWidget 行为:
    索引 2 为 File Search tab,索引 3 为 Troubleshooting tab,其余为其他 tab。
    """
    window = make_stub_main_window()
    window.settings = MagicMock()
    window.file_search_dialog = None
    window.file_search_tab = MagicMock(name="file_search_tab")
    window.file_search_layout = MagicMock(name="file_search_layout")
    window.troubleshooting_dialog = None
    window.troubleshooting_tab = MagicMock(name="troubleshooting_tab")
    window.troubleshooting_layout = MagicMock(name="troubleshooting_layout")
    other_tab = MagicMock(name="other_tab")

    def _widget(index: int) -> object:
        if index == 2:
            return window.file_search_tab
        if index == 3:
            return window.troubleshooting_tab
        return other_tab

    tab_widget = MagicMock()
    tab_widget.widget = _widget
    window.tab_widget = tab_widget
    return window


class TestEnsureFileSearch:
    def test_constructs_on_first_activation(
        self, qapp: object, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """首次激活 File Search tab 时构造对话框与控制器并加入布局。"""
        dialog_cls = MagicMock(name="FileSearchDialog")
        controller_cls = MagicMock(name="FileSearchController")
        monkeypatch.setattr(
            "app.views.main_window.FileSearchDialog", dialog_cls, raising=True
        )
        monkeypatch.setattr(
            "app.views.main_window.FileSearchController", controller_cls, raising=True
        )
        window = _make_lazy_window()

        window._ensure_file_search(2)

        dialog_cls.assert_called_once_with()
        controller_cls.assert_called_once_with(
            settings=window.settings,
            dialog=window.file_search_dialog,
            metadata_controller=window.metadata_controller,
        )
        window.file_search_layout.addWidget.assert_called_once_with(
            window.file_search_dialog
        )

    def test_ignores_other_tabs(
        self, qapp: object, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """激活其他 tab(如 Troubleshooting)时不触发 File Search 构造。"""
        dialog_cls = MagicMock(name="FileSearchDialog")
        monkeypatch.setattr(
            "app.views.main_window.FileSearchDialog", dialog_cls, raising=True
        )
        window = _make_lazy_window()

        window._ensure_file_search(3)

        dialog_cls.assert_not_called()
        assert window.file_search_dialog is None

    def test_constructs_only_once(
        self, qapp: object, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """重复激活 File Search tab 不会重复构造或重复加布局。"""
        dialog_cls = MagicMock(name="FileSearchDialog")
        monkeypatch.setattr(
            "app.views.main_window.FileSearchDialog", dialog_cls, raising=True
        )
        window = _make_lazy_window()

        window._ensure_file_search(2)
        window._ensure_file_search(2)

        dialog_cls.assert_called_once()
        window.file_search_layout.addWidget.assert_called_once()


class TestEnsureTroubleshooting:
    def test_constructs_on_first_activation(
        self, qapp: object, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """首次激活 Troubleshooting tab 时构造对话框与控制器并加入布局。"""
        dialog_cls = MagicMock(name="TroubleshootingDialog")
        controller_cls = MagicMock(name="TroubleshootingController")
        monkeypatch.setattr(
            "app.views.main_window.TroubleshootingDialog", dialog_cls, raising=True
        )
        monkeypatch.setattr(
            "app.views.main_window.TroubleshootingController",
            controller_cls,
            raising=True,
        )
        window = _make_lazy_window()

        window._ensure_troubleshooting(3)

        dialog_cls.assert_called_once_with()
        controller_cls.assert_called_once_with(
            settings=window.settings,
            dialog=window.troubleshooting_dialog,
        )
        window.troubleshooting_layout.addWidget.assert_called_once_with(
            window.troubleshooting_dialog
        )

    def test_ignores_other_tabs(
        self, qapp: object, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """激活其他 tab(如 File Search)时不触发 Troubleshooting 构造。"""
        dialog_cls = MagicMock(name="TroubleshootingDialog")
        monkeypatch.setattr(
            "app.views.main_window.TroubleshootingDialog", dialog_cls, raising=True
        )
        window = _make_lazy_window()

        window._ensure_troubleshooting(2)

        dialog_cls.assert_not_called()
        assert window.troubleshooting_dialog is None

    def test_constructs_only_once(
        self, qapp: object, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """重复激活 Troubleshooting tab 不会重复构造或重复加布局。"""
        dialog_cls = MagicMock(name="TroubleshootingDialog")
        monkeypatch.setattr(
            "app.views.main_window.TroubleshootingDialog", dialog_cls, raising=True
        )
        window = _make_lazy_window()

        window._ensure_troubleshooting(3)
        window._ensure_troubleshooting(3)

        dialog_cls.assert_called_once()
        window.troubleshooting_layout.addWidget.assert_called_once()
