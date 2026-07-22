import pandas as pd
import numpy as np
df = pd.read_csv('results/features_clean.csv')
sol = df[df.symbol=='SOLUSDT']
print(f'SOL rows: {len(sol)}')
print(f'SOL wins: {sol.label_value.sum()}, losses: {(1-sol.label_value).sum()}')
print(f'SOL tf profiles: {sol.tf_profile.value_counts().to_dict()}')
print()
print('Label stats per TF:')
for tf in sol.tf_profile.unique():
    sub = sol[sol.tf_profile==tf]
    print(f'  {tf}: {len(sub)} rows, WR={sub.label_value.mean():.2f}')
print()
print('Compare with XAUT (72% WR):')
xaut = df[df.symbol=='XAUTUSDT']
print(xaut.groupby('tf_profile')['label_value'].agg(['count','mean']).to_string())
