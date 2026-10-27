# 输入与来源登记

核查日期：2026-10-23。这里只列本轮实际使用的内容；链接存在不等于阅读全文。未采用二手摘要作为参数证据。

| ID | 来源与版本 | 本轮已读内容 | 用途与限制 |
|---|---|---|---|
| B1 | [冻结快照 v1](../../docs/team/baseline-v1/snapshot.json)，2026-10-23 | 10 个文件的 SHA-256 全部核对；模型代码、README 第 2–5 节、情景参数、架构采样头与验证章节 | 数学起点与假设值，不是标定；实际指纹随本轮 manifest 保存 |
| S1 | [Sensirion SHT4x datasheet V7.3](https://sensirion.com/media/documents/33FD6951/6A7C10A0/HT_DS_Datasheet_SHT4x_V7.3.pdf)，2026-06 | 第 4 页 Table 1 与脚注 9、第 6 页温度响应脚注、第 7 页适用条件 | 湿度阶跃达到 63% 的规格时间为 4 s，条件为 25 °C、1 m/s 气流；装配后响应须另查。不能由此得到本采样头的 τ，也不能把典型精度直接当标准不确定度 |
| S2 | [Sensirion Design Guide for Humidity and Temperature Sensors V2](https://sensirion.com/en/media/documents/FC5BED84/662B494D/Sensirion_Humidity_Temperature_Design_Guide.pdf)，2024-03 | 第 1 页设计摘要、第 14–15 页 §5–5.1 | 死体积、开口、气流路径与安装热质量会影响响应；用于识别缺项，不提供本结构的漏风或壁源数值 |
| S3 | [Nuutinen 等，2003，A closed unventilated chamber for the measurement of transepidermal water loss](https://pubmed.ncbi.nlm.nih.gov/12709124/)，Skin Research and Technology 9(2):85–89；DOI 10.1034/j.1600-0846.2003.00025.x | PubMed 原始论文作者摘要，经检索返回正文核读；直接打开页面未返回摘要。未读付费全文 | 摘要记载小腔体湿度上升、称重校准和覆盖影响考察。只支持“测量结构自身需要验证”的方法；不移植人体性能、时间窗或尺寸到衣物 |
| S4 | [NIST TN 1297 Appendix A](https://www.nist.gov/pml/nist-technical-note-1297/nist-tn-1297-appendix-law-propagation-uncertainty)，1994 版方法，网页更新 2025-09-11 | A.1–A.5，含一阶传播式与协方差项 | 误差预算框架；输入是标准不确定度。未提供本探头的任何测量误差 |
| S5 | [Alduchov 与 Eskridge，1996](https://journals.ametsoc.org/view/journals/apme/35/4/1520-0450_1996_035_0601_imfaos_2_0_co_2.xml)，基线引用 | 本轮出版社页面返回 403，未核读原文 | 本轮只沿用 B1 的 Magnus 近似和程序域 0–50 °C；不声称重新验证系数误差或原文适用范围 |

未找到或未建立的参数证据：织物与夹具的流阻、有效交换量与风速的映射、覆盖时 K 的变化、壁面材料吸放湿率及热容、双面覆盖后的干燥时间、装壳传感器响应、整件衣物含水量映射。所有相应数值均显式标为假设或未知。
