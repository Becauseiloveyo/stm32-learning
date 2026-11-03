# T11 P3 资源与验收角色报告

2026-11-03。按 TEAM 和 integration-t11-p3 任务书，仅负责资源估算、ROADMAP 条件映射和本报告；界面实现与运行验收由协调者负责。阅读 PROJECT、STATUS、TEAM、任务书、P3 交接及冻结入口 `docs/team/baseline-t11-p3/snapshot.json`，并参照 P1/P2 已接受审查及 T06 C 基线。P2 收据检查确认冻结来源当前匹配；不把其他工作者正在改变的投影当稳定接口。

交付：`docs/T11-RESOURCE-REVIEW.md`、`docs/T11-ACCEPTANCE-MATRIX.md`、本报告。估算是明示字段位宽及容量的设计示例，不是现成 MCU 实现；记录了 RAM、非易失双银行容量公式、写入耐久示例、GC 条件与未知项。验收矩阵逐项对应 ROADMAP T11，并标识界面仍需运行验证。角色文档交付完成，不宣称 T11 整体验收。

实际检查：

- `python -X utf8 tools/project_status.py`：恢复检查通过，非重跑模型。
- `python -B -X utf8 tools/check_integration_events.py --check-receipt`：P1 来源记录当前且通过。
- `python -B -X utf8 tools/check_integration_power_transport.py --check-receipt`：P2 当前 190 文件。
- `python -B -X utf8 -m unittest research.integration_t11.p1.test_controller research.integration_t11.p2.test_power research.integration_t11.p2.test_transport research.integration_t11.p2.test_integration`：42 项通过，6.017 秒。

协调者需逐帧实测界面和失效后的下一帧，确认预报生效边界、本地/远端独立时钟、热干扰、满队列及送达措辞；将实际入口及结果纳入总体收据。持久格式、目标 MCU 编译、真实存储擦写与原子性、硬件峰值/热、手机送达及衣物准确率均未验证。无需更改已接受 C 或旧收据；实际序列化若不能容纳条件/负载，需重新设计并做有界及断电验证。
