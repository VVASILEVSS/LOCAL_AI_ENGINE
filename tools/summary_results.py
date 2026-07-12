import glob, json, statistics, sys, os

files = sorted(glob.glob("results/*_candidates.json"))
if not files:
    print("No results/*.json found")
    sys.exit(0)

bad = []
for fn in files:
    try:
        st = os.stat(fn)
        if st.st_size == 0:
            print(f"{fn} -- SKIP (empty file)")
            bad.append((fn, "empty"))
            continue
        # use encoding 'utf-8-sig' to tolerate a UTF-8 BOM
        with open(fn, "r", encoding="utf-8-sig") as f:
            j = json.load(f)
    except Exception as e:
        print(f"{fn} -- FAILED to read JSON: {e!r}")
        bad.append((fn, str(e)))
        continue

    cand = j.get("candidates", [])
    n = len(cand)
    ths = []
    for c in cand:
        try:
            ths.append(float(c.get("minFlowAbsThreshold", 0.0)))
        except Exception:
            pass

    print(fn)
    print(" count:", n)
    if ths:
        print(" min:", min(ths), " max:", max(ths), " mean:", statistics.mean(ths))
    print()

if bad:
    print("Problems found with the following files:")
    for fn, reason in bad:
        print(" -", fn, ":", reason)