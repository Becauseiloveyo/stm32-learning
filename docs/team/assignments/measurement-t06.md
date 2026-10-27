# T06 测量与状态机 C 核心

协调者已启动 T06，T05 已验收。先读 PROJECT、TEAM、本任务书；只读 STATUS，不领取主任务。

## 唯一写入范围

- `firmware/core/hanger_measurement.h`、`hanger_measurement.c`、`hanger_state.h`、`hanger_state.c`，必要的本角色内部 C 头文件（不要覆盖 energy 文件）。
- `firmware/host/measurement_bridge.c`、`measurement_validation.py` 及本角色测试辅助文件，以 `measurement_` 开头。
- `docs/team/reports/measurement-t06.md`。

其他源码、已验收结果与全局文档一律只读。根协调者维护主构建/验证入口、CORE-INTERFACE、状态和验收收据。另一角色独立实现能量原语，不依赖你的未提交接口。不要创建对话或发消息；在文件与最终回复交付。

## 输入及范围

只读当前 theory/protocol.py、model.py::assess，以及 research/state_machine/machine.py、scenarios.py、已保存结果和 MEASUREMENT-CONTRACT、STATE-MACHINE。实现独立于驱动的纯 C99 测量判定和状态更新，不把 Python 模型 simulate 移植为设备算法。

尽可能保持现有合法域行为：原始温湿度计算水汽压，完整阶段/窗口/质量门限，弱证据不授权收衣，批次/安装/启动/配置身份隔离，完整周期预约、迟到、重复、冲突、回退/跳变、空闲过期与恢复。不得信任调用方已经判定的状态。

接口需明确数据类型、单位、范围、错误码、版本及传输层责任。C 结构无法表达 Python 动态类型时，用缺失/无效标志或桥接器明确拒绝，不把错误静默转为有效值。配置哈希如由可信外部配置入口提供，应绑定实际参数，不能以任意调用方字符串掩盖配置变化。支持范围若小于 Python 必须明确拒绝并写出一致性域，不能静默截断或靠测试避开。

禁止 malloc/realloc 和无界历史。给出固定容量、耗尽行为、最长输入、时间上界和弱计数溢出策略。容量不足必须显式 fail-closed，不能淘汰仍可能被重放的身份来继续计数。明确新 boot 与真正进程初始化的区别，掉电不能恢复旧弱证据；可信启动身份/外部持久化尚未实现。不要为通过而宣称小容量原型适合长期运行或特定 STM32 型号。

## 主机验证

现有编译器 `C:\msys64=\ucrt64\bin\gcc.exe`，本机已验证 GCC 16.1.0。用 subprocess 参数列表，子进程 PATH 加该 bin；C99、`-Wall -Wextra -Werror -pedantic`、不启用 fast-math。不安装工具，不操作设备。生成物仅在 `firmware/host/build/measurement/`（协调者会加忽略）。

建议导出一个 Python 可调用的验证入口 `run_validation(compiler, build_dir) -> dict`，compiler 为绝对路径字符串，build_dir 为 pathlib.Path，完成实际构建与运行；失败抛异常，不伪造主收据。可独立 CLI。桥接器只搬运输入，不能在 Python 先判定再给 C。

必须同原始观测/事件向量比对当前真实 Python 参考。覆盖正常强/弱/UNKNOWN、门限边界、NaN/Inf/越界/缺字段、非单调/间隔/阶段、温差/湿变/开夹；T04 保存情景应在容量支持域内重放。比对关键状态、accepted/disposition、弱计数、复核标志、pending/时钟及关键数值；明确未比较字段与理由。新增容量恰满/超限、长计数/时间边界、安全失败测试。数值容差有依据；如果精确门限附近双精度差异存在，处理并报告，不能随意改门限。

报告实际命令、编译器、通过数、最大数值差、结构 sizeof/固定数组/栈开销测算，未测实机限制与协调者调用方法。不要改上游模块以让对照通过。
