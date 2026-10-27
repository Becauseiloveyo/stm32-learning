# 远程提醒协议预研报告

日期：2026-10-23。角色：远程提醒。本轮进行 T07 的独立离线预研，主线 T07 仍依赖 T06；本报告不修改主进度、不代表正式验收。

## 输入和范围

- 已读 `AGENTS.md`、`docs/PROJECT.md`、`docs/STATUS.json`、`docs/TEAM.md`、本角色任务书及 ROADMAP 的 T07。
- 技术输入仅使用 `docs/team/baseline-v1/power-notification-design.md` 和冻结清单 `snapshot.json`；不导入其他角色正在变化的实现。
- 写入仅限 `research/notification/**`、`docs/NOTIFICATION-CONTRACT-DRAFT.md`、本报告。
- 当前是合成输入、模拟授权和本地持久化验证；没有真实衣物标定、设备、网络、云服务或手机后台实测。

## 开始时检查

`python -X utf8 tools/project_status.py`：退出码 0；当时主线 T01，源码与生成清单匹配，已有主测试记录匹配且通过 14 项。该命令未重新运行主测试，本角色不更新其记录。

## 本轮结果

本轮分工交付已完成，等待协调对话审查。已实现三端 SQLite 的离线事件模拟、持久 outbox／任务队列、独立确认、接收端去重和失败情景检查；没有把 T07 标为完成，没有写全局状态或主测试记录。

最终检查为 **34 项测试通过，0 失败、0 错误、0 跳过**，另保存 7 个连续情景检查点和三个可检查的数据库。最终记录时间为 **2026-10-23 18:47:04 +08:00**，运行环境 Python **3.14.6**、SQLite **3.50.4**。

证据：

- [`receipt.json`](../../../research/notification/results/run-20261023-notification-v1/receipt.json)：测试 ID、输入输出 SHA-256、版本、参数和合成标签。
- [`tests.txt`](../../../research/notification/results/run-20261023-notification-v1/tests.txt)：本轮实际逐项结果，34 项用时 10.472 秒。
- [`trace.json`](../../../research/notification/results/run-20261023-notification-v1/trace.json)：虚拟时间、情景轨迹、三端所有表的 JSON 导出。
- 同目录 `device.sqlite3`、`service.sqlite3`、`receiver.sqlite3`：实际持久化样例；三份 `PRAGMA integrity_check` 均返回 `ok`。

`run_checks.py --verify` 已核对上述输出和当前源文件／契约／冻结基线，返回 `fresh_and_passed: true`。源码或契约改变后应重新生成证据；验证指纹本身不是重新运行测试，也不证明硬件性能。

## 字段与行为契约

完整定义见 [`NOTIFICATION-CONTRACT-DRAFT.md`](../../NOTIFICATION-CONTRACT-DRAFT.md)，实现以 `protocol.py` 的 `Event` 及三端存储为准。

| 要点 | 本轮选择及可检查行为 |
|---|---|
| 设备／绑定 | `device_id + producer_id + binding_epoch`；生产者身份区分持久存储实例，重新绑定由模拟服务端增加代次。旧所有者和旧代次不能接收新提醒。 |
| 批次／状态版本 | `batch_seq` 跨批次增加；`garment_session_id` 由生产者和批次派生；同批次 `state_version` 递增。按批次优先排序，旧批次高版本不能覆盖新批次。 |
| 事件 ID | `producer_id:event:event_seq`；计数、最新快照、outbox 在设备同一事务提交，正常重启后保持唯一。存储丢失必须显式重新初始化／重新绑定。 |
| 时间 | `boot_id` 区分单调毫秒时钟范围；`measured_at_s` 为整数 UTC 秒或 null；`clock_valid`、`clock_uncertainty_s` 明确时钟有效性。无有效墙上时钟只存历史。 |
| 有效期 | `ttl_s` 为 1–1800 秒；保守截止为测量时间＋TTL－误差界；等于截止即失效。未来时间首次入库被封为历史，不随重试自动变有效。 |
| 授权边界 | 唯有显式 `SIMULATED_COLLECTION_ALLOWED` 加 `EXPLICIT_SIMULATION_ONLY` 进入模拟路径；origin 固定 synthetic。20 次弱信号重复也只保留研究历史。 |
| 提醒去重 | 上传按不可变 `event_id` 去重；接收提醒按绑定／批次／`episode` 去重。持续同一模拟状态的新确认静默更新，明确退出又进入才形成新 episode。 |
| 保存与确认 | `SERVER_DURABLY_SAVED` 携带精确事件摘要，服务端事务提交后发出；与模拟推送接受、接收端保存、显式模拟已读分别记载。 |
| 配置 | `config_version` 必须等于绑定要求版本才可提醒；要求更新后取消旧配置任务，等设备新建已应用版本的事件。 |

持久化假设是本机 SQLite 提供的事务语义；并未验证 MCU Flash、物理掉电或存储介质故障。脚本没有随机数，seed 为 `null`；示例时间 1000–1130 秒是虚拟 Unix 秒。TTL 1800 秒来自冻结设计的暂定起点；服务端重试 `[60,300,900,3600]` 秒是确定性模拟参数，没有射频能量依据。

## 失败情景证据

| 检查组 | 实际结果 |
|---|---|
| 重复提交、保存确认丢失 | 同一事件反复上传保留 1 条事件、1 个任务；成功后反复调度仍只有 1 次模拟提醒。ID 相同内容不同、状态槽冲突拒绝并回滚。 |
| 断网期间状态改变 | 旧模拟可收取＋最新潮湿一起恢复上传，保存 2 条历史，创建 0 个提醒任务。 |
| 乱序／换批次 | 旧包不能回滚最新 head；新批次版本 1 可淘汰旧批次更高版本；旧快照排列的 6 种顺序均检查通过。 |
| 队列中的状态变化／迟到推送 | 调度前取消旧任务；模拟接收前再核对服务端最新状态，迟到旧提示被拒绝。已保存卡片在 sync 后失效。 |
| 重启和事务中断 | 7 个不同持久边界使用子进程 `os._exit(73)` 强制退出：设备计数后／提交前、服务端事件保存后／提交前／提交后、模拟推送后、接收端提交后。回滚点不会留下半个事务，已提交记录重启后保留。 |
| 接收端保存而服务端未记确认 | 重启两端再投递，产生 2 次模拟推送尝试但只有 1 条提醒；返回 `DUPLICATE_SUPPRESSED`。 |
| 过期／时钟异常 | 首次上传已到期、等待期间到期、截止边界、无效时钟、未来时间、误差界和服务端时钟倒退均覆盖；重试不延长有效期。 |
| 重新绑定／错误身份 | 旧令牌、旧绑定、未知生产者、错误接收者拒绝；新代次需新快照，旧 outbox 不迁移给新所有者。 |
| 模拟手机不可达 | 模拟通道接受不冒充接收；按重试时间恢复后只提醒一次；已经过期的任务在恢复时不补发。 |
| 保存／推送／接收／已读 | 各自有独立证据和时间；未产生接收记录时不标已读，读确认必须显式调用，重复读不改变首次时间。 |
| 弱信号／非法输入／配置变化 | 弱信号重复不授权；没有模拟授权的可收取状态、真实授权枚举、非法时间／版本／TTL 均拒绝；旧配置取消，新配置需新事件。 |
| 明确保留的能力边界 | 已上传模拟可收取后，设备离线变潮但尚未上报，服务端仍可能按最后收到的有效状态模拟提醒。测试明确预期这一事实，恢复上报后才撤销旧卡片。 |

连续演示轨迹的累计模拟提醒数为：断网状态改变后 0；手机不可达时 0；两端重启恢复后 1；重复上传和显式已读后仍为 1；后续研究状态、过期和重新绑定后仍为 1。这个数是历史累计提醒数，后续旧卡片已失效，不是当前有效建议数。

## 文件清单

本角色新建文件：

- `docs/NOTIFICATION-CONTRACT-DRAFT.md`：字段、时间、事务、确认及接口需求。
- `research/notification/README.md`：运行和证据检查说明。
- `research/notification/protocol.py`：设备、模拟服务端、模拟接收端。
- `research/notification/test_protocol.py`：34 项失败情景检查，包括 6 种旧包排列和强制退出子进程。
- `research/notification/crash_worker.py`：测试专用中断进程。
- `research/notification/run_checks.py`：本角色检查、独立记录与新鲜度验证。
- `research/notification/results/run-20261023-notification-v1/receipt.json`。
- `research/notification/results/run-20261023-notification-v1/tests.txt`。
- `research/notification/results/run-20261023-notification-v1/trace.json`。
- `research/notification/results/run-20261023-notification-v1/device.sqlite3`。
- `research/notification/results/run-20261023-notification-v1/service.sqlite3`。
- `research/notification/results/run-20261023-notification-v1/receiver.sqlite3`。
- `docs/team/reports/notification.md`：本报告。

没有修改其他角色代码、冻结输入、主线文档、全局状态、根 README 或主测试记录；没有提交／回滚 Git，没有创建对话／子代理，没有发送跨对话消息。

## 实际命令

均从项目根目录执行：

```powershell
python -X utf8 tools/project_status.py
Get-FileHash -Algorithm SHA256 docs/team/baseline-v1/power-notification-design.md
python --version
python -B -X utf8 -m unittest discover -s research/notification -p test_protocol.py -v
python -B -X utf8 research/notification/run_checks.py --output research/notification/results/run-20261023-notification-v1
python -B -X utf8 research/notification/run_checks.py --verify research/notification/results/run-20261023-notification-v1/receipt.json
git status --short -- research/notification docs/NOTIFICATION-CONTRACT-DRAFT.md docs/team/reports/notification.md
```

- 状态工具退出 0，详情见本报告“开始时检查”，不作为重新测试证据。
- 冻结文件摘要与 `snapshot.json` 相同：`ad4dc90989e807a277a8b0766114fc0f279064ebc838f222d63e4ef041d62685`。
- 直接单测首轮 34 项通过，11.346 秒；随后完善 ACK 范围核对和契约后，独立证据运行再次 34 项通过，10.472 秒，退出 0。应引用后者作为当前证据。
- 独立证据核验退出 0：`{"fresh_and_passed": true, "errors": []}`。
- 已读取生成 JSON 核对上述 7 个轨迹检查点，未改动证据数据库。
- Git 只读检查显示本角色文件为未跟踪；未提交是本项目当前工作树状态，不影响上述本机测试已运行的事实。

## 未覆盖问题和需要协调的接口

1. **真实收衣授权**：须由后续状态机定义独立标定依据与授权引用，不能将弱信号、连续次数或本原型模拟字符串映射成真实授权。
2. **批次与 episode**：需确定开夹、重新挂衣、重新受潮、暂停／恢复、配置变化怎样切换批次或重新通知。当前 episode 仅是可重跑的建议规则。
3. **T06 持久接口**：确定单写者、事件原子提交、Flash 日志／CRC／磨损、计数溢出、恢复出厂和存储回滚处理。测试覆盖的是进程中断，不是物理断电或故障介质。
4. **T05 能量接口**：设备上报超时、每次连接／失败的能量成本、每日重试预算、next_report_due／宽限／暂停原因尚未接入；本原型的服务端秒级重试不构成节能证明。
5. **App 与真实传输**：当前“提醒”只是一条本地持久记录。真实手机后台接收、权限关闭、系统直接展示绕过校验、稳定通知 ID、延迟推送和撤回均待实测。用户已读也只是显式模拟动作，未实现离线读回执重试。
6. **安全／规模／存储**：模拟绑定凭证不是生产认证；没有 TLS、账户授权或攻击测试。尚无多设备、多接收者、并发 worker、容量上限、历史清理、磁盘满、损坏／克隆恢复和服务端集群验证。
7. **无法得知的最新状态**：服务端不能拒绝它尚不知道的离线新状态；保守 TTL 和恢复时优先上传快照降低影响，但不能证明有效期内衣物不变化。

## 建议的协调动作与停止点

协调者先阅读本草案与最终 receipt，按上述两条运行命令复核需要的部分。当前只登记本角色预研产出可审查，不调整 T07 的主线状态。待 T04/T05/T06 前置接口接受后，再对齐状态、批次、episode、真实授权、时间和持久存储约定，执行正式 T07 验收；必要改动另行分配所有权。

本轮在此停止。没有采购、服务申请、部署或手机操作。
