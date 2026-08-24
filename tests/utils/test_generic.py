from unittest.mock import MagicMock, patch

import requests

from app.utils.generic import (
    check_internet_connection,
    check_valid_http_git_url,
    extract_git_dir_name,
    extract_git_user_or_org,
    restart_application,
)

GIT_URLS = [
    "https://github.com/org/RimSort.git",
    "https://github.com/org/RimSort",
    "https://github.com/org/RimSort/",
    "http://github.com/org/RimSort.git",
    "github.com/org/RimSort.git",
    "github.com/org/RimSort",
    "github.com/org/RimSort/",
]


def test_get_git_dir_name() -> None:
    for url in GIT_URLS:
        assert extract_git_dir_name(url) == "RimSort"


def test_get_git_org_or_user() -> None:
    for url in GIT_URLS:
        assert extract_git_user_or_org(url) == "org"


def test_check_valid_http_git_url() -> None:
    assert check_valid_http_git_url("") is False

    assert check_valid_http_git_url("github.com/org/RimSort.git") is False

    assert check_valid_http_git_url("https://github.com/org/RimSort.git") is True

    assert check_valid_http_git_url("http://github.com/org/RimSort.git/") is True


class TestRestartApplication:
    def test_non_frozen_reconstructs_module_invocation(self) -> None:
        """Regression test: `python -m app` sets sys.argv[0] to the resolved
        path of app/__main__.py, not "-m app". Re-launching with
        [sys.executable] + sys.argv (the old behavior) ran that path as a
        plain script, which put app/ instead of the project root on
        sys.path and broke `from app...` imports on restart."""
        with (
            patch("app.utils.generic.sys") as mock_sys,
            patch("app.utils.generic.subprocess.Popen") as mock_popen,
            patch("app.utils.generic.QApplication") as mock_qapp,
        ):
            mock_sys.frozen = False
            mock_sys.executable = "C:\\Python\\python.exe"
            mock_sys.argv = ["C:\\RimSort\\app\\__main__.py", "--extra"]
            mock_qapp.instance.return_value = MagicMock()

            restart_application()

            mock_popen.assert_called_once_with(
                ["C:\\Python\\python.exe", "-m", "app", "--extra"]
            )

    def test_frozen_reuses_executable_directly(self) -> None:
        with (
            patch("app.utils.generic.sys") as mock_sys,
            patch("app.utils.generic.subprocess.Popen") as mock_popen,
            patch("app.utils.generic.QApplication") as mock_qapp,
        ):
            mock_sys.frozen = True
            mock_sys.executable = "C:\\RimSort\\RimSort.exe"
            mock_sys.argv = ["C:\\RimSort\\RimSort.exe", "--extra"]
            mock_qapp.instance.return_value = MagicMock()

            restart_application()

            mock_popen.assert_called_once_with(["C:\\RimSort\\RimSort.exe", "--extra"])


class TestCheckInternetConnection:
    """连通性探测的快速回退行为。"""

    def test_any_url_reachable_returns_true(self) -> None:
        """任一目标可达即在线(第二个失败不影响结果)。"""

        def _head(url: str, **kwargs: object) -> MagicMock:
            if url == "https://github.com":
                raise requests.ConnectionError("blocked")
            return MagicMock()

        with patch("app.utils.generic.requests.head", side_effect=_head):
            assert check_internet_connection(show_error_dialog=False) is True

    def test_all_unreachable_returns_false_without_dialog(self) -> None:
        """全部不可达返回 False;show_error_dialog=False 时不弹窗。"""
        with (
            patch(
                "app.utils.generic.requests.head",
                side_effect=requests.ConnectionError("timeout"),
            ) as mock_head,
            patch("app.utils.generic.dialogue") as mock_dialogue,
        ):
            assert check_internet_connection(show_error_dialog=False) is False
            # 两个目标都被探测(并行)
            assert mock_head.call_count == 2
            mock_dialogue.show_internet_connection_error.assert_not_called()

    def test_all_unreachable_shows_dialog_when_enabled(self) -> None:
        """show_error_dialog=True(默认)时全部失败弹提示框。"""
        with (
            patch(
                "app.utils.generic.requests.head",
                side_effect=requests.ConnectTimeout("timeout"),
            ),
            patch("app.utils.generic.dialogue") as mock_dialogue,
        ):
            assert check_internet_connection() is False
            mock_dialogue.show_internet_connection_error.assert_called_once()

    def test_probe_uses_short_timeout(self) -> None:
        """探测使用短超时(连接 2s/读 3s),不走重试会话。"""
        with patch("app.utils.generic.requests.head") as mock_head:
            check_internet_connection(show_error_dialog=False)
            for call in mock_head.call_args_list:
                assert call.kwargs["timeout"] == (2, 3)
