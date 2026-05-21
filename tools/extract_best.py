import json, os, re, sys

IN = "results/autotune_summary.json"
OUT = "results/autotune_best_params.json"

def symbol_from_path(p):
    # expects paths like "tests/AD/data/BTCUSDT_1h.csv"
    b = os.path.basename(p)
    m = re.match(r"([A-Z0-9]+)_(15m|1h|4h|1d)\.csv", b, re.I)
    if m:
        return m.group(1).upper(), m.group(2)
    # fallback: try split by '_' and take first token
    parts = b.split("_")
    if parts:
        return parts[0].upper(), None
    return b, None

if not os.path.exists(IN):
    print("ERROR: missing", IN, file=sys.stderr)
    sys.exit(2)

j = json.load(open(IN, "r", encoding="utf-8"))
best_map = {}
for key, arr in j.items():
    if not arr:
        continue
    # arr is list of dicts with score/params/info; top element is best (sorted by score desc)
    best = arr[0]
    params = best.get("params", {})
    # key may be stringified tuple like "('tests/AD/data/BTCUSDT_1h.csv', '15m')"
    infile = None
    profile = None
    try:
        tup = eval(key)
        infile, profile = tup
    except Exception:
        # fallback parsing
        parts = key.strip("()").split(",")
        infile = parts[0].strip(" '\"")
        profile = parts[1].strip(" '\"") if len(parts) > 1 else None

    sym, prof_from_name = symbol_from_path(infile)
    profile_key = profile if profile else prof_from_name if prof_from_name else "unknown"
    best_map.setdefault(sym, {})[profile_key] = params

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(best_map, f, indent=2, ensure_ascii=False)
print("WROTE", OUT)