"""Tests for CustomListWidgetItemMetadata.get_updated_timestamp_by_path.

Covers the "recently updated" timestamp resolution: only Steam Workshop /
SteamCMD mods yield a timestamp; every other source (and missing mods) return
None. The method holds no instance state, so we exercise it on a bare instance
created via ``object.__new__`` to avoid the heavy ``__init__``.
"""

from unittest.mock import MagicMock, patch

from app.models.metadata.metadata_structure import ModType
from app.utils.custom_list_widget_item_metadata import CustomListWidgetItemMetadata

MODULE = "app.utils.custom_list_widget_item_metadata"


def _bare_instance() -> CustomListWidgetItemMetadata:
    """A CustomListWidgetItemMetadata without running __init__."""
    return object.__new__(CustomListWidgetItemMetadata)


def _mod(mod_type: ModType) -> MagicMock:
    mod = MagicMock()
    mod.mod_type = mod_type
    return mod


def _aux_entry(acf: int = -1, external: int = -1) -> MagicMock:
    entry = MagicMock()
    entry.acf_time_updated = acf
    entry.external_time_updated = external
    return entry


class TestGetUpdatedTimestampByPath:
    def test_workshop_mod_returns_acf_timestamp(self) -> None:
        """A Steam Workshop mod resolves to its aux DB acf_time_updated."""
        with (
            patch(f"{MODULE}.MetadataController.instance") as mock_instance,
            patch(f"{MODULE}.auxdb_get_aux_db_entry") as mock_aux,
        ):
            mock_instance.return_value.get_mod.return_value = _mod(
                ModType.STEAM_WORKSHOP
            )
            mock_aux.return_value = _aux_entry(acf=12345, external=999)

            result = _bare_instance().get_updated_timestamp_by_path(
                "/mods/ws", MagicMock(), None, None
            )

        assert result == 12345
        mock_aux.assert_called_once()

    def test_steamcmd_mod_falls_back_to_external(self) -> None:
        """A SteamCMD mod with no ACF time falls back to external_time_updated."""
        with (
            patch(f"{MODULE}.MetadataController.instance") as mock_instance,
            patch(f"{MODULE}.auxdb_get_aux_db_entry") as mock_aux,
        ):
            mock_instance.return_value.get_mod.return_value = _mod(ModType.STEAM_CMD)
            mock_aux.return_value = _aux_entry(acf=-1, external=888)

            result = _bare_instance().get_updated_timestamp_by_path(
                "/mods/cmd", MagicMock(), None, None
            )

        assert result == 888

    def test_local_mod_is_skipped(self) -> None:
        """Local mods are never flagged and never hit the aux DB."""
        with (
            patch(f"{MODULE}.MetadataController.instance") as mock_instance,
            patch(f"{MODULE}.auxdb_get_aux_db_entry") as mock_aux,
        ):
            mock_instance.return_value.get_mod.return_value = _mod(ModType.LOCAL)

            result = _bare_instance().get_updated_timestamp_by_path(
                "/mods/local", MagicMock(), None, None
            )

        assert result is None
        mock_aux.assert_not_called()

    def test_git_mod_is_skipped(self) -> None:
        with (
            patch(f"{MODULE}.MetadataController.instance") as mock_instance,
            patch(f"{MODULE}.auxdb_get_aux_db_entry") as mock_aux,
        ):
            mock_instance.return_value.get_mod.return_value = _mod(ModType.GIT)

            result = _bare_instance().get_updated_timestamp_by_path(
                "/mods/git", MagicMock(), None, None
            )

        assert result is None
        mock_aux.assert_not_called()

    def test_ludeon_mod_is_skipped(self) -> None:
        with (
            patch(f"{MODULE}.MetadataController.instance") as mock_instance,
            patch(f"{MODULE}.auxdb_get_aux_db_entry") as mock_aux,
        ):
            mock_instance.return_value.get_mod.return_value = _mod(ModType.LUDEON)

            result = _bare_instance().get_updated_timestamp_by_path(
                "/mods/core", MagicMock(), None, None
            )

        assert result is None
        mock_aux.assert_not_called()

    def test_missing_mod_returns_none(self) -> None:
        """When get_mod returns None, no timestamp is produced."""
        with (
            patch(f"{MODULE}.MetadataController.instance") as mock_instance,
            patch(f"{MODULE}.auxdb_get_aux_db_entry") as mock_aux,
        ):
            mock_instance.return_value.get_mod.return_value = None

            result = _bare_instance().get_updated_timestamp_by_path(
                "/mods/missing", MagicMock(), None, None
            )

        assert result is None
        mock_aux.assert_not_called()

    def test_keyerror_returns_none(self) -> None:
        """A KeyError from get_mod is swallowed and yields None."""
        with (
            patch(f"{MODULE}.MetadataController.instance") as mock_instance,
            patch(f"{MODULE}.auxdb_get_aux_db_entry") as mock_aux,
        ):
            mock_instance.return_value.get_mod.side_effect = KeyError("nope")

            result = _bare_instance().get_updated_timestamp_by_path(
                "/mods/err", MagicMock(), None, None
            )

        assert result is None
        mock_aux.assert_not_called()

    def test_workshop_mod_without_aux_entry_returns_none(self) -> None:
        """A workshop mod with no aux DB entry resolves to None."""
        with (
            patch(f"{MODULE}.MetadataController.instance") as mock_instance,
            patch(f"{MODULE}.auxdb_get_aux_db_entry") as mock_aux,
        ):
            mock_instance.return_value.get_mod.return_value = _mod(
                ModType.STEAM_WORKSHOP
            )
            mock_aux.return_value = None

            result = _bare_instance().get_updated_timestamp_by_path(
                "/mods/ws", MagicMock(), None, None
            )

        assert result is None


def _full_entry(
    ignore_warnings: bool = True,
    color_hex: str = "#ff0000",
    tags: list[str] | None = None,
    user_notes: str = "note",
) -> MagicMock:
    """A prefetched AuxMetadataEntry stub with all fields consumed by __init__."""
    entry = MagicMock()
    entry.ignore_warnings = ignore_warnings
    entry.color_hex = color_hex
    entry.tags = [MagicMock(tag=t) for t in (tags if tags is not None else ["zeta"])]
    entry.user_notes = user_notes
    return entry


def _init_settings(mod_list_updated_indicator: bool = False) -> MagicMock:
    """Settings stub exposing the updated-indicator flag used by __init__."""
    settings = MagicMock()
    settings.mod_list_updated_indicator = mod_list_updated_indicator
    return settings


class TestInitWithAuxEntry:
    """__init__ extracts aux fields from a prefetched entry without DB hits."""

    def test_aux_entry_extracts_fields_without_db_queries(self) -> None:
        """Fields come from the entry; no per-item aux DB query is issued."""
        with (
            patch(f"{MODULE}.MetadataController.instance") as mock_instance,
            patch(f"{MODULE}.auxdb_get_mod_warning_toggled") as mock_warning,
            patch(f"{MODULE}.auxdb_get_mod_color") as mock_color,
            patch(f"{MODULE}.auxdb_get_mod_tags") as mock_tags,
            patch(f"{MODULE}.auxdb_get_mod_user_notes") as mock_notes,
            patch(f"{MODULE}.auxdb_get_aux_db_entry") as mock_aux,
        ):
            # invalid/mismatch/alternative probes resolve to None values
            mock_instance.return_value.get_mod.return_value = None

            item = CustomListWidgetItemMetadata(
                path="/mods/test",
                settings=_init_settings(),
                aux_entry=_full_entry(),
            )

        for mock in (mock_warning, mock_color, mock_tags, mock_notes, mock_aux):
            mock.assert_not_called()
        assert item.warning_toggled is True
        assert item.mod_color is not None and item.mod_color.name() == "#ff0000"
        assert item.mod_tags == ["zeta"]
        assert item.user_notes == "note"

    def test_aux_entry_resolves_timestamp_for_workshop_mod(self) -> None:
        """With the indicator on, a workshop mod resolves its timestamp from the entry."""
        with (
            patch(f"{MODULE}.MetadataController.instance") as mock_instance,
            patch(f"{MODULE}.resolve_workshop_updated_timestamp") as mock_resolve,
        ):
            mock_instance.return_value.get_mod.return_value = _mod(
                ModType.STEAM_WORKSHOP
            )
            mock_resolve.return_value = 555

            entry = _full_entry()
            item = CustomListWidgetItemMetadata(
                path="/mods/ws",
                settings=_init_settings(mod_list_updated_indicator=True),
                aux_entry=entry,
            )

        mock_resolve.assert_called_once_with(entry)
        assert item.updated_timestamp == 555


class TestGetUpdatedTimestampFromEntry:
    def test_workshop_mod_resolves_from_entry(self) -> None:
        """A Steam Workshop mod resolves its timestamp via the prefetched entry."""
        with (
            patch(f"{MODULE}.MetadataController.instance") as mock_instance,
            patch(f"{MODULE}.resolve_workshop_updated_timestamp") as mock_resolve,
        ):
            mock_instance.return_value.get_mod.return_value = _mod(
                ModType.STEAM_WORKSHOP
            )
            mock_resolve.return_value = 777

            entry = _aux_entry(acf=777)
            result = _bare_instance().get_updated_timestamp_from_entry(
                "/mods/ws", entry
            )

        mock_resolve.assert_called_once_with(entry)
        assert result == 777

    def test_local_mod_returns_none(self) -> None:
        """A local mod never resolves a workshop timestamp."""
        with patch(f"{MODULE}.MetadataController.instance") as mock_instance:
            mock_instance.return_value.get_mod.return_value = _mod(ModType.LOCAL)

            result = _bare_instance().get_updated_timestamp_from_entry(
                "/mods/local", _aux_entry(acf=777)
            )

        assert result is None

    def test_missing_mod_returns_none(self) -> None:
        """A KeyError from get_mod is swallowed and yields None."""
        with patch(f"{MODULE}.MetadataController.instance") as mock_instance:
            mock_instance.return_value.get_mod.side_effect = KeyError("nope")

            result = _bare_instance().get_updated_timestamp_from_entry(
                "/mods/err", _aux_entry(acf=777)
            )

        assert result is None
