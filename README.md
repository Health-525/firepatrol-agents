# 火巡智策 · 森林火灾多智能体无人机调度仿真系统

> 面向紫金山固定林区的多智能体(Multi-Agent)火情研判与 2+4+2 无人机资源调度仿真。
> **LangGraph** 编排 6 个 Agent 协作;确定性规则引擎守护所有安全关键数字;React+TS 指挥大屏实时呈现态势地图、Agent 协作流与轮次演化。

## 系统架构

```text
                      ⑥ 交互审批 Agent —— 方案解释 · 审批门(interrupt) · 报告归档
                                  ▲ human-in-the-loop
        ┌──────────────── ① 指挥官 Agent(协调者)────────────────┐
        │           任务分解 · 分发 · 汇总仲裁 · 重规划触发        │
        │                                                      │
   ② 侦察研判 Agent      ③ 灭火调度 Agent        ④ 支援保障 Agent  │
   (R1–R2 角色)          (E1–E4 角色)            (S1–S2 角色)      │
   火情感知/环境/FLP      候选生成/硬约束          有人/无人分支      │
        └────────────── ⑤ 仿真评估 Agent(裁判) ──────────────────┘
                  离散轮次仿真 · 评分 J · 净处置能力 · 触发重规划
        ────────────────────────────────────────────────────────
        黑板 Blackboard: fire_state + fleet(2+4+2) + inventory + Agent 消息流
        规则引擎 Tool 层: FLP / SOC / 药剂兼容 / 硬约束 / 评分(确定性, 不可被 LLM 覆盖)
```

### 为什么选 LangGraph

| 候选框架 | 结论 |
|---|---|
| **LangGraph ✅** | 本业务=显式状态机+轮次环+人机审批门:原生 `interrupt()` 审批中断、`Command(resume)` 恢复、checkpointer 状态检查点(注: 当前为内存级 MemorySaver, 进程重启不保留, 生产化需换 SqliteSaver);节点为纯 Python 函数,无 LLM 也能离线跑图 |
| AutoGen / AG2 | 对话驱动,适合自由协商;本系统拓扑固定、规则优先,对话发散不可控 |
| CrewAI | 角色协作原型快,但审批门/状态机控制粒度不够 |
| 自研总线 | 可行但重复造轮子,缺少检查点与中断语义 |

Agent 框架只负责"编排";**安全关键数字(FLP、SOC、灭火能力、时间区间)一律由 `backend/app/rules/tools.py` 确定性计算**,Agent 只调用并解释 —— 这是《无人机子群与参数规则 V1》的硬性原则。

## 业务链路(LangGraph StateGraph)

```text
START → 指挥官接警 → 侦察研判 → (灭火调度 ∥ 支援保障) → 仿真评估
  → 交互审批(interrupt 审批门)
      ├─ approve → 轮次执行 ──┬─ next_round → 轮次执行(5 min/轮, 1 min 步长折算)
      │                        ├─ replan(风档跳变/FLP↑>20%/SOC<25%) → 侦察研判(实时状态)→ 再次审批
      │                        └─ done → 报告归档 → END
      ├─ adjust(如"最多2架") → 侦察研判(带约束)
      └─ reject → 报告归档(资源释放)→ END
```

## 目录

```text
backend/app/
├─ agentkit/     # Agent 基类、黑板消息、可选 LLM 解释层(OpenAI 兼容: GLM/DeepSeek/Ollama)
├─ agents/       # 6 个 Agent + LangGraph 任务图(graph.py)
├─ rules/        # 确定性规则引擎(FLP/SOC/硬约束/离散仿真/评分 J)
├─ domain/       # Pydantic 契约、黑板存储、场景预设
├─ services/     # MissionService: 后台驱动 LangGraph + 审批恢复
└─ main.py       # FastAPI: 任务闭环 API + SSE 事件流
frontend/src/    # React + TypeScript 指挥大屏
├─ components/   # SitMap 态势地图 / AgentPanel 协作流 / FleetPanel 机群
│                # RoundTimeline 轮次曲线 / ApprovalCard 审批卡 / ReportCard 报告
configs/         # V1 仿真参数(风档/耗电/评分权重/触发阈值)
data/            # 紫金山场景、2+4+2 机队、库存、视觉 fixture
tests/           # 规则数值断言 + 端到端任务闭环(审批/重规划/资源缺口)
```

## 快速开始

后端(Python 3.11+,D 盘 venv):

```bash
python -m venv .venv
.venv\Scripts\activate            # PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
uvicorn backend.app.main:app --reload --port 8000
```

前端(另开终端):

```bash
cd frontend
npm install --registry=https://registry.npmmirror.com
npm run dev      # http://localhost:5173
```

Swagger: `http://localhost:8000/docs`

> **常见问题**
> - 端口被占(`errno 10048`): 多半是之前遗留的 uvicorn 进程。`netstat -ano | findstr :8000` 找到 PID 后在任务管理器结束,或直接换端口 `--port 8001`。
> - 改了 `configs/simulation.json` 不生效: 仿真配置启动时缓存, 需重启后端。
> - 依赖已按验证环境锁定版本(`requirements.txt`), 升级包后请跑 `pytest tests/ -v` 回归。
> - 知识库启动日志若出现 `PDF 加载失败`, 说明缺 `pypdf`, 论文内容会被跳过。

## 演示场景(内置)

| 场景 | 看点 |
|---|---|
| `standard` | 标准火情 B₀=108 FLP,有人区域 → 支援走"通信+指引"分支,双机轮换+换电补水 |
| `wind_shift` | 第 3 轮风速 5.2→6.8 m/s 跳档,FLP 抬升 → **强制重规划** → 二次审批 → 增援 |
| `no_people` | 确认无人 → 支援双机全走物流分支(送电池至前向补给点) |
| `overwhelmed` | 重大火情,净处置能力 C_net<0 → 输出**资源缺口**,拒绝虚假完成时间 |

## 主要 API

```text
GET  /api/health | /api/agents | /api/scene | /api/scenarios | /api/fleet | /api/inventory
POST /api/missions                     # 建案 {scenario}
GET  /api/missions/{id}                # 黑板全量快照
POST /api/missions/{id}/approval       # 审批 {decision: approve|reject|adjust, feedback, people_status}
GET  /api/missions/{id}/events         # SSE: agent_message / snapshot / done
```

## 可选: 接入 LLM 解释层

默认离线确定性运行。配置后 Agent 的"解释性文字"由 LLM 生成(数字仍出自规则引擎):

```bash
set FIREOPS_LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4   # GLM; 或 DeepSeek/Ollama 兼容地址
set FIREOPS_LLM_API_KEY=xxx
set FIREOPS_LLM_MODEL=glm-4-flash
```

## 测试

```bash
.venv\Scripts\python -m pytest tests/ -v
```

覆盖: FLP/κ/SOC/评分数值断言;标准闭环(审批→轮次→报告);风档突变重规划;重大火情资源缺口;用户约束调整。

## 设计原则(继承《无人机子群与参数规则 V1》)

1. 三子群"2 侦察 + 4 灭火 + 2 支援"是**角色层级**,Agent 代表角色而非单机;无人机是资源库记录。
2. Agent 只负责选择与解释,可执行性判断全部由规则引擎完成,LLM 不得覆盖安全数字。
3. 生成方案 ≠ 执行:审批门(human-in-the-loop)是硬阻塞,批准后才锁定资源。
4. 网格面积用于展示、FLP 用于计算;时间由仿真输出区间或资源缺口,不输出无依据单点。
5. 每 5 分钟轮次刷新;关键事件(风档跳变/FLP↑>20%/返航 SOC<25%/药剂不足)强制重规划。

## 当前简化与已知限制(诚实口径)

- **LLM 决策范围**: 灭火调度的"出动规模策略"由 GLM 决定(输出改变候选生成, 失败回退全枚举); 其余节点的 LLM 为解释性研判。所有安全数字(FLP/SOC/时间)仍由规则引擎唯一产出, GLM 输出经数字事后审计(未见来源的数字会被标注 ⚠)。
- **环境观测流**: 风速变化由每轮观测值驱动(带确定性抖动), 观测到风档跳档才触发重规划; 演示场景的风序为预设观测序列。
- **物理口径简化**: 火场压制按各格 FLP 占比线性分摊; 风档跳变对存量 FLP 的放大采用 K_wind 比例重标定; 航线为直线距离(爬升耗电 f_climb 已按地形高差计入, 未考虑地形遮蔽绕飞)。
- **时间模型**: 5 分钟轮次, 一轮一事(补给轮/换电轮不架次); 墙钟估计按规则文档 11.3 的 ~20 分钟周转周期口径。
- **GLM 降级**: 连续失败 ≥2 次时前端徽标转红(⚠ GLM 降级·确定性模式), 系统自动回落确定性文案, 不中断。
- **状态持久化**: 黑板与任务状态在内存中, 进程重启即清空(演示场景可接受; 生产化需 SQLite checkpointer + 黑板落库)。
- **未来工作**: 侦察覆盖分配(参考 DSP 分布式搜索)、MARL 学习型调度(参考多智能体强化学习路线)、地形遮蔽航线规划。
