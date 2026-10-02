import pandas as pd, os
ROOT = r"D:\PyrosimLSTM"
for folder, fn in (("zjc.LSTM(new)","\u4e3b\u673a\u8231.csv"),("JK_LSTM","\u673a\u5e93.csv"),
                   ("SBZV.lstm","\u58eb\u5175\u4f4f\u8231.csv"),("LZJ.LSTM","\u7089\u7076\u95f4.csv")):
    p = os.path.join(ROOT, folder, fn)
    df = pd.read_csv(p, skiprows=[1])
    cols = [c.strip().strip('"') for c in df.columns]
    d = df[[cols[1], cols[2], cols[3]]]
    t = d.iloc[:,0].to_numpy(float)
    print(f"{folder:14s} cols={cols}")
    print(f"   \u6e29\u5ea6 {t[0]:9.2f} -> {t[-1]:9.2f}  \u5cf0\u503c {t.max():9.2f}  \u552f\u4e00\u503c {len(set(t.round(3)))}")
    for i in (1,2):
        v = d.iloc[:,i].to_numpy(float)
        print(f"   {cols[i][:20]:<20} {v[0]*1e6:9.2f} -> {v[-1]*1e6:9.2f} ppm  \u5cf0\u503c {v.max()*1e6:9.2f}")
    print()