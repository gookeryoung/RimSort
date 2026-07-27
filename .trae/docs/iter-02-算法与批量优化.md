# 迭代 02 - 算法与批量优化(P2)

## 需求清单

- [x] B 项:`get_mods_from_list` 改用反向索引,O(M×N) → O(M+N)
- [x] C-2 项:`_find_about_xml` 先试标准路径
- [x] C-3 项:删除 `*.rsc` 的 tee+list 重复 glob
- [x] E 项:`_get_path_to_color_map` / `_get_path_to_updated_map` 改批量查询
- [x] F 项:新增 `AuxMetadataController.get_many` / `upsert_many` 批量接口
- [x] A 项:`recreate_mod_list` 把 session 提到循环外,批量预取 + 单次 commit

## 迭代目标

修复明显的 O(n²) 算法与重复 I/O,不改架构。所有改动需通过现有测试 + 基准对比验证收益。

## 改动文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `app/controllers/metadata_controller.py` | 修改 | `get_mods_from_list` 改反向索引,O(M×N)→O(M+N) |
| `app/controllers/metadata_db_controller.py` | 修改 | 新增 `get_many` / `upsert_many` 批量接口 |
| `app/sort/mod_sorting.py` | 修改 | `_get_path_to_color_map` / `_get_path_to_updated_map` 改批量查询 |
| `app/views/mods_panel.py` | 修改 | `recreate_mod_list` 单 session + 批量 upsert |
| `app/models/metadata/metadata_factory.py` | 修改 | `_find_about_xml` 快速路径 + 删除 tee+list 重复 glob |

## 关键决策与依据

1. **反向索引结构选择 `dict[str, list[(path, mod)]]`**:
   - 同时支持 `package_id_normalized` 与 `package_id_normalized_stripped` 两个 key 查找
   - 用 `seen_candidate_paths: set` 去重,避免同一 path 被两个 key 命中后重复处理
   - 一次遍历 `all_mods` 同时构建 `duplicate_mods` 与 `pid_to_paths_mods`,消除原二次遍历

2. **`get_many` 用 `Sequence[Path | str]` 而非 `list`**:
   - pyright 提示 `list` 是不变类型,接受 `list[str]` 会报协变错误
   - `Sequence` 协变,可同时接受 `list[str]` 与 `list[Path]`,API 更通用

3. **`upsert_many` 设计为共用一次 commit**:
   - 内部先 `get_many` 取已存在 entry,内存中分批构造新 entry
   - `add_all` + 单次 `commit`,失败时 `rollback` 并抛出
   - 调用方在共享 session 内继续构造 UI 项,避免 N 次连接开销

4. **`recreate_mod_list` 三阶段重构**:
   - 第一阶段:遍历 uuids 收集 `(uuid_key, mod_path)` 对
   - 第二阶段:共享 session 批量 `upsert_many`
   - 第三阶段:在共享 session 内构造所有 `CustomListWidgetItem`
   - 保留 `aux_metadata_session` 传给 `CustomListWidgetItemMetadata`,因为后者在 setData 时仍需访问 session

5. **`_find_about_xml` 快速路径**:
   - 95%+ mod 使用标准 `About/About.xml` 路径,`is_file()` 一次 syscall 即命中
   - 标准路径未命中才回退到双层 `iterdir` 大小写不敏感扫描
   - 不删回退路径:兼容非标准目录结构(如 `ABOUT/ABOUT.XML`)

6. **删除 `itertools.tee` + `next` + `list(glob)` 三次扫描**:
   - 原实现:`gen = path.glob("*.rsc")` → `tee(gen, 2)` → `next(gen)` × 2 → `list(path.glob("*.rsc"))` 共扫描 3 次
   - 改为:`rsc_files = list(path.glob("*.rsc"))` 单次扫描,用 `len(rsc_files)` 判断

## 代码实现情况

### `get_mods_from_list` 核心改动

```python
# 一次遍历构建两个索引
pid_to_paths_mods: dict[str, list[tuple[str, AboutXmlMod]]] = {}
for path, mod_data in all_mods.items():
    if isinstance(mod_data, AboutXmlMod):
        pid = str(mod_data.package_id)
        duplicate_mods.setdefault(pid, []).append(path)
        pid_to_paths_mods.setdefault(pid, []).append((path, mod_data))

# 主循环改用索引查表
seen_candidate_paths: set[str] = set()
candidate_paths: list[str] = []
for lookup_key in (package_id_normalized, package_id_normalized_stripped):
    for cand_path, _cand_mod in pid_to_paths_mods.get(lookup_key, []):
        if cand_path not in seen_candidate_paths:
            seen_candidate_paths.add(cand_path)
            candidate_paths.append(cand_path)
```

### `upsert_many` 批量接口

```python
@staticmethod
def upsert_many(session, item_paths, default_factory=None, **update_fields):
    normalized = [str(p) if isinstance(p, Path) else p for p in item_paths]
    existing = AuxMetadataController.get_many(session, normalized)
    new_entries = []
    for path in normalized:
        if path not in existing:
            entry = default_factory() if default_factory else AuxMetadataEntry(path=path)
            entry.path = path
            for k, v in update_fields.items():
                setattr(entry, k, v)
            new_entries.append(entry)
            existing[path] = entry
        else:
            for k, v in update_fields.items():
                setattr(existing[path], k, v)
    if new_entries:
        session.add_all(new_entries)
    session.commit()
    return existing
```

## 整合优化情况

- 删除 `metadata_controller.py` 中的 `import itertools`(已不使用)
- `metadata_factory.py` 中 `itertools.tee` 被简化为单次 `list(glob)`,删除 `import itertools`
- `mod_sorting.py` 中 `_get_path_to_color_map` 与 `_get_path_to_updated_map` 复用同一个 `get_many` 接口,消除两个 N 次查询的循环

## 测试验证结果

- `tests/controllers/test_metadata_controller.py`:全部通过
- `tests/controllers/test_metadata_db_controller.py`:全部通过
- `tests/views/test_mods_panel.py`:全部通过
- `tests/sort/test_mod_sorting.py`:全部通过
- `tests/models/metadata/`:全部通过
- `tests/benchmarks/test_perf_baseline.py`:9/9 通过

### 基准对比(本机 Intel i7-13700K, Python 3.12.13)

| 测试 | P1 基线 | P2 优化后 | 加速比 |
|------|---------|-----------|--------|
| `get_mods_from_list[small-200x100]` | 1.96 ms | 0.25 ms | 7.8x |
| `get_mods_from_list[medium-1000x500]` | 45.11 ms | 2.27 ms | 19.9x |
| `get_mods_from_list[large-5000x1000]` | 465.28 ms | 26.13 ms | 17.8x |
| `find_about_xml_direct_path` | 13.2 μs | 8.28 μs | 1.6x |
| `aux_metadata_session_per_item` | 282.13 ms | 237.73 ms | 1.2x(参考,实际批量优化由 `upsert_many` 替代) |

`get_mods_from_list[large]` 满足验收标准"≥ 80% 降低"(实际 94.4% 降低)。

## 遗留事项

- `aux_metadata_session_per_item` 基准仍保留作为"未优化前"参照,实际生产路径已不再使用该模式
- `path_to_mod_tags` 仍逐条查询 aux DB,未来可改为接受预构建的 `path_to_tags` map(同 color/updated 模式)

## 下一轮计划(P3:启动加速)

1. `do_dds_cleanup` 移到后台 daemon 线程
2. 评估 `SteamcmdInterface` 改惰性初始化
3. 评估主窗口骨架先显示,metadata 异步加载
4. 复测启动各阶段耗时
