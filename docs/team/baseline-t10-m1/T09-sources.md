# T09 原始来源登记

检索日期：2026-10-27。以下区分全文、摘要/检索片段、官方规格和实际下载数据。只使用可核对信息；未取得的参数不补造。本次不是专利新颖性检索，不能据此声称首创。

| ID | 来源与链接 | 本轮可用信息及限制 |
|---|---|---|
| S01 | Jerkovic 等，2025，*Drying Performance of Fabrics on the Human Body*，Materials 18(11):2655，[开放全文](https://pmc.ncbi.nlm.nih.gov/articles/PMC12155929/)，DOI 10.3390/ma18112655 | 阅读方法、材料表和数据声明。表 2 的面密度/厚度可作试样级参考；例如 PES-I：232 g/m²、0.89 mm；PES-L1：149 g/m²、0.71 mm。测试为恒定实验环境及多种暴露/加热方式，不能直接当阳台整衣参数或洗衣机甩干数据。论文内有结果，未发现可直接下载的全体原始时序文件。 |
| S02 | Çay，2017，*Modelling of the Drying Behaviour of Regenerated Cellulosic Fabrics*，[期刊页](https://dergipark.org.tr/en/pub/tekstilvekonfeksiyon/article/372036)，[全文 PDF](https://dergipark.org.tr/en/download/article-file/393371) | 阅读试验方法和模型介绍。支持把早期与降速阶段分别建模；实验为 35°C、小圆形试样、加水模拟汗湿，并非甩干整衣。未把拟合系数移作本项目材料常数。 |
| S03 | *Fabric properties and electric efficiency limits of mechanical moisture extraction from fabrics*，[原出版社](https://www.tandfonline.com/doi/abs/10.1080/07373937.2021.2005620)，DOI 10.1080/07373937.2021.2005620 | 出版社检索结果提供定义和部分实验段落；直接打开返回 403，未宣称完整复核全文。只用于干基水分/机械脱水非零残留的背景。文中特定试验起终点不能视作全部家庭甩干衣物范围。 |
| S04 | *Prediction of the water evaporation rate of wet textile materials in a pre-defined environment*，2020，32(3):356–365，[出版社摘要索引](https://www.sciencedirect.com/org/science/article/abs/pii/S0955622220000120)，[DOI](https://doi.org/10.1108/IJCST-06-2019-0077) | 检索可见摘要，直接打开返回 403。支持热/质交换与蒸发冷却应共同考虑；未取得完整实验数据，不引用其比例为本设备修正系数。 |
| S05 | 美国国家气象局，[Discussion on Humidity](https://www.weather.gov/lmk/humidity) | 官方科普明确高 RH、甚至 100% RH 也不必降水；用于否定湿度单独确认下雨。 |
| S06 | 美国国家气象局，[降水概率说明](https://www.weather.gov/media/pah/WeatherEducation/pop.pdf) | 概率对应地点和预报时段；不能把“预报有雨”当作本地实时雨滴确认。不是中国天气接口字段规范。 |
| S07 | METER，[PHYTOS 31 官方资料](https://metergroup.com/products/phytos-31/)，[手册](https://library.metergroup.com/Manuals/20434_PHYTOS31_Manual_Web.pdf) | 电容表面对凝露/雨等水分响应，支持“润湿不等于确认雨”的设计。产品价格、阈值及精度不移用到自制 PCB。 |
| S08 | Hydreon，[RG-15 官方规格](https://rainsensors.com/products/rg-15/) | 光学检测的实际器件参照；标称干燥无输出 110 μA、下雨 2–4 mA。仅作为条件规格，不是采购决定或自制传感器性能。 |
| S09 | ST，[STM32U031x4/6/8 数据手册 DS14581 Rev 2](https://www.st.com/resource/en/datasheet/stm32u031f8.pdf) | 首页：12 KB SRAM、UFQFPN32 5×5 mm、WLCSP27 2.55×2.34 mm。电流规格均有模式/条件，不能等同开发板或整机。 |
| S10 | Espressif，[ESP32-C3 数据手册 v2.4](https://www.espressif.com/sites/default/files/documentation/esp32-c3_datasheet_en.pdf)，[睡眠文档](https://docs.espressif.com/projects/esp-idf/en/stable/esp32c3/api-reference/system/sleep_modes.html) | 指定射频条件下峰值可到 335 mA；深睡眠无线断电。关注建链成本/瞬时供电，不用芯片深睡眠电流代表整机。 |
| S11 | IETF，[RFC 8445 ICE](https://www.rfc-editor.org/info/rfc8445/) | 已阅读官方协议有关信令、STUN/TURN 和候选路径说明。P2P 可能需要中继；本轮不实现 NAT 穿透。 |
| S12 | NASA POWER，[Hourly API](https://power.larc.nasa.gov/docs/services/api/temporal/hourly/) | 已实际下载武汉示例点 2024 年五变量逐小时数据，单位以响应为准。URL/字段/指纹见 [weather-source.json](../../research/design_extension/data/weather-source.json)。格点数据不是衣物、阳台或预报实测。 |
| S13 | 欧盟 JRC，[PVGIS API](https://joint-research-centre.ec.europa.eu/photovoltaic-geographical-information-system-pvgis/using-pvgis-5/api-non-interactive-service_en)，[Hourly radiation](https://joint-research-centre.ec.europa.eu/photovoltaic-geographical-information-system-pvgis/using-pvgis-5/pvgis-5-tools/hourly-radiation_en) | 已实际调用 v5_3/seriescalc，ERA5 2023，六种姿态；URL/姿态/指纹见 [pvgis-sources.json](../../research/design_extension/data/pvgis-sources.json)。未使用 beta 接口，没有实地遮挡/玻璃参数。 |
| S14 | TI，[BQ25570 官方产品资料](https://www.ti.com/product/BQ25570)，[数据手册](https://www.ti.com/lit/ds/symlink/bq25570.pdf) | 收能/低静态电流方案参考；输出能力必须单独与无线峰值核对。本轮不冻结此器件，不声称适配任意太阳能板。 |

数学守恒方程、参数集合策略与预算情景是项目自己的工程综合；上述论文没有验证本项目整套设备。NASA 2024 和 PVGIS 2023 不同年，分别统计，未合并成同步驱动。
