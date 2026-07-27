# 迭代 04 - 扫描与解析加速(P4)

## 需求清单

- [x] C-4 项:切换默认 XML 解析器到 `lxml.etree`,保留 BS4 兜底
- [x] C-5 项:评估 `About.xml` 解析结果 mtime 缓存(评估结论:暂不实施,见决策)
- [x] C-1 项:`refresh_metadata` 改信号驱动(暂缓,见决策)
- [x] H-4 项:`mods_metadata` 增量刷新(暂缓,见决策)

## 迭代目标

减少大批量 mod(数千)扫描耗时。优先实施低风险高收益的 XML 解析器切换,架构级改动暂缓。

## 改动文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `app/utils/xml.py` | 修改 | `xml_path_to_json` 默认解析器从 `ET` 切换到 `LET`(lxml.etree) |

## 关键决策与依据

1. **切换到 `lxml.etree` 作为默认解析器**:
   - `lxml.etree` 是 C 实现,解析速度比 stdlib `xml.etree.ElementTree` 快 3-5x
   - Element API 与 stdlib 兼容,`etree_to_dict` 无需修改
   - 项目已通过 `lxml-stubs` 提供 type stub,pyright 不会报类型错误
   - 失败时仍回退到 `BeautifulSoup("lxml-xml")`,容错性不变
   - 基准对比:323 μs → 78.5 μs(4.1x 加速)

2. **`About.xml` mtime 缓存暂不实施**:
   - 需在 `MetadataMediator` / `metadata_factory` 中引入持久化缓存层
   - 失效策略需考虑 mod 目录 mtime 变化、`About.xml` 内部引用文件变化
   - 复杂度高,留待后续独立迭代评估

3. **`refresh_metadata` 改信号驱动暂缓**:
   - 现有 `waitForDone()` 在 `WorkThread` 中执行,不卡 UI
   - 改信号驱动需重写 `MetadataMediator.refresh_metadata` 与所有调用方
   - 当前进度反馈已通过 `EventBus` 提供,基本满足需求

4. **`mods_metadata` 增量刷新暂缓**:
   - 需引入 mod 目录 mtime 跟踪与差分逻辑
   - 内存中 `mods_metadata` dict 的增删需谨慎处理引用
   - 复杂度高,留待后续独立迭代评估

## 代码实现情况

```python
# app/utils/xml.py
from lxml import etree as LET

def xml_path_to_json(path: str) -> dict[str, Any]:
    ...
    try:
        # 优先用 lxml.etree(C 实现,比 stdlib ET 快 3-5x)
        with __open_file_maybe_compressed(path) as f:
            tree = LET.parse(f)
            root = tree.getroot()
            data = etree_to_dict(root)
    except Exception as e:
        # lxml 解析失败,回退到 BeautifulSoup(lxml-xml 容错性更好)
        logger.debug(f"Error parsing XML file with lxml.etree: {e}")
        logger.debug("Trying to parse with BeautifulSoup as a fallback")
        ...
```

## 测试验证结果

- `tests/utils/test_xml.py`:全部通过
- `tests/benchmarks/test_perf_baseline.py`:`test_xml_path_to_json_baseline` 通过
- 现有 `BeautifulSoup` 兜底路径未被触发(lxml 解析所有测试 XML 均成功)

### 基准对比

| 测试 | P1 基线 | P4 优化后 | 加速比 |
|------|---------|-----------|--------|
| `xml_path_to_json` | 323.0 μs | 78.5 μs | 4.1x |

## 遗留事项

- `About.xml` mtime 缓存方案待后续独立评估
- `mods_metadata` 增量刷新方案待后续独立评估
- `refresh_metadata` 信号驱动重构待后续独立评估

## 下一轮计划(P5+P6:UI 渲染优化与缓存回归门禁)

1. `_FOLDER_SIZE_CACHE` 加 LRU 上限
2. 将 P1 的基准测试接入 CI,设 `--benchmark-compare-fail=mean:20%` 门禁
3. 评估 `QListWidget.setLayoutMode(Batched)` 简单方案
4. 评估 Diff 更新替代 `clear()` + 全量 `addItem`
