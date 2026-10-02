"""
LSTM火灾预测服务器
基于PyrosimLSTM模型提供火灾演化态势预测API
"""

import os
import sys
import json
import torch
import numpy as np
from flask import Flask, request, jsonify
from flask_cors import CORS
from sklearn.preprocessing import StandardScaler

# 添加模型路径
PYROSIM_LSTM_PATH = r"D:\PyrosimLSTM"
sys.path.insert(0, PYROSIM_LSTM_PATH)

app = Flask(__name__)
CORS(app)  # 允许跨域请求

# ============== LSTM模型定义 ==============
class Seq2SeqLSTM(torch.nn.Module):
    """Seq2Seq LSTM模型"""
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
        self.fc = torch.nn.Linear(hidden_dim, output_dim)
        
    def forward(self, x):
        encoder_output, (hidden, cell) = self.encoder_lstm(x)
        decoder_input = encoder_output[:, -1:, :].repeat(1, self.pred_len, 1)
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
        '甲板': 'jiaban_LSTM',
        '电站间': 'zjc.LSTM(new)',  # 使用主机舱模型作为fallback
    }
    
    def __init__(self):
        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        self.models = {}
        self.scalers = {}
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
        """加载所有可用的模型"""
        for compartment_name, model_dir in self.COMPARTMENT_MODEL_MAP.items():
            model_path = os.path.join(PYROSIM_LSTM_PATH, model_dir, 'models', 'best_model.pth')
            if os.path.exists(model_path):
                try:
                    self._load_model(compartment_name, model_path)
                    print(f"[OK] 加载模型成功: {compartment_name}")
                except Exception as e:
                    print(f"[ERROR] 加载模型失败 {compartment_name}: {e}")
            else:
                print(f"[WARN] 模型文件不存在: {model_path}")
    
    def _load_model(self, compartment_name, model_path):
        """加载单个模型"""
        model = Seq2SeqLSTM(
            input_dim=self.config['input_dim'],
            hidden_dim=self.config['hidden_dim'],
            num_layers=self.config['num_layers'],
            output_dim=self.config['output_dim'],
            seq_len=self.config['sequence_length'],
            pred_len=self.config['prediction_length'],
            dropout=0.0
        ).to(self.device)
        
        try:
            checkpoint = torch.load(model_path, map_location=self.device, weights_only=False)
        except:
            checkpoint = torch.load(model_path, map_location=self.device)
        
        model.load_state_dict(checkpoint['model_state_dict'])
        model.eval()
        
        self.models[compartment_name] = model
        self.scalers[compartment_name] = checkpoint['scaler']
    
    def predict(self, compartment_name, input_sequence):
        """
        进行火灾预测
        
        Args:
            compartment_name: 舱室名称
            input_sequence: 输入序列 shape=(seq_len, 3) [温度, CO, CO2]
        
        Returns:
            预测结果 shape=(pred_len, 3)
        """
        # 查找对应的模型
        model = None
        scaler = None
        
        if compartment_name in self.models:
            model = self.models[compartment_name]
            scaler = self.scalers[compartment_name]
        else:
            # 尝试模糊匹配
            for name in self.models.keys():
                if name in compartment_name or compartment_name in name:
                    model = self.models[name]
                    scaler = self.scalers[name]
                    break
        
        # 如果没有找到对应模型，使用主机舱模型作为默认
        if model is None:
            if '主机舱' in self.models:
                model = self.models['主机舱']
                scaler = self.scalers['主机舱']
                print(f"[WARN] 未找到 {compartment_name} 的专用模型，使用主机舱模型")
            else:
                raise ValueError(f"无法找到舱室 {compartment_name} 对应的预测模型")
        
        # 确保输入数据格式正确
        input_array = np.array(input_sequence)
        if input_array.shape[0] < self.config['sequence_length']:
            # 如果数据不足，用最后一个值填充
            padding_length = self.config['sequence_length'] - input_array.shape[0]
            padding = np.tile(input_array[-1:], (padding_length, 1))
            input_array = np.vstack([padding, input_array])
        elif input_array.shape[0] > self.config['sequence_length']:
            # 如果数据太多，只取最后的部分
            input_array = input_array[-self.config['sequence_length']:]
        
        # 进行预测
        with torch.no_grad():
            input_scaled = scaler.transform(input_array)
            input_tensor = torch.FloatTensor(input_scaled).unsqueeze(0).to(self.device)
            prediction_scaled = model(input_tensor)
            prediction_scaled = prediction_scaled.cpu().numpy().squeeze()
            prediction = scaler.inverse_transform(prediction_scaled)
        
        return prediction
    
    def get_available_compartments(self):
        """获取可用的舱室列表"""
        return list(self.models.keys())


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
        'available_compartments': get_predictor().get_available_compartments()
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
        input_sequence = []
        for point in history_data:
            temp = point.get('temperature', 20)
            # 尝试获取CO数据，可能有不同的字段名
            co = point.get('co', point.get('carbonMonoxide', 0))
            co2 = point.get('co2', point.get('carbonDioxide', point.get('smoke', 0) * 0.001))
            
            # 将前端单位转换为模型期望的单位
            # 温度: ℃ (保持不变)
            # CO: kg/m3 (前端是ppm，需要转换)
            # CO2: mol/mol (前端可能是ppm或其他)
            co_converted = co * 1e-6 if co > 0.1 else co  # 如果CO很大，假设是ppm
            co2_converted = co2 * 1e-6 if co2 > 0.1 else co2
            
            input_sequence.append([temp, co_converted, co2_converted])
        
        # 调用预测
        pred = get_predictor()
        prediction = pred.predict(compartment_name, input_sequence)
        
        # 限制预测步数
        if prediction_steps < len(prediction):
            prediction = prediction[:prediction_steps]
        
        # 格式化输出
        predictions = []
        time_step = 0.1  # 每步约0.1秒
        for i, row in enumerate(prediction):
            temp, co, co2 = row
            predictions.append({
                'time': round(i * time_step, 2),
                'temperature': round(float(temp), 2),
                'co': round(float(co) * 1e6, 4),  # 转回ppm
                'co2': round(float(co2) * 1e6, 4),  # 转回ppm
                # 为了与前端兼容，添加烟雾和氧气的估算值
                'smoke': round(float(co2) * 1e6 * 10, 2),  # 估算烟雾浓度
                'oxygen': round(max(15, 21 - float(temp) / 100), 2)  # 估算氧气浓度
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
    """获取模型配置"""
    pred = get_predictor()
    return jsonify({
        'code': 200,
        'data': {
            'sequenceLength': pred.config['sequence_length'],
            'predictionLength': pred.config['prediction_length'],
            'inputDim': pred.config['input_dim'],
            'outputDim': pred.config['output_dim'],
            'timeStepSeconds': 0.1,
            'description': '基于LSTM的火灾演化态势预测模型'
        },
        'message': 'success'
    })


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
    
    print("\n[OK] 服务启动在 http://localhost:5000")
    print("[API] 端点:")
    print("   - GET  /api/lstm/health       健康检查")
    print("   - POST /api/lstm/predict      火灾预测")
    print("   - GET  /api/lstm/compartments 获取舱室列表")
    print("   - GET  /api/lstm/config       获取模型配置")
    print("=" * 50)
    
    app.run(host='0.0.0.0', port=5000, debug=False)

