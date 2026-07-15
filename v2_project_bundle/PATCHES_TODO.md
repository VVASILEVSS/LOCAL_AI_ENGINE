# НЕПРИМЕНЁННЫЕ ПАТЧИ (на твоей локальной Windows)
# Дата: 06.06.2026
# ============================================

## ПАТЧ 1: scheduler.py — убрать дубликат ML (УЖЕ НА СЕРВЕРЕ!)
### На сервере строка 356 уже закомментирована.
### На твоей машине найди и закомментируй:
#   python -c "f=open('core/scheduler.py','r',encoding='utf-8'); lines=f.readlines(); [print(f'{i+1}: {l}',end='') for i,l in enumerate(lines) if 'enforce_risk_rules' in l]"
# Найди строку вида:
#                 parsed = enforce_risk_rules(parsed)
# Замени на:
#                 # parsed = enforce_risk_rules(parsed)  # DUPLICATE: already called inside analyze_multi_images

# ЭТО УБРАЛО: двойной ML в автоскане (symbol empty in data)


## ПАТЧ 2: ollama_client.py — lowercase TF для Binance
### Проблема: "Invalid interval" т.к. H1 вместо 1h
### Найди (~строка 1524):
#                     ref_tf = str(data.get("active_reference_tf") or "4h").strip()
### Замени на:
#                     ref_tf = str(data.get("active_reference_tf") or "4h").strip().lower()


## ПАТЧ 3: ollama_client.py — TF из CSV имени + исправить заголовок
### Проблема: ? в заголовке терминала, M15 в TG вместо 4h
### Найди:
#                             ml_log_lines.append(f"  OHLCV paths returned: {csv_paths}")
### ПОСЛЕ неё добавь:
#                             # Extract actual TF from CSV filename
#                             _csv_name = str(csv_paths[0]).replace("\\","/").split("/")[-1].replace(".csv","")
#                             for _t in ["1d","4h","1h","15m"]:
#                                 if _csv_name.endswith("_" + _t):
#                                     ref_tf = _t; break
#                             data["reference_tf"] = ref_tf
#                             _ml_tf = ref_tf
#                             # Fix header (already in ml_log_lines[0])
#                             if ml_log_lines:
#                                 ml_log_lines[0] = f"ML FILTER [PHASE1] {_ml_sym} | {signal_status_ml} | {ref_tf} | price={_ml_price_str}"


## УЖЕ ПРИМЕНЁНО (проверить что на месте):

### Патч 4: OHLCV fallback 28→41 фичей
# context_dict расширен с 28 до 41 фичей
# Исправлены баги: vol_std_pct, volatility_pct, row_atr_pct
# Проверить: в логе должно быть "features=41/50"

### Патч 5: TG формат (compact)
# format_json_for_tg — ML блок уже обновлён
# reference_tf priority > active_reference_tf
# Цена: :,.0f для >100, :.2f для <100

### Патч 6: handlers.py — duplicate enforce_risk_rules
# Уже удалён (остался только комментарий на строке 336)
