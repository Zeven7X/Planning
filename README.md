# SIMD Die 仿真平台规划

本仓库归档 Timeloop → gem5 / Garnet / HeteroGarnet → ASTRA-sim 跨层仿真平台的方案与交付样例。

## 文件索引

| 文件 | 内容与用途 |
| --- | --- |
| [SIMD Die 平台仿真方案：L0 / L1](presentations/SIMD_Die平台仿真方案_L0_L1.pptx) | 最新汇报 PPT，26 页、25 个可编辑表格；平台架构、三方输入输出、数据包、复用与自研模块、HBM / memory 模型及阶段规划 |
| [跨层仿真平台方案 v0.1](docs/跨层仿真平台方案_v0.1.md) | 初版详细方案：职责、接口契约、计账边界、平台串联、闭环与验收 |
| [SimAI 对当前项目的参考总结](docs/SimAI对当前项目的参考总结.md) | SimAI 的计算、collective flow、网络模型及对 Timeloop / HeteroGarnet / ASTRA-sim 路线的借鉴建议 |
| [接口样例说明](examples/two_die_contract/README.md) | 两 die、七节点串行人工样例的说明与运行方式 |
| [contract_example.json](examples/two_die_contract/contract_example.json) | 平台中立的建议接口样例，不是仿真器原生格式 |
| [validate_contract.py](examples/two_die_contract/validate_contract.py) | 静态契约与人工 golden 时间校验脚本 |
| [validation_result.json](examples/two_die_contract/validation_result.json) | 已生成的样例校验结果 |
| [初版方案交付包 ZIP](archives/simulation_plan_v0.1.zip) | 原始文档、样例、脚本及结果的打包归档 |

## 当前约定

- 计算架构是 **SIMD**；当前关注性能、时延、带宽、竞争与背压，**不纳入功耗分析**。
- 计算核内部由核心团队负责；die 平台以 gem5 / Garnet 为基础，跨 die 系统侧对接 ASTRA-sim。
- 最新阶段规划以 PPT 为准。详细方案 v0.1 是历史初稿，其中涉及的能耗内容不属于当前实施范围。
- PPT 中的配置、带宽计算与时延样例用于方案说明，不代表实际硬件或仿真测量结果；引用来源在演讲者备注中。
- 此仓库是规划与接口样例交付，不是已经实现并联调完成的仿真平台。

## 运行接口样例

需要 Python 3 标准库，无额外依赖。在仓库根目录运行：

```sh
cd examples/two_die_contract
python validate_contract.py contract_example.json --self-test
```

人工样例期望：7 个节点，完成时间 1,804,000 ps，跨 die 有效数据 256 bytes，12 个非法输入被拒绝。

校验脚本不运行 Timeloop、gem5 或 ASTRA-sim，也不验证真实队列、credit、packet / flit 行为及 Chakra 转换。后续应锁定三方版本、落地适配器并执行联调验收。

归档日期：2026-09-19。
