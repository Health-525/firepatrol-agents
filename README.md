<div align="center">

# 🔥 火巡智策 · FirePatrol

**森林火灾多智能体无人机调度仿真系统**

面向紫金山固定林区的多智能体(Multi-Agent)火情研判与 **2+4+2** 无人机资源调度仿真。
**LangGraph** 编排 6 个 Agent 协作,确定性规则引擎守护所有安全关键数字,
React + TypeScript 指挥大屏实时呈现态势地图、3D 地形、Agent 协作流与轮次演化。

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.141-009688?logo=fastapi&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-1.2-1C3C3C?logo=langchain&logoColor=white)
![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=black)
![TypeScript](https://img.shields.io/badge/TypeScript-5.6-3178C6?logo=typescript&logoColor=white)
![Three.js](https://img.shields.io/badge/Three.js-0.185-000000?logo=threedotjs&logoColor=white)

**规则算数字 · Agent 做研判 · 人类做审批**

</div>

---

## ✨ 核心特性

- 🤖 **6 Agent 协作架构** — 指挥官(协调者)+ 侦察研判 / 灭火调度 / 支援保障(对应 R/E/S 三子群角色)+ 仿真评估(裁判)+ 交互审批,主从拓扑 + 共享黑板,消息全量可回放
- 🧠 **每轮自主研判** — 执行中没有写死的 if-else 触发表:每轮由仿真评估 Agent 的 GLM 大脑对全量快照研判「继续 / 局部调整 / 重规划 / 终止」,LLM 不可用时保守降级兜底
- 🛡️ **安全数字不由 LLM 产出** — FLP 火线强度、SOC 电量模型、灭火能力、时间区间一律由 `rules/tools.py` 确定性计算,Agent 只调用并解释,LLM 输出经数字事后审计(无来源数字标 ⚠)
- 👨‍✈️ **Human-in-the-loop 审批门** — LangGraph `interrupt()` 硬阻塞:生成方案 ≠ 执行,approve 后才锁定资源;支持 adjust(带约束重规划)与 reject
- 🗺️ **指挥大屏** — 态势地图(FLP 热力 + 机队动画)、three.js 3D 地形(轨道控制)、Agent 协作消息流、轮次曲线、审批卡 / 报告卡、阶段步进器、追问对话
- 🚁 **有人/无人双分支支援** — 确认有人 → 通信广播 + 疏散指引(A* 坡度感知路径);确认无人 → 电池 / 药剂物流补给
- 📚 **经验知识库** — 风变 / 单机失能 / 多火点 / 通信退化等经验模式自动分块检索,运行时可查、判断留痕

## 🏗️ 系统架构

```text
                      ⑥ 交互审批 Agent —— 方案解释 · 审批门(interrupt)· 报告归档
                                  ▲ human-in-the-loop
        ┌──────────────── ① 指挥官 Agent(协调者)────────────────┐
        │           任务分解 · 分发 · 汇总仲裁 · 重规划路由          │
        │                                                      │
   ② 侦察研判 Agent      ③ 灭火调度 Agent        ④ 支援保障 Agent  │
   (R1–R2 角色)          (E1–E4 角色)            (S1–S2 角色)      │
   火情感知/环境/FLP      候选生成/硬约束          有人/无人分支      │
        └────────────── ⑤ 仿真评估 Agent(裁判) ──────────────────┘
             离散轮次仿真 · 评分 J · 净处置能力 · 每轮自主研判 judge_round
        ────────────────────────────────────────────────────────
        黑板 Blackboard: fire_state + fleet(2+4+2) + inventory + Agent 消息流
        规则引擎 Tool 层: FLP / SOC / 药剂兼容 / 硬约束 / 评分(确定性, 不可被 LLM 覆盖)
```

### 为什么选 LangGraph

| 候选框架 | 结论 |
|---|---|
| **LangGraph ✅** | 本业务 = 显式状态机 + 轮次环 + 人机审批门:原生 `interrupt()` 审批中断、`Command(resume)` 恢复、checkpointer 状态检查点(注:当前为内存级 MemorySaver,进程重启不保留,生产化需换 SqliteSaver);节点为纯 Python 函数,无 LLM 也能离线跑图 |
| AutoGen / AG2 | 对话驱动,适合自由协商;本系统拓扑固定、规则优先,对话发散不可控 |
| CrewAI | 角色协作原型快,但审批门 / 状态机控制粒度不够 |
| 自研总线 | 可行但重复造轮子,缺少检查点与中断语义 |

Agent 框架只负责"编排";**安全关键数字(FLP、SOC、灭火能力、时间区间)一律由 `backend/app/rules/tools.py` 确定性计算**,Agent 只调用并解释 —— 这是《无人机子群与参数规则 V1》的硬性原则。

## 🔄 业务链路(LangGraph StateGraph)

```text
START → 指挥官接警 → 侦察研判 → (灭火调度 ∥ 支援保障) → 仿真评估
  → 交互审批(interrupt 审批门)
      ├─ approve → 轮次执行 → 自主研判(judge_round)
      │                        ├─ continue → next_round → 轮次执行(5 min/轮, 1 min 步长折算)
      │                        ├─ replan   → 侦察研判(实时状态)→ 再次审批
      │                        └─ terminate/完成 → 报告归档 → END
      ├─ adjust(如"最多2架") → 侦察研判(带约束)
      └─ reject → 报告归档(资源释放)→ END
```

## 🧠 每轮自主研判(judge_round)

执行轮次里没有预设的触发表。每轮结束后,仿真评估 Agent 的 GLM 大脑拿到黑板全量快照(各格 FLP 与风况、增长趋势、每机 SOC/位置/药剂、库存、人员疏散进度、预期 vs 实际偏差),走「观察 → 定向 → 决策 → 留痕」四步,产出结构化判断:

```json
{
  "situation": "E3 连续两轮 SOC 降幅高于同任务 E2 约 40%,位置 2 轮未更新",
  "severity": "urgent",
  "evidence": ["E3 soc 62→48(1轮)", "同任务 E2 88→81"],
  "options": [
    {"action": "派 E4 补位,E3 召回检修", "pros": "压制能力不中断", "cons": "E4 周转余量变薄"},
    {"action": "继续观察一轮", "pros": "省一次周转", "cons": "若确为故障,损失两轮压制窗口"}
  ],
  "chosen": "派 E4 补位,E3 召回检修",
  "rationale": "单机异常的代价不对称:误判只损失一次周转,漏判损失两轮压制窗口",
  "expected": "下轮压制恢复至计划水平",
  "fallback": "若 E4 硬约束不过,降级为维持并上报缺口",
  "decision": "replan",
  "escalate": false
}
```

- **severity 四档**:`info` / `watch` / `urgent` / `critical`,优先级序为 **人员安全 > 通信连续 > 火势不扩大 > 资源节约**,拿不准往高一级处理
- **经验模式库**(`data/knowledge/`)覆盖风况突变、火势跳涨、单机失能、执行中新发现人员、多火点、通信退化、库存见底、疏散受阻、传感器矛盾等征象 —— 是经验素材而非代码分支,靠检索匹配、允许组合与超出
- **降级兜底**(`agents/judgment.py`):GLM 不可用 / 输出非法时回落保守规则(风档跳变重规划、有备份补位 / 无备份降级终止、断供提前补、冷却期内 urgent 让位 critical……),系统不中断、不裸奔
- 所有判断写入黑板消息流,前端可回放、可审计 —— "智能体现在哪"的答案就是这一串判断记录

## 📁 目录结构

```text
backend/app/
├─ agentkit/     # Agent 基类、黑板消息、GLM 大脑(OpenAI 兼容: GLM/DeepSeek/Ollama)
├─ agents/       # 6 个 Agent + LangGraph 任务图(graph.py) + 自主研判(judgment.py)
├─ rules/        # 确定性规则引擎: FLP/SOC/硬约束/离散仿真/评分 J + 疏散 A*/地形/知识检索
├─ domain/       # Pydantic 契约、黑板存储、场景预设
├─ services/     # MissionService: 后台驱动 LangGraph + 审批恢复
└─ main.py       # FastAPI: 任务闭环 API + SSE 事件流
frontend/src/    # React 18 + TypeScript + Vite 指挥大屏
├─ components/   # SitMap 态势地图 / Terrain3D 三维地形 / AgentPanel 协作流
│                # FleetPanel 机群 / RoundTimeline 轮次曲线 / PhaseStepper 阶段步进
│                # ApprovalCard 审批卡 / ReportCard 报告 / ChatPanel 追问对话
configs/         # V1 仿真参数(风档/耗电/补给周转/评分权重)
data/            # 紫金山场景、2+4+2 机队、库存、地形、经验知识库、视觉 fixture
tests/           # 规则数值断言 + 自主研判降级 + 疏散 + 端到端任务闭环
```

## 🚀 快速开始

**后端**(Python 3.11+):

```bash
python -m venv .venv
.venv\Scripts\activate            # PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
uvicorn backend.app.main:app --reload --port 8000
```

**前端**(另开终端):

```bash
cd frontend
npm install --registry=https://registry.npmmirror.com
npm run dev      # http://localhost:5173
```

接口文档(Swagger):`http://localhost:8000/docs`

<details>
<summary><b>💡 常见问题</b></summary>

- **端口被占(`errno 10048`)**:多半是之前遗留的 uvicorn 进程。`netstat -ano | findstr :8000` 找到 PID 后在任务管理器结束,或直接换端口 `--port 8001`。
- **改了 `configs/simulation.json` 不生效**:仿真配置启动时缓存,需重启后端。
- **升级依赖后**:依赖已按验证环境锁定版本(`requirements.txt`),升级包后请跑 `pytest tests/ -v` 回归。
- **知识库启动日志出现 `PDF 加载失败`**:说明缺 `pypdf`,论文内容会被跳过。

</details>

## 🎬 演示场景(内置)

| 场景 | 看点 |
|---|---|
| `standard` | 标准火情 B₀=108 FLP,有人区域 → 支援走"通信+指引"分支,双机轮换 + 换电补水 |
| `wind_shift` | 第 3 轮风速 5.2→6.8 m/s 跳档,FLP 抬升 → **自主研判触发重规划** → 二次审批 → 增援 |
| `no_people` | 确认无人 → 支援双机全走物流分支(送电池至前向补给点) |
| `overwhelmed` | 重大火情,净处置能力 C_net<0 → 输出**资源缺口**,拒绝虚假完成时间 |

## 📡 主要 API

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/health` `/api/agents` `/api/scene` `/api/scenarios` `/api/fleet` `/api/inventory` | 元信息 / 场景 / 机队 / 库存 |
| `GET` | `/api/terrain` `/api/knowledge` `/api/llm-status` | 地形网格 / 知识库 / LLM 连接状态 |
| `POST` | `/api/missions` | 建案 `{scenario}` |
| `GET` | `/api/missions/{id}` | 黑板全量快照 |
| `POST` | `/api/missions/{id}/approval` | 审批 `{decision: approve\|reject\|adjust, feedback, people_status}` |
| `GET` | `/api/missions/{id}/events` | SSE:`agent_message / snapshot / done` |
| `POST` | `/api/missions/{id}/chat` | 对当前任务追问(LLM 引用黑板数据回答) |

## 🔌 可选:接入 LLM 解释层

默认离线确定性运行。配置后 Agent 的解释性文字与每轮研判由 LLM 生成(数字仍出自规则引擎):

```bash
set FIREOPS_LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4   # GLM; 或 DeepSeek/Ollama 兼容地址
set FIREOPS_LLM_API_KEY=xxx
set FIREOPS_LLM_MODEL=glm-4-flash
```

连续失败 ≥2 次时前端徽标转红(⚠ GLM 降级 · 确定性模式),系统自动回落确定性文案,不中断。

## ✅ 测试

```bash
.venv\Scripts\python -m pytest tests/ -v
```

覆盖:FLP / κ / SOC / 评分数值断言与数字审计;自主研判在线解析与降级兜底(风档跳变、火势连涨、单机失能有/无备份、断供、冷却、人群被困);疏散 A*(绕火、坡度偏好、无路上报);端到端任务闭环(标准审批 → 轮次 → 报告、风变重规划、资源缺口、用户约束调整)。

## 📐 设计原则(继承《无人机子群与参数规则 V1》)

1. 三子群"2 侦察 + 4 灭火 + 2 支援"是**角色层级**,Agent 代表角色而非单机;无人机是资源库记录。
2. **测量归工具,判断归 Agent**:数字(SOC/FLP/风速/库存/时间)永远来自测量与仿真工具,Agent 只引用、不编造、不心算外推;数字*意味着什么、要不要改计划*由 Agent 研判,可执行性校验仍由规则引擎强制。
3. 生成方案 ≠ 执行:审批门(human-in-the-loop)是硬阻塞,批准后才锁定资源。
4. 网格面积用于展示、FLP 用于计算;时间由仿真输出区间或资源缺口,不输出无依据单点。
5. 每 5 分钟轮次刷新、每轮重新研判;`cannot_control` 时输出资源缺口,不给虚假完成时间。

## ⚠️ 当前简化与已知限制(诚实口径)

- **LLM 决策范围**:灭火调度的"出动规模策略"与每轮研判由 GLM 决定(失败回退确定性枚举 / 保守降级);其余节点的 LLM 为解释性研判。所有安全数字(FLP/SOC/时间)仍由规则引擎唯一产出,LLM 输出经数字事后审计(未见来源的数字会被标注 ⚠)。
- **环境观测流**:风速变化由每轮观测值驱动(带确定性抖动),研判基于观测而非全知;演示场景的风序为预设观测序列。
- **物理口径简化**:火场压制按各格 FLP 占比线性分摊;风档跳变对存量 FLP 的放大采用 K_wind 比例重标定;航线为直线距离(爬升耗电 f_climb 已按地形高差计入,未考虑地形遮蔽绕飞)。
- **时间模型**:5 分钟轮次,一轮一事(补给轮 / 换电轮不架次);墙钟估计按规则文档 11.3 的 ~20 分钟周转周期口径。
- **状态持久化**:黑板与任务状态在内存中,进程重启即清空(演示场景可接受;生产化需 SQLite checkpointer + 黑板落库)。
- **未来工作**:侦察覆盖分配(参考 DSP 分布式搜索)、MARL 学习型调度(参考多智能体强化学习路线)、地形遮蔽航线规划。

---

<div align="center">

**规则引擎守住安全底线,LLM Agent 贡献研判智能,人类握住最终决定权。**

</div>
