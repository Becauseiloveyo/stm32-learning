# T07 状态机到离线研究提醒

标准库本地模拟。设备实际调用 T04 原始观测状态机，唯一提醒文案为“研究：需独立复核”，所有收衣授权关闭。完整接口和边界见 [正式契约](../../docs/NOTIFICATION-CONTRACT.md)，角色交付见 [报告](../../docs/team/reports/notification-t07.md)。

```powershell
python -B -X utf8 -m unittest discover -s research/notification_t07 -p test_notification.py -v
python -B -X utf8 research/notification_t07/run_checks.py --output research/notification_t07/results/<新目录>
python -B -X utf8 research/notification_t07/run_checks.py --verify research/notification_t07/results/<目录>/receipt.json
```

`--output` 必须是本范围中不存在的目录；不覆盖／清理旧记录。正式交付目录在报告列出，名字含 accepted 也不代表角色自行完成协调验收。

当前协调验收目录为 `results/accepted-v2/`；`results/accepted/` 是来源已过期的历史版本，请勿引用为当前通过。主入口为 `python -B -X utf8 tools/project_status.py --record-notification-tests --check`，会加入独立边界测试、只读数据库对照和临时目录中的完整链路复算。

输出：`tests.txt`、`trace.json`、四个 SQLite 示例、`manifest.json`、`receipt.json`。trace 保存连续情景和三段 boot 内的原始事件／观测／T04 输出；verify 核对指纹并在内存重放这些输入。示例数据库可用现有 SQLite 查看器只读检查；不要用 `Device(path)` 检查证据，因为它表示真正 reopen，会新建 boot 并撤销证据。

模块：`storage.py` 持久事务与显式初始化；`contract.py` 研究投影；`device.py` 原始状态处理及有界 outbox；`service.py` 队列、接收和确认；`fixtures.py` 确定性原始合成观测；`scenarios.py` 连续情景；`test_notification.py` 单测；`crash_worker.py` 测试专用进程退出；`run_checks.py` 证据生成及只读核验。

原型仅模拟单设备写者、串行服务和两个先后绑定的接收者。SQLite 进程崩溃不是 MCU 物理掉电。服务端／接收端历史和 Python 状态机历史仍无界，没有真实手机可达、设备功耗或衣物准确率证据。
