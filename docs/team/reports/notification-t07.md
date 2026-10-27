# T07 正式通知链路角色报告

日期：2026-10-27（2026-10-26 开始）。本文件由协调者接管后补齐；正式验收见 [T07-review](../../verification/T07-review.md)，进度以 STATUS 为准。

已读 AGENTS、PROJECT、STATUS、TEAM 的 T07 补充、notification-t07 任务书、ROADMAP T07、STATE-MACHINE、CORE-INTERFACE、hanger_state.h、实际 Python 状态机及只读旧通知原型／第 8–9 节设计。

写入仅限 `research/notification_t07/**`、`docs/NOTIFICATION-CONTRACT.md`、本报告。当前主线 T07 in_progress，T06 已验收。恢复命令 `python -B -X utf8 tools/project_status.py` 退出 0；该命令未重跑上游测试／编译。

实现：设备从真实 `StateMachine.handle(raw_event)` 投影研究提醒；持久序号与有界 outbox 原子更新；reopen 新 boot 清证据；队列满使用单独保留的撤销快照并锁存停止；服务端以最新快照淘汰旧任务；TTL 锚定原始有效证据时间。只使用标准库，真实收衣授权关闭。

## 交付与协调整合

角色提供 `contract.py`、`storage.py`、`device.py`、`service.py`、`fixtures.py`、`crash_worker.py`、`test_notification.py`、`scenarios.py` 以及恢复后版本的 `run_checks.py`、正式契约、README。协调者审查并补充 ACK 反例、18 项独立检查、主收据入口、数据库对照与完整链路复算；最终只读本轮证据为 `research/notification_t07/results/accepted-v2/`。

最初角色因额度停止，协调者接管期间角色恢复造成脚本并行变化。主检查检测来源不稳定，没有宣告通过；角色随后明确停止，协调者统一文件并重生成。`results/accepted/` 和失败协调记录保留历史，不作为当前证据。没有覆盖或删除旧数据库。

最终命令：

```powershell
python -B -X utf8 research/notification_t07/run_checks.py --output research/notification_t07/results/accepted-v2
python -B -X utf8 tools/project_status.py --record-notification-tests --check
```

均退出 0。本模块 43 项检查通过；协调主记录合计 61 项，时间 `2026-10-27T14:20:09+08:00`，输入／产物运行中稳定。连续情景 53 条原始事件、22 个合成周期、3 个 boot 域、11 个检查点；实际生成 3 条累计研究提醒，后续部分失效，不是当前有效收衣建议数。4 个 SQLite 文件完整性为 ok，最终表与 trace 一致，临时目录完整复算一致。

证据：

- [正式契约](../../NOTIFICATION-CONTRACT.md)：API、单位／范围、状态、ACK、时间、配置及 T06/T05 映射。
- [场景收据](../../../research/notification_t07/results/accepted-v2/receipt.json) 与 [清单](../../../research/notification_t07/results/accepted-v2/manifest.json)：43 项日志、来源指纹及参数。
- [主收据](../../verification/notification-tests.json)：61 项检查和独立复算。

没有改动已验收的测量／能量／C 源码；没有采购、部署、真实手机连接或提交／清理 Git。源数据为确定性合成观测，实际时间／功耗／身份安全均未标定。

剩余限制：无真实收衣授权或手机送达证据；SQLite 进程中断不等于 MCU 物理掉电；生产者唯一身份、防回滚／克隆、Flash、队列长期恢复、服务集群和 T05 无线能量接入待后续。服务端无法提前知道设备尚未上报的离线变化。T08 由协调者按路线图另行启动，本报告不推进该任务。
