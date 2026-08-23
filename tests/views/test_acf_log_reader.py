"""Tests for AcfLogReader._populate_from_metadata refresh guard.

The panel is populated from two signals: metadata_refreshed (wired by
BaseModsPanel, arrives mid-flow inside the loading animation's nested
event loop) and refresh_finished (wired by start_acf_reader, emitted at
the end of _do_refresh). During the main refresh flow the first trigger
must be skipped so the panel is populated exactly once, with complete
data. These tests exercise the guard on a bare instance created via
``object.__new__`` to avoid the heavy ``__init__``.
"""

from unittest.mock import MagicMock

from PySide6.QtWidgets import QApplication
from pytest import MonkeyPatch

from app.views.acf_log_reader import AcfLogReader

MAIN_CONTENT_CLS = "app.views.main_content_panel.MainContent"


def _bare_reader() -> AcfLogReader:
    """An AcfLogReader without running __init__, with populate steps mocked.

    Fault injection note: private attributes and helper methods are
    replaced directly to isolate the guard logic from the full GUI stack.
    """
    reader = AcfLogReader.__new__(AcfLogReader)
    reader.metadata_controller = MagicMock()
    reader.editor_table_view = MagicMock()
    reader.editor_model = MagicMock()
    reader._is_first_population = False
    reader._update_active_pfids = MagicMock()  # type: ignore[method-assign]
    reader._clear_table_model = MagicMock()  # type: ignore[method-assign]
    reader._extract_acf_entries = MagicMock(return_value=[])  # type: ignore[method-assign]
    reader._get_acf_mods_from_metadata = MagicMock(return_value={})  # type: ignore[method-assign]
    reader._batch_add_acf_rows = MagicMock()  # type: ignore[method-assign]
    return reader


def _main_content_stub(refresh_in_progress: bool) -> MagicMock:
    stub = MagicMock()
    stub.refresh_in_progress = refresh_in_progress
    return stub


class TestPopulateFromMetadataGuard:
    def test_skips_population_while_refresh_in_progress(
        self, monkeypatch: MonkeyPatch, qapp: QApplication
    ) -> None:
        """A mid-flow metadata_refreshed trigger must be skipped."""
        monkeypatch.setattr(
            f"{MAIN_CONTENT_CLS}._instance",
            _main_content_stub(refresh_in_progress=True),
        )
        reader = _bare_reader()

        reader._populate_from_metadata()

        reader._update_active_pfids.assert_not_called()
        reader._clear_table_model.assert_not_called()

    def test_populates_when_refresh_not_in_progress(
        self, monkeypatch: MonkeyPatch, qapp: QApplication
    ) -> None:
        """An out-of-flow metadata_refreshed (e.g. cache refresh) populates."""
        monkeypatch.setattr(
            f"{MAIN_CONTENT_CLS}._instance",
            _main_content_stub(refresh_in_progress=False),
        )
        reader = _bare_reader()

        reader._populate_from_metadata()

        reader._update_active_pfids.assert_called_once()
        reader._clear_table_model.assert_called_once()
        reader._batch_add_acf_rows.assert_called_once_with([], {})

    def test_populates_when_main_content_not_created(
        self, monkeypatch: MonkeyPatch, qapp: QApplication
    ) -> None:
        """No MainContent instance must not be mistaken for a refresh."""
        monkeypatch.setattr(f"{MAIN_CONTENT_CLS}._instance", None)
        reader = _bare_reader()

        reader._populate_from_metadata()

        reader._clear_table_model.assert_called_once()
