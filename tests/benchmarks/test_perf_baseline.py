"""性能基线基准测试。

为 RimSort 已识别的性能热点建立可复现基线,供后续优化对比与 CI 回归门禁使用。
遵循 ``python-performance`` SKILL 的"未测量不优化"原则。

运行方式::

    # 建立基线
    .venv\\Scripts\\python.exe -m pytest tests/benchmarks/test_perf_baseline.py \\
        --benchmark-save=baseline \\
        --benchmark-min-rounds=10 \\
        --benchmark-warmup=on

    # 对比基线(优化后)
    .venv\\Scripts\\python.exe -m pytest tests/benchmarks/test_perf_baseline.py \\
        --benchmark-compare \\
        --benchmark-compare-fail=mean:10%

硬件信息会随 ``--benchmark-save`` 一并保存到 ``.benchmarks/`` 下的 JSON 文件,
便于后续对比时确认硬件一致性。
"""

from __future__ import annotations

import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.controllers.metadata_controller import MetadataController
from app.models.metadata.metadata_structure import (
    AboutXmlMod,
    CaseInsensitiveStr,
    ModType,
)
from app.sort.mod_sorting import ModsPanelSortKey, sort_paths
from app.utils.xml import xml_path_to_json

# 测试数据路径
_MOD_EXAMPLES = Path(__file__).parent.parent / "data" / "mod_examples"
_LOCAL_MOD_1_ABOUT = _MOD_EXAMPLES / "Local" / "local_mod_1" / "About" / "About.xml"


# ---------------------------------------------------------------------------
# 辅助:构造 mock MetadataController
# ---------------------------------------------------------------------------


def _make_about_xml_mod(
    package_id: str,
    mod_type: ModType = ModType.LOCAL,
    path_str: str | None = None,
) -> AboutXmlMod:
    """构造一个最小可用的 AboutXmlMod 实例。"""
    mod = AboutXmlMod(package_id=CaseInsensitiveStr(package_id))
    mod._mod_type = mod_type  # 写入私有字段,绕过 property 校验
    if path_str is not None:
        mod.mod_path = Path(path_str)
    return mod


def _build_metadata_controller(
    n_installed_mods: int,
) -> types.SimpleNamespace:
    """构造一个仅含 ``mods_metadata`` 与 ``game_version`` 的轻量 controller。

    ``get_mods_from_list`` 只读取这两个属性,因此 SimpleNamespace 足够。
    """
    mods_metadata: dict[str, AboutXmlMod] = {}
    for i in range(n_installed_mods):
        pid = f"author.mod{i}"
        path = f"/fake/local/mod_{i}"
        mods_metadata[path] = _make_about_xml_mod(pid, ModType.LOCAL, path)

    controller = types.SimpleNamespace(
        mods_metadata=mods_metadata,
        game_version="1.5",
    )
    # 把 unbound method 绑定到 SimpleNamespace 上
    controller.get_mods_from_list = types.MethodType(
        MetadataController.get_mods_from_list, controller
    )
    return controller


# ---------------------------------------------------------------------------
# 基准 1: xml_path_to_json 解析标准 About.xml
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def about_xml_path() -> Path:
    """标准 About.xml 测试文件路径。"""
    return _LOCAL_MOD_1_ABOUT


def test_xml_path_to_json_baseline(benchmark, about_xml_path: Path) -> None:
    """xml_path_to_json 解析标准 About.xml 的基线。

    该函数在 metadata 扫描时被每个 mod 调用一次,累积开销显著。
    基线值用于评估 P4 阶段切换到 lxml.etree 的收益。
    """
    result = benchmark(xml_path_to_json, str(about_xml_path))
    assert isinstance(result, dict)
    assert "ModMetaData" in result


# ---------------------------------------------------------------------------
# 基准 2: get_mods_from_list 在不同规模下的耗时(O(n²) 算法)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "n_installed, n_active",
    [
        (200, 100),  # 小规模
        (1000, 500),  # 中规模
        (5000, 1000),  # 大规模(典型用户场景)
    ],
    ids=["small-200x100", "medium-1000x500", "large-5000x1000"],
)
def test_get_mods_from_list_baseline(
    benchmark,
    n_installed: int,
    n_active: int,
) -> None:
    """get_mods_from_list 算法基线。

    当前实现为 O(M×N) 嵌套循环。本基准在不同规模下记录耗时,
    为 P2 阶段反向索引优化提供对比数据。预期大规模场景下
    优化后耗时降低 ≥ 80%。
    """
    controller = _build_metadata_controller(n_installed)
    # 取前 n_active 个 package_id 作为 active 列表
    package_ids = [
        str(mod.package_id)
        for mod in list(controller.mods_metadata.values())[:n_active]
    ]

    # 用 list 形式传入,跳过 XML 解析,只测核心算法
    active, inactive, duplicates, missing = benchmark(
        controller.get_mods_from_list, package_ids
    )
    assert len(active) + len(missing) == n_active


# ---------------------------------------------------------------------------
# 基准 3: sort_paths 各 sort key
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sort_paths_data() -> list[str]:
    """构造 2000 个 path 用于排序基准。"""
    return [f"/fake/local/mod_{i}" for i in range(2000)]


@pytest.fixture(scope="module")
def sort_paths_metadata_controller() -> types.SimpleNamespace:
    """为 sort_paths 提供 mock 元数据(2000 项)。"""
    return _build_metadata_controller(2000)


@pytest.mark.parametrize(
    "sort_key",
    [
        ModsPanelSortKey.PACKAGEID,
        ModsPanelSortKey.FILESYSTEM_MODIFIED_TIME,
    ],
    ids=["by-packageid", "by-mtime"],
)
def test_sort_paths_baseline(
    benchmark,
    sort_paths_data: list[str],
    sort_paths_metadata_controller: types.SimpleNamespace,
    sort_key: ModsPanelSortKey,
) -> None:
    """sort_paths 排序基线。

    测试主要排序键的耗时。FILESYSTEM_MODIFIED_TIME 路径会触发
    ``path_to_folder_size`` 的目录扫描与 mtime 获取,预期较慢。
    """
    # patch MetadataController.instance() 以返回 mock
    from app.controllers import metadata_controller as mc_module
    from app.sort import mod_sorting as ms_module

    original_instance = MetadataController.instance
    try:
        MetadataController.instance = classmethod(  # type: ignore[assignment]
            lambda cls, **kw: sort_paths_metadata_controller
        )
        # 清空 folder size 缓存避免跨用例污染
        ms_module._FOLDER_SIZE_CACHE.clear()

        result = benchmark(
            sort_paths,
            sort_paths_data,
            key=sort_key,
            descending=False,
        )
        assert len(result) == len(sort_paths_data)
    finally:
        MetadataController.instance = original_instance  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# 基准 4: AuxMetadataController 单条 vs 批量(模拟 recreate_mod_list 内循环)
# ---------------------------------------------------------------------------


@pytest.fixture
def aux_controller(tmp_path: Path):
    """构造一个临时 SQLite aux metadata controller。"""
    from app.controllers.metadata_db_controller import AuxMetadataController

    db_path = tmp_path / "aux.db"
    return AuxMetadataController(db_path)


def test_aux_metadata_session_per_item_baseline(
    benchmark,
    aux_controller,
    tmp_path: Path,
) -> None:
    """循环内为每项创建 session 的开销基线(模拟当前 recreate_mod_list 行为)。

    当前实现:N 次 Session() + N 次 get_or_create + N 次 update + N 次 commit。
    本基准记录该模式在 500 项下的耗时,为 P2 阶段"session 提到循环外 + 批量预取"
    优化提供对比数据。预期优化后耗时降低 ≥ 70%。
    """
    n_items = 500
    paths = [str(tmp_path / f"mod_{i}") for i in range(n_items)]

    def per_item_session_workflow() -> None:
        for path in paths:
            with aux_controller.Session() as session:
                aux_controller.get_or_create(session, path)
                aux_controller.update(session, path, outdated=False)

    benchmark(per_item_session_workflow)


def test_aux_metadata_batch_session_baseline(
    benchmark,
    aux_controller,
    tmp_path: Path,
) -> None:
    """批量查询 + 单次 commit 的开销基线(P2 优化目标对照)。

    与 ``test_aux_metadata_session_per_item_baseline`` 对比,验证
    "批量预取 + 一次 commit"的收益。本基准模拟 P2 阶段优化后的写法:
    一次 IN 查询 + 内存修改 + 一次 commit,而非 N 次 get_or_create+update。

    注意:为公平对比,本基准也处理"项不存在需创建"的场景,
    但所有创建与修改共用一次 commit。
    """
    from app.models.metadata.metadata_db import AuxMetadataEntry

    n_items = 500
    paths = [str(tmp_path / f"mod_{i}") for i in range(n_items)]

    def batch_workflow() -> None:
        with aux_controller.Session() as session:
            # 一次 IN 查询所有现有 entry
            existing = {
                entry.path: entry
                for entry in session.query(AuxMetadataEntry)
                .filter(AuxMetadataEntry.path.in_(paths))
                .all()
            }
            # 内存中创建缺失项 + 标记所有项 outdated=False
            new_entries: list[AuxMetadataEntry] = []
            for path in paths:
                entry = existing.get(path)
                if entry is None:
                    new_entries.append(AuxMetadataEntry(path=path, outdated=False))
                else:
                    entry.outdated = False
            if new_entries:
                session.add_all(new_entries)
            session.commit()  # 单次 commit

    benchmark(batch_workflow)


# ---------------------------------------------------------------------------
# 基准 5: _find_about_xml 标准路径 vs iterdir
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def mod_dir_with_standard_about(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """构造一个标准 About/About.xml 结构的 mod 目录。"""
    mod_root = tmp_path_factory.mktemp("mod_standard")
    about_dir = mod_root / "About"
    about_dir.mkdir()
    (about_dir / "About.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        "<ModMetaData><name>bench</name><packageId>bench.mod</packageId>"
        "</ModMetaData>",
        encoding="utf-8",
    )
    return mod_root


def test_find_about_xml_current_baseline(
    benchmark,
    mod_dir_with_standard_about: Path,
) -> None:
    """当前 _find_about_xml(iterdir 双层遍历)的基线。

    为 P2 阶段"先试标准路径"优化提供对比数据。
    """
    from app.models.metadata.metadata_factory import _find_about_xml

    result = benchmark(_find_about_xml, mod_dir_with_standard_about)
    assert result is not None
    assert result.name == "About.xml"


def test_find_about_xml_direct_path_baseline(
    benchmark,
    mod_dir_with_standard_about: Path,
) -> None:
    """直接路径访问的基线(P2 优化目标对照)。

    与 ``test_find_about_xml_current_baseline`` 对比,验证
    "先试 path/About/About.xml"策略的收益。本基准使用预期优化后的写法。
    """
    from pathlib import PurePath

    def direct_path_lookup(mod_path: Path) -> Path | None:
        candidate = mod_path / "About" / "About.xml"
        return candidate if candidate.exists() else None

    result = benchmark(direct_path_lookup, mod_dir_with_standard_about)
    assert result is not None
    assert result.name == "About.xml"
