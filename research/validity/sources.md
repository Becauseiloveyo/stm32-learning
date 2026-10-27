# T03 一手来源与已读范围

核查：2026-10-24。资料数值不自动成为装壳探头参数。外部资料仅约束定义和方法，本轮所有新增情景幅度与时间常数仍未标定。

| ID | 原始来源与版本 | 本轮核读范围 | 使用边界 |
|---|---|---|---|
| V1 | [Sensirion SHT4x datasheet V7.3，2026-06](https://sensirion.com/media/documents/33FD6951/6A7C10A0/HT_DS_Datasheet_SHT4x_V7.3.pdf) | 第 4 页 Table 1 与脚注 4／9；第 6 页 Table 2 脚注 12；第 7 页 §2.3 | 湿度 τ63 规格 4 s 有 25 °C、1 m/s 条件；安装影响另查。重复性定义有 3σ 约定，不能据此把典型 ± 精度等同标准差。本研究不从规格换算整机不确定度 |
| V2 | [Sensirion Design Guide V2，2024-03](https://sensirion.com/en/media/documents/FC5BED84/662B494D/Sensirion_Humidity_Temperature_Design_Guide.pdf) | 第 1 页摘要、§5–5.1（第 14–15 页） | 安装气路、死体积和热质量影响响应；没有给本夹具流量、壁源、热参数的值 |
| V3 | [NIST TN 1297 Appendix A，1994 版方法](https://www.nist.gov/pml/nist-technical-note-1297/nist-tn-1297-appendix-law-propagation-uncertainty) | A.1–A.5，尤其式 A-3；网页注明更新 2025-09-11 | 一阶传播包含标准不确定度和协方差；没有提供本装置输入分布、相关性或总误差 |
| L1 | 已验收的 [T01 契约](../../docs/MEASUREMENT-CONTRACT.md)、theory 三个 Python 源和 [T02 比较](../../docs/PROBE-CONCEPTS.md) | 协议、观测与判定实现；结构比较 §7–8、T02 清单 | 本轮运行前后核对哈希；它们是计算基线，不是物理标定 |
| L2 | [旧误差草案](../../docs/MODEL-VALIDITY-DRAFT.md)、[首轮来源](../probe/sources.md) | 全文数学假设与待验证项 | 本轮重新计算新增结果，不把旧通过数当本轮检查 |

可获得性：V1、V2 的官方 PDF 和 V3 官方页面本轮均可读取；仅核读上表部分，不声称阅读全文。旧草案的 Magnus 原始论文网页曾返回 403；本轮没有重新取得全文，因此公式只声明沿用已验收代码（0–50 °C 是程序域），不声明已量化近似式误差。旧人体蒸发论文不用于任何织物参数或性能推断，本轮不复述其数值。

推导与来源分界：有限壁储量衰减、双一阶响应差、偏置传播、质量守恒和协方差算例是本项目明确假设下的计算，不是上述厂家／NIST 对衣架结构给出的实验结果。未知的织物传质、材料放湿、装壳动态、风速映射与热量闭合参数仍标记未知。
