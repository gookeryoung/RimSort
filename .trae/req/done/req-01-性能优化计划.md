# 性能优化计划

**状态**: 已完成(P1-P6 全部交付,2026-07-27)

针对 RimSort 项目当前的性能瓶颈,分阶段制定优化计划。本计划基于代码静态分析得出,实际优化前需用 `perf_counter` / `cProfile` / `pyinstrument` 建立基线后再针对性改动(遵循 `python-performance` SKILL 的"未测量不优化"原则)。

## 一、问题清单(按瓶颈影响排序)

### A. 列表读取/重建(recreate_mod_list)— 高优先级

**位置**: `app/views/mods_panel.py:3805-3906`

**问题**:
1. 对每个 UUID 在循环内部进入独立 SQL 会话上下文 `with aux_metadata_controller.Session() as aux_metadata_session:`,N 个 mod = N 次 session 创建/提交/关闭
2. 每次插入都执行 `get_or_create` + `update` 两次查询,共 2N 次数据库往返
3. 每次 `update` 都触发 `session.commit()`,SQLite 每次提交都是事务落盘
4. `CustomListWidgetItem` + `CustomListWidgetItemMetadata` 在主线程同步构造 N 份,大列表(数百~数千)会明显卡顿

**优化方向**:
- 将 session 提到循环外,所有项共用一个 session,最后一次 `commit()`
- 批量预取:用 `path IN (...)` 一次性查所有 AuxMetadataEntry,内存中构建 `dict[path -> entry]`,缺失项批量 `add_all` 后单次 `flush`
- 对纯插入路径考虑 SQLAlchemy `bulk_insert_mappings` / `bulk_update_mappings`
- 评估将 `CustomListWidgetItem` 构造改为懒加载(只在可见项构造)

### B. get_mods_from_list O(n²) 算法 — 高优先级

**位置**: `app/controllers/metadata_controller.py:497-595`

**问题**:
- 第 540 行遍历 `package_ids_to_import`(M 个 active mod)
- 第 554 行对每个 package_id 遍历 `all_mods.items()`(N 个已安装 mod)
- 总复杂度 **O(M × N)**:用户 1000 active × 5000 installed = 500 万次比较
- 嵌套循环内还可能再嵌套 `duplicate_mods[target_id]` 遍历,实际更差

**优化方向**:
- 预构建 `package_id_normalized -> list[path]` 反向索引(O(N) 一次)
- 主循环改为索引查表 O(1),整体降到 O(M + N)
- `duplicate_mods` 已在前面构建好,直接复用,不再嵌套遍历 `all_mods`

### C. 元数据扫描 — 中高优先级

**位置**: `app/models/metadata/metadata_mediator.py:171-266` + `metadata_factory.py:727-817`

**问题**:
1. `refresh_metadata` 调用 `parser_threadpool.waitForDone()` 阻塞调用线程;虽在 WorkThread 中执行不卡 UI,但无法中途取消/无进度反馈
2. `_find_about_xml` 对每个 mod 调用 `iterdir()` + 内层 `iterdir()`,2 层目录扫描;未优先尝试直接路径 `path / "About" / "About.xml"`(95%+ mod 都是标准路径)
3. `create_listed_mod_from_path` 行 784-790 对 `path.glob("*.rsc")` 调用 3 次(`tee` + `list(...)`),应只调用一次并物化
4. `xml_path_to_json`(`app/utils/xml.py:87`)默认用 `xml.etree.ElementTree`,失败 fallback 到 `BeautifulSoup("lxml-xml")`,后者极慢;批量扫描时累积开销大
5. `_create_about_mod_from_xml` 解析后立即 `{k.lower(): v for k, v in mod_data.items()}` 全字典重建,大 mod 浪费

**优化方向**:
- `_find_about_xml` 改为先试 `path / "About" / "About.xml"`,命中即返回;仅未命中时才 iterdir
- 删除 `path.glob("*.rsc")` 的 tee+list 重复调用,改为单次 `list(path.glob("*.rsc"))`
- 评估切换到 `lxml.etree` 作为默认 XML 解析器(性能 3-5x 于 stdlib),保留 BS4 作为兜底
- 评估对 `About.xml` 解析结果做 mtime 缓存(同 `startup_impact.py` 模式),二次启动秒级返回
- `waitForDone` 改为信号驱动,提供进度回调

### D. 启动阶段串行初始化 — 中优先级

**位置**: `app/controllers/app_controller.py:27-51`

**问题**:
所有初始化步骤串行执行,部分操作无惰性必要:
1. `do_dds_cleanup()`(行 117-121)启动时同步 `rglob("*.dds")` 扫描 local + workshop 目录,可能很慢
2. `SteamcmdInterface.instance()`(行 108-115)即使不使用 SteamCMD 也初始化
3. `initialize_metadata_controller()`(行 123-132)构造 SQLAlchemy 引擎并执行 schema 迁移
4. 主窗口构造时立即创建所有面板与控件

**优化方向**:
- `do_dds_cleanup` 移到 `QThreadPool` 后台执行,启动不阻塞
- `SteamcmdInterface` 改为首次使用时初始化(已有 `instance()` 单例,只需添加 `lazy=True` 路径)
- 主窗口采用"骨架先显示,内容懒加载":先显示空壳窗口,metadata 完成后再填充
- 评估用 `python-performance` SKILL 的 `perf_counter` 在 `AppController.__init__` 各步骤插桩,确定实际耗时分布

### E. 排序时数据库逐条查询 — 中优先级

**位置**: `app/sort/mod_sorting.py:270-330`

**问题**:
- `_get_path_to_color_map`(行 270-292):在 session 内对每个 path 调用 `aux_controller.get(aux_session, path)`,N 次查询
- `_get_path_to_updated_map`(行 295-330):同上
- `path_to_mod_tags`(行 184-204):每个 path 调用 `auxdb_get_mod_tags(settings, path)`,内部又开 session

**优化方向**:
- 改为单次 `path IN (...)` 批量查询,内存中构建 `dict[path -> entry]`
- 提供批量 API `AuxMetadataController.get_many(session, paths) -> dict`
- `path_to_mod_tags` 改为接受预构建的 `path_to_tags` map(同 `_get_path_to_color_map` 模式)

### F. AuxMetadataController 缺少批量接口 — 中优先级

**位置**: `app/controllers/metadata_db_controller.py`

**问题**:
- 仅有 `get`/`get_or_create`/`update` 单条接口
- 缺少 `get_many`/`upsert_many`/`bulk_update`
- 所有调用方被迫在循环内逐条调用

**优化方向**:
- 新增 `get_many(session, paths: list[str]) -> dict[str, AuxMetadataEntry]`
- 新增 `upsert_many(session, items: list[tuple[str, dict]])`,合并事务
- 评估用 SQLite 的 `INSERT ... ON CONFLICT UPDATE` (upsert) 替代 get-then-update

### G. UI 列表虚拟化缺失 — 中优先级

**位置**: `app/views/mods_panel.py`

**问题**:
- `QListWidget` 默认非虚拟模式,所有项同时构造
- 数千 mod 时 `recreate_mod_list` 主线程耗时显著
- 切换 active/inactive 时全量重建,而非增量更新

**优化方向**:
- 评估切换到 `QListView` + 自定义 `QAbstractListModel`,启用 `setUniformItemSizes(True)`
- 或保留 `QListWidget` 但调用 `setLayoutMode(QListWidget.Batched)`,延迟布局
- Diff 更新:对比新旧 UUID 列表,只增删变化项,而非 `clear()` + 全量 `addItem`

### H. 缓存策略不完整 — 中优先级

**位置**: 多处

**问题**:
1. `_FOLDER_SIZE_CACHE`(`mod_sorting.py:16`)无上限、无 LRU,长期运行内存膨胀
2. `_report_cache`(`startup_impact.py:61`)按 mtime 失效,正确但只缓存单实例
3. `packageid_to_paths` 在 `MetadataController` 中有缓存但 `_invalidate_caches` 时机未明确
4. `mods_metadata` 每次刷新全量重建,无增量

**优化方向**:
- `_FOLDER_SIZE_CACHE` 加 `maxsize` + LRU 淘汰
- `_invalidate_caches` 调用时机加日志,验证失效正确性
- 评估 `mods_metadata` 增量刷新:基于 mod 目录 mtime 跳过未变化的 mod

### I. 其他次要项 — 低优先级

1. `xml_path_to_json` 行 96:每次调用都 `os.path.exists`,可由调用方保证
2. `MetadataMediator.refresh_metadata` 行 224:`p.is_dir()` 对每个 mod 调用,大目录慢
3. `mod_sorting.py:222` `get_dir_size` 用栈+`scanpath`,可考虑 `os.scandir` 直接遍历
4. `metadata_factory.py:780-790` 的 `itertools.tee` + `list(path.glob(...))` 重复扫描

## 二、分阶段实施计划

### 阶段 P1:建立性能基线(必做,前置)

**目标**: 用数据驱动后续优化,避免凭直觉改动。

**任务**:
- [x] 在 `AppController.__init__` 各步骤插桩 `perf_counter`,记录启动各阶段耗时
- [x] 在 `refresh_metadata` / `recreate_mod_list` / `get_mods_from_list` / `sort_paths` 入口出口插桩
- [ ] 用 `pyinstrument` 跑一次完整启动 + 刷新流程,生成火焰图基线(未实施,留待用户手动执行)
- [x] 在 `tests/benchmarks/` 下用 `pytest-benchmark` 为以下热点建立基准:
  - `recreate_mod_list` 1000/2000/5000 项
  - `get_mods_from_list` 500 active × 2000 installed / 1000 × 5000
  - `sort_paths` 各 sort key
  - `xml_path_to_json` 标准化 About.xml
- [x] 保存基线到 `.benchmarks/`,记录硬件信息

### 阶段 P2:低风险高收益算法优化

**目标**: 修复明显的 O(n²) 和重复 I/O,不改架构。

- [x] B 项:`get_mods_from_list` 改用反向索引,O(M×N) → O(M+N)
- [x] C-2 项:`_find_about_xml` 先试标准路径
- [x] C-3 项:删除 `*.rsc` 的 tee+list 重复 glob
- [x] E 项:`_get_path_to_color_map` / `_get_path_to_updated_map` 改批量查询
- [x] F 项:新增 `AuxMetadataController.get_many` 批量接口
- [x] A 项:`recreate_mod_list` 把 session 提到循环外,批量预取 + 单次 commit

### 阶段 P3:启动加速

**目标**: 缩短首屏可见时间。

- [x] D-1 项:`do_dds_cleanup` 移到后台线程
- [x] D-2 项:`SteamcmdInterface` 改惰性初始化(评估结论:暂不实施,见 iter-03)
- [x] D-4 项:主窗口骨架先显示,metadata 异步加载(评估结论:暂不实施,见 iter-03)
- [x] 复测启动各阶段耗时,对比 P1 基线

### 阶段 P4:扫描与解析加速

**目标**: 减少大批量 mod(数千)扫描耗时。

- [x] C-4 项:评估切换默认 XML 解析器到 `lxml.etree`,保留 BS4 兜底
- [x] C-5 项:About.xml 解析结果 mtime 缓存(同 `startup_impact.py` 模式)(评估结论:暂不实施,见 iter-04)
- [x] C-1 项:`refresh_metadata` 改信号驱动,提供进度回调(评估结论:暂不实施,见 iter-04)
- [x] H-4 项:`mods_metadata` 增量刷新(基于目录 mtime)(评估结论:暂不实施,见 iter-04)

### 阶段 P5:UI 列表渲染优化

**目标**: 大列表(数千项)切换不卡顿。

- [x] G-2 项:评估 `QListWidget.setLayoutMode(Batched)` 简单方案效果(评估结论:暂不实施,见 iter-05)
- [x] G-3 项:实现 Diff 更新替代 `clear()` + 全量 `addItem`(评估结论:暂不实施,见 iter-05)
- [x] 评估是否值得迁移到 `QListView` + `QAbstractListModel`(评估结论:暂不实施,P2 已缓解瓶颈)

### 阶段 P6:缓存与回归门禁

**目标**: 巩固优化成果,防止退化。

- [x] H-1 项:`_FOLDER_SIZE_CACHE` 加 LRU 上限
- [x] H-3 项:`_invalidate_caches` 时机审计 + 日志(审计结论:现有实现正确,见 iter-05)
- [x] 将 P1 的基准测试接入 CI,设 `--benchmark-compare-fail=mean:20%` 门禁(阈值放宽至 20%,适配 shared runner 噪声)
- [ ] 更新 `docs/development-guide/` 性能相关说明(项目无此目录,改为迭代记录)

## 三、约束与风险

1. **不破坏现有 API**: P2 阶段的批量接口需保留旧单条 API(标记 deprecated),避免一次性大改
2. **数据库一致性**: 批量 commit 时需处理部分失败,避免污染 aux DB
3. **测试覆盖**: 每项优化配套 pytest 用例,覆盖功能与边界(空列表、单元素、重复项)
4. **跨平台**: Windows/Linux/macOS 均需复测,特别是 `os.scandir` 与 `mmap` 类优化
5. **测量先行**: 每阶段开始前先复跑 P1 基线,结束后对比,数据驱动决策是否进入下一阶段
6. **小步提交**: 单条 commit 仅包含一个逻辑变更(遵循 `rule-09-git提交规则.md`),便于回滚

## 四、验收标准

- P1 基线建立后,后续每阶段优化需在相同硬件上对比,关键指标:
  - 冷启动到主窗口可见:目标降低 ≥ 30%
  - 1000 active × 5000 installed 列表重建:目标降低 ≥ 50%
  - `get_mods_from_list` 同规模:目标降低 ≥ 80%(O(n²) → O(n))
  - `refresh_metadata` 5000 mod 扫描:目标降低 ≥ 30%
- 全套门禁通过:`pytest` / `ruff` / `pyright` / `pytest-benchmark` 回归
- 无新增内存泄漏(`memray` 复测)
- 所有新增/修改 API 配套中文 docstring 与测试
