import pandas as pd
import numpy as np

df = pd.read_csv('results/features_clean.csv')
print(f'Before: {len(df)} rows')

# Remove SOL (100% WR artifact)
df = df[df.symbol != 'SOLUSDT']
print(f'After removing SOL: {len(df)} rows')

# Stats per symbol
print()
print('=== FINAL LABELS ===')
if 'symbol' in df.columns and 'label_value' in df.columns:
    s = df.groupby('symbol')['label_value'].agg(['count','sum','mean'])
    s.columns = ['total','wins','wr']
    print(s.to_string())
    print(f'Total W/L: {int(df.label_value.sum())}W / {int((1-df.label_value).sum())}L')
    print(f'Overall WR: {df.label_value.mean():.3f}')

# Save final
df.to_csv('results/features_final.csv', index=False)
print(f'Saved: results/features_final.csv ({len(df)} rows, {len(df.columns)} cols)')
print()
print('Columns:', list(df.columns))
