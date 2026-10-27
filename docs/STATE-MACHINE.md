# T04 多周期研究证据状态机 v1.0

日期：2026-10-25（2026-10-24 启动）。入口为 [machine.py](../research/state_machine/machine.py)，当前仅在电脑上运行，无硬件、无线连接或真实衣物标定。所有生成情景标记 `SYNTHETIC_UNCALIBRATED`；所有输出 `collection_authorized=false`，没有收衣完成事件。

## 1. 与已验收单周期层的关系

当前 [测量契约](MEASUREMENT-CONTRACT.md) 负责一个周期的原始观测、阶段、单位、特征窗口及演示门限。新层从 `theory.model` 导入当前 `assess`，对合法时间包络中的原始观测重新判定，不接受调用方传来的“弱信号”、状态、真值或授权作为证据。

`theory.model.EvidenceTracker` 保留为上游的简单计数基线，没有批次、安装或启动域边界。本轮没有修改它；新入口 `StateMachine.handle(event)` 承担多周期边界。重复弱信号只形成研究记录，不能补出独立标定。水汽来源明显也不等于来源已归因到衣物，参见 [模型适用性第 7 节](MODEL-VALIDITY.md)。

## 2. 配置与身份

配置唯一入口为 `machine.py::Configuration`，其中嵌套已验收的 `MeasurementProtocol`、`Gates` 和本层 `Policy`，以及状态机行为版本。`config.sha256` 覆盖这四部分的完整快照，数值规范化后计算 SHA-256；不是仅复用上游协议指纹。协议、门限或状态机策略改变都形成新的配置身份。版本未知或不完整快照拒绝；配置错误不自动补默认字段。

| 身份 | 谁提供 | 作用和要求 |
|---|---|---|
| `boot_id` | 可信的启动管理调用方 | 每次启动／重启使用全新身份，定义单调时间域；不同启动之间不比较秒数 |
| `batch_id` | 安装／换衣调用方 | 一批衣物的身份；同一 boot 内切换到另一批次后，不得重新使用旧批次名 |
| `installation_id` | 安装调用方 | 每次安装／重新夹取都使用全新身份；同批次重新夹取也要换身份 |
| `cycle_id` | 采样调用方 | 一个已启动周期的身份；同一 boot 内一经启动，取消或失败后也不能重用 |
| `event_id` | 事件生产者 | 在一个 boot 内唯一；相同内容重发幂等，相同 ID 改变被消费字段则故障 |
| `config_sha256` | 当前配置对象计算 | 含协议、Gates、策略和版本；每个事件都要匹配当前配置 |

身份字符串长度 1–128，无前后空白和控制字符。新层不生成实际设备的唯一标识，也不证明上报身份真实。配置哈希用于一致性，不是身份认证。行为改变需要升级状态机版本，不能只依靠相同数值配置标识不同算法实现；产物清单另记录源码哈希。

`configure` 事件以**旧的当前**配置哈希标识调用上下文，并携带完整 `new_config`。成功变更清空证据和未完成周期，要求新的安装轮次与稳定等待；与当前配置完全相同的更新不清空。携带非当前哈希的迟到包被拒绝，不修改当前有效域。

## 3. 时间、间隔和研究假设

`monotonic_s` 是当前 boot 内调用方提供的单调秒数，不能用设备墙上时间、Unix 时间或另一 boot 的时钟。一个周期的 `start_s` / `end_s` 也属于该 boot；`observations[*].time_s` 则按上游契约从该周期的 0 秒起算。不得用外层单调时间替换原始观测的相对时间。

所有策略数值只在 `Policy` 定义，当前运行快照见 [manifest](../research/state_machine/results/manifest.json)，文档不复制一套默认值。

| Policy 参数 | 单位／有效范围 | 含义 |
|---|---|---|
| `stabilization_wait_s` | 有限正数，s | 安装或恢复后的研究等待时间；经过它只意味着计时完成，不能证明实物已稳定 |
| `min_cycle_gap_s` | 有限正数，s | 前一预留周期终点到下一启动的最小间隔，等于时允许 |
| `max_evidence_gap_s` | 有限正数，s；不小于最小周期间隔 | 前次有效弱周期结束到下一启动／空闲检查的最大闲置间隔，严格超过时清空弱链 |
| `max_event_gap_s` | 有限正数，s | 当前域相邻被处理事件允许的最大时钟跨度，严格超过时进入故障 |
| `completion_grace_s` | 有限正数，s | 计划周期终点后的允许交付延迟；超过终点＋宽限＋时间容差，周期失效 |
| `timestamp_tolerance_s` | 有限正数，s | 包络终点和数值比较容差；小于采样周期及关键策略时长的千分之一 |
| `review_after_weak_cycles` | 正整数，次数 | 触发“需要独立复核”的研究标记，永远不是收衣阈值 |

**最大证据间隔是闲置间隔，不是证据总年龄。** 没有 pending 周期时，以 `now-last_evidence_end_s` 检查；在边界上启动可保留连续弱链。已按时启动后，测量耗时和允许的交付宽限不再追加到该闲置间隔。因此结束到结束的间隔和整个证据链年龄可能更长，初次弱信号也可远早于这个限值。当前实现没有“所有历史弱证据总年龄”的规则，不能据此宣称证据始终新鲜。边界测试覆盖等于、略超过和进行中的周期。

有效当前域事件时间必须严格递增；不同事件具有相同时间也被视为异常。幂等重发和已消费周期的重发在时间推进前处理。旧 boot、旧安装／批次或错误配置的数据不能推进当前时钟。时间回退不降低时钟高水位；大幅向前跳变保留新的高水位但清空证据，需要之后显式恢复。合法的新 boot 可以从 0 秒开始。

一次 `start_cycle` 就预留完整区间，取消、开夹或失败也保留其计划终点，直到新 boot。这样重命名消息或周期不能重用重叠区间；同一 boot 的重新安装也可能需等待预留终点和最小间隔后才能再启动。这是保守软件规则，不是物理稳定性的判断。

时钟只在 `handle` 收到事件时推进，没有后台定时器。调用方必须定期提供 `tick`，或确保下一事件触发超时检查；没有后续事件时，快照可能仍显示 MEASURING。这里没有实现能量调度、实时中断或设备时钟漂移估计。

## 4. 状态与转换

| 状态 | 含义 | 主要后续路径 |
|---|---|---|
| `AWAITING_INSTALLATION` | 初始／新启动／开夹／配置改变后缺有效安装 | 新身份的 install → STABILIZING |
| `STABILIZING` | 等待研究计时条件，不宣称测点稳定 | 到时的 tick 或 start → READY 或 MEASURING |
| `READY` | 可发起单周期采样 | 合法 start → MEASURING |
| `MEASURING` | 一个周期已预留，等待完整原始观测 | finish 调用 assess；超时或并发启动 → FAULT |
| `AWAITING_RECHECK` | 已记录弱信号，仍未确认 | 满足间隔后再次 start；闲置超限 → READY 并清零 |
| `SOURCE_PRESENT` | 有明显水汽来源，不能归因衣物 | 弱链为零，可按间隔再测 |
| `FAULT` | 当前周期／时钟／输入不可信 | recover → STABILIZING；或新 install；开夹需重装 |

所有状态均无收衣授权。`independent_review_due` 表示计数达到研究复核次数，仍不表示已干、建议收衣或置信概率。

事件必有 `event_version="1.0"`、`kind`、`event_id`、`boot_id`、`monotonic_s`、`config_sha256`。各事件额外字段和作用：

| kind | 额外字段 | 行为 |
|---|---|---|
| `boot` | 无 | 仅显式新启动身份可开启新时间域；清空安装、周期和证据，不恢复持久化计数 |
| `install` | batch_id、installation_id | 安装／换衣／重新夹取，清空证据、取消 pending，重新等待 |
| `configure` | new_config | 完整配置快照；改变后清空并重装 |
| `clip` | 当前 batch_id、installation_id | 清空证据与 pending，撤销安装 |
| `tick` | 无 | 观察时间，检查超时／闲置过期和等待完成 |
| `recover` | 当前 batch_id、installation_id | 仅已有安装的 FAULT 可重新等待；不会恢复旧证据 |
| `start_cycle` | 当前 batch_id、installation_id、cycle_id | 当前事件时间就是开始时间，按协议预留区间，不接受回填历史开始时间 |
| `finish_cycle` | 当前 batch_id、installation_id、cycle_id、start_s、end_s、observations | 与预留区间匹配且按时交付才调用真实 assess；不信任调用方 assessment |

结构有效的原始观测进入 `assess` 后：弱信号累计一次，强信号清零并进入 SOURCE_PRESENT，UNKNOWN 清零进入 FAULT。原始记录含任何 `clip_event is True` 时，即使其他无效值使单周期判定提前返回，也撤销安装、要求重装。未开始、未完成、超时、包络时间不匹配和失配身份都不能形成弱证据。

换衣批次、安装轮次变化、有效配置变化、开夹、重启和当前域异常均清零。不允许以简单地删除坏周期、继续之前弱链的方式掩盖测量失败。重复旧消息与不同域的迟到包则隔离拒绝，既不累计，也不把迟到包当成当前测点的异常来污染当前证据。

## 5. 返回、拒绝原因与恢复

每步返回 `accepted`、`disposition`、`reason`、可空的真实 `assessment`，以及状态快照：当前身份、完整配置哈希、时钟高水位、等待期限、预留终点、pending 周期、弱证据周期 ID 列表、计数、研究复核标志和恒为 false 的授权。

`APPLIED` 表示该事件被处理，不表示衣物可收；`REJECTED` 表示未成功执行请求，可能同时进入安全故障；`IGNORED_DUPLICATE` 为重复事件／已消费周期，无重复累计。理由按执行顺序返回首个适用原因，不承诺穷举一条恶意输入的所有错误。

| 原因类别 | 代表原因 | 恢复方式 |
|---|---|---|
| 重发 | duplicate_event、cycle_id_reused | 无需恢复，保持当前快照；不能改消息 ID 来重算旧周期 |
| 其他域 | stale_or_unknown_boot、retired_boot、configuration_mismatch、batch_or_installation_mismatch、retired_installation_or_batch | 丢弃迟到包；由可信调用方提交当前域事件，不自动切换身份 |
| 未就绪／间隔 | not_ready、cycle_gap_too_short | 在有效当前时钟下等待；不能越过故障或缺安装状态 |
| 时间异常 | invalid_monotonic_time、non_increasing_event_time、event_time_jump、unrepresentable_deadline | FAULT；保持时间高水位，显式恢复／重新安装，实际重启必须换 boot_id |
| 周期异常 | cycle_already_active、cycle_timeout、no_matching_cycle_start、cycle_interval_mismatch | FAULT，清零；显式恢复并重新采样，不能复活旧周期 |
| 观测异常 | invalid_cycle: 后接 assess 的原因 | FAULT；读数恢复后显式恢复并等待，不把坏周期计入连续次数 |
| 安装变化 | observed_clip_requires_reinstallation | 撤销安装；使用新 installation_id 重装 |
| 消息／配置异常 | malformed_event、malformed_event_payload、event_id_conflict、invalid_configuration、invalid_cycle_id、invalid_installation_identity | FAULT；纠正输入并显式恢复／重新安装 |

顶层未知“声称结果”字段和观测中的场景真值／模拟参数／导出 delta 不进入决策或去重摘要。去重签名仅覆盖消费字段；同消息 ID 的有效观测改变会触发冲突。原始观测需要 Python JSON 对应的数值、布尔、null 类型，不能把 CSV 字符串直接传进来。生成文件严格 JSON，无 NaN；直接调用的非有限观测由上游判定拒绝。

## 6. 调用与确定性重放

```python
from research.state_machine.machine import DEFAULT_CONFIG, StateMachine, replay

controller = StateMachine(DEFAULT_CONFIG)
# 真实调用方提供唯一 boot_id／event_id 及可信单调时间，再 install/start/finish。
output = controller.handle({
    "event_version": "1.0", "kind": "boot", "event_id": "startup-event",
    "boot_id": "unique-startup-domain", "monotonic_s": 0.0,
    "config_sha256": DEFAULT_CONFIG.sha256,
})
assert output["collection_authorized"] is False
# replay(events, config) 按保存的到达顺序运行，不排序、不修复丢失输入。
```

从项目根运行：

```powershell
python -X utf8 -m unittest discover -s research/state_machine -p "test_*.py" -v
python -X utf8 research/state_machine/run.py
python -X utf8 research/state_machine/run.py --verify
```

生成目录 `research/state_machine/results/`：`events.json` 保存每个情景的初始配置和全部输入事件／原始观测；`steps.jsonl` 保存逐步输出；`summary.csv` 为可读摘要；`manifest.json` 记录源码（相对研究目录）、输入（相对项目根）、输出（相对 results）哈希、全部配置快照、单位、情景假设和随机性说明。

`--verify` 不生成或修改产物：先核对哈希，再用保存的原始事件独立重放，再重新生成内存中的所有情景和输出，逐字节核对输出及完整 manifest。参考情景无随机事件，观测噪声为零，仍记录模拟器固定种子；测试中的随机变异另有固定种子，不混成实测。

## 7. 当前保障与不能保证的事项

- 已实现软件身份隔离、当前域时钟检查、严格单周期包络、取消／超时、原始观测判定、显式恢复、幂等重发和连续证据清零。记录有限的环境保护原因，并不代表发现所有实际模型失配。
- 调用方必须可信地产生批次／安装／启动／周期唯一身份，正确关联测量时间和原始样本，报告开夹并按时调用 tick。伪造一套新身份、新时间并把同一组读数重新贴标，单靠本层无法知道它其实是旧数据；未观测的滑移也无法检出。
- 进程内保留退休 boot、安装、批次、周期及消息历史。新进程完全清空证据，必须重新启动和安装；不从磁盘恢复旧弱链。跨进程防重放仍依赖调用方的唯一新 boot_id 和控制事件来源，不能复用旧 boot 身份来导入延迟数据。
- **messages、cycles、seen_boots、安装／批次历史集合以及 evidence 列表会随运行增长。当前是 PC 参考实现，没有验证 MCU 有界存储、掉电一致性、并发调用、长期运行或实时开销。** 将来移植必须另外设计容量上限、退休水位／回收规则和持久化防重放，不可直接删历史集合后声称保障仍相同。
- 等待、最小间隔、空闲过期和复核次数只管理软件研究流程，不能证明真实响应稳定、实物漏风受控、独立误差消失或整件衣物已干。重复相关信号没有变成独立标定，且当前没有物理稳定性阈值的新发明。
- 对未来调度、固件和通知层只交接状态、原因、身份和 next opportunity 所需的时间约束；本轮未实现 T05 能量调度、T06 C 移植、T07 远程推送。报告见 [measurement-t04.md](team/reports/measurement-t04.md)，全局验收由协调者完成。
