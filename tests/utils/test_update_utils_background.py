"""Tests for UpdateManager background release fetch (startup freeze fix)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

from app.utils.update_utils import UpdateManager, _ReleaseFetchWorker


def _make_manager() -> UpdateManager:
    """构造 UpdateManager stub(依赖均为 MagicMock)。"""
    settings = MagicMock()
    settings.check_for_update_startup = False
    return UpdateManager(
        settings=settings, main_content=MagicMock(), mod_info_panel=MagicMock()
    )


class TestValidateLocalPrerequisites:
    def test_disable_env_var_skips(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """RIMSORT_DISABLE_UPDATER 置位时静默跳过。"""
        monkeypatch.setenv("RIMSORT_DISABLE_UPDATER", "1")
        manager = _make_manager()
        assert manager._validate_local_prerequisites() is False

    def test_interpreter_mode_skips_with_dialog(self) -> None:
        """Python 解释器模式弹窗跳过(非编译产物不检查更新)。"""
        manager = _make_manager()
        with patch("app.utils.update_utils.dialogue") as mock_dialogue:
            assert manager._validate_local_prerequisites() is False
            mock_dialogue.show_warning.assert_called_once()

    def test_compiled_mode_passes(self) -> None:
        """编译产物模式下本地校验通过。"""
        manager = _make_manager()
        import app.utils.update_utils as update_utils_module

        with patch.dict(
            update_utils_module.__dict__, {"__compiled__": {}}, clear=False
        ):
            assert manager._validate_local_prerequisites() is True


class TestDoCheckForUpdateBackground:
    def test_starts_background_worker(self) -> None:
        """本地校验通过后网络段移入后台 worker,不再阻塞主线程。"""
        manager = _make_manager()
        import app.utils.update_utils as update_utils_module

        with (
            patch.dict(update_utils_module.__dict__, {"__compiled__": {}}, clear=False),
            patch("app.utils.update_utils._ReleaseFetchWorker") as mock_worker_cls,
        ):
            worker = MagicMock()
            mock_worker_cls.return_value = worker
            manager.do_check_for_update()
            mock_worker_cls.assert_called_once()
            worker.start.assert_called_once()
            # 三种结果信号均已连接(成功/离线/API 失败)
            assert worker.fetched.connect.called
            assert worker.offline.connect.called
            assert worker.fetch_failed.connect.called

    def test_disabled_env_does_not_start_worker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RIMSORT_DISABLE_UPDATER", "1")
        manager = _make_manager()
        with patch("app.utils.update_utils._ReleaseFetchWorker") as mock_worker_cls:
            manager.do_check_for_update()
            mock_worker_cls.assert_not_called()


class TestReleaseFetchWorker:
    def test_offline_emits_offline(self) -> None:
        worker = _ReleaseFetchWorker()
        with patch(
            "app.utils.update_utils.check_internet_connection", return_value=False
        ) as mock_check:
            mock_check.return_value = False
            signals: list[tuple[str, Any]] = []
            worker.offline.connect(lambda: signals.append(("offline", None)))
            worker.fetched.connect(lambda d: signals.append(("fetched", d)))
            worker.fetch_failed.connect(lambda e: signals.append(("failed", e)))
            worker.run()
        assert signals == [("offline", None)]
        # 后台线程禁用弹窗
        mock_check.assert_called_once_with(show_error_dialog=False)

    def test_success_emits_fetched(self) -> None:
        worker = _ReleaseFetchWorker()
        response = MagicMock()
        response.json.return_value = {"tag_name": "v1.0.0"}
        result: dict[str, Any] = {}

        with (
            patch(
                "app.utils.update_utils.check_internet_connection", return_value=True
            ),
            patch("app.utils.update_utils.http.get", return_value=response) as mock_get,
        ):
            worker.fetched.connect(lambda d: result.update(d))
            worker.run()
            mock_get.assert_called_once()

        assert result == {"tag_name": "v1.0.0"}

    def test_request_failure_emits_fetch_failed(self) -> None:
        worker = _ReleaseFetchWorker()
        errors: list[str] = []
        with (
            patch(
                "app.utils.update_utils.check_internet_connection", return_value=True
            ),
            patch(
                "app.utils.update_utils.http.get",
                side_effect=requests.ConnectionError("blocked"),
            ),
        ):
            worker.fetch_failed.connect(lambda e: errors.append(e))
            worker.run()
        assert errors == ["blocked"]


class TestManagerSignalCallbacks:
    def test_offline_callback_shows_dialog(self) -> None:
        """离线回调在主线程补弹提示(保留原同步实现行为)。"""
        manager = _make_manager()
        with patch("app.utils.update_utils.dialogue") as mock_dialogue:
            manager._on_release_fetch_offline()
            mock_dialogue.show_internet_connection_error.assert_called_once()

    def test_fetch_failed_callback_shows_api_error(self) -> None:
        manager = _make_manager()
        with patch("app.utils.update_utils.dialogue") as mock_dialogue:
            manager._on_release_fetch_failed("boom")
            mock_dialogue.show_warning.assert_called_once()

    def test_fetched_callback_compares_versions(self) -> None:
        """成功回调走版本比较(已是最新则静默返回)。"""
        manager = _make_manager()
        release_data = {"tag_name": "v0.0.1", "assets": []}
        with (
            patch.object(
                manager, "_compare_versions_and_prompt", return_value=None
            ) as mock_compare,
            patch.object(manager, "_handle_update_process") as mock_handle,
        ):
            manager._on_release_data_fetched(release_data)
            mock_compare.assert_called_once_with(release_data)
            mock_handle.assert_not_called()
