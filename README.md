# 船舶火灾智能损管系统

Vue 3 + Three.js + LSTM + FDS 的舰船火灾损管仿真系统。五个舱室的火灾演化由 **FDS 火灾动力学仿真**产出的真实发展过程训练而来，不是经验公式外插。

> 舰艇：海鹰级护卫舰 FFG-572，全长 115m
> 舱室：主机舱、电站间、灶炉间、士兵住舱、机库

---

## 这个系统在解决什么问题

### 损管要回答的不是"温度会是多少"

值班员在火场里真正要判断的是三件事：

- **这个舱还能扑吗？** —— 决定投入多少灭火力量
- **要不要撤人？** —— 舱内能见度与毒性气体
- **要不要封隔壁？** —— 火灾会不会蔓延过去

这三个问题的共同点是：**它们问的是"接下来会不会越过某条线"，而不是"现在多少度"。**
要回答"会不会越过"，模型必须学到**状态如何演化成下一状态**，而不是复述一条背熟的曲线。

### 常见做法为什么解决不了

火灾演示里的温度曲线大多是写死的：起火、爬升、到峰值、慢慢降。这条曲线本身没错，
错在它**没有区分火情**。我们把这件事量化过：

**证据一：换火势，模型不响应。**
用增量目标重训的模型，喂进 52℃ / 73℃ / 83℃ / 92℃ / 111℃ 五种起始温度，
模型输出**全部是 253.9℃**——一个常数。它学到的是训练数据里的平台温度，
不是"火怎么长"。

**证据二：线上自回归会塌缩成常数。**
换 v6 配方之前，机库 / 士兵住舱 / 电站间 用的是 v3 模型（主机舱是 v2）。
用同一套回归工具重测（`LSTM_MODEL_ROOTS=models_v3,models_v2` 即可复现）：

| 舱室 | 300 秒自回归 MAE | 物理不可能点 | 300 秒末预测 | 真实值 |
|---|---|---|---|---|
| 机库 | 266.84℃ | 46,931 / 48,000 | 453.5℃ ±7.1 | 189.4℃ ±65.1 |
| 士兵住舱 | 165.12℃ | 60,838 / 84,000 | 239.4℃ ±11.3 | 60.4℃ ±12.6 |
| 电站间 | 327.15℃ | **84,000 / 84,000** | 379.5℃ **±0.0** | 52.7℃ ±10.4 |

**合计 216,000 个预测点里 191,769 个（88.8%）物理上不可能**——判据是温度
偏离当前火势锚点超过 150℃、或 CO₂ 超过 30%、或数值非有限。

看电站间那行：**±0.0℃ 的完美常数**。曲线看着平滑、收敛、不报警，
值班员会当成结论——但它和真值差了 327℃。这种输出比没有数字更危险，
因为它看起来像是"算过了"。

换成 v6 配方后，这五个舱的**物理不可能点全部归零**（见 §1）。

> **怎么复现这张基线表**（不需要动 `models_v6/` 目录）：
> ```bash
> # 终端 1：强制服务加载旧配方
> set LSTM_MODEL_ROOTS=models_v3,models_v2
> py -3 -u lstm-prediction-server\server.py
>
> # 终端 2：跑回归
> py -3 tools\regress_online.py 机库 士兵住舱 电站间
> ```
> 结果落在 `tools/v3_baseline_regress.txt`。去掉环境变量重启即恢复 v6。

**证据三：训练数据本身没有火灾过程。**
原始 PyroSim 工况是 `HRRPUA=4000` 且**没有任何 `RAMP_Q`**——火焰在 t=0 就是满功率。
结果是温度在 **0.3 秒内**从 20℃ 冲到 415℃，四个舱全部如此。
模型面对这种数据只能学出一个阶跃函数，于是复现出最初那个老问题：
仿真开局喂 20℃，模型一步跳到四百多度。

### 本项目的做法

把整条链路换成可验证的：

```
FDS 场模拟          多工况火灾发展轨迹      Seq2Seq LSTM        损管态势预测
（真物理）    →     起火→增长→充分   →    学"状态→演化"  →    标出哪段可信
  6.10.1               发展→衰减              增量目标              哪段是外推
```

三个要素缺一不可，每个都有对应的失败实验记录（见 §4）：

1. **释热按 IMO FTP t² 斜坡增长** —— 否则数据里没有"增长段"
2. **温度测舱室上层烟气层，不测羽流核心** —— 否则测的是火焰不是舱室
3. **目标必须是增量而非绝对值** —— 否则 MSE 最优解是常数
4. **多工况训练** —— 单工况 22.00℃ vs 多工况 3.81℃（A/B 对照，测试集是模型完全没见过的火情）

### 怎么证明它真的学到了东西

这是整套东西里最重要的一环。**不给出证据的"智能预测"和写死的曲线没有区别。**

- **留一工况交叉验证**：训练时把一整个火情（不同释热量、不同门宽）完全剔除，
  训练完在它身上评估。损管实况就是如此——你永远不会遇到和训练集一模一样的火。
- **端到端回归**（`tools/regress_online.py`）：拿真实 FDS 轨迹打 HTTP 接口，
  和真值逐点比对。结果见 §1。
- **逐点标注可信度**：模型只能可靠地推 30 秒（一次前向的长度），
  之后是累积外推。返回值里每一点都带 `basis` 和 `extrapolated` 字段，
  **超出可靠时域的部分会明确标出来**，不会把外推冒充成预测。
- **降级标记**：某个舱室的模型不可信时，`/api/lstm/health` 会把它列进
  `degraded_compartments`，前端显示红色横幅。

### 这套东西不做什么

它不做 CFD 替代，不做真实火场指挥决策。模型只在训练工况覆盖到的
火情范围内可信，出了这个范围它会**明说**，而不是编一个看起来合理的数。

---

## 1. 实测精度

留一工况交叉验证：训练时完全剔除某个火情（不同释热量/门宽），训练完在它身上评估。这正是损管实况——你永远不会遇到和训练集一模一样的火。

| 舱室 | 容积 | FDS 峰温 | 工况数 | 留一验证 MAE | 线上 300s 自回归 MAE | 0~30s | 120~300s |
|---|---|---|---|---|---|---|---|
| 灶炉间 | 193 m³ | 252℃ | 4 | 6.07℃ | 25.29℃ | **2.48℃** | 30.78℃ |
| 机库 | 1,404 m³ | 220℃ | 4 | 4.16℃ | 19.95℃ | **1.48℃** | 24.68℃ |
| 士兵住舱 | 552 m³ | 74℃ | 5 | 2.76℃ | 4.94℃ | **1.60℃** | 5.62℃ |
| 电站间 | 460 m³ | 60℃ | 6 | 1.11℃ | 4.44℃ | **0.90℃** | 5.17℃ |
| 主机舱 | 1,423 m³ | 135℃ | 5 | 4.70℃ | 12.10℃ | **2.72℃** | 14.60℃ |

容积与 FDS 峰温取自 `config/compartments.json` 与 `tools/fds/train30/*.csv`，是实测值不是估计。

**怎么读这张表**：

- **0~30 秒**是模型单次前向的可靠区间（seq=60 @ 0.5s = 30 秒），误差 0.9~2.7℃。这是可信的数字。
- **120~300 秒**是**累积外推**，误差劣化到 5~31℃。返回值里逐点标了 `basis` 和 `extrapolated`，超出可靠时域的部分是灰的，不会把外推冒充成模型输出。
- 300 秒后的趋势交给 SOLAS II-2 舱室火灾增长律外推，**明确标注来源**。

复现这张表：

```bash
py -3 tools/regress_online.py          # 拿真实 FDS 轨迹打 5001 与真值逐点比对
py -3 tools/selftest_server.py         # 服务自检，21 项
```

---

## 2. 架构

三个进程，端口固定：

| 服务 | 端口 | 技术 | 职责 |
|---|---|---|---|
| 前端 | 5182 | Vue 3 + Three.js + Vite | 三维舰体、舱室内部、火焰渲染、态势面板 |
| 后端 | 3001 | Node + Express + Sequelize | 舱室数据、告警、火灾控制指令、驱动演化引擎 |
| LSTM 服务 | 5001 | Python + Flask + PyTorch | 模型加载、火灾演化推理、可信度标注 |

```
浏览器 ──5182──> Vite ──3001──> Express ──5001──> Flask/LSTM
                    │                             ▲
                    └──> Three.js 渲染           └──> models_v6/*.pth
```

演化链路的推进方式：后端每 1 秒按**实际经过的墙钟时间**向 5001 请求一步，5001 用该舱室的真实火灾轨迹作为状态基准，模型在其上做前向推理，返回温度/CO/CO₂。返回值写回数据库，前端轮询展示。

---

## 3. 快速开始

三个服务，端口固定 **5182 / 3001 / 5001**，按 LSTM → 后端 → 前端的顺序起。

> **不需要装 FDS。** FDS 只在你要**重新生成训练数据**时才用得上（见 §6）。
> `models_v6/` 和 `tools/fds/train30/` 都已随仓库提供，克隆下来直接能跑。
> 模型文件没有伪装成数据——`models_v6/*/best_model.pth` 是训练产物，
> `tools/fds/train30/*.csv` 是 FDS 仿真产出后转换的结果。

### 3.1 前置：MySQL

后端启动时会 `sequelize.authenticate()`，**连不上数据库直接 `process.exit(1)`**。
所以这是硬前置，不是可选。

```bash
# 建库
mysql -u root -p -e "CREATE DATABASE boat_fire_system
                     DEFAULT CHARACTER SET utf8mb4
                     COLLATE utf8mb4_unicode_ci;"

# 配置连接（复制模板后按实际情况改）
copy backend\.env.example backend\.env
```

`backend/.env` 关键项：

```ini
PORT=3001
DB_HOST=localhost
DB_PORT=3306
DB_NAME=boat_fire_system
DB_USER=root
DB_PASSWORD=你的密码
```

### 3.2 前端配置

```bash
copy config\.env.example config\.env.development
```

⚠️ 模板里 `VITE_LSTM_BASE_URL` 必须是 **5001**（与 `server.py` 的 `app.run(port=5001)` 一致）。
写成 5000 会让健康检查一直失败、预测全部走降级，而且**不报任何错**。

AI 分析功能需要 `VITE_AI_API_KEY`（GLM 智谱 或 通义千问）。**不填也能跑**，
只是 AI 面板会走降级分析。

### 3.3 安装依赖

```bash
# 前端
pnpm install

# 后端（独立目录、独立 lockfile）
cd backend && pnpm install && cd ..

# Python 侧
# ⚠️ 不要用仓库里的 requirement.txt —— 那是整份 Anaconda freeze
#    （500+ 包，含 jupyter/streamlit/spyker），装一遍要几十分钟。
# 实际只需要这六个：
pip install "torch>=2.5" "flask>=2.2" flask-cors \
            "numpy>=1.26" "pandas>=2.2" "scikit-learn>=1.2" joblib
```

> ⚠️ **前后端是两个独立的 Node 工程，必须分别安装。**
> 根目录的 `pnpm install` 只装前端（vite 等），**不含** `sequelize` / `mysql2` ——
> 这两个声明在 `backend/package.json` 里。不单独装后端的话，启动时会报
> `Cannot find module 'sequelize'`。
>
> 两边都用 pnpm，别一边 pnpm 一边 npm —— 会装出两份依赖树，
> `pnpm-lock.yaml` 与 `package-lock.json` 也会互相打架。

### 3.4 初始化数据库

```bash
cd backend && npm run init-db && cd ..
```

这一步会 **`sync({ force: true })` 删表重建**，再灌入一艘默认船和五个舱室。
⚠️ 会清空现有数据，只在首次部署时跑。

### 3.5 启动三个服务

开三个终端，按顺序：

```bash
# 终端 1 —— LSTM 服务
py -3 -u lstm-prediction-server\server.py
```

```bash
# 终端 2 —— 后端
cd backend && node src/app.js
```

```bash
# 终端 3 —— 前端
npx vite --port 5182 --strictPort
```

### 3.6 确认起来了

别光看窗口没报错，按下面逐条验：

```bash
# ① LSTM：应列出 6 个舱室（含"炉灶间"是"灶炉间"的别名），degraded 为空
curl -s http://localhost:5001/api/lstm/health

# ② 后端：应返回 {"status":"ok",...}
curl -s http://localhost:3001/health

# ③ 数据库：应返回 5 个舱室
curl -s http://localhost:3001/api/compartments
```

然后浏览器打开 **http://localhost:5182**。

在"舱室列表"里任选一个舱室点起火，右侧"态势预测"面板的温度应该在几十秒内
爬升到 70~100℃ 区间。**如果温度一直卡在基值不动**，见 §9 排障表。

### 3.7 可选：跑一遍自检

```bash
py -3 tools/selftest_server.py     # 21 项，验解码/网格/可信区间/降级标记
py -3 tools/regress_online.py      # 拿真实 FDS 轨迹打服务，与真值逐点比对（约 5 分钟）
```

---

## 4. 三条不可省的设计约束

这三条是踩过坑才定下来的，每一条都有实测支撑。**改动训练管线前请先读完这一节。**

### 4.1 释热必须用 IMO FTP t² 斜坡

PyroSim 原始工况是 `&SURF HRRPUA=4000.0` 且没有任何 `RAMP_Q`——火焰在 **t=0 就是满功率**。结果是温度在 **0.3 秒内**从 20℃ 冲到 415℃，四个舱全部如此。训练数据里根本不存在"火灾增长段"，模型只能学出一个阶跃函数。

配套的两点：
- **HRRPUA 单位是 kW/m²**，不是总量。FDS 手册 p.346 的 `HRR_PER_UNIT_AREA` 定义如此。
- **必须用 `Q_BURNER` 面积分测量**（`&DEVC QUANTITY='HRRPUA', SURF_ID='FIRE', SPATIAL_STATISTIC='SURFACE INTEGRAL'`）。`hrr.csv` 的 `HRR` 列在规定释热场景下不可靠——我们实测它与规格对不上，而面积分测出来的 600.0 kW 与 IMO FTP 曲线严格吻合。

### 4.2 温度必须测舱室上层烟气层

原热电偶在火源正上方 0.6m，测的是火焰羽流，瞬时饱和。改成 `TL1..TL9` 上层烟气温度阵列求平均（净高 90% 处）后，才拿到有意义的舱室温度。

### 4.3 目标必须是增量，不能是绝对值

这是**最反直觉的一条**。用绝对温度作目标时，MSE 的最优解是条件均值。实测：单轨迹模型喂 52/73/83/92/111℃ 五种输入，**全部输出 253.9℃**（平台温度），完全无响应。

改成预测增量（`target=delta`）后，零增量是中性答案，模型才真正学到动力学。服务端解码要连做两步还原：

```python
pred_scaled = (raw_output * delta_std) + input_scaled[-1]      # 先加回输入末值
prediction  = pred_scaled * channel_std + channel_mean        # 再还原物理量
```

漏掉任何一步都会得到 0℃ / 40% CO₂ 这种物理上不可能的数。

> **A/B 对照实验**（测试集是模型完全没见过的工况）：
> 单工况 MAE **22.00℃** → 多工况 MAE **3.81℃**，好 5.8 倍。
> 这推翻了"多工况更不敏感"的旧结论——那次测的是阶跃数据，模型里根本没有可学的信号。

---

## 5. 已知限制（如实说明）

这些是实测出来的，不是推测。

**① 可靠预测时长只有 30 秒。** 上面表格已经量化：30 秒内误差 0.9~2.7℃，之后逐段劣化。系统会在返回值里如实标注哪些点超出了可靠时域。

**② 逐拍读数有 ±10~15℃ 抖动。** 灶炉间最明显——它在起火最陡的 30 秒内温度变化最快，模型在这个区段的逐拍预测噪声最大。趋势是对的（会一路涨到 232℃ 平台），但单拍读数偏抖。

**③ 主机舱的舱段尺寸是等效值。** 主机舱用的是 `config/compartments.json` 里的舱段 **18×7.6×10.4 m = 1,423 m³**、燃烧面 9.0×5.4 m、峰值 1 MW。这与前端三维舰体、后端舱室种子用的是同一份定义，也与其他四个舱（193~1,423 m³）同量级。

早先曾用 PyroSim 网格域 66×62×32 m = 13.1 万 m³，那是**错的**——那个体积里 6.5 MW 只有 0.05 kW/m³，火根本点不着（层温全程 20~28℃），而且换个探头位置也没用。**这是尺度错误，不是探头问题。**

**④ 逐拍推进依赖引擎 tick 速率。** 若机器负载过高，演化会慢于真实时间。后端已改为按实际墙钟时间推进，代价是一次最多推 20 步（模型单次前向上限）。

---

## 6. 重新训练 / 重新生成数据

### 重新训练模型

```bash
RETRAIN_V6_FOLDS=1 py -3 tools/retrain_v6.py            # 全部舱室
RETRAIN_V6_FOLDS=1 py -3 tools/retrain_v6.py 灶炉间      # 单个舱室
```

`RETRAIN_V6_FOLDS` 限制交叉验证折数（留一验证每折要完整训一遍，5 工况约 90 分钟）。产物落在 `models_v6/<舱室>/best_model.pth`，服务端重启后自动加载（优先级 `models_v6 > models_v3 > models_v2`）。

训练管线内置**完整性闸门**：点数不等于 3601、或文件在 15 分钟内被写过，一律跳过。FDS 是边跑边追加输出的，中途取文件会拿到半截轨迹——它在增长段戛然而止，模型会把它当成"接下来该降温"的规律学进去。

### 重新生成 FDS 算例

```bash
# 生成生产算例（IMO FTP 斜坡 + 上层烟气层阵列 + 显式围护）
py -3 tools/prep_fds_design.py <输入.fds> <输出.fds> <T_END> <CHID> [峰值kW] [MESH_DIV] [seal] [舱段坐标] [火源]

# 生成多工况变体（释热倍数 × 门宽倍数）
py -3 tools/gen_conditions2.py <基准.fds> <前缀> [输出目录] [T_END]

# 批量跑（2 并发，自带跳过已完成 / 孤儿检测）
py -3 tools/fds_queue.py [舱室前缀]
```

主机舱的算例需要显式给舱段坐标，因为它的 PyroSim 模型里没有薄板舱壁，反推不出围护：

```bash
py -3 tools/prep_fds_design.py "D:\pyrosim模型\主机舱\ship_2.fds" zjc.fds 1800 zjc 1000 1 seal \
    "-48,-30,-3.6,4.0,-5.2,5.2" "-44.5,-3.2,3.2,9.0,5.4"
```

### 数据转换与验收

```bash
py -3 tools/convert_growth.py 主机舱   # FDS devc -> 训练格式 + 增长段验收
```

验收分档：闪燃型看层温 ≥150℃；非闪燃-毒气型看层温 ≥50℃ **且** 烟气层 CO₂ ≥2.0%。

---

## 7. API

### LSTM 服务（5001）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/lstm/health` | 状态、可用舱室、**降级舱室名单** |
| GET | `/api/lstm/config` | 各模型真实结构（seq/pred/采样间隔/可靠时长） |
| GET | `/api/lstm/compartments` | 支持的舱室列表 |
| POST | `/api/lstm/predict` | 单次预测 |
| POST | `/api/lstm/evolve` | **闭环演化**，推进 N 步，返回新步与更新后的窗口 |
| POST | `/api/lstm/forecast` | 自回归长时程预测（受可靠时域约束） |
| POST | `/api/lstm/projection` | 分段投影：LSTM 段 + 增长律段，逐点标 `basis` |

### 后端（3001）

`/api/fire/:compartmentId`、`/api/fire/:compartmentId/control`、`/api/fire/:compartmentId/history`、
`/api/fire/events/list`、`/api/fleet/status`、`/api/engine/info`、`/api/compartments`、`/api/alerts`、`/api/ai/*`

控制指令：`start` / `stop` / `suppress` / `evacuate` / `reset`

---

## 8. 项目结构

```
├── src/                          前端
│   ├── views/UnifiedPanel.vue    损管主面板（实例化两次：左态势 / 右控制台）
│   ├── three/                    三维场景装配（environment.js / loaders.js）
│   ├── services/
│   │   ├── lstmPredictionService.js   LSTM 调用、按模型网格重采样、降级标记
│   │   ├── pyrosimService.js          舱室与火源几何
│   │   └── sseClient.js               实时数据推送
│   └── composables/useOpsConsole.js    共享状态与演化引擎驱动
│
├── backend/src/
│   ├── controllers/              火灾、舱室、告警、AI
│   └── services/fireSimulation.js      演化引擎（按墙钟时间推进）
│
├── lstm-prediction-server/
│   └── server.py                 Seq2SeqLSTM 定义、模型加载、
│                                  decode_raw、四个演化端点
│
├── models_v6/                    在用模型（五舱，v6 配方）
│
├── tools/                        数据与训练管线
│   ├── prep_fds_design.py        FDS 生产算例生成
│   ├── gen_conditions2.py        多工况变体生成
│   ├── fds_queue.py              批量运行队列
│   ├── convert_growth.py         数据转换 + 增长段验收
│   ├── retrain_v6.py             v6 训练管线（留一交叉验证）
│   ├── regress_online.py         端到端回归（真实轨迹打服务比对）
│   ├── selftest_server.py        服务自检 21 项
│   └── exp_multicond.py          A/B 对照实验
│
└── config/compartments.json      舱室权威定义（前后端共用）
```

三维模型（`.glb`）在 `public/models/`，其中舱室内部模型在 `public/models/compartments/`。

---

## 9. 排障

### 起不来

| 现象 | 原因与处理 |
|---|---|
| 后端一启动就退出，日志 `数据库连接失败` | MySQL 没连上。后端 `sequelize.authenticate()` 失败会 `process.exit(1)`。先建 `boat_fire_system` 库并填 `backend/.env` |
| `Cannot find module 'sequelize'` | 只在根目录装了依赖。`backend/` 是独立工程，要 `cd backend && pnpm install` |
| 前端健康检查永远失败、预测全走降级 | `config/.env.development` 里 `VITE_LSTM_BASE_URL` 写成了 5000。必须是 **5001** |
| 5001 端口被占用 | 改 `lstm-prediction-server/server.py` 的 `app.run(port=5001)`，同时改 `config/.env.development` 的 `VITE_LSTM_BASE_URL` 和 `vite.config.js` 的代理目标，三处要一致 |

### 起来了但不对

| 现象 | 原因 |
|---|---|
| **起火后温度一直卡在基值不动** | ① 检查 5001 是否真在跑：`curl -s localhost:5001/api/lstm/health`；② 检查后端日志有没有 `timeout of 15000ms exceeded`，有就是演化请求超时，示数会冻结 |
| 温度爬升但很慢 | 机器负载过高导致 tick 变慢。已改为按墙钟时间推进；若仍慢，看 `backend_out.txt` |
| 前端显示降级横幅 | 该舱室还是旧配方模型。查 `/api/lstm/health` 的 `degraded_compartments` |
| 逐拍读数 ±10~15℃ 抖动 | 已知限制，见 §5②。起火最陡的 30 秒内最明显 |
| 接口整体超时 | torch 占满 16 核。服务已限 4 线程，`LSTM_TORCH_THREADS` 可调 |
| 舱室列表只有 4 个或名称不对 | 没跑 `npm run init-db`，或跑之前数据库里是旧数据（该脚本会 `force:true` 删表重建） |
| 训练提示"跳过：文件仍在写入" | 完整性闸门正常工作，FDS 还在跑。等 15 分钟 |
| 演化温度被钳到 0℃ | 旧版解码缺增量还原。当前版本 `decode_raw` 是唯一解码入口 |

### 正常长什么样（用来判断"是不是真跑通了"）

在"舱室列表"任选一个舱室点起火。下面的"终值"是**训练数据里 FDS 实测的峰温**，
仿真平台值会比它略低一点（模型是"保持 + 缓升"型，倾向于欠预测）：

| 舱室 | 起火后 30 秒 | 终值（FDS 实测峰温） | 烟气层 CO₂ 峰 | 特征 |
|---|---|---|---|---|
| 灶炉间 | 70~90℃ | **252℃** | 3.66% | 增长最快，1~2 分钟冲到 200℃ 以上 |
| 机库 | 25~40℃ | **220℃** | 4.25% | 释热量最大，烟最重 |
| 主机舱 | 20~30℃ | **135℃** | 3.02% | 1 MW 油池火，**不闪燃**，平台在 130℃ 附近 |
| 士兵住舱 | 22~30℃ | **74℃** | 1.42% | 可燃物少的小舱室，**永远不会**烧到几百度 |
| 电站间 | 24~30℃ | **60℃** | 3.20% | 同上，但毒性气体浓度更关键 |

**温度逐拍有抖动是正常的**（§5②），看的是趋势不是单点。
如果 5 分钟内温度没有任何上升趋势，那才是坏了。

---

## 10. 许可与数据来源

- 舱室几何、火灾工况设定来自 PyroSim 工程文件（`D:\pyrosim模型\`），**不随仓库分发**
- 训练数据 `tools/fds/train30/*.csv` 由 FDS 6.10.1 仿真产出后转换，随仓库分发
- `models_v6/` 为本项目训练产物，随仓库分发
- 船舶三维模型（`.glb`）见 `public/models/`
- `requirement.txt` 是整份 Anaconda 环境冻结（500+ 包），仅供还原开发机用；实际依赖见 [§3](#3-快速开始)
