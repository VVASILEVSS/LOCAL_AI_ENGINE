# Usage: python indicator_test_full_trace.py data.csv 15m trace_out.csv
# Robust trace generator that avoids pandas parse_dates warnings and uses
# compute_full_internal_trace from indicator_test_full.py
import sys
import pandas as pd
from indicator_test_full import compute_full_internal_trace

def safe_read_csv_with_time(path):
    # Read without parse_dates to avoid pandas compatibility issues,
    # then convert 'time' column to datetime if present.
    df = pd.read_csv(path)
    if 'time' in df.columns:
        df['time'] = pd.to_datetime(df['time'], errors='coerce')
    return df

if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python indicator_test_full_trace.py data.csv <profile> out_trace.csv")
        sys.exit(1)

    infile = sys.argv[1]
    profile = sys.argv[2]
    outfile = sys.argv[3]

    try:
        df = safe_read_csv_with_time(infile)
    except Exception as e:
        print(f"Failed to read input CSV '{infile}': {e}", file=sys.stderr)
        sys.exit(2)

    try:
        trace_df = compute_full_internal_trace(df, profile=profile)
    except Exception as e:
        print(f"compute_full_internal_trace failed: {e}", file=sys.stderr)
        sys.exit(3)

    try:
        trace_df.to_csv(outfile, index=False)
    except Exception as e:
        print(f"Failed to write trace CSV '{outfile}': {e}", file=sys.stderr)
        sys.exit(4)

    # quick summary
    try:
        total = len(trace_df)
        divs = trace_df['ad_divergence'].fillna('none').astype(str).ne('none').sum()
        strong = trace_df['ad_confirmation'].fillna('none').astype(str).str.contains('strong', na=False).sum()
        print(f"Wrote {outfile} (rows={total}, divergences={divs}, strong_confirmations={strong})")
    except Exception:
        # If summary fails, still print success
        print("Wrote", outfile)