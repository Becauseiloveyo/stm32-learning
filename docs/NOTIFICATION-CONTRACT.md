# T07 离线研究提醒契约 v1

日期：2026-10-27（2026-10-26 开始接入）。执行入口为 [notification_t07](../research/notification_t07/README.md)。本契约调用已验收 [T04 状态机](STATE-MACHINE.md) 的 Python 参考实现，并定义向 [T06 C 有界接口](CORE-INTERFACE.md) 的映射。当前没有实际云服务、手机、无线设备或收衣授权；主线验收和状态由协调者维护。

旧 [预研草案](NOTIFICATION-CONTRACT-DRAFT.md) 与 `research/notification/` 保留为历史。新实现不导入旧原型、不保留 `SIMULATED_COLLECTION_ALLOWED` 分支；以下规则取代旧草案的正式接入建议。

## 1. 唯一允许的提醒路径

`Device.process(raw_event, wall_s=...)` 调用真实 `StateMachine.handle`；原始 finish 包经已验收 `assess` 判断。传入的自称状态、assessment 或授权不作为测量证据。返回的 `output` 就是 T04 的实际结果，`event` 是它的通知投影。

研究提醒须同时满足：

- T04 状态为 `AWAITING_RECHECK`，且 `independent_review_due is True`；有效原始弱周期数已达到当前配置的研究复核次数。
- 没有设备控制器安全故障，当前绑定、boot、批次、安装、完整配置和状态版本仍是服务端最新快照。
- 证据测量时间可用、时钟有效、首次接收没有未来时间问题，且证据尚未到期。

通知类型仅有 `RESEARCH_REVIEW` 与 `STATE_UPDATE`；后一类只更新状态／历史。研究提醒的固定文案为：**“研究：需独立复核。弱信号尚未确认，不是收衣建议。”** 全部输入情景和证据带 `SYNTHETIC_UNCALIBRATED`；全部 `collection_authorized` 必须严格为 false。连续弱信号既不产生真实收衣建议，也不会通过模拟授权后门产生“已干”事件。

测试必须实际走通一条研究提醒，再验证取消／去重，不能以零提醒路径替代这些行为。生成的连续情景保存原始观测、配置、逐步 T04 输出；只读核验会重新在内存调用 T04 重放这些输入。

## 2. API 与控制器责任

从项目根导入：

```python
from research.notification_t07.device import Device
from research.notification_t07.service import Service, Receiver
from research.state_machine.machine import DEFAULT_CONFIG

# 显式首次初始化；目标路径必须不存在。
device = Device.provision(device_path, "hanger_1", "producer_1", wall_s=10000)
service = Service(service_path, create=True)
receiver = Receiver(receiver_path, "owner_a", create=True)
service.bind("hanger_1", "producer_1", "owner_a", "fixture_token", DEFAULT_CONFIG, 10000)

# 真实控制事件由采集层给出：boot_id 必须使用 device.boot_id。
# device.process(raw_event, wall_s=trusted_wall_s)
snapshot, pending = device.envelope()
ack = service.submit(snapshot, pending, "fixture_token", server_now_s)
device.acknowledge(ack)
service.dispatch(receiver, server_now_s, reachable=True)
```

`Device(path, wall_s=...)` 表示设备进程真正重开，每次都会在事务中增加 boot 代次，冷初始化 T04、生成 boot 事件，并持久保存无证据的新快照。它不是只读检查数据库的操作。历史研究提醒不会在 reboot 后恢复；需重新安装、等待和采样。设备不允许调用方通过 `process` 伪造 boot；旧对象在另一对象 reopen 后被 boot 代次隔离，不能继续写入或上传。

服务／接收端 reopen 为 `Service(path)`、`Receiver(path, owner)`，不会改变设备 boot。首次初始化显式 `create=True`；任何已有路径都不会被覆盖。单个存储文件缺失、空白、SQLite 损坏、元数据／outbox 摘要不符时拒绝恢复，不隐式重新分配原身份。

`rebind(new_epoch, wall_s=...)` 消费服务端新授予代次，开启新 boot、清空旧证据。已撤销代次的 outbox 条目在同一事务删除，`discarded_on_rebind` 记录丢弃数量；它们不移交给新所有者。旧数据明细不在设备另存无界归档。

当前为可信单写者的本地控制器。服务端模拟注册表的 token 只是测试字符串；SHA-256 只核对一致性，不是认证、签名或防篡改信任根。生产端的身份分配、TLS、密钥、账号绑定和事件真实性仍由设备层／服务另行实现。

## 3. 身份与字段

| 字段或派生值 | 含义与范围 |
|---|---|
| `schema_version` | 固定 `T07-1`；未知版本拒绝。 |
| `device_id`、`producer_id` | 1–16 个 ASCII 字母、数字、下划线或短横线。producer 标识一个持久存储实例；正式初始化必须全局分配唯一实例，本地情景使用确定性名字。 |
| `binding_epoch` | 服务端绑定代次，从 1 递增。设备使用已经授予的代次，不自创绑定授权。 |
| `event_seq` | 生产者实例内从 1 原子递增；跨 boot／重新绑定不清零。当前有界到 `2**31-1`，超界拒绝而不回绕。 |
| `event_id` | 派生 `{producer_id}:event:{event_seq}`；重复上传同一个 ID 和完全相同的规范化 JSON。 |
| `state_version` | 派生为生命周期 `event_seq`，不是批次内归零版本。不同批次／安装名字不参与字典序比较。 |
| `boot_seq`、`upstream.boot_id` | 从 1 增加的持久 boot 代次，ID 为 `{producer_id}-b{boot_seq}`；在当前范围最长 28 字符，可映射 C 的 32 字节身份槽。 |
| `upstream` | T04 的真实 snapshot，含状态、boot、批次、安装、配置哈希、时钟高水位、pending 周期、弱证据 ID、最后证据结束时刻、复核标记和 false 授权。 |
| `applied_config` | 完整真实应用配置快照，由 `candidate.config.snapshot()` 得出；不能从服务端要求的配置直接填充。 |
| `episode` | 持久研究复核轮次计数；T04 `independent_review_due` 从 false 进入 true 时增加。连续复核或正在再次采样时仍属同一轮；清零后重新达到条件才进入新轮次。 |
| `notice_key` | 绑定、设备／生产者、boot／批次／安装／配置域及 episode 的确定性摘要。接收端用它避免持续状态反复打扰。 |
| `evidence_key` | 绑定、身份域、最后原始有效周期 ID 与 `last_evidence_end_s` 的摘要，用于冻结同一原始证据的时间映射和 TTL。 |
| `measured_at_s` | 最后有效弱周期结束时刻映射到 UTC Unix 秒；没有有效时钟则 null。**不是 tick、上传或重传时刻。** |
| `confirmed_at_s` | 这条通知投影形成时的可信墙钟；用于区分最近状态确认和原始测量。不会改写 `measured_at_s`。 |
| `uncertainty_s` | 在原始有效 finish 时保存的测量墙钟双边误差界，非负有限秒数；同一证据不随 tick 改变。 |
| `clock_valid` | 当前投影具备有效原始测量时间和当前状态确认时间；严格布尔。失效时不能研究提醒。历史测量时间若已知仍可保留作说明。 |
| `ttl_s` | 正整数，当前上限／默认 1800 秒；来源为设计中的暂定 30 分钟，仍未通过实物验证。 |
| `controller_fault` | 通常 null；outbox 耗尽后为 `OUTBOX_CAPACITY_EXHAUSTED`，无论上游快照为何状态都禁止提醒。 |

传输计数采用非负有界整数，不接受 bool 冒充整数。`wall_s`、不确定度使用有限非负秒数，当前主机域为 `0..1e12 s`，排除 bool／NaN／Inf。原始单调秒数的声明域不超过 T06 的 `1e9 s`；非法当前域时间按 T04 故障规则处理，超出声明域的输入在调用前拒绝。原始观测的相对秒数、摄氏度和 %RH 单位沿用测量契约。

Event 的 JSON 保存 dataclass 字段；event_id、state_version、notice_kind、notice_key、evidence_key、expires_at_s 都由已有字段派生，不接受另一份可能冲突的手填值。业务输入只通过设备 raw-event 入口生成；服务端检查投影一致性但不重新鉴定远端观测真伪。

## 4. 时间与证据保鲜

假设受信任控制器在 finish 到达时提供 `wall_s` 和当前 boot 的 `raw.monotonic_s`。首次接受新的有效原始弱周期时：

```text
measured_at_s = wall_s - (raw.monotonic_s - upstream.last_evidence_end_s)
expires_at_s  = measured_at_s + ttl_s - uncertainty_s
```

允许完成交付宽限时，也扣掉该延迟；不能把晚交付时间当测量结束。相同 `(boot, evidence_end)` 后续 tick／start／重发沿用原记录；只有新的有效原始 finish 才可产生新的测量时间。finish 没有墙钟时，后续 tick 不会替它补日期。

服务端首次接收证据需要 `measured_at_s + uncertainty_s <= server_now`，状态确认时间也不能在未来。`evidence_admission` 对同一 evidence_key 固定测量时间／误差界／TTL 和首次时间准入；冲突回滚，首次未来时间不能靠等待或换一个 tick 事件重新激活。须用新有效原始周期取得新证据。

当 `server_now >= expires_at_s` 时已到期，截止等号不提醒。设备当前墙钟回退会锁存时钟无效直到真正新 boot；新 boot 仍需超过持久墙钟高水位并重新取得证据。服务端也持久保存时间高水位，回退时拒绝操作。以上没有实现真实 RTC、漂移估计、NTP 或可信校时。

`bindings.last_contact_s` 只在授权且完整提交成功后更新，包括内容相同的重传；它与 `events.saved_at_s`（首次保存）及 `confirmed_at_s`（设备状态投影形成）分开。未来时间／无时间的研究记录可以保存，但保存本身不会替它产生时间有效性。

T04 的证据链闲置间隔不是所有历史弱周期的总年龄。本层 TTL 锚定最后有效周期，不能据此声称整条历史链或真实衣物一直新鲜。

## 5. 有界设备队列与持久化

设备 `meta`（含校验摘要）保存身份、序号、boot、当前完整配置、episode、时钟高水位和最新 head；`outbox` 存未获得保存确认的事件原文及 SHA-256。默认最多 32 条未确认事件，容量通过 provision 指定。raw 事件先作用于 T04 副本；元数据、队列、序号提交成功后才替换内存状态，提交前不对外发送。

当队列已满仍发生新的状态变化时，不能丢掉新变化而保留旧有效 head。实现会原子增加序号、把**独立预留 head 槽**替换为带容量故障的安全快照，并锁存停止；旧 outbox 最多保留容量条数。下一次提交总是带该安全 head，因此服务端能取消旧提醒。安全 head 本身可能没有 outbox 行，仍能得到保存确认。

锁存后不再处理采样事件；ACK 可以清除旧 outbox，reopen／重新绑定也不会自动解除锁存。本轮未实现正常运行中的解锁／回收协议，需要明确重新初始化生产者和授权关系；不能自动重置同一身份继续。该策略牺牲可用性以保留撤销语义，不是长期产品运行方案。

设备仅 outbox 条数有界。Python T04 的运行期历史集合和证据列表、服务端事件／准入／推送历史、接收卡片仍无界；单条 JSON 大小也不是 MCU 有界内存证明。不能把“32 条队列”写成整套系统内存已完成 STM32 适配。

所有 SQLite 使用 DELETE journal、FULL synchronous、BEGIN IMMEDIATE。实际测试用子进程 `os._exit(73)` 覆盖设备计数后／提交前／提交后、服务端事件后／提交前／提交后、模拟推送后、接收保存后未 ACK。进程崩溃恢复不等于物理断电、存储介质故障、Flash 写磨损或板上电源跌落。

数据库完整性、元数据／outbox 摘要能发现本轮测试的损坏，但不是防恶意回滚或克隆。复制旧数据库及其合法摘要、恢复旧备份、重复分配 producer、多个真实设备共享身份仍未解决。存储写失败／无法提交时设备必须停止上传，不能继续发送最后一个旧 head；本机 API 以异常交给外层，未模拟真实磁盘满后的设备电源控制。

## 6. 服务端事务、取消与确认

一次 submit 携带当前 snapshot 和 pending；pending 不能包含更高状态版本或 boot。服务端在同一事务中校验绑定和不可变内容、保存事件、先推进最新 head、取消旧任务，再为当前有效的研究事件创建任务。更旧包只补历史，不回滚 head。新 boot／批次／安装／配置／状态发生后，旧任务由 head 检查失效。

同事件 ID 的内容变化拒绝并回滚整个上传，包括已插入的新事件和 head。相同 ID 重试只返回已保存确认；不会创建第二份任务。每次调度和接收显示前再次核对当前 head、绑定、配置和到期时间。

| 阶段 | 保存条件 | 含义 |
|---|---|---|
| `SERVER_DURABLY_SAVED` | 服务端事务提交后返回 `saved_hashes`、绑定及 `acknowledged_at_s` | 事件已存储；历史事件也可确认，不保证它能提醒。 |
| `SIMULATED_PUSH_ACCEPTED` | push_attempts 保存一次本地通道模拟接受 | 不代表任何推送供应商或手机收到。可有多次尝试。 |
| `SIMULATED_RECEIVER_SAVED` | 接收端校验后持久保存 notice_key 和研究卡片 | 是本地模拟显示记录，不是真机屏幕证据。 |
| `DUPLICATE_SUPPRESSED` | 已有相同 notice_key | 同事件不重复提醒；更新版本静默更新卡片并清除该版本的已读标志。 |
| `user_read_at_s` | 显式测试调用 `Receiver.read`，且对应版本已有有效接收确认 | 模拟“已读”，不表示真人看到、更不表示衣物已收取。 |

设备收到保存确认后即可删除匹配的 outbox 项。**重复或迟到 ACK**：本绑定中已发行但已不在队列的旧 ID 安全无操作；旧绑定 ACK 也不能释放新绑定条目。在队列中的条目必须摘要匹配；混合 ACK 中任何待发摘要不匹配会整体回滚，不能先删除正确部分再报错。未发行的新 ID 拒绝。没有为 ACK 去重引入无界设备摘要历史。

接收端按域＋episode 去重。已经显示的卡片在下一次可达 sync 时标记失效；真实手机离线时的 OS 通知无法由此保证撤回。服务端重试 `[60,300,900,3600]` 秒为确定性测试参数；始终按原 evidence TTL 到期，不因接收者恢复而续期。

服务端无法知道设备离线期间尚未上报的新变化：已保存的旧状态在 TTL 内仍可能产生研究提醒。测试明确保留这一反例。恢复时的最新快照同步和 TTL 降低陈旧风险，但不消除这个知识边界。

## 7. 配置、App 展示及 T06/T05 映射

`Service.require_config(device_id, config, now_s)` 只改变要求配置哈希，立即阻止不匹配的提醒。设备须真实处理 T04 configure 事件、更新完整配置、重新安装并重新测量；服务端要求不替代设备已应用确认。

`app_status` 返回最新研究状态、测量时间、状态确认时间、最近通信时间、required／applied 配置及当前 eligibility。客户端应展示研究文案、原因、数据年龄、过期／时间未知、当前 ACK 阶段；不能把模拟推送接受写成“已送达”。没有新测量时，刷新 App 仅取得已保存状态，不能伪造新测量时间。

`measured_at_s` 当前只覆盖有效弱证据的最后周期；强水汽来源、开夹或故障清掉弱证据后可为 null，不能因此显示“从未测量”。完整测量历史、电池／充电档位和预计上报时间尚未接入。

| 当前 Python / 通知投影 | T06 字段及外部责任 |
|---|---|
| `StateMachine.handle(raw)` | `hs_handle(hs_machine*, hs_event*, hs_output*)`；调用方先解析／校验原始观测和完整配置，不直接 memcpy ABI。 |
| accepted / disposition / reason / assessment | `hs_output` 同名成员；C 返回 1 不表示事件接受，更不表示质量或授权通过。 |
| `upstream.state / boot_id / batch_id / installation_id` | `hs_snapshot` 对应成员；Python 可允许更长身份，转 C 前须检查 ≤32 个可打印 ASCII 字节且无首尾空格，不截断。 |
| `last_monotonic_s / last_evidence_end_s / pending_cycle` | C 对应 double 和 has_* 字段；不存在不能用 0 秒代替，时域按 boot 隔离。 |
| `weak_cycles / independent_review_due / collection_authorized` | `hs_snapshot.weak_cycles` 与 `hs_output` 标志；collection 必须为 0。本层研究通知不创造新的 C 收衣状态。 |
| `applied_config` 和 config_sha256 | C `hs_output.config`／实际配置解析；不能传入一个未解析的自称哈希就当配置一致。 |
| `capacity_latched` | C 耗尽时外层必须生成禁提醒的安全状态，类似本层 outbox 停用；本轮调用 Python，因此没有实接该 C 锁存分支。 |
| 生产者身份、boot_seq、event_seq、墙钟映射、outbox／ACK | C 核心没有这些持久化功能，由外部控制器负责。本轮 SQLite 行为不能冒称 C 固件已有实现。 |

T06 原生消息历史 64 条、每 boot 周期 16 个等限制继续适用；本 Python 接入没有取消这些限制，也没有证明长期回收。本通知链路未执行 C；协调入口更新时的 C 主机构建记录独立保存在 `verification/core-tests.json`，不代表通知代码已在 C 固件或硬件上运行。

T05 的无线准入、单次超时、跨日额度、实际失败支出和重启恢复尚未实际接入本层。设备重连须先由 T05/外部控制器批准并计费，只有真实服务端保存 ACK 才释放队列。服务端的退避不代表设备省电；本轮不声称端到端功耗、峰值稳定或后台送达率。

## 8. 复跑与剩余限制

```powershell
python -B -X utf8 -m unittest discover -s research/notification_t07 -p test_notification.py -v
python -B -X utf8 research/notification_t07/run_checks.py --output research/notification_t07/results/<新目录>
python -B -X utf8 research/notification_t07/run_checks.py --verify research/notification_t07/results/<目录>/receipt.json
```

输出路径必须在本角色目录内且不存在。manifest／receipt 绑定本层源码、真实上游 Python/C 源码与契约、输出指纹、运行环境及确定性参数；运行前后确认来源稳定。不把协调工具或 core-tests 收据作为源，避免协调入口变化混淆模型来源。verify 只读哈希并在内存重放保存的原始事件，不打开／更改示例数据库，不重跑全部 unittest。

当前协调验收使用 `research/notification_t07/results/accepted-v2/`。早期 `accepted/` 与 `verification/T07-interrupted-run.json` 保留历史，不是当前通过证据。协调入口 `python -B -X utf8 tools/project_status.py --record-notification-tests --check` 另核对四个数据库的实际表与 trace，并在临时目录完整复算链路；主收据见 [notification-tests.json](verification/notification-tests.json)，结论见 [T07 审查](verification/T07-review.md)。

限制还包括：多进程服务竞态、多接收者策略、系统通知与数据库之间的崩溃窗口、真实身份认证和恶意输入安全、队列容量恢复与数据保留政策、磁盘满／故障介质、原始采集真实性、真实时钟不确定度、离线已读回执重试。没有采购、部署、真实手机通知或实物标定；测试通过的具体范围与文件见角色报告，正式 T07 验收由协调者完成。
