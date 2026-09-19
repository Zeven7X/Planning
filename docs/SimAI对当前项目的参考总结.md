# Alibaba SimAI 对当前跨层仿真项目的参考总结

日期：2026-09-19
适用范围：Timeloop/Accelergy → gem5/HeteroGarnet → ASTRA-sim 的跨层仿真方案

## 结论

SimAI 对本项目最有价值的不是直接拿来替换 HeteroGarnet，而是它对两类问题的拆分：

1. 用可替换的计算耗时模型表示计算阶段；
2. 把集合通信展开为有依赖的点到点 flow，再交给网络后端仿真。

因此建议保留当前路线：Timeloop 负责计算映射和核内服务时间，HeteroGarnet 负责 die 内 NoC、DMA、共享存储端点及反压，ASTRA-sim 负责多 die、多卡和多机柜的全局调度与外部网络。可以借鉴 SimAI 的 workload、flow 和事件完成语义，但不能把 SimAI 的 GPU/NVLink 模型直接当作 die 内 NoC 模型。

## SimAI 的整体结构

当前公开的 SimAI 由几个相互配合的部分组成：

| 组件 | 作用 | 对本项目的对应物 |
| --- | --- | --- |
| AICB / AIOB | 生成接近真实训练或推理框架的工作负载，并记录计算模式与通信模式 | 工作负载生成器；本项目还需要把算子映射成 tile 级 boundary access |
| SimAI 计算模型 | 通过 GPU kernel 实测、已有 profile 或性能外推得到计算时间 | Timeloop 输出的 `core_service` |
| SimCCL | 把 NCCL 的算法、协议、channel 和 chunk 决策翻译成点到点 flow | ASTRA 的 collective 展开层 |
| astra-sim-alibabacloud | 负责执行层调度和计算—通信依赖 | ASTRA-sim 系统层及其适配器 |
| ns-3-alibabacloud | 模拟 RDMA、交换机、队列、拥塞控制和网络链路 | 多卡／多机柜外部网络后端 |

SimAI 提供三种主要使用粒度：

- analytical：用有效总线带宽估算通信时间，速度快，适合规模和参数扫描；
- simulation：将更细粒度的 flow 交给 ns-3，模拟队列、协议和拥塞；
- physical：面向真实 RDMA 环境的流量生成，公开版本能力仍在演进。

## 计算模型是怎么做的

SimAI 计算模型主要针对 GPU 软件 kernel。常见方式是：

### 实测计算时间

在真实 GPU 上执行算子或 kernel，记录执行时间，形成按算子、形状、数据类型和 GPU 型号组织的 profile。之后仿真器只安排一个计算完成事件，不在仿真过程中重新执行真实 GPU 计算。

### 复用计算时间文件

用户可以直接提供已有的 computation description file。这样可以把“计算性能获取”与“系统通信仿真”分开，多次复用相同的计算结果。

### 对未发布设备做性能外推

SimAI-CP-Model 会区分计算密集型和内存带宽密集型 kernel，用已知 GPU 的实测结果以及目标 GPU 的算力、带宽参数进行估计。它适合做架构趋势分析，但不能替代目标硬件或 Timeloop 的结构化建模。

### 事件调度

得到计算时间后，SimAI 把计算阶段放进事件队列。当前阶段完成后，才推进下一计算阶段或触发通信；阻塞／非阻塞通信则由工作负载策略决定是否等待通信完成。

这对本项目的直接映射是：

```text
Timeloop mapping + workload
        ↓
core_service：输入可用后，计算服务多少时间
        ↓
gem5 依赖调度器
        ↓
计算完成，生成输出 tile
        ↓
触发下一个 boundary_access
```

需要特别约束计算时间的范围。`core_service` 可以包含计算阵列和私有 SRAM 的内部限制，但不能再次包含由 HeteroGarnet 建模的共享 SRAM、NoC、DMA、D2D 或外部网络等待。否则会重复计时。

## 通信模型是怎么做的

SimAI 把通信拆成两个阶段：先决定通信算法和流量，再模拟流量的传输。

### 从 collective 到 flow

上层可能只有一个 AllReduce、AllGather、ReduceScatter 或 AlltoAll。SimCCL 根据参与者数量、消息大小、拓扑和配置，选择算法、协议、channel 和 chunk，并输出点到点 flow。

每条 flow 通常至少需要：

```text
algorithm
protocol
flow_size
src
dest
connection_type
channel/chunk 信息
前序和完成关系
```

例如 Ring AllReduce 会被展开成多个参与者之间的分轮传输，而不是在网络仿真器里用一个“平均 AllReduce 延迟”代替。

### analytical 模式

SimAI-Analytical 使用 bus bandwidth 估算集合通信时间。它适合回答：

- 参与者数量增加后通信是否成为瓶颈；
- 带宽从 100G 提升到 200G 或 400G 的收益；
- TP、DP、PP、EP 参数如何影响端到端迭代时间。

它不负责准确表达 die 内 router、VC、credit、buffer occupancy 或具体路径竞争。

### ns-3 模式

SimAI-Simulation 把 flow 交给扩展的 ns-3 后端。公开代码包含 RDMA QP、ACK/NACK、交换机缓冲、PFC、ECN，以及 DCQCN、HPCC、TIMELY、DCTCP 等拥塞控制能力；也提供 NVSwitch 和多层服务器网络拓扑模型。

这对于未来多卡、多机柜网络有参考价值，但它的重点是 GPU 集群和 RDMA 网络。不能直接回答 die 内 NoC 中一个 flit 如何经过 router、VC 分配、credit 和 SerDes。

## SimAI 的计算—通信重叠

SimAI 的工作负载调度会区分：

- blocking communication：后续计算必须等通信完成；
- non-blocking communication：通信发出后允许部分后续计算继续；
- synchronization point：在真正依赖数据的位置等待通信完成。

对你的项目，应该把这种语义落实为显式 DAG：

```text
计算完成
  ├─ 产生 output tile
  ├─ 触发 egress DMA
  └─ 允许没有数据依赖的下一阶段继续

真正消费 output tile 的阶段
  └─ 等待 fabric + ingress + destination-visible 完成
```

但是“允许重叠”不等于“完全没有干扰”。如果计算和通信共享 DMA、SRAM 端口或 NoC，必须由 gem5 的资源模型决定它们是否排队；不能只在 ASTRA 里把两个事件同时发出就认为已经准确模拟了重叠。

## 对本项目最值得借鉴的设计

### 1. 计算 profile 可替换

Timeloop 团队交付按阶段或 tile 组织的计算服务时间。gem5 只消费该时间，不需要接管计算核内部模型。计算 profile 应与 workload、mapping、核心配置和工具版本绑定。

### 2. collective 与网络传输分层

ASTRA 团队负责把多 die／多卡 collective 展开为 P2P flow。HeteroGarnet 负责 die 内 flow 的实际传输，外部网络后端负责 die 边界之外的传输。一个 collective 只能展开一次。

### 3. 完成事件驱动后继任务

不能用预先写死的通信时间直接推进下一阶段。必须由真实的 `rx_delivered` 或 `consumer_visible` 事件解锁后继计算，才能让 NoC 拥塞和有限缓冲反馈到端到端时间。

### 4. 快速模型和详细模型并存

SimAI 用 analytical 模式快速扫描，用 ns-3 做详细验证。本项目也可以采用：

```text
HeteroGarnet：小规模、die 内高精度参照
ASTRA analytical/profile：大规模多 die、多卡参数扫描
ASTRA + 详细 die 事件耦合：代表性场景校准
```

快速模型必须记录适用范围，不能把某个场景的平均带宽当作任意负载下的固定硬件能力。

### 5. 保留可导出的 flow

建议在当前 `boundary_access.jsonl` 之外，增加一层可选的 `communication_flow.jsonl`，用于表示 collective 展开后的 flow：

```json
{
  "flow_id": "allreduce.layer12.round0.chunk3",
  "collective_id": "grad.layer12",
  "src_rank": 0,
  "dst_rank": 1,
  "src_endpoint": "die0.dma0",
  "dst_endpoint": "die1.dma0",
  "payload_bytes": 1048576,
  "algorithm": "ring",
  "protocol": "simple",
  "channel": 2,
  "chunk_id": 3,
  "deps": ["allreduce.layer12.round0.chunk2"]
}
```

这层文件让 ASTRA 的通信算法选择与 gem5 的 die 内传输解耦，后续也方便导出给其他网络后端。

## 不应该直接照搬的地方

### 不要把 busbw 当作 die 内总线模型

SimAI analytical 的 busbw 是通信性能估计口径。它不等价于 AXI/CHI/CXL 事务、NoC router pipeline、VC、credit、buffer 或共享存储仲裁。

### 不要用 GPU kernel 时间替代 Timeloop 的边界服务模型

GPU kernel profile 适合描述 GPU 级计算完成时间；你的系统需要进一步知道 tile 级数据什么时候可用、输出什么时候产生、哪些 buffer 会被占满。因此仍然需要 `boundary_access.jsonl` 和 `resources.json`。

### 不要让 ASTRA 和 HeteroGarnet 重复模拟同一段链路

建议边界为：

```text
计算端点 → die 内 DMA / shared memory / HeteroGarnet → D2D TX/RX boundary queue
        |                                           |
        +------------- gem5 计时 ------------------+

D2D boundary queue → die 间、卡间、机间网络 → 对端 boundary queue
                              ASTRA / ns-3 计时
```

如果 D2D PHY 的 SerDes 和协议开销放在 ASTRA，就不能再在 gem5 中计一次；如果放在 HeteroGarnet，则外部模型只从边界队列开始计时。

## 对你的项目的推荐落地方式

第一阶段先不复用 SimAI 的 AICB 或 SimCCL 代码，只复用设计思想：

1. Timeloop 团队输出 `core_service.jsonl` 和 `boundary_access.jsonl`；
2. 你把 boundary access 编译成 HeteroGarnet 的端点消息和依赖事件；
3. ASTRA 团队在多 die 层把高层通信操作展开为 `communication_flow.jsonl`；
4. 你把属于本 die 的 flow 映射到本地 DMA、共享存储和 HeteroGarnet；
5. 外部 flow 由 ASTRA 的网络后端仿真；
6. 用同一套 `flow_id / transfer_id / chunk_id` 关联三个平台的完成日志。

第二阶段再评估复用 SimCCL：

- 如果未来通信库语义接近 NCCL，SimCCL 的 collective→flow 转换值得复用；
- 如果是自研通信库、非 GPU 端点或独特 die 拓扑，保留自己的 flow 编译器更稳妥；
- SimCCL 生成的 flow 仍需转换成你们项目的 endpoint、buffer 和依赖语义。

## 适用性判断

| 需求 | SimAI 参考价值 |
| --- | --- |
| Timeloop 计算阶段注入 | 高：可以借鉴可替换 profile 和完成事件 |
| die 内 NoC router/VC/credit | 低：继续使用 HeteroGarnet |
| D2D 多 die 粗粒度通信 | 中高：借鉴 flow 和端点边界 |
| 多卡 collective 算法 | 高：SimCCL 的分层方式值得研究 |
| 多机柜 RDMA/拥塞控制 | 高：ns-3-alibabacloud 有直接参考价值 |
| 自研加速器内部精确计算 | 中：需要 Timeloop 和自有边界访问模型 |
| 直接替代当前平台 | 低：模型粒度和硬件对象不完全匹配 |

## 推荐结论

SimAI 应作为你们的“上层分布式工作负载和通信算法参考实现”，而不是替换当前 die 内仿真平台。

推荐的长期结构是：

```text
Timeloop/Accelergy
  └─ 计算映射、tile 服务时间、边界访问
       ↓
gem5/HeteroGarnet
  └─ die 内 NoC、DMA、共享存储、有限缓冲、反压
       ↓ boundary flow
ASTRA-sim / SimCCL 思路
  └─ 多 die、多卡 collective、全局依赖
       ↓
ns-3 或其他外部网络后端
  └─ RDMA、交换机、拥塞、机柜网络
```

这套组合既保留了你需要的 die 内精度，也吸收了 SimAI 在大规模 AI 通信工作负载方面的经验。

## 参考资料

- [SimAI 官方仓库](https://github.com/aliyun/SimAI)
- [SimAI NSDI'25 论文与介绍](https://www.usenix.org/conference/nsdi25/presentation/wang-xizheng-simai)
- [AICB 官方仓库](https://github.com/aliyun/aicb)
- [SimCCL 官方仓库](https://github.com/aliyun/SimCCL)
- [SimCCL 与 SimAI 的 FlowModel 集成说明](https://github.com/aliyun/SimCCL/blob/master/docs/integration/integration-with-simai.md)
- [ns-3-alibabacloud 官方仓库](https://github.com/aliyun/ns-3-alibabacloud)
- [SimAI Workload / Tutorial](https://github.com/aliyun/SimAI/blob/master/docs/Tutorial.md)
