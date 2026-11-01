# T12 来源及假设登记

核对日期 2026-11-01。官方裸器件/模块数据只说明所列封装，不能推出装配后的板、电池或探头外壳大小；本阶段不冻结型号和采购。

| ID | 可复核原始来源 | 使用范围 |
|---|---|---|
| L01 | [ST 官方 STM32U0 产品表](https://www.st.com/en/microcontrollers-microprocessors/stm32u0-series/products.html?querycriteria=productId%3DSS2133)；[DS14581 下载入口](https://www.st.com/resource/en/datasheet/stm32u031f8.pdf) | 本轮官网产品表/检索记录支持 U031K8 的 UFQFPN32 5×5×0.55 mm、12 KB SRAM；PDF 本轮打开超时，不声称重新读到全文。T09 留有先前数据手册审查。这里只作芯片占位参考，不是整板或当前固件可适配证明。 |
| L02 | [ESP32-C3-MINI-1/1U 官方数据手册 v2.2](https://documentation.espressif.com/esp32-c3-mini-1_datasheet_en.pdf)，表 1-1、尺寸/布局章节 | 本轮全文可读取；板载天线 MINI-1 模块尺寸 13.2×16.6×2.4 mm，供电 3.0–3.6 V。尺寸未包含外部电源、天线周边、主控或电池空间；真实 RF/峰值/电源设计另验，不能等同日平均用电。 |
| L03 | [Sensirion SHT4x 官方数据手册 v6.5, 2024-04](https://sensirion.com/media/documents/33FD6951/661CD142/HT_DS_Datasheet_SHT4x.pdf) | 本轮全文可读取；温度响应受基板导热与安装设计影响（表 2 注 9），推荐环境及长期高湿偏移说明见 2.3。说明不能直接把裸器件响应或电流当装壳整机性能；本轮不据此设真实热恢复时间或判干误差。 |
| L04 | [T09 假设配置](../../research/design_extension/assumptions.json) | 负载/效率/可用容量分数均是工程假设。可复用非光伏值作初始预算，需区分负载侧与电池侧。光伏字段和旧面积结果不进入当前供电模型。 |
| L05 | [T10 总审查](../verification/T10-review.md)、[M6 接口](../GARMENT-PREDICTION-INTEGRATED.md) | 候选推演在电脑；不从 PC 时间推断 MCU 耗能。中央接触与无热干扰是外部声明，不是已实现传感器。 |

衣架/电池外包络、柔性衣物代理区域、主机尺寸、装配余量、净空阈值、充电功率/效率、动作工作量和可用容量均须在新配置中明确标为合成或设计假设。电池容量与物理尺寸不绑定；不凭 mAh 猜出商品电池体积，也不引用未核对价格。没有材料强度、布料接触力、实际天线场或滴水轨迹仿真。
