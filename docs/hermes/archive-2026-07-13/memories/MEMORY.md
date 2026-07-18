Working GLM via alibaba: glm-5.1, glm-5.2, glm-5.2-fast-preview. Z.ai (bigmodel.cn) — balance 0, error 1113, dead. User prefers free/affordable models.
§
STO Zhurnal: SCHEMA_VERSION=8 (schema_version в ТАБЛИЦЕ schema_version MAX(version)). QR=текстовая сводка. Ruff 651 автофикс. constants.py НЕ трогать. license.py TRIAL_DAYS=14 хардкод=норма. reports.py→подпакет.
§
1С Бух КЗ — ИП «ТНВД-СЕРВИС» (ОУР, ИНН 790719302966). Репо: github.com/VVASILEVSS/1C-Buhgalteriya-Kazakhstan-TNVD. Счёт 3300(Кт)=ЛИЧНЫЙ ДОХОД→3350. 3500(Кт)=KASPI PAY→1040. 6000(Кт)=ОБОРОТ. Июль НЕ ТРОГАТЬ. ИПН Кт 3120. Cron: мес.(1–5)+год.(25–31 дек). Skill: 1c-buhgalteriya-kazakhstan-tnvd.
§
LOCAL_AI_ENGINE TG bot: cmd_scan parse pipe+comma+space (7718a51). format_json_for_tg каждый ТФ отдельно (46a0012). enforce_risk_rules (ollama_client.py:~602) → матрёшка (~877) + D1 cap ±10% (~890) + Super Z lower>price ±3% (6227f87). RE-RUN after fallback (ca6936b). ALL charts to LLM (a112f19). HEAD=0c86a09. VP POC (a1ce685, fallback-0). 1D fetch fix (0c86a09, .lower()). timeframes=["15m","1h","4h","1D"].
§
ROADMAP LOCAL_AI_ENGINE: P1=Volume Profile POC ГОТОВО (a1ce685, core/volume_profile.py). P2=Order Block SMC. P3=FVG. Liquidity zones ниже, не дублировать. Order book/Camarilla/Funding/Liq heatmap — НЕТ.
§
STO ROADMAP: Рассылка напоминаний (WhatsApp/TG) в СТО и Мойку. За час/день/нач.дня/конец пред.дня. appointments (reminder_sent, reminder_at). НЕ реализовано.
§
PYTHONPATH contamination от Hermes venv → `PYTHONPATH=""` prefix. Skill: openai-compatible-llm (references/telegram-bot-patterns.md + windows-venv-traps.md).
§
docs/hermes/CONTEXT.md — контекст для новой машины. Z.AI мёртв (balance 0). ollama_service.py:75 URL баг для Z.AI. Hermes v0.18.2 на Asus-pc. `hermes update --check` проверяет без установки.
§
Super Z — коллаборатор (VVASILEVSS/LOCAL_AI_ENGINE), пушит в main. Задания через exchange/outbox/<date>_<task>.md. Коммиты SZ: 7d35c6a, 46a0012, 5db4ed3, 97dffe9, 6227f87, f7844cc.
§
_fill_missing_tf_zones (web_dashboard.py:~315): fallback 0=VP POC → 1=ZigZag → 2=prev → N/A. _find_zone case-insensitive.