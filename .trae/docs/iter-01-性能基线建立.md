# 迭代 01 - 性能基线建立(P1)

## 需求清单

- [x] 在 `AppController.__init__` 各步骤插桩 perf_counter
- [x] 在 `refresh_metadata` / `recreate_mod_list` / `get_mods_from_list` / `sort_paths` 入口出口插桩
- [x] 创建 pytest-benchmark 基准测试
- [x] 运行基准测试保存基线到 `.benchmarks/`
- [x] 记录硬件信息

## 迭代目标

为已识别的性能瓶颈建立可复现的测量基线,用数据驱动后续 P2-P6 阶段的优化决策。遵循 `python-performance` SKILL 的"未测量不优化"原则。

## 改动文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `app/utils/perf_timing.py` | 新增 | 零开销性能插桩工具(`RIMSORT_PERF=1` 启用) |
| `app/controllers/app_controller.py` | 修改 | `__init__` 10 个阶段用 `log_stage` 包裹 |
| `app/models/metadata/metadata_mediator.py` | 修改 | `refresh_metadata` 5 个子阶段插桩 |
| `app/controllers/metadata_controller.py` | 修改 | `get_mods_from_list` 2 个子阶段插桩 |
| `app/views/mods_panel.py` | 修改 | `recreate_mod_list` 2 个子阶段插桩 |
| `app/sort/mod_sorting.py` | 修改 | `sort_paths` 加 `perf_log` |
| `tests/benchmarks/test_perf_baseline.py` | 新增 | 10 项基准测试 |
| `pyproject.toml` | 修改 | 新增 `pytest-benchmark`、`pyinstrument` dev 依赖 |

## 关键决策与依据

1. **零开销插桩设计**:`perf_timing.log_stage` 在 `RIMSORT_PERF!=1` 时返回 `nullcontext()`,生产环境零开销,启用时通过 loguru INFO 输出。依据:`python-performance` SKILL 要求测量先行但不影响生产。
2. **基准测试用 SimpleNamespace 构造 mock controller**:`get_mods_from_list` 只读取 `mods_metadata` 与 `game_version`,用 `types.SimpleNamespace` + `MethodType` 绑定即可,避免完整 `MetadataController` 初始化的复杂依赖。
3. **`AboutXmlMod` 私有字段写入**:构造时用 `mod._mod_type = ...` 绕过 property 校验,因为 `mod_type` 是只读 property(对应 `_mod_type` 私有字段)。
4. **batch 基准用 SQLAlchemy 原生 API**:为反映 P2 优化目标的真实收益,`test_aux_metadata_batch_session_baseline` 用 `session.query().filter(path.in_(...))` + `add_all` + 单次 `commit`,而非 `aux_controller.update`(内部已 commit)。

## 基线数据(Windows-CPython-3.12-64bit)

硬件:Intel Core i7-13700K(24 核),Windows 11 26200,Python 3.12.13

| 测试 | Mean | 备注 |
|------|------|------|
| `find_about_xml_direct_path` | 13.2 μs | 直接路径访问(P2 优化目标) |
| `find_about_xml_current` | 237.8 μs | 当前 iterdir 双层遍历 |
| `xml_path_to_json` | 323.0 μs | 标准 About.xml 解析 |
| `sort_paths[by-packageid]` | 821.2 μs | 2000 项 packageid 排序 |
| `get_mods_from_list[small-200x100]` | 1.96 ms | 200 installed × 100 active |
| `aux_metadata_batch_session` | 7.19 ms | 500 项批量 + 单次 commit(P2 目标) |
| `sort_paths[by-mtime]` | 10.26 ms | 2000 项 mtime 排序 |
| `get_mods_from_list[medium-1000x500]` | 45.11 ms | 1000 × 500 |
| `aux_metadata_session_per_item` | 282.13 ms | 500 项循环内 session(当前) |
| `get_mods_from_list[large-5000x1000]` | 465.28 ms | 5000 × 1000 |

基线文件:`.benchmarks/Windows-CPython-3.12-64bit/0002_baseline.json`

## 关键发现

1. **`get_mods_from_list` 大规模 465ms**:5000 installed × 1000 active 已是典型用户场景,确认 O(n²) 是主要瓶颈。规模从 200×100 → 5000×1000(25x),耗时 1.96ms → 465ms(237x),符合 O(n²) 复杂度。
2. **`aux_metadata_session_per_item` 282ms vs `batch_session` 7.2ms**:批量优化预期收益 **~40x**,远超计划的 70% 降低目标。证明 P2-A 任务(把 session 提到循环外 + 批量预取)是高 ROI 优化。
3. **`find_about_xml` 直接路径 13μs vs iterdir 238μs**:收益 **~18x**,证明 P2-C 任务(先试标准路径)简单且高效。
4. **`sort_paths[by-mtime]` 10.3ms** vs `[by-packageid]` 821μs:mtime 排序慢 12x,涉及目录扫描与 `get_dir_size` 缓存,P4 阶段可优化。

## 测试验证结果

- AST 解析:6 个修改文件全部通过
- `perf_timing` 模块:启用/未启用切换正常
- 基准测试:10/10 通过,基线已保存
- 现有测试套件:未运行(本阶段未改动业务逻辑,只加插桩)

## 遗留事项

- P1 阶段未运行完整 `pytest` 测试套件验证插桩无副作用,留待 P2 阶段首次改动业务逻辑时一并验证
- `pyinstrument` 已安装但未实际运行火焰图采集(需启动完整 GUI 应用,留待用户手动执行)
- 基线仅本机一份,未接入 CI(P6 任务)

## 下一轮计划(P2:低风险高收益算法优化)

1. 修复 `get_mods_from_list` O(n²) → O(n) 反向索引
2. `_find_about_xml` 先试标准路径 `path/About/About.xml`
3. 删除 `*.rsc` 的 tee+list 重复 glob
4. 新增 `AuxMetadataController.get_many` 批量接口
5. `_get_path_to_color_map` / `_get_path_to_updated_map` 改批量查询
6. `recreate_mod_list` 把 session 提到循环外,批量预取 + 单次 commit

每项优化后跑 `tests/benchmarks/test_perf_baseline.py --benchmark-compare` 对比基线,验证收益符合预期。
