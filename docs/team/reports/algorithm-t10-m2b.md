# T10 M2b：条件时间工作报告

2026-10-29。M1/M2a 限定范围已验收；冻结清单 10 项及两份收据已验收来源均由检查工具核对通过。执行了 `python -B -X utf8 tools/project_status.py`、`python -B -X utf8 tools/check_garment_m1.py --check-receipt`、`python -B -X utf8 tools/check_garment_m2.py --check-receipt`，均通过；仅是只读校验，不重复旧测试。已读任务书、验收记录、M1 模型及登记、M2a 未闭合边界与 T09 设计；全部作为只读输入。

交付计算器按每个指定先验成员、每个指定未来分段环境推进 M1 两区，不读取 RH 测量、材料标签、生成真值或未来实况；配置标签和参数均由人工选择。输出 `scenario_only`、`observation_mapping=unavailable`、`SYNTHETIC_UNCALIBRATED` 和恒 false 授权。真实中央映射未知，不做观测校正或完整估计器声明；调用者需要自己保证所输入情景是事前假设，结构白名单不能辨别伪装的事后实况。

目标为两个区同一网格点 X≤合成目标，不取各区各自首次穿越的最大值。记录首次网格检出、前一网格点、步长及随后采样点再湿标记；可能漏掉步间短暂的共同达标/再湿，前一网格点不是全局首次连续穿越的严格边界。持续满足、固定等待、实物收衣结论均不在本包。任何未达、超域或积分失败的成员保留并使时间上界未知，不因仅展示成功成员而虚构有限上界。若样本已达到后后续算步失败，也保留失败状态及未知上界。

测试 `python -B -X utf8 research/garment_prediction/estimation_m2b/test_eta.py`：8 项通过，覆盖初值/材料闭合变化、分段驱动、未达/超域/步失败、目标时两区共同性及再湿、输入非法与白名单隔离、两个解析极限。线性排液 X=X0 exp(-kt) 双区示例解析共同首次约 1386.294 s，10 s 首次网格检出 1390 s，1 s 为 1387 s；仅该单调解析例可将误差限制于采样间隔。另在同温雨水和排液下 Xeq=0.2、X0=0.8、k=0.001 1/s 时，目标 0.3 解析约 1791.759 s、10 s 网格检出 1800 s；目标等于或低于平衡值在 10000 s 内未检出，上界未知。数值均是退化合成验证，非实物参数或精度；有限精度和平衡邻域仍需谨慎解释。

`python -B -X utf8 research/garment_prediction/estimation_m2b/replay.py`：验证 41 个冻结及源码输入，生成配置对应的 12 组合情景结果及 manifest。4 个检出、8 个时域未检出；最早已知检出 5780 s，上界 null。命令、单位、seed=null、参数来源和 SHA-256 在 `config.json` 与 `results/manifest.json`，仅 Windows 环境验证，非跨平台字节承诺。

新增 `research/garment_prediction/estimation_m2b/eta.py`、`config.json`、`test_eta.py`、`replay.py`、`results/scenarios.json`、`results/manifest.json`、`docs/GARMENT-ETA.md` 和本报告。既有 M1/M2a 来源与收据只读；不更新全局状态、不提交、不部署。

剩余 T10 缺口：无已标定中央观测—状态映射、接触证据获取、真实材料及未知初值约束、现实天气/遮挡驱动和独立实测；两区不是全衣覆盖保证。本包不会自动缩小参数集合，不证明真实误差或推荐收衣。建议协调者先审查模型边界、样本时间语义及解析和失败保留，再决定下一项；不自动推进 T12/T11。
