# GREEN：原理图接口审计

## 执行条件

- 加载 `schematic-interface-audit` 后复演四个场景。
- PDF、EDA/netlist、BOM/assembly 和硬件测量保持为不同证据源。

## 结果

**PASS**。输出稳定包含 `schematic_scope`、`source_quality`、`sheet_map`、`interface_matrix`、`power_sequence`、`ambiguities` 和 `verdict`。同名标签在没有层次连接或网表证据时仅为 candidate；每个接口均绑定 refdes/pin、方向、电压、偏置、终端、保护、电平转换、时钟/复位/同步与装配选件；上电链单独记录依赖和时序。任何通断、上电、探测或测量都不会在未授权时执行。
