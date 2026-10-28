# T10 M2a 工作报告：中央观测与条件反例

2026-10-29。仅写 `research/garment_prediction/observation_m2/**`、`docs/GARMENT-OBSERVATION.md` 和本报告；M1 已验收文件及全局文件只读。未进入 M2b/ETA、未增加硬件或部署。交付供协调者审查。

已读 AGENTS、PROJECT、STATUS、TEAM、M2a 任务书、M1 验收说明和指定输入。`python -B -X utf8 tools/project_status.py` 返回 0，系只读历史检查；冻结清单 10 项与 M1 收据 reviewed_sha256 的 15 项均匹配。重放合并并核查 21 个不同的已验收输入。T09 §4–6 和 CENTRAL-CONTACT-REVIEW 用于关系与限制；项目资料登记 C01 仅查出版社摘要，C03 查论文方法/讨论，本轮无新增网络访问，不将文献样本参数迁移为本装置数值。

`contract.py` 只接收白名单观测，不输出衣物含水。输入含来源、单位、误差、测量/接收时间；非法值拒绝，缺测、迟延、过期、过大误差、时间不同步和已知/未知充电热干扰均降级。未接触或未知都不允许有效衣物干燥记录；诊断数据不等于有效历史。代码本身不写记录，也不做接触检测。合成门限不代表传感器规格或准确度；本接口 RH 为 0–1，旧测量协议是 %RH，未接旧协议。

## 反例及限制

M1 两区在当前闭合中独立。两例设相同合成参数、驱动和中央初始条件，仅另一计算区初始干基 X 分别 0.3/1.2 kg/kg。假设普通区对应中央仅是此反例条件，不是确定的实际位置。0、300、600 s 的理想中央水量、X 和温度逐点相同；600 s 中央水量约 0.164164 kg，未观测区分别约 0.028245/0.116481 kg，相差约 0.088235 kg。在模型、共享驱动、区域独立和其他信息不揭示隐藏区的条件下，即使理想知晓中央状态也不能唯一确定全衣水量；不能推广为所有传感器或家庭实测结论。

配置中的固定温湿度只是接口人工输入，**不是模型生成的 RH 曲线**，不作为物理反例证据。理想中央状态及另一水量只在 `producer_only` 作验证；`observe` 仅接收白名单包。测试变更隐藏水量、材料标签及未来天气不改变消费者输入和输出；消费者不接收理想水量或 X。示例 `synthetic_external` 接触证据不是已实现的真实检测。

中央覆盖、气路、真实延迟响应、局部位置与两区的对应均未闭合；不将全开放 M1 后处理冒充覆盖腔体，不以线性权重或水量直接生成传感器读数。说明中列出候选气路关系和统一质量/潜热账边界。二值雨头不能量化吸水。`whole_garment_state` 恒为未知，`collection_authorized=false`；无 ETA、概率或真实收衣决策。

## 检查、文件及后续

- `python -B -X utf8 research/garment_prediction/observation_m2/test_contract.py`：9 项通过，覆盖接触、缺失、延迟、误差、时序、热干扰、非法值、隔离及条件反例。
- `python -B -X utf8 research/garment_prediction/observation_m2/replay.py`：验证 21 个合并的已验收输入后生成示例与清单。seed=null，所有场景 `SYNTHETIC_UNCALIBRATED`；命令、来源及指纹保存在 manifest。
- 新增 `contract.py`、`witness.py`、`test_contract.py`、`config.json`、`replay.py`、`results/witness.json`、`results/manifest.json`、`docs/GARMENT-OBSERVATION.md`、本报告。未运行无关旧测试。

需协调者审查接口、来源和反例条件；接触证据的获取、局部气路/覆盖、观测误差及慢区可辨识性尚无真实标定。M2b 须在明确派发后先解决可用证据与未知映射的表达，不能向消费者泄漏理想或隐藏状态。本轮停止写入。

## 协调审查后的时间语义修订

首版样本时间 590 s、证据点时间 595 s 时错误允许记录；首版的 9 项通过不覆盖这一问题，已由本节取代。现要求已确认接触、未接触及已知充电热状态通过外部证据显式声明覆盖起止和签发时间，`0 ≤ start ≤ end ≤ issued ≤ now`，unknown 的来源和时段均为 null。闭区间覆盖所有必要的局部样本时刻用于接触判断；热状态覆盖所有必要环境及局部样本时刻。缺测仍拒绝有效记录。签发可晚于采样，但必须显式声明覆盖当时，不能仅凭晚一点的点状证据或固定等待推断覆盖；区间外不授权。配置将两项证据的合成覆盖声明设为 580–595 s、签发 595 s，样本为 590 s。此为外部人工情景声明，尚无自动检测或真实性保证。

当接触证据不覆盖局部样本时有效接触状态降为 unknown；确实有未接触证据覆盖全部局部样本时返回 not_contact，部分覆盖另保留 not_contact_evidenced 原因。`contact_state_effective` 表示该组样本时刻，不推断现时状态；已知热干扰仍拒绝。签发证据的过期门限保持为额外保守筛选，不替代覆盖检查。

实际运行 `python -B -X utf8 research/garment_prediction/observation_m2/test_contract.py`：14 项通过，新增覆盖样本发生在区间之前或之后、首版 595 s 点证据、局部样本部分覆盖、未来/倒序区间、闭区间边界、明确追溯覆盖、未接触与未知区分。`python -B -X utf8 research/garment_prediction/observation_m2/replay.py` 重生成示例及 manifest，核对 21 个已验收输入。生成侧 M1 数值和理想中央轨迹与修订前完全相同；改变的是观测证据格式及其可验证时间范围。文件更新：`contract.py`、`config.json`、`test_contract.py`、`results/witness.json`、`results/manifest.json`、`docs/GARMENT-OBSERVATION.md` 与本报告。仍无气路映射、真实接触证据或实物标定；未进入 M2b。
