# 气路研究：首轮预研与 T02 接入

全部输出是 **SYNTHETIC_UNCALIBRATED**；不预测含水量、整件干燥时间或准确率。首轮独立分析源自 `docs/team/baseline-v1/`，不导入主模型；2026-10-24 的 T02 新入口按任务书只读使用已验收的 `theory`，原源码和数据保留不变。

- [三种结构、气路图与候选取舍](../../docs/PROBE-CONCEPTS.md)
- [误差与适用性草案](../../docs/MODEL-VALIDITY-DRAFT.md)
- [原始来源与阅读范围](sources.md)
- [交付报告](../../docs/team/reports/probe.md)
- [T02 交付报告](../../docs/team/reports/probe-t02.md)

## 当前 T02 入口

```powershell
python -B -X utf8 research/probe/t02_replay.py
python -B -X utf8 -m unittest discover -s research/probe -p test_t02_replay.py -v
python -B -X utf8 research/probe/t02_replay.py --verify
```

只写 `results-t02/`；从 `theory.run_simulation.verification_protocols()` 获取默认和 3 s 长周期变体，不另设可编辑默认时序。14 项角色检查覆盖跨模型解析、协议变化、反例、域拒绝、合法观测隔离和新鲜度；不修改主测试收据。

| 新产物 | 内容 |
|---|---|
| `results-t02/manifest.json` | 30 个只读输入、2 个新源码和 8 个产物指纹；协议、物理参数、门限、单位、种子与限制 |
| `case-summary.csv` | 两协议×8 情景＝16 个周期，主模型无噪声观测与独立解析比较 |
| `envelope.csv` | 两协议×135 参数点；每点物理参数与指纹，不分配结构名次 |
| `domain-summary.csv` | 2 个刻意越过无凝露域的情景，不剪裁、不判定残段 |
| `observations.jsonl` | 16 个周期的合法契约观测，数值／布尔原类型；不含物理参数或真值字段 |
| `research-traces.csv` | 1568 点研究真值、解析观测、主模型对照，与判定输入分离 |
| `pair-comparison.json` | 两协议中壁源匹配与非唯一参数对的重合误差 |
| `protocol-mapping.json` | 阶段边界、实际首尾窗口采样时刻与协议指纹 |
| `noise-reference.json` | 单独保存的主模型默认含噪声名义情景与种子，不作无噪声精确对照 |

除第一行外，上表新产物都位于 `results-t02/`。`--verify` 校验输入和输出后，重放已存观测，并在内存重算全部新产物逐字节比较，不写文件。默认计时沿用 T01；改变时序或源码须重生成 T02 结果，不能沿用旧哈希。NOT_APPLICABLE 是研究输出域状态，不是主判定器新增产品状态。

## 首轮复现（历史分析）

从项目根目录运行，无第三方依赖：

```powershell
python -X utf8 research/probe/analyze.py
python -X utf8 -m unittest discover -s research/probe -p test_analysis.py -v
```

`analyze.py` 先核对冻结基线 10 个文件的 SHA-256，再写本目录 `results/`。固定 1 s 采样和 30／20／90 s 时序只用于本次基线分析。没有随机数，seed=null。修改脚本或检查代码后需重新生成输出，再运行检查；manifest 会检测源与 CSV 指纹变化。

## 首轮输出含义

| 文件 | 内容 |
|---|---|
| `results/manifest.json` | 完整参数、协议、来源／输出指纹、未知与越界标记 |
| `results/case-summary.csv` | 8 个完整周期的首尾差、尾值、气体时间常数 |
| `results/case-traces.csv` | 8×141 个时间点；真实差分与一阶观测分开 |
| `results/sensitivity.csv` | 135 点共用网格；不按结构指定较优漏风，9 个形式稳态越界 |
| `results/common-step.csv` | 91 点共同环境阶跃，仅传感器失配产生差分 |
| `results/decaying-wall.csv` | 3×181 点有限储量放湿；从 y=0 开始，不含传感器滤波 |
| `results/temperature.csv` | 7 个温度读数偏置与正确温湿配对的对照 |
| `results/coverage.csv` | 双面独立气层的瞬时通量解析反例，不是干燥时间 |

`formal_equilibrium_delta_Pa` 仅为代数外推；必须同时看 `equilibrium_in_unsaturated_domain`，不可把越界稳态当可实现状态。本轮所有 140 s 周期均未跨越单温度饱和边界，不表示冷表面也不会凝露。

首轮 11 项检查记录验证当时实现和证据完整性，本轮未重跑；其源与输出指纹由 T02 入口核对。它们不是衣物实验。T02 新结果和首轮记录分开，主模型没有反向导入本研究目录，T03 草案仍未正式集成。
