import pandas as pd
import numpy as np

df = pd.read_csv('results/features.csv')
print(f'Before: {len(df)} rows, {len(df.columns)} cols')

leak = ['mfe_pct','mae_pct','price_min','price_max','row_idx','candidate_type']
drop = [c for c in leak if c in df.columns]
df.drop(columns=drop, inplace=True)
print(f'Removed leakage: {drop}')

print()
print('=== LABELS PER SYMBOL ===')
if 'symbol' in df.columns and 'label_value' in df.columns:
    s = df.groupby('symbol')['label_value'].agg(['count','sum','mean'])
    s.columns = ['total','wins','wr']
    print(s.to_string())

meta = [c for c in df.columns if c in ('symbol','tf_profile')]
feature_cols = [c for c in df.columns if c not in meta + ['label_value']]
print(f'Feature columns: {len(feature_cols)}')

corr = df[feature_cols + ['label_value']].corr(numeric_only=True)['label_value'].abs().drop('label_value').sort_values(ascending=False)
print()
print('=== TOP 30 FEATURES by correlation ===')
print(corr.head(30).to_string())

from sklearn.feature_selection import VarianceThreshold
X = df[feature_cols].select_dtypes(include=[np.number]).fillna(0)
vt = VarianceThreshold(threshold=0.01)
vt.fit(X)
keep_mask = vt.get_support()
low_var = [f for f,m in zip(feature_cols, keep_mask) if not m]
print(f'Low variance removed ({len(low_var)}): {low_var[:10]}...')

X_keep = X.loc[:, keep_mask]
keep_cols = list(X_keep.columns)
corr_matrix = X_keep.corr().abs()
upper = corr_matrix.where(np.triu(np.ones(corr_matrix.shape), k=1).astype(bool))
to_drop = [c for c in upper.columns if any(upper[c] > 0.95)]
print(f'High correlation removed ({len(to_drop)}): {to_drop[:10]}...')

final_features = [c for c in keep_cols if c not in to_drop]
print(f'Final: {len(final_features)} features')
print(final_features)

df_clean = df[meta + final_features + ['label_value']]
df_clean.to_csv('results/features_clean.csv', index=False)
print(f'Saved: results/features_clean.csv ({len(df_clean)} rows, {len(df_clean.columns)} cols)')
