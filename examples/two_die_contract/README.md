# 跨层仿真方案交付包

- `跨层仿真平台方案_v0.1.md`：三方职责、输入输出、计账边界、IR、gem5 实现路径、ASTRA 串联、事件耦合、闭环与验收。
- `contract_example.json`：两 die、七节点、无竞争串行场景的平台中立 JSON 示例。
- `validate_contract.py`：仅检查示例静态契约和人工 golden 时间，不运行任何仿真器。
- `validation_result.json`：本次契约校验及 12 个故意损坏输入的拒收检查结果。

运行方法（Python 3 标准库，无额外依赖）：

```text
python validate_contract.py contract_example.json --self-test
```

期望：7 个节点，完成时间 1,804,000 ps，外部跨 die 有效数据 256 bytes，12 个非法输入被拒绝。

接口是项目建议 v0.1，不是官方格式，也不是完整生产 JSON Schema。脚本支持的范围刻意限制为串行人工样例；它不验证真实队列占用、credit、Chakra 转换、packet/flit 行为或跨平台精度。这些验收在主文档第 9 节列出，尚需实现。

本次没有构建或运行 gem5、Timeloop、ASTRA-sim，也没有修改 `D:/GEM5/gem5_esl` 中的仿真器代码。主文档引用了本地源码检查与官方资料，具体原生接口必须在三方选定版本上锁定并验证。
