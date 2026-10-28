# 无太阳能依赖盘点

范围：依据冻结快照和稳定源码只读审查；仅交付本报告。新指令移除产品光伏供电，衣物日照仍是干燥物理输入。冻结快照是历史而非现行需求；参数、场景均为假设，非实测或实物验证。

| 位置与符号 | 建议处理及风险 |
|---|---|
| 冻结 PROJECT R05/R12/R14、ROADMAP T10，PREDICTION-RAIN-POWER-DESIGN 第10节及 HANGER-INTEGRATION-PLAN | 当前项目入口应由协调者更新：取消光伏布置、面积优化及其验收，改电池和人工充电规划；保留冻结文本和历史来源并标明已被新指令替代。不要把历史推荐结构当现行方案。 |
| `analysis/energy_budget.py`：`panel_watts`、`sun_hours`、`harvest_efficiency`、`solar_wh`、`recovery_surplus`；`analysis/energy-budget.csv` | 现为光伏日均预算及日照恢复；未来须改为电池能量、人工充电时序及缺充情景，旧 CSV 的恢复时间、净能量不可用作新版结论。 |
| `research/energy/model.py`：`Solar`、`Scenario.solar`、`solar_battery_wh`；`research/energy/scenarios.py` 和测试 | 电池侧输入叠加负载与溢出并核算余额，不能把太阳能字段改名就当作人工充电的完整证据。未来定义充电起止、功率、效率、容量限制及停机/恢复行为，重新生成场景及检查。 |
| `research/energy_t05/contracts.py`、`model.py`、`scenarios.py`：`Solar`、`account`、`solar_wh` | 调度受供电时序影响，充电须显式建模；无未来能源借支、瞬时负载不能借入、停机恢复和状态机测量有效性仍需验证。旧合成停止/恢复、等待及可用性结果依赖光伏输入，不能通过零参数或重命名沿用。 |
| `firmware/core/hanger_energy.h`、`.c`：`solar_battery_wh`、`he_account`；`firmware/host/energy_validation.py` | 接口和桥接验证有光伏语义；未来修改须同步契约、Python/C 对照与误差、冲量、溢出、停机重启测试，重新生成证据。本轮不改实现。 |
| `research/design_extension/analyze.py`：`solar_Wh`、`required_area_cm2`；`assumptions.json`、PVGIS 数据及历史输出 | 面板面积和光伏发电量保留为历史研究，不作为当前供电依据；历史气象辐照数据可保留作环境背景，需区分水平/板面与衣物平面。 |
| 冻结预测设计 `G_g`、风速、表面温度、遮挡，theory 热误差说明 | 衣物所受日照、辐射热和蒸发冷却继续保留；不将面板充电电流当衣物日照，缺失日照不填零。T10 原板遮挡部分应另行调整，热模型情景仍需独立验证。 |

未来证据应记录充电输入电池侧或充电器侧、单位 Wh/W、效率、时序、负载、初始容量和种子，区分合成与实测。重跑受影响模型、测试和清单后才可引用新结果；既有收据仅证明旧源码与旧场景，不能证明新方案续航或可靠性。不涉及采购、硬件选型和实际部署。

检查：读取 AGENTS、TEAM、任务书、冻结 snapshot；使用 `rg -n -i 'solar|photovoltaic|harvest|\bpv\b|太阳能|光伏|日照' research theory firmware analysis docs/team/baseline-no-solar-20261028`（输出因生成数据巨大而截断），再以 `rg -l` 限定源码/JSON 和排除结果/构建目录，补充定向 `rg -n`。PowerShell 花括号列表尝试出现解析错误，随后用显式文件路径成功。Python hashlib 对 snapshot 四个副本逐一比对 SHA-256，全部 True。未运行模型测试：本次无源码改动；清点非完整逐条审计，生成物和其他文档引用需协调者整合时复核。

新增/修改：仅 `docs/team/reports/no-solar-audit.md`。本角色盘点完成；不代表主线验收。建议协调者更新现行需求与计划、标注历史，再在获授权的后续实现任务中更新模型及重建证据。

## 2026-10-28 最终一致性复核

按追加授权只读 README、PROJECT、FEATURES、ROADMAP、NEXT-PHASE-PLAN、STATUS。现行范围一致取消光伏，保留衣物日照；STATUS `next_task` 为 T10，ROADMAP 依赖与计划顺序均为 T10→T12→T11→T08。FEATURES 第 8 节、STATUS limitations 与计划均明确人工充电及无光伏端到端验证尚未实现；历史光伏研究和旧收据在现行入口被标为历史。

发现两处建议协调者澄清，均不是现行任务直接要求光伏：

1. `docs/ROADMAP.md` H01 验收句“峰值供电和实际收能”沿用旧术语，可能被误读为未来要验证光伏收能。建议改为实际电池供电及人工充电性能，保留未来硬件验证尚未授权的边界。T05 边界“不把拟定太阳能功率或容量冻结成采购规格”亦属已完成旧任务，可明确历史语境。
2. `docs/STATUS.json` 旧 planning history 条目的 `resume_action` 仍写“执行 T12…收能范围…T12 验收后再进入 T10”，`next_task` 为 T12；它是旧记录而非顶部现行 `next_task`，但建议在该历史条目明确标注已被当前 T10→T12 顺序和无光伏范围取代，避免搜索时误用。不要覆盖历史证据。

检查命令：`rg -n -i '太阳能|光伏|solar|\bpv\b|T10|T11|T12|T08|充电|供电|历史' README.md docs/PROJECT.md docs/FEATURES.md docs/ROADMAP.md docs/NEXT-PHASE-PLAN.md docs/STATUS.json`；另读取 STATUS 旧记录上下文及 ROADMAP H01。只做文档一致性核对，未验证实际实现或运行测试。
