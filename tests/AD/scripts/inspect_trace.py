# inspect_trace.py
import sys
import pandas as pd

def load_df(path):
    # Попробуем с parse_dates; если не выйдет — загрузим без него
    try:
        return pd.read_csv(path, parse_dates=['time'], infer_datetime_format=True)
    except Exception as e:
        print("Warning: parse_dates failed, loading without parse. Error:", e)
        return pd.read_csv(path)

def main():
    if len(sys.argv) < 2:
        print("Usage: python inspect_trace.py <trace_csv>")
        sys.exit(1)

    path = sys.argv[1]
    df = load_df(path)

    cols = ['time','ad_divergence','ad_bias','ad_confirmation','cmf','flow_slope_pct']
    present = [c for c in cols if c in df.columns]
    print(f"Loaded {path} with {len(df)} rows. Columns available: {', '.join(df.columns[:20])}")

    # 1) rows with divergence
    if 'ad_divergence' in df.columns:
        divs = df[df['ad_divergence'].fillna('none') != 'none']
        print(f"\nDivergences found: {len(divs)}")
        if len(divs):
            print(divs[present].head(20).to_string(index=False))
    else:
        print("\nNo column 'ad_divergence' in file.")

    # 2) strong confirmations count
    if 'ad_confirmation' in df.columns:
        strong = df[df['ad_confirmation'].fillna('').str.contains('strong', na=False)]
        print(f"\nStrong confirmations: {len(strong)}")
    else:
        print("\nNo column 'ad_confirmation' in file.")

    # 3) bias changes
    if 'ad_bias' in df.columns:
        prev = df['ad_bias'].shift(1).fillna('')
        changes = df[df['ad_bias'].fillna('') != prev]
        print(f"\nBias changes (total transitions including first): {len(changes)}")
        print("Sample bias-change rows:")
        print(changes[['time','ad_bias']].head(10).to_string(index=False))
    else:
        print("\nNo column 'ad_bias' in file.")

    # 4) quick stats for cmf and flow_slope_pct if present
    if 'cmf' in df.columns:
        print(f"\nCMF stats: mean={df['cmf'].mean():.4f}, min={df['cmf'].min():.4f}, max={df['cmf'].max():.4f}")
    if 'flow_slope_pct' in df.columns:
        print(f"flow_slope_pct stats: mean={df['flow_slope_pct'].mean():.4f}, min={df['flow_slope_pct'].min():.4f}, max={df['flow_slope_pct'].max():.4f}")

if __name__ == '__main__':
    main()