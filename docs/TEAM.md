# 第一轮团队协作规则

日期：2026-10-23。用户已明确要求在“微开发”项目中新建分工对话，分派开发工作，以减少过长上下文的影响。本文件定义职责；实际对话 ID 和协调进度见 `STATUS.json` 的 `team_work`。

## 工作方式

### 2026-10-28 无太阳能版本规划分工

用户明确授权将简单繁琐的工作分派给最新可用的 GPT-6.1 Terra。本轮使用 `gpt-6.1-terra` 子代理：资料依赖盘点采用 medium，T10 验收清单整理采用 high；协调者负责架构、全局文档、最终验收和 GitHub 同步。模型名称按本会话可用工具选取，不推断其他未知版本。

- 太阳能依赖审查：任务书 `assignments/no-solar-audit.md`，唯一写入 `reports/no-solar-audit.md`。
- T10 准备审查：任务书 `assignments/t10-readiness.md`，唯一写入 `reports/t10-readiness.md`。
- 冻结输入：`baseline-no-solar-20261028/snapshot.json` 及其副本。这些记录的是调整前历史；用户最新指令移除光伏供电优先。原模型源码本轮不变，可只读核对。
- 两角色不得修改主线、执行模型实现、提交、推送、创建更多代理或向其他对话派发工作。报告交付后由协调者整合；不创建新的用户侧对话。
- 后续机械性清点/文档核对优先 medium；涉及数值边界与测试设计采用 high。每次只给当前任务最小输入、明确写入路径及验收条件，不自动启动后续任务。

### T07 接入补充

T06 验收后，远程提醒角色按 `assignments/notification-t07.md` 实现正式状态映射和离线持久通知链路。唯一写入范围为 `research/notification_t07/**`、`docs/NOTIFICATION-CONTRACT.md`、`reports/notification-t07.md`。旧通知原型及草案保持只读；已验收 Python/C 源码和结果也保持只读。协调者负责独立验收、工具、全局状态与主收据。T07 不部署服务，不接真实手机，也不执行 T08。

2026-10-27 接管记录：已查询该角色对话，其执行因额度限制终止，未在运行。以上 T07 新范围由协调者接手完成文档、证据生成和整合修复；保留原代码及报告中的交付来源，不将中断误记为验收完成。角色再次恢复前须重新分配所有权。

### T06 接入补充

T05 验收后，测量角色按 `assignments/measurement-t06.md` 实现 `hanger_measurement`／`hanger_state` C 核心及本角色主机桥接验证；供电角色按 `assignments/energy-t06.md` 实现 `hanger_energy` C 原语及独立桥接验证。唯一写入文件以前述任务书为准，两角色不互改、不依赖对方未验收接口。生成物分别在 `firmware/host/build/measurement/` 与 `energy/`。已验收 Python 与产物全部只读。协调者负责主验证入口、独立检查、`CORE-INTERFACE.md`、工具和全局收据，原角色结束后再整合。T06 不包含设备操作或 T07 实施。

### T05 接入补充

T04 验收后，供电角色按 `assignments/energy-t05.md` 完成状态机与能量调度接入。唯一写入范围为 `research/energy_t05/**`、`docs/ENERGY-SCHEDULING.md`、`reports/energy-t05.md`。旧 `research/energy`、草案和旧报告保留作只读历史；当前 theory、research/state_machine、测量契约及其已验收结果同样只读。新层调用当前真实状态机，不以能源预算完成代替有效测量。协调者负责工具、全局状态与正式验收收据。此补充替代该角色第一轮所有权，不提前执行 T06／T07。

### T04 接入补充

T03 验收后，测量角色按 `assignments/measurement-t04.md` 实现正式多周期状态机。本轮唯一写入范围为 `research/state_machine/**`、`docs/STATE-MACHINE.md`、`reports/measurement-t04.md`。当前 theory、测量契约、适用性文档及既有研究结果均保持只读，新层调用已验收的单周期判定器。协调者维护工具、全局状态和独立验收收据。此补充覆盖该角色第一轮写入范围，不授权提前执行后续任务。

### T03 接入补充

T02 验收后，气路角色按 `assignments/probe-t03.md` 完成正式误差与适用性分析。本轮唯一写入范围调整为 `research/validity/**`、`docs/MODEL-VALIDITY.md`、`reports/probe-t03.md`。当前 theory、research/probe、旧草案和旧报告是已验收的只读输入，协调者也保持这些输入不变；完整热／双腔模型缺少闭合依据时可明确暂缓。协调者维护恢复工具、新测试收据和验收记录。此补充优先于下面第一轮历史范围。

### T02 接入补充

用户继续启动下一步后，气路角色按 `assignments/probe-t02.md` 接入已验收的 T01；此前“只做预研／不导入当前 theory”的限制仅对第一轮历史分析有效。此轮可只读导入稳定的 `theory` 协议和模型，其他角色不改这些输入。气路角色新增报告 `reports/probe-t02.md`，其余写入范围不变；不改 T03 误差草案或其他角色文件。协调者维护主状态、检查工具和验收记录。最新执行阶段仍只读 STATUS。

所有分工对话运行在同一个本地项目目录。没有创建独立 worktree，因此必须按文件划分修改权限。各对话从 PROJECT、自己的任务书和指定输入恢复信息，不继承整段聊天历史。

第一轮仅由测量角色实现 T01，其他角色交付独立预研／原型。后续接入按上方补充任务书和 STATUS 执行，不提前完成尚未满足依赖的任务；协调对话负责验收。

## 分工与可修改范围

| 角色 | 本轮交付 | 唯一可修改范围 |
|---|---|---|
| 协调与验收（原对话） | 分派任务、解决接口冲突、维护主进度、验收 T01 | 项目控制文档、工具、主测试记录；其他领域文件仅在原负责对话结束后整合 |
| 测量协议 | 正式完成 T01 的实现与验证 | `theory/**`、`docs/MEASUREMENT-CONTRACT.md`、`docs/team/reports/measurement.md` |
| 气路与误差 | 三种结构的理论比较、反例与误差预研 | `research/probe/**`、`docs/PROBE-CONCEPTS.md`、`docs/MODEL-VALIDITY-DRAFT.md`、`docs/team/reports/probe.md` |
| 供电调度 | 独立的储能／负载时间序列原型 | `research/energy/**`、`docs/ENERGY-SCHEDULING-DRAFT.md`、`docs/team/reports/energy.md` |
| 远程提醒 | 离线事件协议与失败情景原型 | `research/notification/**`、`docs/NOTIFICATION-CONTRACT-DRAFT.md`、`docs/team/reports/notification.md` |

未列入的文件由协调对话管理。任务书位于 `docs/team/assignments/`，各分工对话只读。对话 ID 不写入任务书正文，统一由 STATUS 登记，避免多个名单互相矛盾。

## 冻结输入

本轮预研基线位于 `docs/team/baseline-v1/`，来源与 SHA-256 见该目录 `snapshot.json`。分工对话不得修改基线。它保存本轮派发前的模型和主要设计文本；路径变化后其中的相对链接仅作历史引用，图表数值优先读已复制的 CSV／manifest。

协议对话可以修改当前 `theory/`。其他对话需要模型背景时读取冻结副本，不导入正在变化的主模型；需要自己的脚本时放在本角色的研究目录。基线参数的来源依旧是“假设／待验证”，冻结不等于标定。

## 每个对话开始时

1. 阅读 `AGENTS.md`、`docs/PROJECT.md`、本文件和自己的任务书。
2. 可运行只读 `python -X utf8 tools/project_status.py`；团队工作期间提示主测试过期可能是协议对话正在修改，记录即可，不抢改别人的文件。
3. 在自己的报告中写明已读基线、工作范围和限制，然后开展任务。不要把本角色任务扩成整个项目。

## 每个对话结束时

在自己的报告中记录：

- 角色与本轮任务范围，交付是否达到本角色验收条件；
- 输入来源及版本，哪些结论依赖假设；
- 新建／修改文件清单；
- 实际运行的命令、结果与未验证项；
- 需要协调对话确认的接口变化、主线依赖及建议下一动作。

在对话最终回复中给出报告路径和简短结果。不要求向协调对话或其他对话主动发送消息；协调者通过文件及应用的对话读取／等待工具获取结果。报告提交不等于主线任务验收通过。

## 协调与整合

协调对话先确认各角色完成了哪些范围，再审查证据。T01 验收后统一更新主测试记录与 STATUS；预研成果在对应前置任务完成后逐项接入。若需要扩大某角色的写入范围，先明确移交所有权，避免两个对话同时修改同一文件。

新对话可以降低上下文负担，但不能保证没有推断错误。每个成果仍需来源、反例和检查；不通过“多个人都同意”代替技术证据。
