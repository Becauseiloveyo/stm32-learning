# T05：能源调度接入真实研究状态机

`SYNTHETIC_UNCALIBRATED`。所有场景都是未标定的合成输入，所有事件均无收衣授权；结果不表示真实衣物判干精度或实物续航。

本目录使用 Python 标准库，直接调用当前 `research.state_machine.machine.StateMachine` 和 `theory.model`。默认完整周期由上游协议推导为 30 + 20 + 90 = 140 秒。旧 `research/energy/` 是只读历史原型。

在项目根目录运行：

```powershell
python -B -X utf8 -m unittest discover -s research/energy_t05 -p "test_*.py" -v
python -B -X utf8 research/energy_t05/run.py
python -B -X utf8 research/energy_t05/run.py --verify
```

生成命令只覆盖本目录 `results/` 下的已定义产物。`--verify` 不写文件：核对源码、上游输入和产物指纹，重放保存的真实 T04 事件，再从保存的完整输入重算所有能源结果并逐字节比较，最后检查输入与当前场景定义及完整清单一致。它不只是哈希检查。上游来源不匹配会报错，不自动改写上游。`-B` 避免在只读依赖目录写入字节码。

| 文件 | 内容 |
|---|---|
| `contracts.py` | 单位、输入结构、整数秒有效范围、负载假设和完整动作准入预算 |
| `model.py` | 秒级调度、真实状态机调用、能量区间账本、取消／恢复与服务统计 |
| `scenarios.py` | 六组共享输入，分别运行 fixed／demand |
| `run.py` | 生成、指纹和只读重算入口 |
| `test_energy.py` | 28 项自检，含解析守恒、边界、因果性和 20 组有种子的参数组合 |
| `results/inputs.json` | 完整场景、外部请求、观测条件、环境、参数与配置快照 |
| `results/case-*-ledger.jsonl` | 每段已付／未供负载、收能、转换损失、储能和弃能；瞬时动作单独留行 |
| `results/case-*-events.jsonl` | 墙钟、真实 T04 输入／输出和完整原始观测 |
| `results/case-*-service.json` | 请求、动作、机会、安装事件、模式、最终状态及统计 |
| `results/summary.csv` | 12 次对照的完整指标与分项能耗 |
| `results/manifest.json` | 来源、源码、产物 SHA-256，单位、配置、合成标签、种子与数量 |

`input_sha256` 相对项目根；`source_sha256` 相对本目录；`output_sha256` 相对 `results/`。清单不自我哈希，也不将说明文档作为可执行源码。

正式契约、对照解释及限制见 [ENERGY-SCHEDULING.md](../../docs/ENERGY-SCHEDULING.md)，交付记录见 [energy-t05.md](../../docs/team/reports/energy-t05.md)。正常情景中按需策略虽省电且完成相同外部请求，其 40 分钟间隔会使弱证据链过期，不能据此断言判定质量等价。固定策略的 737 秒成功等待又包含本场景刻意设置的 60 秒机会／63 秒请求相位，不能推广为普遍延迟。
