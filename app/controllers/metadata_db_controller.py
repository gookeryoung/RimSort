from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.models.metadata.metadata_db import AuxMetadataEntry, Base
from app.models.metadata.metadata_structure import ModType
from app.utils.steam.steamfiles.wrapper import acf_to_dict


class MetadataDbController:
    def __init__(self, db: Path | str) -> None:
        # Ensure parent directory exists before opening SQLite file
        db_path = Path(db) if not isinstance(db, Path) else db
        try:
            if db_path.parent:
                db_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.exception(
                f"Failed to ensure database directory exists for {db_path}: {e}"
            )

        self.engine = create_engine(f"sqlite+pysqlite:///{db_path}")
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)


class AuxMetadataController(MetadataDbController):
    _instances: dict[
        Path, "AuxMetadataController"
    ] = {}  # db_path : AuxMetadataController

    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path)
        Base.metadata.create_all(self.engine)
        self._migrate_schema()

    def _migrate_schema(self) -> None:
        """Add columns that may be missing from older database versions
        and migrate column types that have changed."""
        with self.engine.connect() as conn:
            rows = conn.execute(text("PRAGMA table_info(auxiliary_metadata)"))
            columns = {row[1]: row for row in rows}
            existing_columns = set(columns.keys())
            for col in ("external_time_created", "external_time_updated"):
                if col not in existing_columns:
                    conn.execute(
                        text(
                            f"ALTER TABLE auxiliary_metadata ADD COLUMN {col} INTEGER DEFAULT -1"
                        )
                    )
                    logger.info(f"Migrated auxiliary_metadata: added column '{col}'")

            # Migrate published_file_id from NOT NULL INTEGER to nullable TEXT.
            # Can't UPDATE to NULL on the old table (NOT NULL constraint),
            # so we rebuild via rename-create-copy-drop with conversion in the COPY.
            if "published_file_id" in columns:
                col_info = columns["published_file_id"]
                col_notnull = col_info[3]  # notnull flag
                col_type = col_info[2]  # type string
                if col_notnull or col_type.upper() == "INTEGER":
                    old_col_names = list(columns.keys())
                    conn.execute(
                        text(
                            "ALTER TABLE auxiliary_metadata "
                            "RENAME TO _auxiliary_metadata_old"
                        )
                    )
                    conn.commit()
                    Base.metadata.create_all(self.engine)
                    new_rows = conn.execute(
                        text("PRAGMA table_info(auxiliary_metadata)")
                    )
                    new_col_set = {row[1] for row in new_rows}
                    shared = [c for c in old_col_names if c in new_col_set]
                    select_exprs = []
                    for col in shared:
                        if col == "published_file_id":
                            select_exprs.append(
                                "CASE WHEN published_file_id IN (-1, '-1') "
                                "THEN NULL "
                                "ELSE CAST(published_file_id AS TEXT) END"
                            )
                        else:
                            select_exprs.append(col)
                    col_list = ", ".join(shared)
                    select_list = ", ".join(select_exprs)
                    conn.execute(
                        text(
                            f"INSERT INTO auxiliary_metadata ({col_list}) "
                            f"SELECT {select_list} FROM _auxiliary_metadata_old"
                        )
                    )
                    conn.execute(text("DROP TABLE _auxiliary_metadata_old"))
                    logger.info(
                        "Migrated auxiliary_metadata: "
                        "published_file_id NOT NULL INTEGER -> nullable TEXT"
                    )

            conn.commit()

    @classmethod
    def get_or_create_cached_instance(cls, db_path: Path) -> "AuxMetadataController":
        """
        Get or create a cached instance of the controller.
        This cached controller is only for the specified db_path.
        """
        if db_path not in cls._instances:
            cls._instances[db_path] = cls(db_path)
        return cls._instances[db_path]

    @staticmethod
    def update(
        session: Session, item_path: Path | str, **kwargs: Any
    ) -> AuxMetadataEntry | None:
        """
        Update an aux metadata entry by the mod path.

        :param session: The database session.
        :type session: Session
        :param item_path: The key path.
        :type item_path: Path | str
        :param kwargs: The fields to update.
        :return: The updated aux metadata entry if found, otherwise None.
        :rtype: AuxMetadataEntry | None
        """
        if isinstance(item_path, Path):
            item_path = str(item_path)

        entry = AuxMetadataController.get(session, item_path)
        if entry is None:
            return None

        for key, value in kwargs.items():
            setattr(entry, key, value)

        try:
            session.commit()
        except Exception as e:
            session.rollback()
            logger.exception(f"Failed to update aux metadata entry: {e}")
            raise e

        return entry

    @staticmethod
    def get(session: Session, item_path: Path | str) -> AuxMetadataEntry | None:
        """Get an aux metadata entry by the key path.

        :param session: The database session.
        :type session: Session
        :param item_path: The key path.
        :type item_path: Path | str
        :return: The aux metadata entry if found, otherwise None.
        :rtype: AuxMetadataEntry | None
        """
        if isinstance(item_path, Path):
            item_path = str(item_path)

        return (
            session.query(AuxMetadataEntry)
            .filter(AuxMetadataEntry.path == item_path)
            .first()
        )

    @staticmethod
    def get_or_create(session: Session, item_path: Path | str) -> AuxMetadataEntry:
        """Get or create an aux metadata entry by the key path.

        :param session: The database session.
        :type session: Session
        :param item_path: The key path.
        :type item_path: Path | str
        :return: The aux metadata entry.
        :rtype: AuxMetadataEntry
        """

        if isinstance(item_path, Path):
            item_path = str(item_path)

        entry = AuxMetadataController.get(session, item_path)

        if entry is None:
            entry = AuxMetadataEntry(path=item_path)
            try:
                with session.begin_nested():
                    session.add(entry)
                    session.flush()
            except IntegrityError:
                session.rollback()
                # Query again to get the existing entry
                entry = AuxMetadataController.get(session, item_path)
                if entry is None:
                    logger.error(
                        f"Failed to create or retrieve aux metadata entry for path: {item_path}"
                    )
                    raise RuntimeError(
                        f"Failed to create or retrieve aux metadata entry for path: {item_path}"
                    )
            except Exception as e:
                session.rollback()
                logger.exception(f"Failed to create new aux metadata entry: {e}")
                raise e

        return entry

    @staticmethod
    def get_many(
        session: Session, item_paths: Sequence[Path | str]
    ) -> dict[str, AuxMetadataEntry]:
        """批量查询多个 path 对应的 aux metadata entry。

        用单次 ``IN`` 查询替代 N 次 ``get``,将排序与列表重建等场景的
        数据库往返从 N 次降到 1 次。

        :param session: 数据库会话
        :param item_paths: 待查询的 path 列表
        :return: ``{path_str: entry}`` 字典,未命中的 path 不包含在结果中
        """
        if not item_paths:
            return {}
        normalized = [str(p) if isinstance(p, Path) else p for p in item_paths]
        entries = (
            session.query(AuxMetadataEntry)
            .filter(AuxMetadataEntry.path.in_(normalized))
            .all()
        )
        return {entry.path: entry for entry in entries}

    @staticmethod
    def upsert_many(
        session: Session,
        item_paths: Sequence[Path | str],
        default_factory: Any = None,
        **update_fields: Any,
    ) -> dict[str, AuxMetadataEntry]:
        """批量 get-or-create + update,共用一次 commit。

        适用于 ``recreate_mod_list`` 等"对每个 path 都 get_or_create +
        update 相同字段"的场景,将 N 次 session 创建 + N 次 commit 降到
        1 次 session + 1 次 commit。

        :param session: 数据库会话
        :param item_paths: 待处理的 path 列表
        :param default_factory: 可选,用于创建新 entry 的工厂函数,
            签名 ``() -> AuxMetadataEntry``;默认用 ``AuxMetadataEntry(path=...)``
        :param update_fields: 对所有 entry(含新建与已存在)设置的公共字段
        :return: ``{path_str: entry}`` 字典,包含所有 item_paths 对应的 entry
        """
        if not item_paths:
            return {}
        normalized = [str(p) if isinstance(p, Path) else p for p in item_paths]
        existing = AuxMetadataController.get_many(session, normalized)
        new_entries: list[AuxMetadataEntry] = []
        for path in normalized:
            if path not in existing:
                if default_factory is not None:
                    entry = default_factory()
                    entry.path = path
                else:
                    entry = AuxMetadataEntry(path=path)
                for k, v in update_fields.items():
                    setattr(entry, k, v)
                new_entries.append(entry)
                existing[path] = entry
            else:
                entry = existing[path]
                for k, v in update_fields.items():
                    setattr(entry, k, v)
        if new_entries:
            session.add_all(new_entries)
        try:
            session.commit()
        except Exception as e:
            session.rollback()
            logger.exception(f"Failed to upsert {len(normalized)} aux metadata entries: {e}")
            raise e
        return existing

    @staticmethod
    def get_value_equals(
        session: Session, key: str, value: str
    ) -> list[AuxMetadataEntry]:
        """Get aux metadata entries where the key equals the value.

        :param session: The database session.
        :type session: Session
        :param key: The key to search for.
        :type key: str
        :param value: The value to search for.
        :type value: str
        :return: A list of aux metadata entries.
        :rtype: list[AuxMetadataEntry
        """
        return (
            session.query(AuxMetadataEntry)
            .filter(getattr(AuxMetadataEntry, key) == value)
            .all()
        )

    @staticmethod
    def query(session: Session, query: str) -> list[AuxMetadataEntry]:
        """Query the aux metadata entries.

        :param session: The database session.
        :type session: Session
        :param query: The query string.
        :type query: str
        :return: A list of aux metadata entries.
        :rtype: list[AuxMetadataEntry]
        """
        result = session.execute(text(query)).all()
        return [AuxMetadataEntry(**row._mapping) for row in result]

    @staticmethod
    def add(
        session: Session, item: AuxMetadataEntry | Iterable[AuxMetadataEntry]
    ) -> None:
        """Add an aux metadata entry or entries to the database.

        :param session: The database session.
        :type session: Session
        :param item: The aux metadata entry or entries to add.
        :type item: AuxMetadataEntry | Iterable[AuxMetadataEntry]
        """
        if isinstance(item, AuxMetadataEntry):
            session.add(item)
        else:
            session.add_all(item)

        session.commit()

    @staticmethod
    def delete(session: Session, *paths: Path) -> None:
        """Delete mod(s) from database."""

        for path in paths:
            session.query(AuxMetadataEntry).filter(
                AuxMetadataEntry.path == str(path)
            ).delete()

        session.commit()

    def reset(self) -> None:
        """Reset the database by dropping all tables and recreating them."""
        Base.metadata.drop_all(self.engine)
        Base.metadata.create_all(self.engine)

    @staticmethod
    def update_from_acf(session: Session, acf_path: Path, mod_type: ModType) -> None:
        """Update the aux metadata from an ACF file.

        :param session: The database session.
        :type session: Session
        :param acf_path: The path to the ACF file.
        :type acf_path: Path
        :param mod_type: The mod type.
        :type mod_type: ModType
        """
        if not acf_path.exists():
            logger.warning(f".acf file not found at {acf_path}.")
            return

        try:
            acf_data = acf_to_dict(str(acf_path))
        except Exception as e:
            logger.error(f"Error reading .acf file at {acf_path}: {e}")
            return

        workshop_items = {
            published_file_id: data
            for published_file_id, data in acf_data.get("AppWorkshop", {})
            .get("WorkshopItemDetails", {})
            .items()
        }

        entries = (
            session.query(AuxMetadataEntry)
            .filter(
                AuxMetadataEntry.published_file_id.in_(workshop_items.keys()),
                AuxMetadataEntry.type == str(mod_type),
            )
            .all()
        )

        for entry in entries:
            data = workshop_items.get(str(entry.published_file_id), None)
            if data is None:
                continue

            entry.acf_time_updated = data.get("timeupdated", -1)
            entry.acf_time_touched = data.get("timetouched", -1)

        session.commit()
