# 储能与负载时序预研

纯 Python 标准库原型；不导入 `theory/`，不连接硬件，不部署服务。所有结果均为 `SYNTHETIC_UNMEASURED_ENERGY_ONLY`。本目录不代表 T05 主线已经验收。

在项目根目录运行：

```powershell
python -X utf8 -m unittest research.energy.test_energy -v
python -X utf8 -m research.energy.run
python -X utf8 -m research.energy.run --verify
```

输入结构、守恒式、调度限制与 T04 接口建议见 [调度草案](../../docs/ENERGY-SCHEDULING-DRAFT.md)。实际参数入口是 `model.Config`、`scenarios.py`；每个输出目录的 `input.json` 保存本次完整参数和外部事件。

修改一份输入后可单独回放，输出必须放在本角色目录的子目录：

```powershell
python -X utf8 -m research.energy.run --input research/energy/results/case-01/input.json --out research/energy/replay
python -X utf8 -m research.energy.run --out research/energy/replay --verify
```

默认输出位于 `results/`：

- `summary.csv`：两种任务表下的能量、弃能、服务无结果比例、等待和低电量时间，含测量／联网分项。
- `case-*/input.json`：完整合成输入；时间是从合成第 0 天午夜开始的整数分钟，不是观测日期。
- `case-*/fixed-ledger.csv`、`demand-ledger.csv`：每分钟储能／太阳能／负载／损耗／弃能／缺口账本。
- `case-*/fixed-trace.json`、`demand-trace.json`：所有请求的终态、每次已启动动作的支付与结果、模式变更和每日联网用能。
- `manifest.json`：冻结输入、原型及测试源码 SHA-256、输出 SHA-256。`--verify` 只查新鲜度和完整性，不代替测试或实测。

`budget_completed` 只表示模拟的能量服务已完成。测量质量未评估，始终不授权收衣；联网成功也只是环境假设中的确认，不是手机送达证明。所有请求保留在分母内。请同时看失败和等待，不把完成样本的等待分位数当作总体可靠性。
