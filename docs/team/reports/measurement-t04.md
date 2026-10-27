# T04 测量角色交接

日期：2026-10-25（2026-10-24 启动）。本角色有限交付已完成并通过自检，提交协调者独立验收。未更新主任务状态，本轮停止在 T04，不执行 T05–T07。

已读 AGENTS、PROJECT、STATUS、TEAM 的 T04 补充、本轮任务书和 ROADMAP T04；已读取测量契约、MODEL-VALIDITY 第 4–8 节、已验收协议及 assess／EvidenceTracker、总体架构第 7 节。只读恢复检查通过，T01／T02／T03 已验收且来源、旧测试收据仍匹配。`research/state_machine/` 初始不存在，本轮新增。

唯一写入范围：research/state_machine/**、docs/STATE-MACHINE.md、本报告。当前 theory、旧测量契约和其他研究均只读，不改全局状态／工具／收据，不创建对话／代理，不跨对话发消息，不提交／重置／清理。

实现边界：单调时间域和周期相对秒数分开；批次、安装轮次、周期及协议＋门限＋策略配置身份隔离；调用当前原始观测 assess；所有输出 collection_authorized=false。等待时长、复核次数及间隔是软件研究策略，不是物理标定。

## 本轮实现

新入口为 `research/state_machine/machine.py::StateMachine.handle(event)`，纯标准库、无 I/O 或后台时钟；`replay(events, config)` 按原到达顺序运行。状态包括等待安装、稳定等待、就绪、测量、等待复核、水汽来源明显和故障。新层调用当前 `theory.model.assess`；不修改上游 EvidenceTracker 或协议。

每个事件带版本、消息身份、boot 身份、单调秒数和完整配置身份；安装／采样再附 batch、installation、cycle 身份。周期开始预留协议区间，完成时核对开始／结束时刻、交付期限及原始观测。原始观测自身仍使用从 0 起的周期相对秒数。

`Configuration` 指纹覆盖状态机版本、完整协议、Gates 和 Policy；门限变化不会继续使用只覆盖协议的旧配置身份。有效配置变化撤销安装，清空证据后重新等待。批次切换、开夹、重新安装、未知周期、当前域时间异常和重启同样不能继承弱链。

完整事件重发幂等；同一周期改消息 ID 仍拒绝累计；同一消息 ID 改消费内容进入冲突故障；新周期 ID 不能复用过去时间窗或跳过 start。已启动但失败／取消的区间仍预留，直到新 boot，避免重新贴标签立即重用该段时间。

旧 boot、旧安装／批次及非当前配置包在当前时钟推进前隔离拒绝。新 boot 清空计数、安装和该域周期历史；旧进程证据不持久化恢复。原始记录有开夹标志时，即使另一无效值让 assess 提前返回 UNKNOWN，仍撤销安装而不是仅一般恢复。

## 参数入口与协调初审关注点

| 内容 | 唯一入口／约定 |
|---|---|
| 单周期参数与演示 Gates | 只读引用 `theory.protocol`；T01 默认参数未改 |
| 等待、间隔、宽限、复核次数、时间容差 | `machine.py::Policy`；单位和有效范围见 STATE-MACHINE 第 3 节 |
| 完整配置和行为版本 | `machine.py::Configuration`、`VERSION` |
| 事件模式、身份及真实 assess 调用 | `machine.py::StateMachine` |
| 情景覆盖、合成输入 | `scenarios.py::scenarios`；无随机事件，观测噪声为零 |
| 当前运行快照／指纹 | `results/manifest.json`，只作为输出记录 |

已落实协调初审的两个重点：

1. **`max_evidence_gap_s` 明确定义为最大闲置间隔，不是证据总年龄。** 无 pending 时，比较前次有效弱周期结束到新 start／tick（或其他当前域事件）的间隔；等于上限仍可启动，略超过则清零。按时启动后，测量期间即使结束到当前时刻已超过该限值，也不会因此抹去前一弱记录；交付仍受独立周期超时约束。`test_idle_evidence_gap_boundary_and_pending_window_are_distinct` 和 `test_idle_tick_expiry_and_no_background_clock` 验证边界及无后台时钟行为。
2. **历史集合无有界存储保证。** messages、cycles、seen_boots、安装／批次历史及 evidence 列表会随运行增长。这是 PC 参考实现，尚未验证 MCU 容量、回收、持久化、实时开销或长期运行；STATE-MACHINE 第 7 节明确指出移植必须另行设计容量与防重放回收规则。本轮没有抢做 T06。

## 实际检查与结果

以下命令均从项目根执行，没有运行任何 `--record-*`，也没有重跑未改变的 theory／probe／validity 测试。

| 实际命令／检查 | 结果 |
|---|---|
| `python -X utf8 tools/project_status.py`，初始与产物生成后各一次 | 只读恢复检查通过；T01 的 37、T02 的 25、T03 的 19 项既有收据及来源仍匹配。它们是旧收据核对，不宣称本轮重跑 |
| `python -X utf8 -m unittest discover -s research/state_machine -p 'test_*.py' -v` | 第一轮 30 项通过；最后补充身份长度和不可打印字符边界后，最终 **31 项通过**、退出 0、测试主体约 0.889 秒 |
| `python -X utf8 research/state_machine/run.py` | 退出 0；生成 19 情景、156 事件、41 次真实单周期评估、36 次弱记录；保存含重发的 7048 条原始观测行 |
| `python -X utf8 research/state_machine/run.py --verify` | 退出 0；源／输入／产物哈希、保存事件重放、完整重新计算、逐字节产物和完整 manifest 均一致；最终源码修改后已重新生成并再次通过 |
| PowerShell here-string → `python -X utf8 -` 只读摘要检查 | 核对 4 个源码、9 个输入、3 个输出哈希条目及 4 套完整配置；检查关键情景逐步原因；156 个输出及其中所有 assessment 的授权均为 false |

31 项中包含多组 subTest，不能把测试方法数量当成实际测量次数。主要证据：

- 使用实际 simulate 生成的无噪声原始观测调用实际 assess；湿表面大漏风连续 10 次仍为等待复核、无授权，达到复核次数只设置研究标记。
- 强信号清零、UNKNOWN 清零并显式等待恢复；缺数据不能用调用者伪造“弱信号”替代；真实强读数覆盖外部弱信号声称。
- 守卫观测在读取真值、场景名、漏风或导出 delta 时会抛错；判定和去重仍正常，移除诊断字段后相同消息保持幂等。
- 相同消息、不同消息 ID 的同周期、周期改名、旧窗口、完成前无 start、并发 start 的拒绝行为。
- 换衣、开夹中断、旧安装迟到、门限／协议／策略各自变化、重启、旧 boot 返回和新进程空证据边界。
- 安装等待、最小周期间隔、最大事件跨度、最大闲置间隔和完成宽限的等号／略超边界；时间相等、倒退和大跳变清零且不降低时钟高水位。
- 周期外层时间包络与原始观测窗口分别验证；缺阶段和缺终点不能形成证据。
- 非法配置、缺失字段、非有限／字符串／布尔时间、身份长度和不可打印字符等显式拒绝。
- 固定种子 20261024 的 120 次事件变异测试，不制造额外弱记录或授权；这只是测试输入变异，生成的参考事件序列无随机性。
- 重放文件校验测试在本角色临时目录运行，比较 verify 前后的文件内容及修改时间，确认不写产物；篡改 summary 后明确拒绝。临时测试目录已由测试上下文释放，没有清理共享工作树。

## 结果读法

| 生成情景类别 | 已观察行为 |
|---|---|
| normal_weak | 湿表面大漏风连续 4 个合法周期，最终弱计数 4，但仍 AWAITING_RECHECK、无收衣授权 |
| strong_resets | 弱→强→弱，最后计数 1，强信号未被解释为衣物来源确定 |
| missing_value／missing_tail／observed_clip_with_invalid | 当前坏周期使旧弱链清零；恢复／重装后新有效周期只计 1 |
| unfinished_timeout／renamed_old_window | 超时或旧窗口改名，最终 FAULT，计数 0 |
| duplicate_delivery | 两次重发均未多计；之后另一个合法周期使总计数为 2 |
| batch_switch／clip_reinstallation／change_gates／change_policy／change_protocol／reboot | 新域从零起；一次新有效弱周期后为 1，旧包不被导入 |
| same_time／backward_time／large_jump | 时钟异常清零，显式恢复和等待后重新计 1 |
| evidence_expiry | 闲置超过上限清零，下一有效周期计 1 |
| forged_assessment | 没有原始观测，即便声称弱信号和授权 true，也得到 UNKNOWN／FAULT、计数 0、输出授权 false |

生成输入 `events.json` 的 forged_assessment 情景**故意包含不可信的 true 授权声称**，用于证明新层忽略它。不能把输入攻击样本中的字段与正式输出混为一谈；`steps.jsonl`、summary 和 manifest 的授权均为 false。

## 新增文件

- `research/state_machine/machine.py`：独立状态机、完整配置、事件去重与重放。
- `research/state_machine/scenarios.py`：19 个可读参考事件情景、原始观测生成器。
- `research/state_machine/run.py`：生成、保存输入、逐步输出、清单和 `--verify` 只读重算。
- `research/state_machine/test_state_machine.py`：31 项行为／不变量／文件重放检查。
- `research/state_machine/results/events.json`：输入事件、初始配置及原始观测，约 3.7 MB，含重发，不是独立实测样本。
- `research/state_machine/results/steps.jsonl`、`summary.csv`、`manifest.json`。
- `docs/STATE-MACHINE.md`：状态／事件／时序／身份／异常／恢复／限制与调用接口。
- 本报告 `docs/team/reports/measurement-t04.md`。

未修改 theory、旧契约、适用性文档、其他研究目录、全局文档或测试收据。新 manifest 对上述只读输入和本轮状态机文档保存快照指纹；之后改任何被记录输入都需要核对并重新生成。

## 未验证项、限制与协调复核入口

本角色未发现阻塞 T04 有限交付的实现问题；以下限制必须保留：

- 没有硬件／实物校准、安装滑移检测、真正响应稳定证据或衣物收衣阈值。模型失配不能被软件计数消除。
- boot／batch／installation／cycle 身份及原始样本与时间的关联依赖可信调用方。若调用方把旧观测重新贴全新身份和新时间，本层无法识别其物理来源相同。
- 没有持久化弱证据恢复。新进程必须使用新的 boot_id；没有可信新启动身份的外部日志重放不在保障范围内。
- 无后台计时、实时并发、安全认证、MCU 有界存储或掉电一致性。需要调用方供 tick；未设计历史集合容量／回收，不适合直接宣称 STM32 长期运行就绪。
- 最大证据间隔仅限制闲置时间，既不限制整个证据链年龄，也不证明连续周期统计独立。
- 未实现 T05 调度、T06 C 核心或 T07 推送；未来仅可按文档接收状态、身份、原因和时间约束，不能绕过原始判定直接注入弱信号。

协调者下一动作：阅读 STATE-MACHINE 第 2–5、7 节及本报告，独立运行本轮 unittest、`research/state_machine/run.py --verify`；按任务书由协调者运行 `tools/project_status.py --record-state-machine-tests`，记录 T04-review、验收和主 STATUS。当前本角色没有执行此主收据命令，也没有把 T04 自行标为 done。提交后停止，不继续下一任务。
