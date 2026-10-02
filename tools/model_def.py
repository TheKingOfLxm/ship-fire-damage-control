"""Seq2Seq LSTM 模型定义。

与 D:\\PyrosimLSTM 下各舱室原有的 model.py 保持同样的 state_dict 键名，
这样既能加载旧 checkpoint 继续做基线对比，也能保存新模型给 server.py 用。

相对原实现的唯一改动：decoder 输入加上可学习的位置嵌入。
原实现把编码器末状态 repeat 成 150 份完全相同的向量喂给 decoder ——
输入信号恒定，decoder 只能靠自身循环状态演化，表达能力被大幅限制。
加上位置嵌入后，decoder 至少能区分"预测的是第几步"。
"""
import torch
import torch.nn as nn


class Seq2SeqLSTM(nn.Module):
    def __init__(self, input_dim=3, hidden_dim=128, num_layers=2, output_dim=3,
                 seq_len=150, pred_len=150, dropout=0.2):
        super().__init__()
        self.hidden_dim = hidden_dim
        self.num_layers = num_layers
        self.seq_len = seq_len
        self.pred_len = pred_len

        self.encoder_lstm = nn.LSTM(
            input_dim, hidden_dim, num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0,
        )
        self.decoder_lstm = nn.LSTM(
            hidden_dim, hidden_dim, num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0,
        )
        # decoder 步位置嵌入（新增）
        self.pos_embed = nn.Parameter(torch.zeros(pred_len, hidden_dim))
        nn.init.normal_(self.pos_embed, std=0.02)

        self.fc = nn.Linear(hidden_dim, output_dim)
        self.init_weights()

    def init_weights(self):
        for name, param in self.named_parameters():
            if "weight" in name:
                nn.init.xavier_normal_(param)
            elif "bias" in name:
                nn.init.constant_(param, 0.0)

    def forward(self, x):
        encoder_output, (hidden, cell) = self.encoder_lstm(x)
        # 编码器末状态广播到预测长度，再叠加位置嵌入
        decoder_input = encoder_output[:, -1:, :].repeat(1, self.pred_len, 1)
        decoder_input = decoder_input + self.pos_embed.unsqueeze(0)
        decoder_output, _ = self.decoder_lstm(decoder_input, (hidden, cell))
        return self.fc(decoder_output)


class TimeSeriesDataset(torch.utils.data.Dataset):
    def __init__(self, data, seq_len, pred_len):
        self.data = data
        self.seq_len = seq_len
        self.pred_len = pred_len

    def __len__(self):
        return len(self.data) - self.seq_len - self.pred_len + 1

    def __getitem__(self, idx):
        x = self.data[idx:idx + self.seq_len]
        y = self.data[idx + self.seq_len:idx + self.seq_len + self.pred_len]
        return torch.FloatTensor(x), torch.FloatTensor(y)
