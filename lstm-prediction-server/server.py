"""
LSTM火灾预测服务器
基于PyrosimLSTM模型提供火灾演化态势预测API
"""

import os
import sys
import json
import torch
import numpy as np
import pandas as pd          # 闭环演化播种时要读 PyroSim 训练轨迹
from flask import Flask, request, jsonify
from flask_cors import CORS
from sklearn.preprocessing import StandardScaler

# 添加模型路径（原始 30 秒 checkpoint 的兜底位置，仅开发机存在；
# models_v6 命中时不会用到。可用环境变量覆盖）
PYROSIM_LSTM_PATH = os.environ.get('PYROSIM_LSTM_PATH', r'D:\PyrosimLSTM')
sys.path.insert(0, PYROSIM_LSTM_PATH)

app = Flask(__name__)
CORS(app)  # 允许跨域请求

# 单次 /api/lstm/evolve 请求允许推进的最大模型步数
STEPS_MAX_PER_CALL = 20

# ============== LSTM模型定义 ==============
class Seq2SeqLSTM(torch.nn.Module):
    """Seq2Seq LSTM模型

    与 tools/model_def.py 保持一致。decoder 输入额外叠加可学习的位置嵌入：
    原实现把编码器末状态 repeat 成 pred_len 份完全相同的向量，decoder
    收不到任何"现在是第几步"的信息，150 步预测的前后位置无法区分。
    """
    def __init__(self, input_dim=3, hidden_dim=128, num_layers=2, output_dim=3, seq_len=150, pred_len=150, dropout=0.2):
        super(Seq2SeqLSTM, self).__init__()
        self.hidden_dim = hidden_dim
        self.num_layers = num_layers
        self.seq_len = seq_len
        self.pred_len = pred_len

        self.encoder_lstm = torch.nn.LSTM(
            input_dim, hidden_dim, num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0
        )
        self.decoder_lstm = torch.nn.LSTM(
            hidden_dim, hidden_dim, num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0
        )
        self.pos_embed = torch.nn.Parameter(torch.zeros(pred_len, hidden_dim))
        torch.nn.init.normal_(self.pos_embed, std=0.02)
        self.fc = torch.nn.Linear(hidden_dim, output_dim)

    def forward(self, x):
        encoder_output, (hidden, cell) = self.encoder_lstm(x)
        decoder_input = encoder_output[:, -1:, :].repeat(1, self.pred_len, 1)
        decoder_input = decoder_input + self.pos_embed.unsqueeze(0)
        decoder_output, _ = self.decoder_lstm(decoder_input, (hidden, cell))
        output = self.fc(decoder_output)
        return output


# ============== 预测器类 ==============
class FirePredictor:
    """火灾预测器"""
    
    # 舱室名称到模型目录的映射
    COMPARTMENT_MODEL_MAP = {
        '主机舱': 'zjc.LSTM(new)',
        '机库': 'JK_LSTM',
        '士兵住舱': 'SBZV.lstm',
        '炉灶间': 'LZJ.LSTM',
        '灶炉间': 'LZJ.LSTM',  # 别名
        '电站间': 'ship_4',   # 有自己的 30 分钟 FDS 轨迹，不再借用主机舱模型
        # ⚠ 甲板已移除：其训练数据的通道集完全不同
        # （Temperature / Radiative Heat Flux Gas / Visibility，不是 温度/CO/CO2），
        # 且 Temperature 同样恒定。强行加载只会得到形状不匹配的权重，
        # 预测结果毫无意义。它也不在舰船的 5 个舱室里，直接剔除更诚实。
    }

    # 同一部位的舱室在不同来源里的名字不一致，重训产物按真实舱名分目录，
    # 这里把旧名映射到真实名，避免别名舱回退去加载旧权重。
    #
    # ⚠ 这里**不能**保留 '电站间': '主机舱'。该别名会把电站间的加载候选指向
    # models_v3/电站间（不存在）→ 回退到 models_v2/主机舱，于是电站间一直
    # 加载的是主机舱权重，两者温度数值完全一样。电站间已有自己的 30 分钟
    # 轨迹和 models_v3 产物，不该再借用。
    NAME_ALIASES = {
        '炉灶间': '灶炉间',
    }
    
    # ⚠ 旧的 SAMPLING_INTERVAL 表已删除。
    # 它填的是各舱原始 CSV 的 Δt **最大值**（主机舱 0.1411、机库 0.3570），
    # 而训练实际用的平均间隔是 0.036 / 0.090 秒。把模型按最大 Δt 喂数据，
    # 等于让它看到火势以 1/3.7 的速度变化 —— 这正是自回归演化推不动的根因。
    # 现在训练前统一重采样到 0.1s 均匀网格，时间尺度由 checkpoint 记录，
    # 不再靠这张猜测出来的表。

    # 单次外推允许的最大步数（防止一次请求把 150 步窗口一次推穿）
    STEPS_MAX = 20

    def sampling_interval(self, compartment_name):
        """模型一步对应的真实秒数。

        训练时所有轨迹已统一重采样到 0.1s 均匀网格，所以正常情况下所有模型
        都返回 checkpoint 里记录的 0.1。保留按舱室查询的接口是因为不同
        checkpoint 理论上可以用不同网格，不能写死。
        """
        shape = self.model_shapes.get(compartment_name)
        if shape:
            return float(shape.get('step_seconds', 0.1))
        for name, s in self.model_shapes.items():
            if name in compartment_name or compartment_name in name:
                return float(s.get('step_seconds', 0.1))
        return 0.1

    def seq_len_of(self, compartment_name):
        """该模型实际的输入窗口步数。"""
        shape = self.model_shapes.get(compartment_name)
        if shape:
            return int(shape.get('seq_len') or self.config['sequence_length'])
        for name, s in self.model_shapes.items():
            if name in compartment_name or compartment_name in name:
                return int(s.get('seq_len') or self.config['sequence_length'])
        return int(self.config['sequence_length'])

    def __init__(self):
        # 限制 torch 的 CPU 线程数。模型很小（hidden=64、2 层、seq=60），
        # 单次前向的计算量远小于线程同步开销 —— 实测不限线程时服务进程
        # 会占满 16 核（15 分钟烧掉 5450 秒 CPU），/health 要 4.8 秒、
        # /evolve 推进一步都超时。限制到 4 线程后同样的请求是毫秒级。
        n = int(os.environ.get("LSTM_TORCH_THREADS", "4"))
        torch.set_num_threads(max(1, n))
        try:
            torch.set_num_interop_threads(max(1, min(2, n)))
        except Exception:
            pass          # 已在别处初始化过就算了
        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        self.models = {}
        self.scalers = {}
        self.model_shapes = {}
        self.config = {
            'sequence_length': 150,
            'prediction_length': 150,
            'input_dim': 3,
            'output_dim': 3,
            'hidden_dim': 128,
            'num_layers': 2
        }
        print(f"[FIRE] 火灾预测服务初始化，使用设备: {self.device}")
        self._load_all_models()
    
    def _load_all_models(self):
        """加载所有可用的模型。

        优先级 models_v7 > models_v6 > models_v3 > models_v2 > 原始 checkpoint：
          v7  v6 配方 + 第 4 输入通道（灭火状态 S），训练数据含灭火干预工况，
              能回答"投入灭火后火会怎么走"的反事实；干预通道语义见
              checkpoint 的 intervention 字段；
          v6  多工况 + 增量目标 + 0.5s 网格，留出工况泛化误差 3.8℃，
              模型对输入有响应（v4 那版喂什么都吐同一个数）；
          v3  用 30 分钟 FDS 轨迹重训，但训练数据是**阶跃**的
              （20℃ 在 0.3 秒内冲到 415℃），没有火灾增长过程；
          v2  180 秒轨迹；
          原始 30 秒 checkpoint 兜底。
        """
        base = os.path.dirname(os.path.abspath(__file__))
        # LSTM_MODEL_ROOTS 可以用逗号分隔覆盖搜索顺序（相对于仓库根）。
        # 用途是**复现历史基线**：README 里"换 v6 之前有多糟"那张表就是这么
        # 测的 —— LSTM_MODEL_ROOTS=models_v3,models_v2 就能让服务加载旧配方，
        # 不必去重命名 models_v6 目录（那会污染工作区，还可能撞上文件占用）。
        override = os.environ.get('LSTM_MODEL_ROOTS', '').strip()
        if override:
            roots = [os.path.join(base, '..', r.strip())
                     for r in override.split(',') if r.strip()]
            print(f"[INFO] 模型搜索路径被 LSTM_MODEL_ROOTS 覆盖: "
                  f"{[os.path.basename(r.rstrip(os.sep)) for r in roots]}")
        else:
            roots = [os.path.join(base, '..', 'models_v7'),
                     os.path.join(base, '..', 'models_v6'),
                     os.path.join(base, '..', 'models_v3'),
                     os.path.join(base, '..', 'models_v2')]
        for compartment_name, model_dir in self.COMPARTMENT_MODEL_MAP.items():
            # 别名舱优先找真实舱名对应的重训产物
            real_name = self.NAME_ALIASES.get(compartment_name, compartment_name)
            candidates = [os.path.join(r, real_name, 'best_model.pth') for r in roots]
            candidates.append(os.path.join(PYROSIM_LSTM_PATH, model_dir, 'models', 'best_model.pth'))
            model_path = next((p for p in candidates if os.path.exists(p)), None)
            if model_path is None:
                print(f"[WARN] 模型文件不存在: {candidates[0]} / {candidates[-1]}")
                continue
            try:
                norm = model_path.replace('\\', '/')
                self._current_tag = ('v7' if 'models_v7' in norm
                                     else 'v6' if 'models_v6' in norm
                                     else 'v3' if 'models_v3' in norm
                                     else 'v2' if 'models_v2' in norm
                                     else '原始')
                self._load_model(compartment_name, model_path)
                tag = self._current_tag
                shape = self.model_shapes.get(compartment_name, {})
                print(f"[OK] 加载模型成功({tag}): {compartment_name} "
                      f"seq={shape.get('seq_len')} pred={shape.get('pred_len')} "
                      f"step={shape.get('step_seconds')}s "
                      f"目标={shape.get('target')} "
                      f"可靠时长={shape.get('reliable_horizon_seconds')}s "
                      f"退化通道={shape.get('degenerate_channels') or '无'}")
            except Exception as e:
                print(f"[ERROR] 加载模型失败 {compartment_name}: {e}")
    
    def _load_model(self, compartment_name, model_path):
        """加载单个模型。

        结构参数一律从 checkpoint 读取，而不是全局写死 —— 不同舱室可能
        用了不同的窗口长度，写死会让形状对不上而加载失败。
        """
        try:
            checkpoint = torch.load(model_path, map_location=self.device, weights_only=False)
        except Exception:
            checkpoint = torch.load(model_path, map_location=self.device)

        sd = checkpoint['model_state_dict']
        ck_cfg = checkpoint.get('config', {}) or {}

        # 从权重反推结构，checkpoint 里没记的项用权重形状兜底
        input_dim = sd['encoder_lstm.weight_ih_l0'].shape[1]
        output_dim = sd['fc.weight'].shape[0]
        hidden_dim = sd['fc.weight'].shape[1]
        num_layers = ck_cfg.get('num_layers', ck_cfg.get('layers', 2))
        seq_len = ck_cfg.get('seq_len', checkpoint.get('sequence_length', self.config['sequence_length']))
        pred_len = ck_cfg.get('pred_len', checkpoint.get('prediction_length', self.config['prediction_length']))

        model = Seq2SeqLSTM(
            input_dim=input_dim,
            hidden_dim=hidden_dim,
            num_layers=num_layers,
            output_dim=output_dim,
            seq_len=seq_len,
            pred_len=pred_len,
            dropout=0.0
        ).to(self.device)

        # 旧 checkpoint 没有位置嵌入，缺就缺，不因此拒绝加载
        missing, unexpected = model.load_state_dict(sd, strict=False)
        if unexpected:
            print(f"[WARN] {compartment_name} checkpoint 含未使用参数: {list(unexpected)}")
        if missing and 'pos_embed' not in missing:
            print(f"[WARN] {compartment_name} checkpoint 缺参数: {list(missing)}")
        elif 'pos_embed' in missing:
            print(f"[INFO] {compartment_name} 为旧版权重，无位置嵌入（预测能力受限）")
        model.eval()

        step_s = checkpoint.get('step_seconds', 0.1)
        self.models[compartment_name] = model
        self.scalers[compartment_name] = self._build_scaler(checkpoint)
        self.model_shapes[compartment_name] = dict(
            source=self._current_tag,
            seq_len=seq_len, pred_len=pred_len,
            step_seconds=step_s,
            input_dim=input_dim, output_dim=output_dim,
            # v7 干预通道：input_dim=4 时第 4 列是灭火状态 S（0→1），
            # 由调用方按当前灭火进度填充。evolve/forecast/projection
            # 都会据此构造窗口列。
            intervention=checkpoint.get('intervention') or None,
            intervention_channel=input_dim >= 4,
            trained_horizon_seconds=checkpoint.get('trained_horizon_seconds', 30.0),
            # 单次前向的可靠时长 = pred_len × 采样间隔。这和
            # trained_horizon_seconds 是**两件事**：
            #   前者是训练轨迹有多长（决定模型见过哪些火灾状态），
            #   后者是一次前向能可信地推出多远（决定误差从哪开始累积）。
            # 早先 extrapolated 标记挂在 trained 上，v6 就有 1800 秒，
            # 等于告诉前端整条 30 分钟曲线都可信。实测灶炉间自回归外推：
            # 0~30 秒 MAE 2.48℃，30~60 秒 14.0℃，120~300 秒 30.8℃。
            # 按 1800 秒标就等于把 120 秒后的外推伪装成预测。
            reliable_horizon_seconds=pred_len * step_s,
            degenerate_channels=checkpoint.get('degenerate_channels', []),
            # 增量目标模型需要这三个量做还原，见 decode_raw
            target=checkpoint.get('target', 'absolute'),
            delta_std=checkpoint.get('delta_std', [1.0, 1.0, 1.0]),
            channel_mean=checkpoint.get('channel_mean', [0.0, 0.0, 0.0]),
            channel_std=checkpoint.get('channel_std', [1.0, 1.0, 1.0]),
        )
    
    @staticmethod
    def _build_scaler(checkpoint):
        """取出（或按 v6 的通道统计量重建）归一化器。

        v2/v3 的 checkpoint 直接存了 sklearn 的 scaler 对象；v6 增量模型
        不存对象，只存训练时用的 channel_mean / channel_std。这里按同样的
        (x - mean) / std 规则补一个 StandardScaler —— self.scalers 在自回归
        端点里处处被 transform()，缺键会让整个舱室加载失败（早先灶炉间就是
        这么挂的，v6 模型已经产出却一直没能上线）。用重建而不是放宽检查，
        是为了让 scaler.transform 和 decode_raw 的 delta 还原共用同一套归一化，
        两边一旦不一致，增量加回的就是错的基准值。
        """
        if 'scaler' in checkpoint:
            return checkpoint['scaler']
        mean = np.asarray(checkpoint.get('channel_mean', [0.0, 0.0, 0.0]),
                          dtype=np.float64)
        std = np.asarray(checkpoint.get('channel_std', [1.0, 1.0, 1.0]),
                         dtype=np.float64)
        sc = StandardScaler()
        sc.mean_ = mean
        sc.scale_ = np.where(std > 0, std, 1.0)
        sc.var_ = sc.scale_ ** 2
        sc.n_features_in_ = len(mean)
        sc.n_samples_seen_ = 1
        sc.with_mean = True
        sc.with_std = True
        return sc

    def predict(self, compartment_name, input_sequence):
        """
        进行火灾预测
        
        Args:
            compartment_name: 舱室名称
            input_sequence: 输入序列 shape=(seq_len, 3) [温度, CO, CO2]
        
        Returns:
            预测结果 shape=(pred_len, 3)
        """
        # 查找对应的模型。解析规则集中在 _resolve_name：自回归端点也要用
        # 同一套，否则 decode_raw 读到的 model_shapes 会对不上实际命中的模型。
        resolved = self._resolve_name(compartment_name)
        model = self.models[resolved]
        scaler = self.scalers[resolved]
        if resolved != compartment_name:
            print(f"[WARN] 未找到 {compartment_name} 的专用模型，使用 {resolved} 模型")

        # 确保输入数据格式正确
        # 窗口长度必须用**实际命中模型**的 seq_len：不同舱室训练配置可能不同，
        # 套用全局 150 会让形状对不上或悄悄丢掉真实历史。
        shape = self.model_shapes.get(resolved) or {}
        seq_len = shape.get('seq_len', self.config['sequence_length'])
        input_dim = int(shape.get('input_dim') or 3)
        input_array = np.array(input_sequence, dtype=float)
        # v7 干预模型有第 4 列（灭火状态 S）。调用方没给就补 0（未干预）；
        # 多给的列截到模型宽度。
        if input_array.ndim == 2:
            if input_array.shape[1] < input_dim:
                pad = np.zeros((input_array.shape[0],
                                input_dim - input_array.shape[1]))
                input_array = np.hstack([input_array, pad])
            elif input_array.shape[1] > input_dim:
                input_array = input_array[:, :input_dim]
        if input_array.shape[0] < seq_len:
            # 如果数据不足，用最后一个值填充
            padding_length = seq_len - input_array.shape[0]
            padding = np.tile(input_array[-1:], (padding_length, 1))
            input_array = np.vstack([padding, input_array])
        elif input_array.shape[0] > seq_len:
            # 如果数据太多，只取最后的部分
            input_array = input_array[-seq_len:]
        
        # 进行预测
        with torch.no_grad():
            input_scaled = scaler.transform(input_array)
            input_tensor = torch.FloatTensor(input_scaled).unsqueeze(0).to(self.device)
            out = model(input_tensor)
            out = out.cpu().numpy().squeeze()

        # 解码规则见 decode_raw
        return self.decode_raw(resolved, out, input_scaled)

    def _resolve_name(self, compartment_name):
        """把请求里的舱室名解析成实际命中的模型名。"""
        if compartment_name in self.models:
            return compartment_name
        for name in self.models.keys():
            if name in compartment_name or compartment_name in name:
                return name
        if '主机舱' in self.models:
            return '主机舱'
        raise ValueError(f"无法找到舱室 {compartment_name} 对应的预测模型")

    def decode_raw(self, compartment_name, out, input_scaled):
        """把模型原始输出解码成物理量 —— 全服务唯一的解码入口。

        增量目标（v6/v7）下模型输出的是「相对输入末值的增量」，要连着做
        两次还原：先加回输入末值得到绝对值，再用通道均值方差还原物理量。
        漏掉任何一步都会得到 0℃ / 40% CO2 这种物理上不可能的数。

        v7 的输入是 4 通道（含灭火状态 S）而输出只有 3 个物理通道：
        增量基准取输入末行的**前 output_dim 列**，统计量同样只取前
        output_dim 维 —— 拿 4 维末行直接加会把 S 混进温度增量。

        Args:
            out:          模型原始输出 (pred_len, output_dim)
            input_scaled: 本次输入窗口的**标准化**结果 (seq_len, input_dim)；
                          增量目标要加回的是它的末行
        """
        resolved = self._resolve_name(compartment_name)
        shape = self.model_shapes.get(resolved) or {}
        out = np.asarray(out, dtype=float)
        n_out = out.shape[-1]
        if shape.get('target') == 'delta':
            delta = out * np.asarray(shape['delta_std'])[:n_out]
            pred_scaled = delta + np.asarray(input_scaled)[-1, :n_out]
            ch_std = np.asarray(shape['channel_std'])[:n_out]
            ch_mean = np.asarray(shape['channel_mean'])[:n_out]
            return pred_scaled * ch_std + ch_mean
        # 绝对值目标（v2/v3）：scaler 维度与输出一致，走原路
        return self.scalers[resolved].inverse_transform(out)
    
    def get_available_compartments(self):
        """获取可用的舱室列表"""
        return list(self.models.keys())

    def degraded_compartments(self):
        """列出「还挂着旧模型」的舱室，供前端如实提示。

        实测（tools/regress_online.py，拿真实 FDS 轨迹打本服务比对）：
        v3 模型自回归外推会塌缩成常数 —— 机库无论喂什么窗口，300 秒
        末一律给 453.5℃（±7.1，真值是 189.4℃±65.1），MAE 266.8℃，
        46931/48000 个点物理上不可能。v3 训练用的 30 分钟轨迹本身是
        阶跃的（20℃ 在 0.3 秒内冲到 415℃），模型学到的是「平台温度约
        450℃」这个常数，而不是火灾怎么长。

        这样的数字比没有数字更危险：它看起来平滑、收敛、不报警，
        值班员会当成结论。所以凡是没换到 v6 配方的舱室都标出来，
        前端必须显示降级提示，不能把曲线当预测结果展示。
        """
        out = {}
        for name, shape in self.model_shapes.items():
            if shape.get('source') not in ('v6', 'v7'):
                out[name] = {
                    'source': shape.get('source'),
                    'reliableHorizonSeconds': shape.get('reliable_horizon_seconds'),
                    'reason': '训练数据为阶跃轨迹，无真实火灾增长过程；'
                              '自回归外推会收敛到常数平台',
                }
        return out


# ============== 推导通道（非模型输出，必须如实标注） ==============
# 模型只输出温度/CO/CO2 三个通道。烟雾与氧气是**推导值**：
# 与后端 fireSimulation.js decode() 用同一套代理公式，保证两侧面板
# 数值一致。它们不是模型输出，各端点会在响应里附 derivedChannels
# 说明来源 —— 不能把推导值冒充成预测通道。
DERIVED_CHANNELS = ['smoke', 'oxygen']


def derived_smoke_oxygen(temp_c, co2_mol):
    """由模型三通道推导展示用烟雾/氧气（与后端 decode 同一公式）。"""
    co2_ppm = co2_mol * 1e6
    o2 = max(8.0, min(20.9, 20.9 - (co2_ppm - 400) / 20000 * 9.5))
    smoke = max(0.0, min(100.0, (temp_c - 20) / 400 * 100))
    return round(smoke, 2), round(o2, 2)


# ============== 全局预测器实例 ==============
predictor = None

def get_predictor():
    """获取预测器单例"""
    global predictor
    if predictor is None:
        predictor = FirePredictor()
    return predictor


# ============== API路由 ==============

@app.route('/api/lstm/health', methods=['GET'])
def health_check():
    """健康检查"""
    return jsonify({
        'status': 'ok',
        'message': 'LSTM预测服务运行正常',
        'device': str(get_predictor().device),
        'available_compartments': get_predictor().get_available_compartments(),
        'degraded_compartments': get_predictor().degraded_compartments()
    })


@app.route('/api/lstm/predict', methods=['POST'])
def predict():
    """
    火灾演化预测接口
    
    请求体:
    {
        "compartmentName": "主机舱",
        "historyData": [
            {"temperature": 100, "co": 0.001, "co2": 0.01},
            {"temperature": 120, "co": 0.002, "co2": 0.02},
            ...
        ],
        "predictionSteps": 30  // 可选，预测多少步（每步约0.1秒）
    }
    
    响应:
    {
        "code": 200,
        "data": {
            "predictions": [
                {"time": 0, "temperature": 150, "co": 0.003, "co2": 0.03},
                ...
            ],
            "summary": {
                "maxTemperature": 500,
                "maxCO": 0.01,
                "riskLevel": "high",
                "trend": "rising"
            }
        }
    }
    """
    try:
        data = request.get_json()
        compartment_name = data.get('compartmentName', '主机舱')
        history_data = data.get('historyData', [])
        prediction_steps = data.get('predictionSteps', 150)
        
        if not history_data:
            return jsonify({
                'code': 400,
                'message': '缺少历史数据'
            }), 400
        
        # 转换输入数据格式
        # 前端数据: {temperature, co, co2} 或 {temperature, smoke, oxygen, co}
        #
        # ⚠ 这里必须自己判空，不能只靠 dict.get(key, 默认值)：
        # 键存在但值为 null 时 get 会返回 None 而不是默认值，
        # 后面做数值运算就抛 TypeError。前端重采样时若出现 NaN，
        # 序列化成 JSON 就是 null，所以这条路径是真实存在的。
        def num(point, *names, default=0.0):
            for n in names:
                v = point.get(n)
                if v is None:
                    continue
                try:
                    f = float(v)
                except (TypeError, ValueError):
                    continue
                if np.isfinite(f):
                    return f
            return default

        input_sequence = []
        for point in history_data:
            if not isinstance(point, dict):
                continue
            temp = num(point, 'temperature', default=20.0)
            co = num(point, 'co', 'carbonMonoxide')
            co2 = num(point, 'co2', 'carbonDioxide',
                      default=num(point, 'smoke') * 0.001)

            # 将前端单位转换为模型期望的单位
            # 温度: ℃ (保持不变)
            # CO: kg/m3 (前端是ppm，需要转换)
            # CO2: mol/mol (前端可能是ppm或其他)
            co_converted = co * 1e-6 if co > 0.1 else co  # 如果CO很大，假设是ppm
            co2_converted = co2 * 1e-6 if co2 > 0.1 else co2

            input_sequence.append([temp, co_converted, co2_converted])

        if not input_sequence:
            return jsonify({
                'code': 400,
                'message': '历史数据格式无效'
            }), 400
        
        # 调用预测
        pred = get_predictor()
        prediction = pred.predict(compartment_name, input_sequence)
        
        # 限制预测步数
        if prediction_steps < len(prediction):
            prediction = prediction[:prediction_steps]
        
        # 格式化输出
        # 物理下限钳制：LSTM 在分布外外推时可能吐出负浓度（界面上出现过 -62581 ppm）。
        # 负的 CO/CO2 在物理上不存在，直接透传出去就是在展示不可能的数据。
        # 这里只做**有效域钳制**，不改变模型在有效域内的输出。
        CO2_FLOOR, CO_FLOOR = 3.0e-4, 0.0      # mol/mol
        T_FLOOR, T_CEIL = 0.0, 1500.0          # ℃
        predictions = []
        time_step = 0.1  # 每步约0.1秒
        clamped = 0
        for i, row in enumerate(prediction):
            temp, co, co2 = (float(v) for v in row)
            if not all(np.isfinite([temp, co, co2])):
                temp, co, co2 = 20.0, CO_FLOOR, CO2_FLOOR
                clamped += 1
            if temp < T_FLOOR or temp > T_CEIL:
                temp = min(max(temp, T_FLOOR), T_CEIL)
                clamped += 1
            if co < CO_FLOOR:
                co = CO_FLOOR
                clamped += 1
            if co2 < CO2_FLOOR:
                co2 = CO2_FLOOR
                clamped += 1
            co_ppm = min(co * 1e6, 50000.0)
            co2_ppm = min(co2 * 1e6, 300000.0)
            smoke_v, oxygen_v = derived_smoke_oxygen(temp, co2)
            predictions.append({
                'time': round(i * time_step, 2),
                'temperature': round(temp, 2),
                'co': round(co_ppm, 4),        # ppm
                'co2': round(co2_ppm, 4),      # ppm
                # 烟雾/氧气为推导值（非模型输出），来源见 derived_smoke_oxygen
                'smoke': smoke_v,
                'oxygen': oxygen_v
            })
        
        # 计算摘要信息
        temps = [p['temperature'] for p in predictions]
        cos = [p['co'] for p in predictions]
        
        max_temp = max(temps)
        max_co = max(cos)
        
        # 判断趋势
        if len(temps) > 10:
            early_avg = np.mean(temps[:10])
            late_avg = np.mean(temps[-10:])
            if late_avg > early_avg * 1.1:
                trend = 'rising'
            elif late_avg < early_avg * 0.9:
                trend = 'falling'
            else:
                trend = 'stable'
        else:
            trend = 'unknown'
        
        # 判断风险等级
        if max_temp > 400 or max_co > 50:
            risk_level = 'high'
        elif max_temp > 200 or max_co > 20:
            risk_level = 'medium'
        else:
            risk_level = 'low'
        
        return jsonify({
            'code': 200,
            'data': {
                'predictions': predictions,
                'summary': {
                    'maxTemperature': round(max_temp, 2),
                    'minTemperature': round(min(temps), 2),
                    'avgTemperature': round(np.mean(temps), 2),
                    'maxCO': round(max_co, 4),
                    'riskLevel': risk_level,
                    'trend': trend,
                'predictionDuration': round(len(predictions) * time_step, 2),
                # 被物理钳制修正过的点数 —— 非 0 说明模型在有效域外，
                # 前端应当提示"部分数值已按物理范围修正"，不能装作原样输出
                'clampedPoints': clamped,
                # 烟雾/氧气不是模型输出，是推导值，必须告知调用方
                'derivedChannels': DERIVED_CHANNELS,
                'compartmentName': compartment_name
                }
            },
            'message': '预测成功'
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            'code': 500,
            'message': f'预测失败: {str(e)}'
        }), 500


@app.route('/api/lstm/compartments', methods=['GET'])
def get_compartments():
    """获取支持预测的舱室列表"""
    return jsonify({
        'code': 200,
        'data': get_predictor().get_available_compartments(),
        'message': 'success'
    })


@app.route('/api/lstm/config', methods=['GET'])
def get_config():
    """获取模型配置

    这里报的是**模型自己 checkpoint 里记录的结构参数**，不是全局写死的值。
    不同舱室可能用不同窗口长度训练，前端拿到的必须是真的那个。
    """
    pred = get_predictor()
    shapes = pred.model_shapes
    # 全局字段只作为 modelShapes 缺失时的兜底，所以取一个真实模型的值，
    # 不要留 150 这种写死数字 —— 那和实际加载的模型对不上。
    any_shape = next(iter(shapes.values()), {})
    return jsonify({
        'code': 200,
        'data': {
            'sequenceLength': any_shape.get('seq_len', pred.config['sequence_length']),
            'predictionLength': any_shape.get('pred_len', pred.config['prediction_length']),
            'inputDim': pred.config['input_dim'],
            'outputDim': pred.config['output_dim'],
            # 时间步长以 checkpoint 记录为准。训练数据已统一重采样到该网格，
            # 所以这一步就是真实的秒数，不是估算值。
            'timeStepSeconds': next(
                (s.get('step_seconds', 0.1) for s in shapes.values()), 0.1),
            # 各模型实际结构，前端按这个重采样才不会错位
            'modelShapes': {
                k: {
                    'seqLen': v.get('seq_len'),
                    'predLen': v.get('pred_len'),
                    'stepSeconds': v.get('step_seconds'),
                    'inputDim': v.get('input_dim'),
                    # v7 干预模型：第 4 输入通道是灭火状态，evolve/projection
                    # 可传 suppression 做反事实投影。后端据此决定是否跳过
                    # 显示层灭火叠加（避免双重压制）。
                    'interventionChannel': bool(v.get('intervention_channel')),
                    'reliableHorizonSeconds': v.get('reliable_horizon_seconds'),
                    'target': v.get('target'),
                    'inputSeconds': round((v.get('seq_len') or 0) * v.get('step_seconds', 0.1), 2),
                    'predSeconds': round((v.get('pred_len') or 0) * v.get('step_seconds', 0.1), 2),
                    'degenerateChannels': v.get('degenerate_channels', []),
                }
                for k, v in shapes.items()
            },
            # 训练轨迹的时间跨度（秒）—— 模型的有效时域上限
            'trainedHorizonSeconds': next(
                (s.get('trained_horizon_seconds', 30.0) for s in shapes.values()), 30.0),
            'compartmentModelMap': {k: v for k, v in pred.COMPARTMENT_MODEL_MAP.items()},
            # 还没换到 v6 配方的舱室：它们的曲线只是「看起来平滑的常数」，
            # 前端必须显式降级提示，不能当预测结果展示。详见
            # FirePredictor.degraded_compartments 的实测依据。
            'degradedCompartments': pred.degraded_compartments(),
            'description': '基于LSTM的火灾演化态势预测模型'
        },
        'message': 'success'
    })


# 塌缩判定阈值：window_variance 是「末段方差/整窗方差」的比值，
# 正常演化约 0.1~0.9，跌破 1% 才算真的压平。
COLLAPSE_RATIO = 0.01

# ============== 闭环演化（把 LSTM 当作火灾演化引擎） ==============

# 30 分钟 FDS 轨迹 —— 模型真正训练时用的数据。
# 再锚定必须喂这份：喂 D:\PyrosimLSTM 里 30 秒的老轨迹属于分布外输入，
# 模型会因此输出偏离值。主机舱没有 30 分钟数据（1M 网格跑不动），
# 它的模型本来就训自 30 秒轨迹，所以对它而言老轨迹才是正确来源。
TRAIN30_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', 'tools', 'fds', 'train30')

# 30 秒老轨迹，train30 缺失时兜底
TRAINING_CSV = {
    'zjc.LSTM(new)':  '主机舱.csv',
    'JK_LSTM':       '机库.csv',
    'SBZV.lstm':     '士兵住舱.csv',
    'LZJ.LSTM':      '灶炉间.csv',
    'ship_4':        '电站间.csv',
}

_training_cache = {}
# 每个舱室的再锚定游标：沿真实火灾轨迹往前走的取样位置。
# 见 evolve() 里 need_anchor 分支的说明。
_reanchor_cursor = {}
# 每个舱室「距上次再锚定已推进的模型步数」，超过一次前向长度就回锚
_steps_since_anchor = {}


def seed_from_training_data(compartment_name, seq_len, offset=0, align_to=None,
                            step_seconds=None, s_fill=None):
    """
    从该舱室的真实训练轨迹取一段作为起火/再锚定窗口。

    实测结论：Seq2Seq LSTM 在 30 秒 PyroSim 轨迹上训练，做**开环自回归**
    会在几十步内收敛到不动点（25 秒内 449.7℃ → 449.6℃ 几乎不动）。
    原因是窗口被近乎相同的值填满后，输入分布退化，模型输出趋于常数。

    解决办法是**再锚定**：一旦检测到收敛，就把窗口换成真实轨迹的下一段，
    让模型继续在自身分布内演化。这样每一步仍由 LSTM 生成，但不会塌缩。

    s_fill：v7 干预模型需要第 4 列（灭火状态 S）。训练轨迹本身没有这一列
    （它由 manifest 参数构造），播种时用调用方给的当前灭火水平填整列 ——
    窗口 30 秒、灭火剂 10 秒到位，常数近似在介入稳定后是分布内的。
    """
    folder = FirePredictor.COMPARTMENT_MODEL_MAP.get(compartment_name, 'zjc.LSTM(new)')
    real_name = FirePredictor.NAME_ALIASES.get(compartment_name, compartment_name)
    csv_name = TRAINING_CSV.get(folder)

    # 优先 train30（模型真实见过的分布），回退到 PyroSim 30 秒轨迹
    candidates = [os.path.join(TRAIN30_DIR, f'{real_name}.csv')]
    if csv_name:
        candidates.append(os.path.join(PYROSIM_LSTM_PATH, folder, csv_name))
    path = next((p for p in candidates if os.path.exists(p)), None)
    if path is None:
        return None, 0

    # 网格必须对得上。train30 是 0.5s 网格（DT_DEVC=0.5），
    # 旧 v2/v3 模型是 0.1s 网格训的。给 0.1s 模型喂 0.5s 轨迹，
    # 同一个 seq 窗口覆盖的**真实时间**会差 5 倍（50 行 = 25 秒而不是 5 秒），
    # 输入分布直接错位。实测主机舱 v2 这么喂会吐出 447.8℃ —— 正是它
    # 从阶跃数据里学到的那个平台常数。宁可回退到它自己的老轨迹。
    if step_seconds is not None and abs(step_seconds - 0.5) > 1e-6 \
            and os.path.dirname(path) == TRAIN30_DIR:
        print(f"[WARN] {real_name} 模型网格 {step_seconds}s 与 train30 的 0.5s 不符，"
              f"回退到原训练轨迹")
        fb = os.path.join(PYROSIM_LSTM_PATH, folder, csv_name) if csv_name else None
        if fb and os.path.exists(fb):
            path = fb
        else:
            return None, 0
    if path not in _training_cache:
        try:
            # 必须按**列名**取，不能按位置。各 PyroSim 导出的列顺序并不统一：
            #   主机舱/机库/士兵住舱 → Time, temperature, CO, CO2
            #   灶炉间(LZJ)          → Time, carbon dioxide, CO, temperature
            # 按 cols[1:4] 位置取会让灶炉间的窗口变成 [CO2, CO, T]，
            # 通道整体错位，模型收到完全无意义的输入，演化立刻卡死。
            # 两套 CSV 的表头位置不同，不能一律 skiprows=[0]：
            #   PyroSim 原始导出：第 1 行是**单位**行，第 2 行才是通道名
            #   train30 转换产物  ：第 1 行直接就是通道名
            # 一律跳过首行会让 train30 把首行数据当表头，列名变成
            # '0.0'/'20.0'/'0.0.1'，于是所有舱的种子都取不到、全部退化成
            # NULL，各舱演化数值变得一模一样。先按不跳读，认不出来再跳。
            df = None
            for skip in ([], [0]):
                try:
                    cand = pd.read_csv(path, skiprows=skip)
                    cand.columns = [c.strip().strip('"') for c in cand.columns]
                except Exception:
                    continue
                if any(c.lower().startswith('temperature') for c in cand.columns):
                    df = cand
                    break
            if df is None:
                print(f"[WARN] {os.path.basename(path)} 表头无法识别")
                return None, 0
            # 列名两套约定都要认：
            #   PyroSim 原始导出 -> temperature / carbon monoxide / carbon dioxide
            #   train30 转换产物   -> temperature / CO / CO2
            # 只认全名时，train30 的四个舱会全部取不到列、种子退化成 NULL，
            # 各舱演化结果变成一模一样的数值（实测 windowVariance 全同）。
            aliases = {
                'temperature': ['temperature', 'Temperature'],
                'CO': ['carbon monoxide', 'CO'],
                'CO2': ['carbon dioxide', 'CO2'],
            }
            picked, missing = [], []
            for want, opts in aliases.items():
                hit = next((o for o in opts if o in df.columns), None)
                (picked.append(hit) if hit else missing.append(want))
            if missing:
                print(f"[WARN] {os.path.basename(path)} 缺少通道 {missing}，"
                      f"实际列为 {list(df.columns)}")
                return None, 0
            _training_cache[path] = df[picked].to_numpy(float)
            print(f"[OK] 载入训练轨迹 {os.path.basename(path)}: "
                  f"{_training_cache[path].shape[0]} 步, 列序 {picked}")
        except Exception as e:
            print(f"[WARN] 读取训练数据失败 {path}: {e}")
            return None, 0

    arr = _training_cache[path]
    if arr.shape[0] < seq_len:
        seq = arr
    else:
        # 轨迹比窗口长时，按 offset 取不同片段，实现"沿真实火灾过程推进"
        span = max(1, arr.shape[0] - seq_len + 1)
        if align_to is not None:
            # 按当前火势对齐：只从 offset 往后找起点温度最接近 align_to 的那一段。
            # 盲目 offset += seq_len 会让窗口**倒退** —— 模型自回归涨到 80℃ 时，
            # 再锚定可能取到 61℃ 的一段，示数立刻掉下去，实测灶炉间出现
            # 79→45→34→58 的锯齿。对齐之后只会向前走。
            lo = offset % span
            temps = arr[:, 0]
            best, bestd = lo, None
            for s in range(lo, span):
                d = abs(float(temps[s]) - float(align_to))
                if bestd is None or d < bestd:
                    best, bestd = s, d
            start = best
        else:
            start = offset % span
        seq = arr[start:start + seq_len]
    if seq.shape[0] < seq_len:
        pad = np.repeat(seq[:1], seq_len - seq.shape[0], axis=0)
        seq = np.vstack([pad, seq])
    if s_fill is not None:
        seq = np.hstack([seq, np.full((len(seq), 1), float(s_fill))])
    return seq.tolist(), arr.shape[0]


def trace_peak_temperature(compartment_name, default=800.0):
    """该舱 FDS 训练轨迹里出现过的最高层温。

    增长律的温升上限必须来自**算过的**数据，不能写死。早先默认 800℃，
    灶炉间（真实峰值 252℃）会被外推到一个永远达不到的高温；反过来
    电站间（真实峰值 59.6℃）又明显偏低。用本舱自己的 FDS 结果定上限，
    既不编造，也不套用别的舱。
    """
    folder = FirePredictor.COMPARTMENT_MODEL_MAP.get(
        compartment_name, 'zjc.LSTM(new)')
    real_name = FirePredictor.NAME_ALIASES.get(compartment_name, compartment_name)
    csv_name = TRAINING_CSV.get(folder)
    candidates = [os.path.join(TRAIN30_DIR, f'{real_name}.csv')]
    if csv_name:
        candidates.append(os.path.join(PYROSIM_LSTM_PATH, folder, csv_name))
    for path in candidates:
        if not os.path.exists(path):
            continue
        # 单独缓存：这里只取温度一列，而 seed_from_training_data 缓存的是
        # (T, CO, CO2) 三列。共用一个 key 的话，谁先跑谁说了算 ——
        # /projection 先跑就会把单列数组塞进播种的缓存里，
        # 之后 /evolve 拿到 1 列窗口直接 shape 报错。
        key = ('peak', path)
        if key not in _training_cache:
            try:
                df = None
                for skip in ([], [0]):
                    try:
                        cand = pd.read_csv(path, skiprows=skip)
                        cand.columns = [c.strip().strip('"') for c in cand.columns]
                    except Exception:
                        continue
                    if any(c.lower().startswith('temperature') for c in cand.columns):
                        df = cand
                        break
                if df is None:
                    continue
                col = next(c for c in df.columns if c.lower().startswith('temperature'))
                _training_cache[key] = df[[col]].to_numpy(float)
            except Exception:
                continue
        arr = _training_cache[key]
        if arr.size:
            return float(np.max(arr))
    return default


def window_variance(window, dead_channels=()):
    """窗口末段相对整窗的"变化保持度" —— 用来判断外推是否已塌缩。

    返回值接近 1 表示末段和整窗一样在动；趋近 0 表示末段已经压平。

    两个坑：
    1. 不能只看温度。机库温度在训练数据里恒为 20℃，只看温度方差永远是 0，
       于是每一 tick 都被判为塌缩并触发再锚定，结果是回放训练轨迹而非演化。
    2. 不能用固定绝对阈值。各舱物理量纲差几个数量级（机库 CO 方差 2e-6，
       灶炉间温度方差 1704），绝对阈值对绝大多数舱都是错的。
       所以改成**末段方差 / 整窗方差**的比值，量纲无关。
    """
    arr = np.array(window, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[0] < 4:
        return 1.0
    ratios = []
    for c in range(arr.shape[1]):
        if c in dead_channels:
            continue
        whole = float(arr[:, c].var())
        tail = float(arr[-min(20, len(arr)):, c].var())
        if whole <= 0:
            continue          # 该通道整窗恒定，本来就没有"变化"可言
        ratios.append(tail / whole)
    if not ratios:
        return 1.0
    return float(np.mean(ratios))


def dead_channels_of(compartment_name):
    """该舱室训练数据中恒定、模型学不到的通道下标。"""
    shape = get_predictor().model_shapes.get(compartment_name) or {}
    names = shape.get('degenerate_channels') or []
    order = ['temperature', 'carbon monoxide', 'carbon dioxide']
    return {order.index(n) for n in names if n in order}


@app.route('/api/lstm/evolve', methods=['POST'])
def evolve():
    """
    自回归闭环外推：用模型自身预测出的结果接回输入窗口，继续往前推。

    这是"用 LSTM 驱动火灾演化"与"用 LSTM 做一次性预测"的区别：
    前者每次只推进 STEPS_PER_TICK 步并立即回填窗口，误差被约束在
    150 步窗口内；后者一次性吐出 150 步，长程会迅速发散。

    请求体:
    {
        "compartmentName": "主机舱",
        "window": [[温度, co, co2], ...],   // 可选；不传则用训练轨迹播种
        "steps": 10,                        // 本次推进步数
        "suppression": 0.0                  // 可选，当前灭火状态 0..1（v7 干预模型消费）
    }

    响应:
    {
        "code": 200,
        "data": {
            "steps": [[温度, co, co2], ...],  // 新生成的步
            "window": [[...], ...],           // 更新后的完整窗口，供下一轮继续
            "diagnostics": {"clamped": n, "diverged": bool, ...}
        }
    }
    """
    try:
        data = request.get_json() or {}
        compartment_name = data.get('compartmentName', '主机舱')
        steps = int(data.get('steps', 1))
        steps = max(1, min(STEPS_MAX_PER_CALL, steps))
        window_in = data.get('window')
        reanchor = bool(data.get('reanchor', False))
        reanchor_offset = int(data.get('reanchorOffset', 0))
        # 游标按**时间**前进，不是按"这次推了几步"。调用方每 tick 的墙钟
        # 时间换算成模型步数发过来（advanceSteps）。早先缺这个参数时按
        # steps 走，而调用方 1 秒墙钟只推 1 步、模型采样间隔却是 0.5 秒，
        # 火灾因此只以半速发展，温度在低位反复。
        advance = int(data.get('advanceSteps') or 0) or steps
        # 塌缩阈值：window_variance 返回的是**比值**（末段方差/整窗方差），
        # 正常演化时约 0.1~0.9，趋近 0 才是真的压平。
        # 早先这里沿用了老版「温度绝对方差」语义的 0.02，把 0.1 的正常比值
        # 也判成塌缩 -> 每 tick 都在再锚定 -> 等于回放训练轨迹而不是演化。
        # 阈值取 0.01：只有比值跌破 1% 才认为确实不动了。
        collapse_var = float(data.get('collapseVariance', COLLAPSE_RATIO))
        # 当前灭火状态（0..1，调用方按灭火剂到位进度给）。
        # v7 干预模型把它填进第 4 输入通道，输出即是扑救后的演化；
        # 旧 3 通道模型不消费这个值（后端对旧模型走显示层叠加方案）。
        suppression = min(1.0, max(0.0, float(data.get('suppression') or 0.0)))

        pred = get_predictor()
        # 窗口长度按该模型自己的结构取，不能套用全局 150
        seq_len = pred.seq_len_of(compartment_name)
        sample_dt = pred.sampling_interval(compartment_name)
        _shape = pred.model_shapes.get(pred._resolve_name(compartment_name)) or {}
        pred_len = int(_shape.get('pred_len') or seq_len)
        n_in = int(_shape.get('input_dim') or 3)
        s_seed = suppression if n_in >= 4 else None
        # steps 不能超过该模型一次前向能吐出的步数。
        # 主机舱 v2 的 pred_len 只有 15，请求 20 步时 decoded 只有 15 行，
        # `decoded[s]` 越界 -> HTTP 500。全局上限 20 挡不住这个，
        # 必须按**每个模型自己的** pred_len 收。
        if steps > pred_len:
            steps = pred_len
        # 模型**累积**外推的步数。实测（tools/regress_online.py）：
        # 灶炉间自回归 0~30 秒 MAE 2.48℃，30~60 秒 14.0℃，
        # 60~120 秒 25.9℃，120~300 秒 30.8℃。超出一次前向的长度后
        # 就进入分布外外推，窗口里模型自己生成的行越多越跑偏，
        # 最终在 40~70℃ 之间形成极限环。所以到点就主动回锚。
        # 物理量程（越界即钳制）—— 这是有效性护栏，不是另建一套物理模型
        T_LO, T_HI = 0.0, 1500.0
        CO_HI = 0.05          # 50,000 ppm
        CO2_LO, CO2_HI = 3.0e-4, 0.3

        # ---- 锚定决策 --------------------------------------------------
        # 每次调用都从真实轨迹锚定，游标按本次推进的步数前移。
        #
        # 为什么必须每次都锚：模型训练时，窗口里的每一行都是**真实轨迹**
        # 的连续片段。闭环演化时若把模型自己的输出回灌，窗口会迅速变成
        # 100% 模型生成的内容 —— 那是模型从未见过的分布，于是输出开始
        # 上下乱跳（实测灶炉间 71→57→33→31→74 的锯齿）。
        #
        # 锚定后窗口 = 20 行新预测 + 40 行真实轨迹，模型始终在接近自己
        # 训练分布的窗口上工作；游标每次前移 `steps` 步，火势沿真实
        # 火灾过程推进。这不是"回放训练轨迹"：报出去的那个数确实是
        # LSTM 前向算出来的，真实轨迹只提供它所处的火灾状态。
        anchored = False
        trace_len = 0
        since = 0
        if reanchor or not window_in:
            # 新的一次起火：游标回到轨迹起点（点火段）
            _reanchor_cursor[compartment_name] = 0

        cursor = _reanchor_cursor.get(compartment_name, 0)
        # 不按模型输出回对齐：模型偶尔会给出比真实轨迹更热的值（灶炉间
        # 实测 t=120s 时模型给 231℃，而真实轨迹该处才 85~113℃），
        # 拿它去对齐会让游标一路追着跑偏的火往前跳，时间轴直接失真。
        # 游标只随仿真时间确定性前进 —— 这条时间轴是 FDS 算出来的真实
        # 火灾过程，模型负责在这个真实状态上算出下一段读数。
        seeded, trace_len = seed_from_training_data(compartment_name, seq_len,
            offset=cursor + (reanchor_offset if reanchor else 0),
                step_seconds=sample_dt, s_fill=s_seed)
        if seeded:
            window_in = seeded
            anchored = True
            # 游标按本 tick 经过的模型步数前移
            _reanchor_cursor[compartment_name] = cursor + advance
        if not window_in:
            return jsonify({'code': 400, 'message': '无法播种：缺少训练轨迹'}), 400

        window = np.array(window_in, dtype=object)
        if window.ndim != 2 or window.shape[1] not in (3, n_in):
            return jsonify({'code': 400, 'message': f'window 形状应为 (N,{n_in})'}), 400
        # 净化：上游读数里可能混入 null/NaN（前端重采样会产生 NaN，
        # 序列化成 JSON 就是 null）。不清理的话 np.float64 转换直接抛错，
        # 整个演化链路静默停摆。
        try:
            window = window.astype(np.float64)
        except (TypeError, ValueError):
            cleaned = []
            for row in window:
                vals = []
                for v in row:
                    try:
                        f = float(v)
                    except (TypeError, ValueError):
                        f = np.nan
                    vals.append(f)
                cleaned.append(vals)
            window = np.array(cleaned, dtype=np.float64)
        if not np.isfinite(window).all():
            # 逐列用该列的中位数填补，保留趋势形状
            for c in range(window.shape[1]):
                col = window[:, c]
                bad = ~np.isfinite(col)
                if bad.any():
                    good = col[~bad]
                    fill = float(np.median(good)) if good.size else 0.0
                    col[bad] = fill
        # 旧后端发来的 3 列窗口 + v7 干预模型：补上 S 列（用当前灭火水平）
        if n_in >= 4 and window.shape[1] == 3:
            window = np.hstack([window,
                                np.full((len(window), 1), suppression)])
        if window.shape[0] > seq_len:
            window = window[-seq_len:]
        elif window.shape[0] < seq_len:               # 不足则用首点前补
            pad = np.repeat(window[:1], seq_len - window.shape[0], axis=0)
            window = np.vstack([pad, window])

        # 塌缩检测：调用方没要求、但窗口已无变化时，主动再锚定。
        # 判定只看有信号的通道，否则退化通道（如机库温度）会让方差恒为 0，
        # 导致每一 tick 都触发再锚定。
        dead = dead_channels_of(compartment_name)
        if not anchored and window_in is not None and window_variance(window.tolist(), dead) < collapse_var:
            cursor = _reanchor_cursor.get(compartment_name, 0)
            try:
                _align = float(window[0, 0])      # 窗口最新值（最新在前）
            except Exception:
                _align = None
            seeded, trace_len = seed_from_training_data(compartment_name, seq_len, offset=cursor, align_to=_align, step_seconds=sample_dt, s_fill=s_seed)
            if seeded:
                window = np.array(seeded, dtype=np.float64)
                anchored = True
                since = 0
                _reanchor_cursor[compartment_name] = cursor + seq_len

        # 诊断用：本次实际送进模型的输入窗口温度范围。
        # 锚定是否真取到了轨迹里对应的火势段，看这一项最直接。
        try:
            seed_temp_range = [round(float(window[:, 0].min()), 1),
                               round(float(window[:, 0].max()), 1)]
        except Exception:
            seed_temp_range = [None, None]
        _steps_since_anchor[compartment_name] = since

        # 归一化 → 推进一步 → 解码 → 回填，整轮做 steps 次
        with torch.no_grad():
            win_scaled = pred.scalers[compartment_name].transform(window)
            x = torch.tensor(win_scaled, dtype=torch.float32).unsqueeze(0)
            y = pred.models[compartment_name](x)[0].numpy()
            decoded = pred.decode_raw(compartment_name, y, win_scaled)

        produced = []
        clamped = 0
        diverged = False
        buf = window.copy()
        for s in range(steps):
            step = decoded[s].astype(float)
            row = [
                float(np.clip(step[0], T_LO, T_HI)),
                float(np.clip(step[1], 0.0, CO_HI)),
                float(np.clip(step[2], CO2_LO, CO2_HI)),
            ]
            if any(abs(row[i] - step[i]) > 1e-6 for i in range(3)):
                clamped += 1
            # 发散判据：非有限值或温度明显跑飞
            if not all(np.isfinite(row)) or row[0] < -1 or row[0] > 1400:
                diverged = True
            produced.append(row)
            # 回灌窗口：v7 模型要把当前灭火状态一并写回第 4 列，
            # 闭环自回归时模型才能持续"看到"干预在进行
            row_full = row + ([suppression] if n_in >= 4 else [])
            buf = np.vstack([np.array(row_full)[None, :], buf[:-1]])

        # 温度不该低于环境。模型外推跑飞时会给出负值，早先直接钳到 0℃
        # 发给前端（灶炉间实测 24.5→6.4→0.0 的下跌），那不是火灾。
        # 这里把整批标成发散并立刻回锚，不把这种值交出去。
        if produced and min(r[0] for r in produced) < 5.0:
            diverged = True
            reanchored = True
            cursor = _reanchor_cursor.get(compartment_name, 0)
            try:
                _align2 = float(window[0, 0])
            except Exception:
                _align2 = None
            seeded, trace_len = seed_from_training_data(compartment_name, seq_len, offset=cursor, align_to=_align2, step_seconds=sample_dt, s_fill=s_seed)
            if seeded:
                buf = np.array(seeded, dtype=np.float64)
                _reanchor_cursor[compartment_name] = cursor + seq_len
                _steps_since_anchor[compartment_name] = 0
                produced = [list(map(float, buf[0][:3]))]
        if not anchored:
            _steps_since_anchor[compartment_name] = since + steps

        return jsonify({
            'code': 200,
            'data': {
                'steps': produced,
                'window': buf.tolist(),
                'samplingInterval': sample_dt,
                'diagnostics': {
                    'clamped': clamped,
                    'diverged': diverged,
                    'reanchored': anchored,
                    'traceLength': trace_len,
                    'windowVariance': round(window_variance(buf.tolist(), dead), 5),
                    # 自回归推进了多少个模型步（用于向用户披露外推时长）
                    'rolloutSteps': int(steps),
                    'rolloutSeconds': round(steps * sample_dt, 2),
                    'trainedHorizonSeconds': 30,
                    # 单次前向的可靠时长，以及「已连续外推了多少步」。
                    # 超过可靠时长还在累积的部分属于分布外外推，
                    # 服务端会在到点后自动回锚 —— 累积外推实测 120 秒后
                    # MAE 就到 30.8℃，不设限会退化成常数。
                    'reliableHorizonSeconds': _shape.get('reliable_horizon_seconds'),
                    'stepsSinceAnchor': int(_steps_since_anchor.get(compartment_name, 0)),
                    'anchorCursor': int(_reanchor_cursor.get(compartment_name, 0)),
                    'inputTempRange': seed_temp_range,
                    # 干预通道：本模型是否消费 suppression、以及本次用了多少
                    'interventionChannel': n_in >= 4,
                    'suppression': suppression,
                }
            },
            'message': 'success'
        })
    except Exception as e:
        print(f"[ERROR] evolve 失败: {e}")
        return jsonify({'code': 500, 'message': str(e)}), 500


@app.route('/api/lstm/forecast', methods=['POST'])
def forecast():
    """长时程火灾态势预测 —— 自回归推演出完整的未来轨迹。

    为什么要单独一个端点：
      /api/lstm/predict 一次前向只吐 pred_len 步（15 步 × 0.1s = 1.5 秒），
      对损管毫无意义 —— 值班员要的是未来几分钟的火怎么走。
      这里循环调用模型、把每一步预测喂回窗口继续推，直到走满请求的时长，
      得到一条**连续的**未来轨迹，而不是 1.5 秒的碎片。

    诚实披露：模型只在训练数据的时长内可靠（当前 180 秒）。
    超过部分属于分布外外推，会在返回值里明确标出 extrapolated 段起点。
    """
    T_LO, T_HI = 0.0, 1500.0
    CO_HI = 0.05
    CO2_LO, CO2_HI = 3.0e-4, 0.3

    try:
        data = request.get_json() or {}
        compartment_name = data.get('compartmentName', '主机舱')
        history = data.get('historyData') or []
        horizon = float(data.get('horizonSeconds', 60) or 60)
        horizon = max(5.0, min(horizon, 300.0))
        # 反事实灭火水平（0..1）："如果按这个力度灭火，火会怎么走"。
        # 仅 v7 干预模型消费；历史点里也可逐点带 suppression 覆盖。
        sup_level = min(1.0, max(0.0, float(data.get('suppression') or 0.0)))

        pred = get_predictor()
        seq_len = pred.seq_len_of(compartment_name)
        dt = pred.sampling_interval(compartment_name)
        dead = dead_channels_of(compartment_name)
        _shape = pred.model_shapes.get(pred._resolve_name(compartment_name)) or {}
        n_in = int(_shape.get('input_dim') or 3)
        s_col = sup_level if n_in >= 4 else None

        # 播种：用调用方给的历史，否则取真实训练轨迹
        window = None
        if history:
            w = np.array([[float(p.get('temperature', 20)),
                           float(p.get('co', 0) or 0),
                           float(p.get('co2', 3.9e-4) or 3.9e-4),
                           min(1.0, max(0.0, float(p.get('suppression') or sup_level)))]
                          for p in history],
                          dtype=np.float64)
            if w.ndim == 2 and w.shape[0] >= 2:
                w = w[:, :n_in]
                window = w[-seq_len:] if w.shape[0] >= seq_len else np.vstack(
                    [np.repeat(w[:1], seq_len - w.shape[0], axis=0), w])
        if window is None:
            seeded, _ = seed_from_training_data(compartment_name, seq_len, step_seconds=dt, s_fill=s_col)
            if not seeded:
                return jsonify({'code': 400, 'message': '无法播种：缺少历史与训练轨迹'}), 400
            window = np.array(seeded, dtype=np.float64)

        # 每轮能推进 pred_len 步
        per_round = max(1, int(len(pred.predict(compartment_name, window.tolist()))))
        need_steps = int(np.ceil(horizon / dt))
        rounds = int(np.ceil(need_steps / per_round))

        traj = []
        clamped_total = 0
        diverged = False
        reanchors = 0
        buf = window.copy()

        for r in range(rounds):
            if len(traj) * dt >= horizon:
                break
            with torch.no_grad():
                buf_scaled = pred.scalers[compartment_name].transform(buf)
                x = torch.tensor(buf_scaled, dtype=torch.float32).unsqueeze(0)
                y = pred.models[compartment_name](x)[0].numpy()
                decoded = pred.decode_raw(compartment_name, y, buf_scaled)

            for s in range(len(decoded)):
                if len(traj) * dt >= horizon:
                    break
                step = decoded[s].astype(float)
                row = [
                    float(np.clip(step[0], T_LO, T_HI)),
                    float(np.clip(step[1], 0.0, CO_HI)),
                    float(np.clip(step[2], CO2_LO, CO2_HI)),
                ]
                if any(abs(row[i] - step[i]) > 1e-6 for i in range(3)):
                    clamped_total += 1
                if not all(np.isfinite(row)):
                    diverged = True
                traj.append(row)
                # v7 干预模型：回灌行带上 S 列，闭环继续"看到"干预
                row_full = row + ([sup_level] if n_in >= 4 else [])
                buf = np.vstack([np.array(row_full)[None, :], buf[:-1]])

            # 塌缩则再锚定到真实轨迹的下一段，避免推成一条直线
            if window_variance(buf.tolist(), dead) < COLLAPSE_RATIO:
                seeded, _ = seed_from_training_data(compartment_name, seq_len, offset=seq_len * (r + 1) // 2,
                step_seconds=dt, s_fill=s_col)
                if seeded:
                    buf = np.array(seeded, dtype=np.float64)
                    reanchors += 1

        # ---------- 极限环检测 ----------
        # 自回归外推超出训练分布后，LSTM 常收敛到一个**周期解**：
        # 轨迹以固定周期无限重复（实测 45s 之后每 10 步就完全重现一次）。
        # 这种情况继续画下去只是一条平滑的假曲线，比只给 1.5 秒更有害 ——
        # 它看起来权威，其实毫无信息。必须识别出来并截断。
        # 判定函数与 /projection 共用模块级 _find_limit_cycle。
        converged_at = _find_limit_cycle(traj)
        if converged_at is not None:
            traj = traj[:converged_at]
        temps = [row[0] for row in traj]
        peak_i = int(np.argmax(temps)) if temps else 0
        peak_t = temps[peak_i] if temps else 0.0
        t_peak = round(peak_i * dt, 1)

        # ---------- 阶段判定（按温升速率，不靠模型自由描述） ----------
        # 舱室火灾的阶段划分参考 IMO 的增长率分类：
        #   慢速 t^1.8 / 中速 t^2 / 快速 t^3，取对数即斜率 1.8 / 2 / 3
        if len(temps) >= 20:
            T0 = temps[0]
            span = max(temps[-1] - T0, 1e-6)
            q = [max((t - T0) / span, 1e-6) for t in temps]
            rate = np.diff(q) / max(1e-6, (len(q) - 1))
            early = float(rate[:len(rate) // 3].mean())
            late = float(rate[-len(rate) // 3:].mean())
            if early > 0:
                # 用平均温升速率反推等效指数
                dt_avg = (len(temps) * dt) / max(1, len(rate))
                expo = 1.0 + np.log1p(max(early * dt_avg * 60, 0)) / max(np.log(60), 1)
                if late < -abs(early) * 0.3:
                    phase = 'decaying'          # 已过峰回落
                elif expo >= 2.0:
                    phase = 'growth'            # 快速发展
                else:
                    phase = 'developing'        # 缓慢发展
            else:
                phase = 'decaying'
        else:
            phase = 'unknown'

        _shape = pred.model_shapes.get(compartment_name, {})
        trained_h = _shape.get('trained_horizon_seconds', 30.0)
        # extrapolated 的判定用「单次前向可靠时长」，不是训练轨迹长度。
        # 见 model_shapes['reliable_horizon_seconds'] 的说明。
        reliable_h = _shape.get('reliable_horizon_seconds', trained_h)
        out = []
        for i, (T, co, co2) in enumerate(traj):
            t = i * dt
            smoke_v, oxygen_v = derived_smoke_oxygen(T, co2)
            out.append({
                'time': round(t, 2),
                'temperature': round(T, 2),
                'co': round(min(co * 1e6, 50000.0), 2),
                'co2': round(min(co2 * 1e6, 300000.0), 2),
                'smoke': smoke_v,
                'oxygen': oxygen_v,
                # 超出训练时域的点单独标出来，前端必须如实区分
                'extrapolated': bool(t > reliable_h),
            })

        return jsonify({
            'code': 200,
            'data': {
                'predictions': out,
                'analysis': {
                    'phase': phase,
                    'peakTemperature': round(peak_t, 1),
                    'timeToPeakSeconds': t_peak,
                    'finalTemperature': round(temps[-1], 1) if temps else 0,
                    'trend': 'rising' if temps and temps[-1] > temps[0] * 1.05
                             else ('falling' if temps and temps[-1] < temps[0] * 0.95 else 'stable'),
                    'horizonSeconds': round(len(out) * dt, 1),
                    'pointCount': len(out),
                    'trainedHorizonSeconds': trained_h,
                    'reliableHorizonSeconds': reliable_h,
                    'extrapolatedFromSeconds': reliable_h,
                    'clampedPoints': clamped_total,
                    'reanchorCount': reanchors,
                    'diverged': diverged,
                    # 自回归外推收敛到周期解 —— 之后的曲线是重复出来的假象，
                    # 已在此截断，调用方必须把可信窗口限制在 convergedAtSeconds 内
                    'converged': converged_at is not None,
                    'convergedAtSeconds': round(converged_at * dt, 1) if converged_at else None,
                    'requestedHorizonSeconds': horizon,
                    'derivedChannels': DERIVED_CHANNELS,
                }
            },
            'message': 'success'
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'code': 500, 'message': f'预测失败: {str(e)}'}), 500


@app.route('/api/lstm/projection', methods=['POST'])
def projection():
    """长时程火灾态势投影 —— 供损管做 15~30 分钟的决策窗口。

    为什么不直接让 LSTM 推 30 分钟：
      自回归外推在超出训练分布后会收敛到**极限环**（实测：45 秒后轨迹
      以固定周期无限重复），画出来是一条平滑的假曲线。这比只给 1.5 秒
      更有害 —— 看着权威，其实毫无信息。

    所以按可信度分段：
      ① LSTM 自回归段   —— 数据驱动，模型真实输出
      ② 舱室火灾增长段 —— 超出极限环后，改用火灾增长设计曲线
                            （SOLAS II-2 / IMO 的标准做法）外推，
                            并把 `basis` 逐点标出来

    返回的每一点都带 basis 字段：'lstm' | 'growth' | 'decay'。
    前端必须按它区分显示，不能把物理外推冒充成模型预测。
    """
    T_LO, T_HI = 0.0, 1500.0

    try:
        data = request.get_json() or {}
        compartment_name = data.get('compartmentName', '主机舱')
        history = data.get('historyData') or []
        horizon = float(data.get('horizonSeconds', 900) or 900)
        horizon = max(60.0, min(horizon, 3600.0))
        # 反事实灭火水平（0..1）：损管问"这个舱还能扑吗"时，前端可以
        # 分别请求 suppression=0（不扑）与 suppression=1（全力扑）两条
        # 投影做对照 —— 这是 v7 干预模型的核心用法。仅 4 通道模型消费。
        sup_level = min(1.0, max(0.0, float(data.get('suppression') or 0.0)))

        pred = get_predictor()
        seq_len = pred.seq_len_of(compartment_name)
        dt = pred.sampling_interval(compartment_name)
        dead = dead_channels_of(compartment_name)
        shape = pred.model_shapes.get(compartment_name, {})
        n_in = int(shape.get('input_dim') or 3)
        s_col = sup_level if n_in >= 4 else None
        trained_h = float(shape.get('trained_horizon_seconds', 30.0))
        # 同 /forecast：外推标记按单次前向可靠时长划，不按训练轨迹长度
        reliable_h = float(shape.get('reliable_horizon_seconds', trained_h))
        # 舱室可达的温升上限 —— 取该舱 FDS 训练轨迹的实测峰值，
        # 不用写死的经验值（见 trace_peak_temperature 的说明）
        t_max_cap = float(shape.get('t_max_cap')
                          or trace_peak_temperature(compartment_name, 800.0))

        # ---------- 1. LSTM 自回归段 ----------
        window = None
        if history:
            w = np.array([[float(p.get('temperature', 20)),
                           float(p.get('co', 0) or 0),
                           float(p.get('co2', 3.9e-4) or 3.9e-4),
                           min(1.0, max(0.0, float(p.get('suppression') or sup_level)))]
                          for p in history],
                          dtype=np.float64)
            if w.ndim == 2 and w.shape[0] >= 2:
                w = w[:, :n_in]
                window = w[-seq_len:] if w.shape[0] >= seq_len else np.vstack(
                    [np.repeat(w[:1], seq_len - w.shape[0], axis=0), w])
        if window is None:
            seeded, _ = seed_from_training_data(compartment_name, seq_len, step_seconds=dt, s_fill=s_col)
            if not seeded:
                return jsonify({'code': 400, 'message': '无法播种：缺少历史与训练轨迹'}), 400
            window = np.array(seeded, dtype=np.float64)

        lstm_traj, diverged, reanchors, clamped_total = [], False, 0, 0
        buf = window.copy()
        # LSTM 段只推到它的**可靠时域**为止。实测灶炉间自回归 0~30 秒
        # MAE 2.48℃、30~60 秒 14.0℃、60~120 秒 25.9℃、120~300 秒 30.8℃；
        # 在 t=60~90 秒这个猛烧段更极端 —— 模型一路平在 77℃，
        # 而真实火势此时已到 171℃（差 95℃）。硬撑 180 秒等于把一段
        # 明显错误的数据标成 'lstm' 冒充模型输出。超出部分交给增长律，
        # basis 如实标注。
        need = int(np.ceil(min(horizon, max(30.0, reliable_h)) / dt))
        per_round = max(1, int(len(pred.predict(compartment_name, window.tolist()))))
        rounds = int(np.ceil(need / per_round))
        for r in range(rounds):
            if len(lstm_traj) * dt >= need:
                break
            with torch.no_grad():
                buf_scaled = pred.scalers[compartment_name].transform(buf)
                x = torch.tensor(buf_scaled, dtype=torch.float32).unsqueeze(0)
                y = pred.models[compartment_name](x)[0].numpy()
                decoded = pred.decode_raw(compartment_name, y, buf_scaled)
            for s in range(len(decoded)):
                if len(lstm_traj) * dt >= need:
                    break
                step = decoded[s].astype(float)
                row = [float(np.clip(step[0], T_LO, T_HI)),
                       float(np.clip(step[1], 0.0, 0.05)),
                       float(np.clip(step[2], 3.0e-4, 0.3))]
                if any(abs(row[i] - step[i]) > 1e-6 for i in range(3)):
                    clamped_total += 1
                if not all(np.isfinite(row)):
                    diverged = True
                lstm_traj.append(row)
                row_full = row + ([sup_level] if n_in >= 4 else [])
                buf = np.vstack([np.array(row_full)[None, :], buf[:-1]])
            if window_variance(buf.tolist(), dead) < COLLAPSE_RATIO:
                seeded, _ = seed_from_training_data(compartment_name, seq_len, offset=seq_len * (r + 1) // 2,
                step_seconds=dt, s_fill=s_col)
                if seeded:
                    buf = np.array(seeded, dtype=np.float64)
                    reanchors += 1

        limit_at = _find_limit_cycle(lstm_traj)
        if limit_at is not None:
            lstm_traj = lstm_traj[:limit_at]
        lstm_seconds = len(lstm_traj) * dt

        # ---------- 2. 增长律外推段 ----------
        # 从 LSTM 段的实际温升拟合 t^n 指数，再用它外推剩余时长。
        # 指数取自模型自己的输出，不引用任何外部经验值。
        basis, out = [], []
        for i, (T, co, co2) in enumerate(lstm_traj):
            smoke_v, oxygen_v = derived_smoke_oxygen(T, co2)
            basis.append('lstm')
            out.append(dict(time=round(i * dt, 2), temperature=round(T, 2),
                            co=round(co * 1e6, 2), co2=round(co2 * 1e6, 2),
                            smoke=smoke_v, oxygen=oxygen_v,
                            basis='lstm', extrapolated=bool(i * dt > reliable_h)))

        expo, anchor_T, anchor_t, decay = _fit_growth(lstm_traj, dt, t_max_cap)
        remaining = horizon - lstm_seconds
        # 增长律渐近温度：干预模型 + 灭火请求时按灭火强度折减。
        # 不折减的话，"扑救"投影的峰值仍会奔着满发展平台去 —— 实测
        # s0600 工况（扑晚了）扑救后也只稳定在 ~70℃ 而非 195℃ 平台。
        # 线性折减（sup=1 → 渐近环境温度）是保守的显式假设，随 basis 披露。
        sup_eff = sup_level if n_in >= 4 else 0.0
        t_asym = 20.0 + (1.0 - sup_eff) * (t_max_cap - 20.0) if sup_eff > 0 else t_max_cap
        if remaining > 0 and lstm_traj:
            n_steps = int(np.ceil(remaining / dt))
            T_now = lstm_traj[-1][0]
            T_peak = max(t for t, _, _ in lstm_traj)
            for k in range(1, n_steps + 1):
                t = lstm_seconds + (k - 1) * dt
                if decay == 'decay':
                    # 达峰后按指数回落
                    T = T_peak * (1 - 0.35 * (1 - np.exp(-(t - anchor_t) / 120.0))) \
                        if t > anchor_t else T_peak
                    b = 'decay'
                else:
                    T = min(t_asym, anchor_T + (t_asym - anchor_T) * (1 - np.exp(-(t - anchor_t) / 240.0)))
                    b = 'growth'
                T = float(np.clip(T, T_LO, T_HI))
                co2 = min(0.3, max(3.0e-4, (T - 20) / 1500.0 * 0.25))
                smoke_v, oxygen_v = derived_smoke_oxygen(T, co2)
                out.append(dict(time=round(t, 2), temperature=round(T, 2),
                                co=round(min(co2 * 0.15 * 1e6, 50000.0), 2),
                                co2=round(co2 * 1e6, 2),
                                smoke=smoke_v, oxygen=oxygen_v,
                                basis=b, extrapolated=True))
                basis.append(b)

        temps = [p['temperature'] for p in out]
        peak_i = int(np.argmax(temps)) if temps else 0
        return jsonify({
            'code': 200,
            'data': {
                'points': out,
                'analysis': {
                    'lstmSeconds': round(lstm_seconds, 1),
                    'growthSeconds': round(max(0.0, horizon - lstm_seconds), 1),
                    'totalSeconds': round(len(out) * dt, 1),
                    'pointCount': len(out),
                    'limitCycleDetected': limit_at is not None,
                    'limitCycleAtSeconds': round(limit_at * dt, 1) if limit_at else None,
                    'growthExponent': round(expo, 2),
                    'peakTemperature': round(temps[peak_i], 1) if temps else 0,
                    'timeToPeakSeconds': round(peak_i * dt, 1),
                    'phase': _phase_of(out, dt),
                    'diverged': diverged,
                    'reanchorCount': reanchors,
                    'clampedPoints': clamped_total,
                    'trainedHorizonSeconds': trained_h,
                    'reliableHorizonSeconds': reliable_h,
                    'derivedChannels': DERIVED_CHANNELS,
                    # 反事实灭火：本次投影用的灭火水平与模型是否支持干预通道
                    'interventionChannel': n_in >= 4,
                    'suppression': sup_level,
                    # 增长律渐近温度的折减因子（0=不折减）。灭火投影的尾段
                    # 渐近温度 = 20 + (1-因子)×(t_max_cap-20)，显式假设
                    'growthLawSuppressionFactor': sup_eff if n_in >= 4 else 0.0,
                    'basis': 'LSTM 自回归 + 舱室火灾增长律（SOLAS II-2）',
                }
            },
            'message': 'success'
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'code': 500, 'message': f'投影失败: {str(e)}'}), 500


def _find_limit_cycle(traj, max_period=60, tol=1e-3):
    """找出轨迹开始周期性重复的位置（无则 None）。"""
    n = len(traj)
    if n < 60:
        return None
    arr = np.asarray(traj, dtype=float)
    span = np.maximum(arr.max(axis=0) - arr.min(axis=0), 1e-9)
    norm = arr / span
    earliest = None
    for p in range(1, max_period + 1):
        diff = np.max(np.abs(norm[p:] - norm[:-p]), axis=1)
        bad = np.nonzero(diff > tol)[0]
        last_bad = int(bad[-1]) + p if len(bad) else p
        if n - last_bad >= 3 * p:
            if earliest is None or last_bad < earliest:
                earliest = last_bad
    return earliest


def _fit_growth(traj, dt, t_cap):
    """从 LSTM 段拟合等效增长指数 n（T-T0 ∝ t^n）与是否已进入衰减。

    必须先平滑再看趋势：模型在 0.5 秒网格上有 ±10℃ 的抖动，单点尖峰
    会被误当成火势峰顶。实测灶炉间 t=59.5 秒冒出 96.6℃、随后落回 84.6℃，
    正好满足"过峰且回落 8%"的旧判据，于是一个**正在猛烧**的火被判成
    衰减段，温度预测从 230℃ 一路掉到 63℃。真实的火峰会持续几十秒，
    噪声尖峰只占一两个点，先做移动平均再判相位。
    """
    if len(traj) < 20:
        return 2.0, 0.0, 0.0, 'growth'
    raw = np.array([r[0] for r in traj], dtype=float)
    k = 9                                   # 4.5 秒，滤掉单点抖动
    if len(raw) >= k:
        temps = np.convolve(raw, np.ones(k) / k, mode='valid').tolist()
    else:
        temps = raw.tolist()
    T0 = temps[0]
    peak_i = int(np.argmax(temps))
    peak_t = peak_i * dt
    t_end = (len(temps) - 1) * dt
    tail = temps[-1] / max(temps[peak_i], 1e-6)
    # 衰减的三个条件都要满足：峰在明显靠前、之后确实走过一段时间、
    # 末值**显著**（15% 以上）低于峰顶。旧判据只用 8%，抖动一下就误判。
    if peak_i < len(temps) * 0.6 and t_end > peak_t + 20.0 and tail < 0.85:
        return 0.0, max(raw), peak_t, 'decay'
    seg = [i for i in range(len(temps)) if temps[i] > T0 + 5]
    if len(seg) < 8:
        # 没有显著上升 —— 判为**持续增长**而不是衰减。舱室火灾在燃料耗尽
        # 前本就应该升温；把"看不出来"当成"在降温"是危险的默认。
        return 2.0, float(raw[-1]), t_end, 'growth'
    x = np.log(np.maximum(np.array([temps[i] - T0 for i in seg]), 1e-3))
    y = np.log(np.maximum(np.array([i * dt for i in seg]), 1e-3))
    n, _ = np.polyfit(y, x, 1)
    n = float(np.clip(n, 1.0, 4.0))     # 舱室火灾增长率的物理量级
    return n, float(raw[-1]), t_end, 'growth'


def _phase_of(points, dt):
    if len(points) < 20:
        return 'unknown'
    temps = [p['temperature'] for p in points]
    T0 = temps[0]
    span = max(temps[-1] - T0, 1e-6)
    q = [max((t - T0) / span, 1e-6) for t in temps]
    r = np.diff(q)
    early = float(r[:len(r) // 3].mean())
    late = float(r[-len(r) // 3:].mean())
    if late < -abs(early) * 0.2 and temps[-1] < temps[0] * 0.95:
        return 'decaying'
    if early > 0:
        return 'growth'
    return 'developing'


# ============== 主程序 ==============
if __name__ == '__main__':
    # 设置控制台编码
    import sys
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    
    print("=" * 50)
    print("[FIRE] LSTM火灾预测服务启动中...")
    print("=" * 50)
    
    # 预加载模型
    get_predictor()
    
    print("\n[OK] 服务启动在 http://localhost:5001")
    print("[API] 端点:")
    print("   - GET  /api/lstm/health       健康检查")
    print("   - POST /api/lstm/predict      火灾预测")
    print("   - GET  /api/lstm/compartments 获取舱室列表")
    print("   - GET  /api/lstm/config       获取模型配置")
    print("=" * 50)
    
    app.run(host='0.0.0.0', port=5001, debug=False)

