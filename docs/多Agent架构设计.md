# 火巡智策 · 多 Agent 架构设计 V1

> 参考 `MuQiuri3721/2026ican` 现有实现(单 Planner + Skill 链 + Tool 层)与《无人机子群与参数规则 V1》,
> 设计可演示、可扩展、安全数字不被 LLM 覆盖的多 Agent 协作架构。

---

## 1. 现状与差距

| 项 | 2026ican 现状 | 差距 |
|---|---|---|
| Agent 层 | `AnalysisPlanner`(固定模板)+ `PlanExecutor`,本质是单智能体 | 无多角色分工、无 Agent 间消息、无协作可视化 |
| Skill 层 | 12 个 Skill 串行链(fire_perception → … → report_archiving) | 串行链无法体现"侦察/灭火/支援"三子群分支决策 |
| Tool 层 | 规则 Tool(FLP、SOC、硬约束、评分、离散仿真) | 已符合"规则计算、智能体解释"原则,**保留不动** |
| 前端 | Vue 工作台(面板式:影像/环境/集群/日志/态势) | 无 Agent 协作可视化、无无人机动画、无轮次时间轴 |

**结论:Tool 层是资产,直接复用;把"单 Planner + Skill 链"重构为"多 Agent 分工 + 消息协作";前端升级为仿真指挥大屏。**

---

## 2. 推荐架构:6 个 Agent、三层结构

```text
┌─────────────────────────────────────────────────────────────┐
│  用户(审批人)                                                │
│    ▲ 方案解释 / 审批 / 调整            ▲ 报告归档             │
└────┬──────────────────────────────┬────────────────────────┘
     │ ⑥ 交互审批 Agent              │
┌────┴──────────────────────────────┴────────────────────────┐
│                     ① 指挥官 Agent(协调者)                  │
│      任务分解 · 分发 · 汇总仲裁 · 重规划触发 · 全局状态机      │
└──┬───────────────┬──────────────────┬──────────────────────┘
   │ ② 侦察研判 Agent│ ③ 灭火调度 Agent  │ ④ 支援保障 Agent
   │ (对应 R1–R2)   │ (对应 E1–E4)      │ (对应 S1–S2)
   │ 火情感知/环境/  │ 候选生成/硬约束/  │ 有人分支(通信广播指引)│
   │ FLP/有人无人    │ 评分/选最优       │ 无人分支(电池药剂物流)│
└───┴───────────────┴──────────────────┴──────────────────────┘
   │                    │ ⑤ 仿真评估 Agent(裁判)
   │                    │ 离散轮次仿真 / 评分 J / 偏差监控
┌──┴────────────────────┴─────────────────────────────────────┐
│  共享黑板(Blackboard):fire_state + fleet(2+4+2) + inventory  │
│  + environment + 事件流                 ← 所有 Agent 读写     │
├─────────────────────────────────────────────────────────────┤
│  规则引擎 Tool 层(确定性):FLP / SOC / 药剂兼容 / 评分 / 仿真   │
│  ⚠ 安全关键数字只能出自规则引擎,Agent 只选择与解释             │
└─────────────────────────────────────────────────────────────┘
```

### 为什么是 6 个,不是 8 个(每架无人机一个)

规则文档明确:"三个子群是**角色层级**,子群内部包含多架状态不同的虚拟无人机";
"每架无人机都必须是资源库中的一条独立记录,**智能体只负责选择与解释**"。

→ **Agent 代表"角色/子群",不代表单架飞机**。E1–E4 是 4 条资源记录,由灭火调度 Agent 统一枚举组合;
若每架飞机一个 Agent,只是把规则查表包装成 8 次 LLM 调用,增加延迟与不确定性,毫无收益。

### 每个 Agent 的职责

| # | Agent | 对应 Skill(复用现有) | 调用的 Tool | 输出 |
|---|---|---|---|---|
| ① | **指挥官 Commander** | (新增,替代 planner.py) | — | 任务分解、消息路由、冲突仲裁、重规划决策 |
| ② | **侦察研判 Recon** | fire_perception / environment_assessment / fire_assessment / people_assessment | PWM-Net 检测、环境读取、FLP 计算 | `fire_state.json`(强度/类型/FLP/增长率/人员状态) |
| ③ | **灭火调度 Suppression** | candidate_generation / constraint_filtering / dispatch_scoring / route_planning | SOC_need、载荷、药剂兼容、枚举、评分 J | 候选方案集(1–4 架 E 组合 + 药剂 + 补给批次) |
| ④ | **支援保障 Support** | people_assessment 的分支 + 新增 logistics | 库存、换电、水源先决校验 | S 子群任务分配(有人分支/无人分支)、补给计划 |
| ⑤ | **仿真评估 Simulator** | closed_loop_monitoring 前置 | 离散轮次仿真(5 min 步进) | 方案排名、预计时间区间、可行性 can/maintain/cannot、资源缺口 |
| ⑥ | **交互审批 Approver** | approval_preparation / report_archiving | — | 面向用户的方案解释、审批流、最终报告 |

---

## 3. 协作机制

### 3.1 拓扑:主从(Orchestrator–Workers)+ 黑板 + 事件触发

- **主从**:指挥官是唯一的任务分发者,专业 Agent 之间不直接通话,避免对话发散、便于审计。
- **黑板**:共享状态库(现有 `AnalysisStore` 扩展),Agent 产出结构化 JSON 写入,其他 Agent 订阅读取。
- **事件触发重规划**:规则文档第 10 节的关键事件(FLP↑>20%、风速换档、返航 SOC<25%、药剂不足、
  通信低于阈值、新发现人员、用户改时限)由仿真评估 Agent 发布,指挥官订阅后进入重规划环。

### 3.2 消息协议(黑板上的事件流)

```json
{"type": "TASK_ASSIGN",   "from": "commander", "to": "recon",       "task_id": "T-001", "goal": "研判 fire_01"}
{"type": "FINDING",       "from": "recon",     "to": "commander",   "data": "fire_state.json 引用 + 摘要"}
{"type": "PLAN_PROPOSAL", "from": "suppression","to": "commander",  "candidates": [...], "best": "P-03"}
{"type": "SIM_RESULT",    "from": "simulator", "to": "commander",   "ranking": [...], "feasibility": "can_control"}
{"type": "APPROVAL_REQ",  "from": "approver",  "to": "human",       "plan": "P-03", "explain": [...]}
{"type": "APPROVAL_DECISION", "from": "human", "to": "commander",   "decision": "approve|reject|adjust"}
{"type": "EXEC_ROUND",    "from": "simulator", "to": "blackboard",  "round": 4, "flp": 38.2, "soc": {...}}
{"type": "REPLAN_TRIGGER","from": "simulator", "to": "commander",   "reason": "风速升至 6–8 m/s 档"}
{"type": "REPORT_READY",  "from": "approver",  "to": "human",       "report_id": "R-001"}
```

每条消息带 `task_id / timestamp / source`,前端直接渲染为 Agent 协作时间线。

### 3.3 一次完整火情的协作时序

```text
用户上传影像/选定地点
  → ①指挥官:建任务 T-001,派研判任务 → ②
②侦察研判:调 PWM-Net fixture + 环境 + FLP Tool → 写 fire_state → FINDING 回 ①
  → ①指挥官:并行派单(③④ 同时开工)
③灭火调度:枚举 E1–E4 组合 → 硬约束过滤 → 生成候选 P-01..P-05
④支援保障:依据 people_status 选有人分支(通信+指引)或无人分支(送电池/药剂)
  → ①指挥官:候选打包 → ⑤
⑤仿真评估:逐方案离散轮次仿真(1 min 步长/5 min 轮次)→ 评分 J → 排名 + 时间区间
  → ①指挥官:取最优 + 次优 → ⑥
⑥交互审批:用自然语言解释关键数字来源(为什么 3 架、为什么水剂、为什么 38–45 min)
  → 用户 approve(资源加锁)→ ① → ⑤ 驱动执行仿真
⑤执行中按轮次发布 EXEC_ROUND;每轮结束由 GLM 大脑自主研判(继续/重规划/终止, 无固定触发表)
执行中单机机电失能 → ⑤ 立即补位决策(方案内换机, 不过审批门) → 研判裁决方案是否仍成立
触发关键事件 → REPLAN_TRIGGER → ① 回到 ②/③/④
  → FLP 归零/研判终止 → 全员返航回收(RECOVERY) → ⑥ 归档报告 → 任务闭环
```

### 3.4 安全原则(继承规则文档)

1. LLM Agent **不产生**任何安全关键数字(FLP、SOC、灭火能力、时间区间),只调用规则 Tool 并解释;
2. 硬约束(药剂兼容、返航 SOC≥25%、载荷上限)在 Tool 层强制,**Agent 的输出若违反直接判废**;
3. 生成方案 ≠ 执行:审批门是硬阻塞(human-in-the-loop);
4. `cannot_control` 时输出资源缺口,不给虚假完成时间。

---

## 4. 技术选型

| 项 | 推荐 | 理由 |
|---|---|---|
| Agent 框架 | **LangGraph**(Python) | 业务本身就是状态机+轮次+重规划环;原生支持 human-in-the-loop 中断(审批门)、checkpointer(任务续跑);Tool 直接映射现有 `ToolRegistry` |
| 备选 | 自研消息总线(FastAPI + asyncio.Queue) | 零新依赖,演示够用;但审批中断、状态恢复要自己写 |
| LLM 接入 | 每个 Agent 一个 system prompt + GLM/Qwen,`tool_choice` 强制走 Tool | 与框架无关,可整体替换 |
| 前端 | Vue 3(已有)+ Canvas 地图 + SSE 推送 | 见下节 |

### 与现有代码的迁移映射

```text
backend/app/agents/planner.py        → ① 指挥官 Agent 的任务分解器(LLM Planner 化)
backend/app/agents/plan_executor.py  → ① 的执行器 + 消息路由
backend/app/skills/orchestrator.py   → 拆解:前 4 个 Skill 归 ②,中 4 个归 ③④,monitoring 归 ⑤
backend/app/skills/*.py 其余         → ⑥(审批+报告)
backend/app/tools/*                  → 原样保留,注册为 Agent 可调用 Tool
backend/app/domain/store.py          → 扩展为黑板(加事件流 + 订阅)
```

---

## 5. 仿真前端设计(指挥大屏)

在现有 Vue 工作台基础上新增四个核心区块,数据全部来自黑板事件流(SSE / WebSocket 推送):

### 5.1 态势地图(核心)
- Canvas/SVG 绘制:100 m² 网格、FLP 热力渐变(火势可视化)、水源、道路/出口、基地、前向补给点;
- **8 架无人机实时动画**:按方案路线移动,图标区分子群(R 蓝/E 红/S 绿),
  悬浮显示 SOC/载荷/状态,状态机着色(flying/working/returning/servicing/charging);
- 5 分钟一轮的"回合感":轮次推进时火情网格颜色随 B_(t+Δt) 演化、被扑灭网格褪色。

### 5.2 Agent 协作面板(多 Agent 的"看得见")
- 左侧 Agent 头像列表(①–⑥,对应三子群配色);右侧消息流:
  `②侦察研判 → ①指挥官:fire_01 强度 2,FLP=60,人员状态 unknown,建议复核`;
- 消息类型图标(派单/发现/方案/仿真/审批/重规划),点击消息可展开底层 Tool 调用与 JSON 数据 ——
  **这是评委看到"多 Agent 协作"的最直接窗口**。

### 5.3 轮次时间轴 + 曲线
- 横轴仿真时间(1 min 步长、5 min 轮次刻度);曲线:总 FLP、各机 SOC、水剂库存、预计 vs 实际偏差;
- 关键事件打点(换电、补给、重规划触发),与地图动画联动(点时间轴任意时刻回放)。

### 5.4 审批与报告
- 方案对比卡:最优 vs 备选(架次/药剂/J 得分/时间区间/风险等级),关键数字标注来源("由规则引擎计算");
- approve / reject / adjust(调整后走重规划)/ terminate;
- 任务结束弹出报告视图(输入、方案版本、审批记录、轮次、消耗、结论)。

### 5.5 前端数据契约(新增 SSE 通道)

```text
GET /api/tasks/{id}/events        # SSE:AGENT_MESSAGE / ROUND_UPDATE / STATE_CHANGE / REPLAN
GET /api/tasks/{id}/snapshot      # 黑板全量(fire_state + fleet + inventory + plan)
POST /api/tasks/{id}/approval     # approve|reject|adjust|terminate
```

---

## 6. 落地步骤(建议顺序)

1. **第 1 步**:黑板 + 事件流(扩展 AnalysisStore),Agent 消息协议定稿;
2. **第 2 步**:6 个 Agent 骨架(可先用确定性桩,行为等价现有 Skill 链),指挥官跑通"研判→方案→仿真→审批"消息环;
3. **第 3 步**:接 LangGraph:审批中断、轮次 checkpointer、重规划环;
4. **第 4 步**:前端大屏四区块(先地图动画 + Agent 消息流,再曲线与报告);
5. **第 5 步**:LLM 逐 Agent 接入(prompt + tool_choice),演示脚本对齐《无人机子群与参数规则》第 11 节算例
   (B₀=60、G=6、两机轮换、38–45 min 区间)作为验收用例。

---

## 7. 一句话总结

**6 个 Agent(指挥官 + 侦察研判/灭火调度/支援保障三个子群角色 + 仿真裁判 + 交互审批),
主从拓扑 + 黑板 + 事件触发重规划;规则引擎继续掌管所有安全数字;
前端做成带地图动画与 Agent 消息流的指挥大屏。**
