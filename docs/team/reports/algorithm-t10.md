# 算法 T10 M1 交付与缺口

首轮记录保留为历史，其中防水相关要求及有限耗尽验收提议已由下方最终修订替代。2026-10-28；本角色只写 `research/garment_prediction/**`、`docs/GARMENT-PREDICTION.md` 和本报告。已读 AGENTS、PROJECT、STATUS、TEAM、任务书及冻结输入，snapshot 中四个副本 SHA-256 均核对一致。全局状态及既有模型、收据未修改。零采购、无部署；日照只输入衣物热量。所有例子为 `SYNTHETIC_UNCALIBRATED`，授权恒 false。

**M1 未完成**：有限时间耗尽的事件定位、事件收支和独立方法对照未实现。当前模型仅有平滑排液，负库存会立即拒绝；零库存和恢复吸湿有测试，但不能替代任务书所需事件检验。需协调者审查并明确后续安排；不自行开始 M2 或声称 T10 完成。

已实现两区域独立开放织物、变质量热容/参考焓、自然交换、蒸发/吸湿、太阳/对流/长波、输入雨水和与库存成正比的排液；质量及能量账采用端点独立数值求积。详细推导、研究域、合成参数与现行观测边界见 `docs/GARMENT-PREDICTION.md`。T09 恒定潜热式改成参考焓形式，等效温度方程的汽化焓随温度变化；有符号吸湿忽略独立吸附焓。非零 R 只表示人为指定截获量，不可从二值湿润信号推导。

检查（在项目根目录）：

- `python -X utf8 tools/project_status.py`：退出 0，只读历史指纹检查，不是新模型测试。
- `python -B -X utf8 research/garment_prediction/test_model.py`：7 项通过。检查平衡、无日照无强迫风蒸发及冷却、吸湿/零库存、雨与排液、非法/缺失/超域、两区、步长及驱动突变。非法大步长通过拒绝而非隐藏负水或超域。
- `python -B -X utf8 research/garment_prediction/replay.py`：生成示例及 SHA-256 清单；配置和实际命令记录在清单，确定性 seed=null。
- 1800 s 普通区例子，步长 20/10/2/1 s 的独立梯形能量账残差约 65.29/16.24/0.649/0.162 J，质量账约 1.91e-5/4.75e-6/1.90e-7/4.75e-8 kg，呈二阶缩小。10 s 容差 20 J 相对约 10.1 kJ 净能量变化约 0.2%；雨/排液 1 s 的 3e-8 kg 门限相对初始 0.18 kg 约 1.7e-7，0.1 J 相对约 6.56 kJ 净能量变化约 1.5e-5。账残差是通量积分截断误差，不能作为物理参数准确性。
- RK4 与 0.25 s midpoint 对照同一 RHS，水质量允许差 1e-7 kg、温度 1e-4°C；这是另一积分算法，不是独立实验/另一物理闭合。突变按精确时点分段对照；未覆盖有限时间耗尽事件。

文件：`research/garment_prediction/model.py`、`test_model.py`、`config.json`、`replay.py`、`results/example.json`、`results/manifest.json`、`docs/GARMENT-PREDICTION.md`、本报告。结果与源码指纹见 manifest。示例是计算输出，不是现实实验或真实准确率。

首轮曾建议补有限事件并扩展防水观测；这些建议已由本报告最终修订取代。慢区尚无可辨识观测，真实参数及独立标定均缺失。

## 协调审查后的 M1 修订（2026-10-28）

首轮的有限耗尽缺口保留：模型没有此类事件求解器。本次证明在限定有效域内选定闭合的连续正库存不可能有限时间耗尽；在零库存流量非负，详见模型说明。不引入无来源的恒定抽排。建议将 M1 对此闭合的验收收窄为非负不变性、初始零库存和数值越界拒绝；是否接受此范围由协调者决定。以后若改变出流闭合产生真正耗尽，仍须事件定位与收支验证。`IntegrationStepError` 区分数值阶段越界与非法初始输入，错误保留原因；并非真实事件。

补充解析退化条件：所有蒸发、对流、长波、太阳面积为零且无雨，线性排液时 `w=w0 exp(-kt)`、`U=(m_d c_d+c_w w)T0`、`T=T0`。这不是实际衣物情景；示例 1800 s 末水约 0.07318253875 kg、温度约 25°C；闭式对照精度分别为 1e-12 kg 和 1e-7 J，梯形账残差约 2.23e-9 kg、0.000233 J。两区异驱动 1200 s 总质量账残差 2.53e-8 kg、总能量账残差 0.0895 J，门限分别 1e-7 kg、0.4 J，均是独立端点数值积分误差；此检查并不证明物理闭合真实。两区总账为加总，不引入跨区流。

新增 `research/garment_prediction/parameter_registry.json`，逐字段登记配置、状态、驱动及代码物性和常量单位、软件边界与合成/近似来源；软件上限不是实物有效域。公式中的风速隐含以 1 m/s 归一化，系数均为合成；近似饱和压和物性不能等同现场标定。吸附热与蒸汽入流温度近似仍未解决。

实际复跑 `python -B -X utf8 research/garment_prediction/test_model.py`：10 项通过，新增解析排液、两区总账和零/微量库存检查。`python -B -X utf8 research/garment_prediction/replay.py` 重生成示例及 manifest，记录修订源码、参数登记、冻结输入与产物指纹。修改/新增文件：`model.py`、`test_model.py`、`replay.py`、`parameter_registry.json`、`results/example.json`、`results/manifest.json`、`docs/GARMENT-PREDICTION.md`、本报告；配置未改。先前 7 项及首轮缺口作为历史保留，不能当成本版验收。尚未有可用传感器健康证据、材料标定或真实准确率，M2 未开始。

## M1 最终修订（2026-10-28，待协调者总验收）

协调者已认可当前平滑水活度与线性排液闭合在有效域内的非负不变性范围，无需为此构造常量抽排；M1 是否整体验收以协调者收据为准。后续若引入确实可能有限耗尽的不同出流闭合，仍需另行处理事件及其守恒账。

`run` 在开始积分前校验方法，即使 duration=0 也拒绝未知方法；`advance` 在调用积分器前校验正的有限 dt、方法及初始状态，非法输入为普通 `ValueError`；仅已验证输入下的积分中间/末状态离开域报告 `IntegrationStepError` 并保留原因。正常情景物理方程、参数及配置未改变。

manifest 的输入和产物路径使用 POSIX 相对路径，`example.json` 与 `manifest.json` 用 UTF-8 和单一 LF 字节写入。实际执行 `python -B -X utf8 research/garment_prediction/test_model.py`：11 项通过；新增测试覆盖零时长无效方法、负/零/NaN/无穷步长、无效方法及真实阶段越界区分。实际执行 `python -B -X utf8 research/garment_prediction/replay.py` 两次，更新清单和示例。重放前后已解析的示例配置与全部数值完全相同；两次重放字节一致，10 个输入及 1 个产物哈希匹配，路径均无反斜杠，两个 JSON 均为 LF。以上为当前 Windows 环境结果；尚未在 Linux 实跑，浮点数学库差异仍可能改变末位，无法保证跨平台数值逐字节相同。

饱和水汽压系数来源：Alduchov & Eskridge 1996 原论文出版商页面直接访问返回 403；[Ferreira 2024 附录 D](https://rmets.onlinelibrary.wiley.com/doi/10.1002/qj.4609) 明确归属并列出系数，本轮按该开放论文间接核对；未声称完整阅读原论文。[NIST 2022 CODATA](https://physics.nist.gov/cgi-bin/cuu/Value?sigma) 搜索摘要支持代码中的截位 Stefan 常数；直接访问受限。来源和访问范围已加入参数登记。各织物、传质与热参数仍为近似或合成，吸附热及传入水汽温度近似仍是限制。

已撤回此前湿膜、冷凝及受水恢复检测器要求。防水仅为充电口，结构归 T12；目标测量位置是衣架底部横杆中央，未接触时不开展衣物干燥记录。两区域是计算结构，不等同两只物理探头，不假定中央一定是慢区；接触确认及局部对整衣代表性待后续研究，不在本轮实现检测或新增传感器。保留误差、延迟、缺失及已知充电热干扰的观测质量边界；二值雨头不提供已知衣物吸水质量。

本次修改：`research/garment_prediction/model.py`、`test_model.py`、`replay.py`、`parameter_registry.json`、`results/example.json`、`results/manifest.json`、`docs/GARMENT-PREDICTION.md` 和本报告；冻结输入与全局文件未改。M2、真实衣物标定与真实收衣授权均未完成；交付后停止写入，等待协调者审查。
