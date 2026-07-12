#!/usr/bin/env python3
"""Тест-кейсы для _detect_direction() — backtest.py"""
import os, sys
os.environ.pop("PYTHONPATH", None)
os.environ.setdefault("TOKEN", "0:A")
os.environ.setdefault("MY_CHAT_ID", "123")
sys.path.insert(0, r"C:\Users\User\LOCAL_AI_ENGINE")

import importlib.util
spec = importlib.util.spec_from_file_location("bt", r"C:\Users\User\LOCAL_AI_ENGINE\core\backtest.py")
bt = importlib.util.module_from_spec(spec); spec.loader.exec_module(bt)

PASS = FAIL = 0
def check(name, signal, parsed, expected):
    global PASS, FAIL
    result = bt._detect_direction(parsed, signal)
    ok = result == expected
    status = "✅" if ok else "❌"
    print(f"  {status} {name}: signal={signal} → {result} (expected {expected})")
    PASS += ok; FAIL += (not ok)

print("=" * 60)
print("_detect_direction() test cases")
print("=" * 60)

# 1. no_signal → flat
check("no_signal → flat", "no_signal", {"trend_structure": "up"}, "flat")

# 2. accumulation → flat
check("accumulation → flat", "accumulation", {"trend_structure": "up"}, "flat")

# 3. aggressive_breakout with trend=up → long (through trend fallback)
check("aggressive_breakout trend=up → long", "aggressive_breakout", {"trend_structure": "up", "ltf_structure": "", "wave_phase": ""}, "long")

# 4. aggressive_breakout with trend=down → short
check("aggressive_breakout trend=down → short", "aggressive_breakout", {"trend_structure": "down", "ltf_structure": "", "wave_phase": ""}, "short")

# 5. aggressive_breakout with no trend info → long (default)
check("aggressive_breakout no info → long (default)", "aggressive_breakout", {"trend_structure": "", "ltf_structure": "", "wave_phase": ""}, "long")

# 6. retest with trend=down → short
check("retest trend=down → short", "retest", {"trend_structure": "down", "ltf_structure": "", "wave_phase": ""}, "short")

# 7. retest with trend=up → long
check("retest trend=up → long", "retest", {"trend_structure": "up", "ltf_structure": "", "wave_phase": ""}, "long")

# 8. false_breakout with ltf=up → short (counter-trend)
check("false_breakout ltf=up → short", "false_breakout", {"trend_structure": "", "ltf_structure": "up", "wave_phase": ""}, "short")

# 9. false_breakout with ltf=down → long
check("false_breakout ltf=down → long", "false_breakout", {"trend_structure": "", "ltf_structure": "down", "wave_phase": ""}, "long")

# 10. false_breakout no info → short (default)
check("false_breakout no info → short (default)", "false_breakout", {"trend_structure": "", "ltf_structure": "", "wave_phase": ""}, "short")

# 11. reversal with wave=down → short
check("reversal wave=down → short", "reversal", {"trend_structure": "", "ltf_structure": "", "wave_phase": "down"}, "short")

# 12. reversal with wave=up → long (default)
check("reversal wave=up → long", "reversal", {"trend_structure": "", "ltf_structure": "", "wave_phase": "up"}, "long")

# 13. unknown → flat
check("unknown → flat", "unknown", {"trend_structure": "up"}, "flat")

# 14. empty string → flat
check("empty → flat", "", {"trend_structure": "up"}, "flat")

# 15. bullish trend via bull keyword
check("generic bull trend → long", "aggressive_breakout", {"trend_structure": "bullish", "ltf_structure": "", "wave_phase": ""}, "long")

# 16. bearish trend via bear keyword
check("generic bear trend → short", "aggressive_breakout", {"trend_structure": "bearish", "ltf_structure": "", "wave_phase": ""}, "short")

# 17. wave_phase impulse_up → long
check("wave impulse_up → long", "aggressive_breakout", {"trend_structure": "", "ltf_structure": "", "wave_phase": "impulse_up"}, "long")

# 18. wave_phase impulse_down → short
check("wave impulse_down → short", "aggressive_breakout", {"trend_structure": "", "ltf_structure": "", "wave_phase": "impulse_down"}, "short")

# 19. current_substructure breakout_up → not used by _detect_direction (only ltf/sub for false_breakout)
check("sub breakout_up via aggressive → long (trend empty, sub not checked for aggressive)", "aggressive_breakout", {"trend_structure": "", "ltf_structure": "", "wave_phase": "", "current_substructure": "breakout_up"}, "long")

# 20. false_breakout with sub=breakout_up (not ltf, but sub) → short
check("false_breakout sub=breakout_up → short", "false_breakout", {"trend_structure": "", "ltf_structure": "", "wave_phase": "", "current_substructure": "breakout_up"}, "short")

print(f"\n{'='*60}")
print(f"RESULTS: {PASS} passed, {FAIL} failed")
print(f"{'='*60}")
sys.exit(0 if FAIL == 0 else 1)
