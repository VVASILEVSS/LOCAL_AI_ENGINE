# test_prefixes.py
# Run compute_full on increasing prefixes to find minimum prefix length that returns dict or raises.
import sys
import pandas as pd
from indicator_test_full import compute_full

if len(sys.argv) < 3:
    print("Usage: python test_prefixes.py data.csv profile")
    sys.exit(1)

infile = sys.argv[1]
profile = sys.argv[2]

df = pd.read_csv(infile)
if 'time' in df.columns:
    df['time'] = pd.to_datetime(df['time'], errors='coerce')

n = len(df)
check_list = [10, 15, 20, 25, 30, 40, 50, 75, 100, min(200, n), n]

print(f"Total rows={n}. Testing prefixes (counts): {check_list}")
for L in check_list:
    if L > n:
        continue
    sub = df.iloc[:L].reset_index(drop=True)
    try:
        out = compute_full(sub, profile=profile)
        ok = isinstance(out, dict)
        print(f"prefix {L:4d}: type={type(out).__name__}, is_dict={ok}")
        if ok:
            # print a couple values for context
            print("  sample:", {k: out.get(k) for k in ('ad_bias','ad_confirmation','ad_divergence','ad_regime')})
    except Exception as e:
        print(f"prefix {L:4d}: RAISED -> {e.__class__.__name__}: {e}")