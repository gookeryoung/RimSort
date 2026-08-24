"""Tests for MainWindow AcfLogReader lazy construction."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tests.views.conftest import make_stub_main_window


def _make_lazy_window() -> object:
    """构造带懒加载所需最小属性的 stub MainWindow。

    tab_widget.widget(index) 按索引返回对应 tab,模拟 QTabWidget 行为:
    索引 0 为 Main Content,索引 1 为 ACF Log Reader tab。
    """
    window = make_stub_main_window()
    window.acf_log_reader = None
    window.acf_log_reader_tab = MagicMock(name="acf_tab")
    window.acf_log_reader_layout = MagicMock(name="acf_layout")
    other_tab = MagicMock(name="main_content_tab")

    tab_widget = MagicMock()
    tab_widget.widget = lambda index: (
        window.acf_log_reader_tab if index == 1 else other_tab
    )
    window.tab_widget = tab_widget
    return window


class TestEnsureAcfLogReader:
    def test_constructs_on_first_activation(
        self, qapp: object, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """首次激活 ACF tab 时构造面板、加入布局并主动 populate。"""
        acf_cls = MagicMock(name="AcfLogReader")
        monkeypatch.setattr("app.views.main_window.AcfLogReader", acf_cls, raising=True)
        window = _make_lazy_window()

        window._ensure_acf_log_reader(1)

        acf_cls.assert_called_once()
        # active_mods_list 取自 main_content_panel.mods_panel
        assert (
            acf_cls.call_args.kwargs["active_mods_list"]
            is window.main_content_panel.mods_panel.active_mods_list
        )
        window.acf_log_reader_layout.addWidget.assert_called_once_with(
            window.acf_log_reader
        )
        window.acf_log_reader._populate_from_metadata.assert_called_once_with()

    def test_ignores_other_tabs(
        self, qapp: object, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """激活其他 tab(如 Main Content)时不触发构造。"""
        acf_cls = MagicMock(name="AcfLogReader")
        monkeypatch.setattr("app.views.main_window.AcfLogReader", acf_cls, raising=True)
        window = _make_lazy_window()

        window._ensure_acf_log_reader(0)

        acf_cls.assert_not_called()
        assert window.acf_log_reader is None

    def test_constructs_only_once(
        self, qapp: object, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """重复激活 ACF tab 不会重复构造或重复 populate。"""
        acf_cls = MagicMock(name="AcfLogReader")
        monkeypatch.setattr("app.views.main_window.AcfLogReader", acf_cls, raising=True)
        window = _make_lazy_window()

        window._ensure_acf_log_reader(1)
        window._ensure_acf_log_reader(1)

        acf_cls.assert_called_once()
        window.acf_log_reader._populate_from_metadata.assert_called_once_with()
