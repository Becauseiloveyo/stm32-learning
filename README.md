# 衣架式干燥监测项目

暂定名称：**基于 STM32 的低功耗衣物干燥监测与远程提醒系统**。

面向家庭阳台的 STM32 课程设计与个人作品：研究干燥状态、条件剩余时间与受水风险，支持离家手机提醒。2026-10-28 已按用户要求取消太阳能，供电候选为可充电电池与人工 USB 充电。当前仅用电脑做理论与软件，暂不采购。

下一步准备见 [开发计划书](docs/NEXT-PHASE-PLAN.md)：先实现完整衣物模型，再完善无光伏的衣架结构、电池预算和通知联动。衣物受日照仍是干燥输入；历史光伏计算保留追溯，不作为当前产品要求。

## 从这里恢复工作

1. 读取 [AGENTS.md](AGENTS.md) 的执行规则。
2. 读取 [项目约束](docs/PROJECT.md) 和 [当前进度](docs/STATUS.json)。
3. 执行 `python -X utf8 tools/project_status.py`，确认文件和已有验证记录是否仍匹配。
4. 在 [开发步骤](docs/ROADMAP.md) 中，只展开 `next_task` 指定的任务和它列出的输入文件。
5. 按任务验收条件工作，结束时更新进度；决策有变化则追加 [决策记录](docs/DECISIONS.md)。

上述文件用于跨对话恢复，不要求先通读聊天历史。状态记录与实际文件不符时，先说明并修正记录，不能直接延续旧结论。

已启用用户授权的分工对话协作。被分派的对话先读 [团队规则](docs/TEAM.md) 和自己的任务书，按文件所有权工作，不抢占主 `next_task`。协调对话负责维护全局进度和验收。

## 文件各自负责什么

| 文件 | 职责 |
|---|---|
| [PROJECT.md](docs/PROJECT.md) | 用户要求、当前约束、证据边界 |
| [详细功能说明](docs/FEATURES.md) | 使用过程、各项产品功能、已有软件证据与待实现边界 |
| [防水与测量兼容设计](docs/WATERPROOFING-DESIGN.md) | 分区、冷凝、透气/响应、雨后恢复及未闭环问题矩阵 |
| [下一阶段开发计划书](docs/NEXT-PHASE-PLAN.md) | 无太阳能范围、T10 工作包/验收、结构与电池预算、Terra 分工 |
| [预测、雨情与供电研究](docs/PREDICTION-RAIN-POWER-DESIGN.md) | T09 热湿模型、面料、受水和通信；光伏预算属历史内容 |
| [原衣架结构研究](docs/HANGER-INTEGRATION-PLAN.md) | 旧面板方案追溯；通风、取放和测点约束迁移到新 T12 |
| [ROADMAP.md](docs/ROADMAP.md) | 任务依赖、输入、输出、验收与停止条件 |
| [STATUS.json](docs/STATUS.json) | 唯一的任务进度、当前任务、下一步和未解决事项 |
| [DECISIONS.md](docs/DECISIONS.md) | 为什么做某个决定、依据和重新评估条件 |
| [theory/README.md](theory/README.md) | 已有模型的推导、参数解释和仿真结果 |
| [模型适用性](docs/MODEL-VALIDITY.md) | 动态失配、误差预算、有限壁源及尚缺实测证据的边界 |
| [多周期状态机](docs/STATE-MACHINE.md) | 批次、时间、配置、重复事件与异常恢复的可执行契约 |
| [能量与测量调度](docs/ENERGY-SCHEDULING.md) | 逐阶段耗电、任务准入、失败与恢复、固定／按需对照 |
| [纯 C 核心接口](docs/CORE-INTERFACE.md) | 主机构建、C/Python 对照、单位、内存与设备层边界 |
| [远程提醒契约](docs/NOTIFICATION-CONTRACT.md) | 离线消息身份、最新状态、持久确认与研究提醒边界 |
| [总体架构](hanger-drying-architecture.md) | 系统与测量方法的设计背景 |
| [原供电与通知研究](power-notification-design.md) | 保留字节指纹的历史依据；光伏方案已退出当前范围 |
| [硬件实验清单](first-validation-plan.md) | 预算允许后使用的实验计划 |

可执行参数以代码及运行生成的参数清单为准；说明文档中的示例数值不是第二套配置。任务进度只在 STATUS.json 中维护，其他文件引用它，避免多个进度表互相矛盾。

## 验证命令

```powershell
# 只读检查当前进度、仿真来源和测试记录
python -X utf8 tools/project_status.py --check

# 运行现有模型检查，并保存与当前源码对应的测试记录
python -X utf8 tools/project_status.py --record-tests --check

# 协调者验收 T02 时，单独运行气路研究及协议接入检查
python -X utf8 tools/project_status.py --record-probe-tests --check

# 协调者验收 T03 时，单独运行模型失配、守恒与误差检查
python -X utf8 tools/project_status.py --record-validity-tests --check

# 协调者验收 T04 时，单独运行多周期状态机和重放检查
python -X utf8 tools/project_status.py --record-state-machine-tests --check

# T04 独立边界检查，以及保存事件的只读重放／复算
python -X utf8 tools/verify_state_machine_boundaries.py
python -X utf8 research/state_machine/run.py --verify

# 协调者验收 T05 时，单独运行能量调度与状态机接入检查
python -X utf8 tools/project_status.py --record-energy-tests --check

# T05 独立能量边界检查与保存输入的只读完整复算
python -X utf8 tools/verify_energy_boundaries.py
python -X utf8 research/energy_t05/run.py --verify

# 协调者验收 T06 时，实际编译 C 核心并运行主机对照
python -B -X utf8 tools/project_status.py --record-core-tests --check

# 协调者验收 T07 时，运行离线通知与独立边界检查
python -B -X utf8 tools/project_status.py --record-notification-tests --check

# 修改模型后，按需重新生成仿真结果；画图额外需要 Matplotlib
python -X utf8 theory/run_simulation.py
python -X utf8 theory/run_simulation.py --plot
```

`--check` 不会替代模型测试或硬件验证。仿真来源发生变化时会提示结果过期；应在对应任务中重新生成和解释结果，不能只刷新日期。

模型、气路、误差、状态机和能量调度检查分别保存在 `docs/verification/latest-tests.json`、`probe-tests.json`、`validity-tests.json`、`state-machine-tests.json` 和 `energy-tests.json`，保留各自运行时间及输入指纹。各套收据在对应任务验收时生成，覆盖自己的代码和相关输入文档；恢复时同时核对 T02–T05 清单声明的输入、源码和产物哈希，不把已有通过记录冒充本轮重新运行的结果。

T06 另在 `docs/verification/core-tests.json` 保存实际主机编译与 C/Python 对照记录，包含编译器、运行命令和来源指纹；恢复检查不会重新编译，也不代表 STM32 实机通过。

T07 的协调收据为 `docs/verification/notification-tests.json`，核对通知实现、独立检查和保存场景的指纹；`tools/notification_acceptance.py` 提供单独入口。离线模拟的保存、推送接受、接收和已读是不同阶段，不是实际手机送达证据。正式完成状态仍以 STATUS 为准。

## 新对话可以直接使用的提示

> 继续此目录中的衣架项目。先读取 AGENTS.md、docs/PROJECT.md、docs/STATUS.json，运行 python -X utf8 tools/project_status.py，再读取 docs/ROADMAP.md 中 next_task 指定的任务及其输入。不要凭聊天记忆补齐事实。按验收条件执行一个任务，结束时更新状态、决策和验证记录。当前只做零采购的理论与软件开发。

项目文件能降低上下文丢失的影响，但它们也会过期；依靠明确来源和可重跑检查恢复状态。GitHub 仓库为 [Becauseiloveyo/stm32-learning](https://github.com/Becauseiloveyo/stm32-learning)，同步状态以 Git 和 STATUS 核对；检查工具不会自动提交、推送或回滚。
