"""
Патчер для ollama_client.py:
1. Добавить _zone_label() — функция вызвана но не определена (NameError)
2. Добавить иерархию сигналов (P0-5)
3. Исправить trailing `...` на `else: pass` (syntax error)
"""

import re

FILE = "/home/z/my-project/LOCAL_AI_ENGINE/core/ollama_client.py"

with open(FILE, "r", encoding="utf-8") as f:
    content = f.read()

# ===== FIX 1: trailing `...` -> `else: pass` =====
old_bare = """        elif "abc вверх" in wave_comment:
            data["abc_risk"] = "abc_risk_up"
        ..."""

new_bare = """        elif "abc вверх" in wave_comment:
            data["abc_risk"] = "abc_risk_up"
        else:
            pass"""

if old_bare in content:
    content = content.replace(old_bare, new_bare)
    print("FIX 1: trailing ... -> else: pass  OK")
else:
    print("FIX 1: trailing ... NOT FOUND (may already be fixed)")

# ===== FIX 2: _zone_label() before section 5.1 =====
zone_label_fn = '''    def _zone_label(tf_str: str) -> str:
        """Нормализация имени ТФ для компактных меток."""
        tf = str(tf_str).strip().upper().replace("MIN", "M")
        label_map = {"5M": "5M", "15M": "15M", "1H": "1H", "4H": "4H", "1D": "1D"}
        return label_map.get(tf, tf)

'''

old_51 = "    # -----------------------------\n    # 5.1) Merge close tf zones\n    # -----------------------------"
new_51 = zone_label_fn + old_51

if old_51 in content:
    content = content.replace(old_51, new_51, 1)
    print("FIX 2: _zone_label() added  OK")
else:
    print("FIX 2: section 5.1 NOT FOUND")

# ===== FIX 3: Signal hierarchy (P0-5) =====
signal_hierarchy = '''
    # -----------------------------
    # 8.1) Иерархия сигналов (P0.1 из ТЗ)
    # Если current_substructure противоречит signal_status -
    # приоритет у substructure (более конкретное поле).
    # -----------------------------
    SIGNAL_PRIORITY = {
        "aggressive_breakout": 0,
        "retest": 1,
        "reversal": 2,
        "false_breakout": 3,
        "accumulation": 4,
        "no_signal": 5,
    }
    raw_sub = str(data.get("current_substructure", "")).lower()
    llm_signal = str(data.get("signal_status", "")).lower()

    sub_to_signal = {
        "breakout_up": "aggressive_breakout",
        "breakout_down": "aggressive_breakout",
        "false_breakout_up": "false_breakout",
        "false_breakout_down": "false_breakout",
        "reversal_attempt_up": "reversal",
        "reversal_attempt_down": "reversal",
    }
    if raw_sub in sub_to_signal and llm_signal in SIGNAL_PRIORITY:
        resolved = sub_to_signal[raw_sub]
        if SIGNAL_PRIORITY.get(resolved, 99) < SIGNAL_PRIORITY.get(llm_signal, 99):
            data["signal_status"] = resolved
            data["signal_status_comment"] = (
                f"Иерархия: substructure={raw_sub} приоритетнее signal={llm_signal}"
            )
            signal_status = resolved

'''

old_9 = "    # -----------------------------\n    # 9) Определение направления\n    # -----------------------------"
new_9 = signal_hierarchy + old_9

if old_9 in content:
    content = content.replace(old_9, new_9, 1)
    print("FIX 3: signal hierarchy added  OK")
else:
    print("FIX 3: section 9 NOT FOUND")

with open(FILE, "w", encoding="utf-8") as f:
    f.write(content)

print("\nВсе патчи применены к ollama_client.py")