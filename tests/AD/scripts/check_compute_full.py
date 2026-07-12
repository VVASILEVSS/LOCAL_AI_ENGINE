# check_compute_full.py
# Quick check: call compute_full on the whole CSV and print result (type + keys).
import sys
import pandas as pd
from indicator_test_full import compute_full

if len(sys.argv) < 3:
    print("Usage: python check_compute_full.py data.csv profile")
    sys.exit(1)

infile = sys.argv[1]
profile = sys.argv[2]

df = pd.read_csv(infile)
# ensure time parsed if exists
if 'time' in df.columns:
    df['time'] = pd.to_datetime(df['time'], errors='coerce')

print(f"Loaded {infile} rows={len(df)}; calling compute_full(profile={profile}) ...")
try:
    out = compute_full(df, profile=profile)
    print("compute_full returned type:", type(out))
    if isinstance(out, dict):
        keys = list(out.keys())
        print("Keys:", keys)
        # print a few key-values
        for k in keys:
            v = out[k]
            print(f"{k}: {repr(v)}")
    else:
        print("Non-dict return value:", repr(out))
except Exception as e:
    import traceback
    print("compute_full raised exception:")
    traceback.print_exc()