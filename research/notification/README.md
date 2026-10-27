# 离线通知协议预研

仅本地、合成输入、Python 标准库。正式约束及限制见 [`docs/NOTIFICATION-CONTRACT-DRAFT.md`](../../docs/NOTIFICATION-CONTRACT-DRAFT.md)，本轮结论见 [`notification.md`](../../docs/team/reports/notification.md)。当前没有手机、云服务、真实衣物授权或后台送达率证据。

从项目根目录运行：

```powershell
python -B -X utf8 -m unittest discover -s research/notification -p test_protocol.py -v
python -B -X utf8 research/notification/run_checks.py
```

`run_checks.py` 新建 `research/notification/results/run-<时间>/`，不会覆盖已有目录。也可用 `--output research/notification/results/<新目录名>` 固定输出位置。目录含：

- `receipt.json`：本角色检查结果、逐项测试 ID、Python／SQLite 版本、输入输出 SHA-256、参数与虚拟数据标签。
- `tests.txt`：本轮实际测试输出。
- `trace.json`：一个连续的离线变化／不可达／重启／去重／已读／过期／重新绑定情景，以及三端存储的完整 JSON 导出。
- `device.sqlite3`、`service.sqlite3`、`receiver.sqlite3`：可独立检查的本地示例数据库，凭证仅为明显的模拟字符串。

核对既有记录的新鲜度，不重跑测试：

```powershell
python -B -X utf8 research/notification/run_checks.py --verify research/notification/results/<目录名>/receipt.json
```

SQLite 数据可以用 Python 自带库或现有 SQLite 查看器读取；`trace.json` 已有可直接阅读的所有表。请勿对证据数据库写入，以免摘要不匹配。全部时间由调用者注入，示例 1000 秒等是虚拟 Unix 秒，不对应本项目测量日期。默认 TTL 1800 秒是冻结输入中的未验证起点，重试间隔仅是离线服务端假设；没有随机数，seed 为 null。

文件职责：`protocol.py` 定义三端存储和契约；`test_protocol.py` 定义失败情景；`crash_worker.py` 是测试专用的强制退出子进程；`run_checks.py` 生成本角色证据。不要把 `crash_worker.py` 用于真实设备或其他服务。
