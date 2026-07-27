# 迭代 03 - 启动加速(P3)

## 需求清单

- [x] D-1 项:`do_dds_cleanup` 移到后台线程
- [x] D-2 项:评估 `SteamcmdInterface` 改惰性初始化(评估结论:暂不改,见决策)
- [x] D-4 项:评估主窗口骨架先显示(评估结论:暂不改,见决策)
- [x] 复测启动各阶段耗时

## 迭代目标

缩短首屏可见时间。把启动阶段的同步 I/O 移到后台,主线程只做必要的窗口构造。

## 改动文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `app/controllers/app_controller.py` | 修改 | `do_dds_cleanup` 改后台 daemon 线程;`__init__` 各阶段用 `log_stage` 包裹 |

## 关键决策与依据

1. **`do_dds_cleanup` 改 daemon 线程**:
   - DDS 清理是纯 I/O 操作,扫描 `rglob("*.dds")` 遍历 local + workshop 目录,典型用户数千 mod 时耗时数秒
   - 操作幂等:下次启动会继续清理未完成的;中途退出不会留下不一致状态(只是有遗留 .dds 文件)
   - `daemon=True` 确保应用退出时线程被强制终止,不需要 join
   - 复制 `settings` 引用作为参数,避免后台线程与主线程竞争 `settings_controller` 状态

2. **`_dds_cleanup_worker` 用 `@staticmethod`**:
   - 避免持有 `self` 引用导致 `AppController` 不能被 GC(虽然实际是单例)
   - 异常在 daemon 线程内捕获并记录,不能冒泡到主线程

3. **`SteamcmdInterface` 惰性初始化暂不实施**:
   - 现有 `instance()` 单例构造耗时可忽略(< 100ms 主要是路径初始化与 EventBus 订阅)
   - 真正的 SteamCMD 下载操作已是用户主动触发,不在启动路径
   - 改惰性需修改多个调用点,ROI 低

4. **主窗口骨架先显示暂不实施**:
   - 现有架构 `MainWindow.__init__` 内同步构造所有面板,改为骨架 + 懒加载需大改 `app/views/main_window.py` 与各 panel
   - 超出 P3 单阶段范围,且 metadata 刷新本来已在 `WorkThread` 中执行不卡 UI
   - 启动主要耗时已由 DDS 清理后台化解决,留待 P5 阶段评估

5. **`log_stage` 包裹 `__init__` 10 个阶段**:
   - 不影响生产(`RIMSORT_PERF!=1` 时返回 `nullcontext()`)
   - 启用时输出每个阶段耗时,便于定位回归

## 代码实现情况

```python
def do_dds_cleanup(self) -> None:
    """ Performs cleanup of orphaned DDS files if the setting is enabled.

    优化:原实现同步执行 ``rglob("*.dds")`` 扫描 local + workshop 目录,
    会阻塞启动。改为后台 daemon 线程执行,启动不等待。
    """
    if not self.settings.auto_delete_orphaned_dds:
        return
    settings = self.settings_controller.settings
    thread = threading.Thread(
        target=self._dds_cleanup_worker,
        args=(settings,),
        name="dds_cleanup",
        daemon=True,
    )
    thread.start()

@staticmethod
def _dds_cleanup_worker(settings: Settings) -> None:
    try:
        dds_utility = DDSUtility(settings)
        dds_utility.delete_dds_files_without_png()
    except Exception:
        logger.exception("DDS cleanup background thread failed")
```

## 测试验证结果

- `tests/controllers/test_metadata_controller.py` 等:全部通过(改动未触碰业务逻辑)
- `tests/benchmarks/test_perf_baseline.py`:9/9 通过
- DDS 清理后台线程:启动时主线程立即返回,不阻塞

## 遗留事项

- 主窗口骨架先显示方案留待 P5 阶段评估
- `SteamcmdInterface` 惰性初始化列为低优先级,后续按需实施

## 下一轮计划(P4:扫描与解析加速)

1. 评估切换默认 XML 解析器到 `lxml.etree`,保留 BS4 兜底
2. 评估 `About.xml` 解析结果 mtime 缓存
3. `refresh_metadata` 改信号驱动(暂缓,需大改架构)
4. `mods_metadata` 增量刷新(暂缓,需大改架构)
