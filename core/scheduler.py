# core/scheduler.py
# Назначение: периодический анализ рынка и сбор контекста для LLM.
# Отвечает за: запуск auto-analysis, передачу цен, зон, ZigZag-контекста и нормализацию ответа.
# Связан с: ollama_client.py, auto_chart.py, db.py, utils.py.
from datetime import datetime

import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot

from core.db import (
    init_all_tables, save_forecast, update_actual_prices, get_setting, set_setting,
    init_breakout_events_table, save_breakout_event,
    get_pending_breakout_events, confirm_breakout_event, get_breakout_stats,
)
from core.backtest import init_backtest_table, save_signal_log, check_pending_forecasts, get_backtest_context
from core.multi_symbol import get_multi_symbol_context, invalidate_cache as invalidate_multi_cache
from core.auto_chart import fetch_and_plot
from core.state_tracker import (
    update_and_save_state,
    load_state,
    compare_state,
    build_state_context,
)
from core.binance_metrics import fetch_binance_metrics
from core.ollama_client import analyze_multi_images, format_json_for_tg, enforce_risk_rules
from core.config import MY_CHAT_ID, PROMPT_VARIANT, AUTO_SIGNAL_ONLY, ACTIONABLE_SIGNALS
from core.utils import fetch_ticker_safe, format_symbol, sort_timeframes
from core.zigzag.benchmark_zigzag import run_benchmark


logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()


def _get_timeframes() -> list:
    val = get_setting("timeframes", ["15m", "1h", "4h", "1D"])
    return val if isinstance(val, list) else ["15m", "1h", "4h", "1D"]


# Phase 3 MT5: кэш последних результатов анализа для /api/signals (web_dashboard.py)
_last_analysis_cache: dict[str, dict] = {}


def _get_symbols() -> list:
    """Phase 3: читаем из БД, не хардкод."""
    syms = get_setting("symbols", ["BTCUSDT", "XAUTUSDT"])
    return syms if isinstance(syms, list) else ["BTCUSDT", "XAUTUSDT"]


def _build_level_alerts(symbol_id: str, tf_zones: dict, live_price: float,
                        vol_ratio: float, atr: float | None,
                        tf_metrics: dict | None = None) -> tuple[str | None, list[dict]]:
    """
    Phase 3: KX-style alerts — подход к уровню + фиксация пробоя.

    Возвращает (alert_text, breakout_events_to_save).
    Alerts отправляются ВСЕГДА, минуя AUTO_SIGNAL_ONLY.
    """
    if not live_price or not tf_zones:
        return None, []

    alerts: list[str] = []
    new_breakouts: list[dict] = []

    # Динамический порог подхода: max(ATR*1.5, price*1.5%)
    price_float = float(live_price)
    atr_val = float(atr) if atr else price_float * 0.01
    approach_threshold = max(atr_val * 1.5, price_float * 0.015)

    vol_ratio_f = float(vol_ratio) if vol_ratio else 0.0
    vol_confirmed = vol_ratio_f >= 1.0

    # Приоритет TF: M15/H1 → немедленно, H4/D1 → старший
    tf_priority = {"15m": "⚡", "15M": "⚡", "1h": "⚡", "1H": "⚡",
                    "4h": "📊", "4H": "📊", "1D": "📊", "1d": "📊"}
    tf_order = ["15m", "15M", "1h", "1H", "4h", "4H", "1D", "1d"]

    sorted_tfs = sorted(tf_zones.items(),
                        key=lambda x: tf_order.index(x[0]) if x[0] in tf_order else 99)

    for tf, zone in sorted_tfs:
        if not isinstance(zone, dict):
            continue
        upper = zone.get("upper")
        lower = zone.get("lower")
        icon = tf_priority.get(tf, "📍")
        label = format_symbol(symbol_id)

        # --- RESISTANCE (upper) ---
        if upper is not None:
            try:
                u = float(upper)
                if u <= 0:
                    pass
                elif live_price > u:
                    # ПРОБОЙ resistance вверх
                    vol_str = f"✅ объём {vol_ratio_f}x" if vol_confirmed else f"⚠️ объём {vol_ratio_f}x — возможен ложный"
                    alerts.append(f"{icon} {label} {tf}: ПРОБОЙ resistance @\u200b{u} ↑ ({vol_str})")
                    new_breakouts.append({
                        "symbol": symbol_id, "timeframe": tf,
                        "level_type": "resistance", "level_price": u,
                        "breakout_dir": "up", "volume_ratio": vol_ratio_f,
                    })
                elif abs(live_price - u) < approach_threshold:
                    # ПОДХОД К RESISTANCE
                    dist_pct = abs(live_price - u) / u * 100
                    alerts.append(f"{icon} {label} {tf}: цена в {dist_pct:.1f}% от resistance @\u200b{u}")
            except (TypeError, ValueError):
                pass

        # --- SUPPORT (lower) ---
        if lower is not None:
            try:
                l = float(lower)
                if l <= 0:
                    pass
                elif live_price < l:
                    # ПРОБОЙ support вниз
                    vol_str = f"✅ объём {vol_ratio_f}x" if vol_confirmed else f"⚠️ объём {vol_ratio_f}x — возможен ложный"
                    alerts.append(f"{icon} {label} {tf}: ПРОБОЙ support @\u200b{l} ↓ ({vol_str})")
                    new_breakouts.append({
                        "symbol": symbol_id, "timeframe": tf,
                        "level_type": "support", "level_price": l,
                        "breakout_dir": "down", "volume_ratio": vol_ratio_f,
                    })
                elif abs(live_price - l) < approach_threshold:
                    # ПОДХОД К SUPPORT
                    dist_pct = abs(live_price - l) / l * 100
                    alerts.append(f"{icon} {label} {tf}: цена в {dist_pct:.1f}% от support @\u200b{l}")
            except (TypeError, ValueError):
                pass

    if not alerts:
        return None, new_breakouts

    alert_text = "🔔 УРОВЕНЬ / ПРОБОЙ (" + format_symbol(symbol_id) + ")\n" + "\n".join(alerts)
    return alert_text, new_breakouts


def _build_zigzag_context(symbol: str, timeframes: list[str]) -> dict:
    try:
        benchmark = run_benchmark(
            symbol=symbol,
            market_type="future" if symbol.endswith("USDT") else "spot",
            timeframes=timeframes,
            limit=200,
            mode="hybrid_atr",
            confirmation_mode="close",
            debug=False,
            output=None,
            output_mode="compact",
        )

        timeframes_data = benchmark.get("timeframes", {}) if isinstance(benchmark, dict) else {}

        # Компактный иерархический контекст
        compact_timeframes = {}
        for tf, data in timeframes_data.items():
            if not isinstance(data, dict):
                continue
            compact_timeframes[tf] = {
                "current_price": data.get("current_price"),
                "upper": data.get("upper"),
                "lower": data.get("lower"),
                "market_mode": data.get("market_mode"),
                "swing_direction": data.get("swing_direction"),
                "pattern_tags": data.get("pattern_tags", [])[:5],
                "pivot_count": data.get("pivot_count"),
                "price_position": data.get("price_position"),
                "zones": data.get("levels", {}),
                # BUG 1 / Liquidity Magnet: prev_structure + curr_structure видны LLM.
                # prev_structure.high = BSL (Buy-Side Liquidity, цель для sweep вверх).
                # prev_structure.low = SSL (Sell-Side Liquidity, цель для sweep вниз).
                # curr_structure = активная зона после BOS (где цена сейчас).
                "structure": data.get("structure"),
            }

        return {
            "symbol": benchmark.get("symbol", symbol),
            "normalized_symbol": benchmark.get("normalized_symbol", symbol),
            "stack": benchmark.get("stack", {}),
            "timeframes": compact_timeframes,
            "confluence_levels": benchmark.get("confluence_levels", [])[:12],
        }
    except Exception as e:
        return {
            "error": True,
            "message": f"ZigZag context error: {type(e).__name__}",
            "symbol": symbol,
            "stack": {},
            "timeframes": {},
            "confluence_levels": [],
        }


def _build_prev_trend_and_substructure(m_htf: dict, m_ltf: dict, tf_zones: dict) -> tuple[str, str]:
    """
    prev_trend:
      up | down | balance | unknown

    current_substructure:
      accumulation | distribution | breakout_up | breakout_down |
      false_breakout_up | false_breakout_down | reversal_attempt_up |
      reversal_attempt_down | balance | correction_up | correction_down | unknown
    """
    htf_phase = str(m_htf.get("phase", "")).lower()
    ltf_phase = str(m_ltf.get("phase", "")).lower()
    htf_res = float(m_htf.get("resistance") or 0) or None
    htf_sup = float(m_htf.get("support") or 0) or None
    ltf_res = float(m_ltf.get("resistance") or 0) or None
    ltf_sup = float(m_ltf.get("support") or 0) or None
    price = float(m_ltf.get("current_price") or m_ltf.get("last_closed_price") or 0) or None

    prev_trend = "unknown"
    if "импульс" in htf_phase or "trend" in htf_phase or "рост" in htf_phase:
        prev_trend = "up"
    elif "снижен" in htf_phase or "down" in htf_phase or "пад" in htf_phase:
        prev_trend = "down"
    elif "баланс" in htf_phase or "накоп" in htf_phase:
        prev_trend = "balance"

    current_substructure = "unknown"

    inside_htf = False
    if price is not None and htf_sup is not None and htf_res is not None:
        lo = min(htf_sup, htf_res)
        hi = max(htf_sup, htf_res)
        inside_htf = lo <= price <= hi

    if inside_htf:
        if "накоп" in ltf_phase:
            current_substructure = "accumulation"
        elif "распред" in ltf_phase:
            current_substructure = "distribution"
        elif "коррект" in ltf_phase and ("up" in ltf_phase or "вверх" in ltf_phase):
            current_substructure = "correction_up"
        elif "коррект" in ltf_phase and ("down" in ltf_phase or "вниз" in ltf_phase):
            current_substructure = "correction_down"
        else:
            current_substructure = "balance"
    else:
        if price is not None and htf_res is not None and price > htf_res:
            current_substructure = "breakout_up"
        elif price is not None and htf_sup is not None and price < htf_sup:
            current_substructure = "breakout_down"

    return prev_trend, current_substructure


def normalize_analysis(data: dict) -> dict:
    if not isinstance(data, dict):
        return data

    signal = str(data.get("signal_status", "")).lower()
    wave_phase = str(data.get("wave_phase", "")).lower()
    ltf_structure = str(data.get("ltf_structure", "")).lower()
    trend_structure = str(data.get("trend_structure", "")).lower()
    htf_structure = str(data.get("htf_structure", "")).lower()

    key_zones = data.get("key_zones") or {}
    support = key_zones.get("support")
    resistance = key_zones.get("resistance")
    price = data.get("price")

    if signal == "false_breakout":
        inside_range = False
        if support is not None and resistance is not None and price is not None:
            lo = min(support, resistance)
            hi = max(support, resistance)
            inside_range = lo <= float(price) <= hi

        if inside_range or ltf_structure in ("correction_down", "correction_up") or wave_phase in (
            "correction_down",
            "correction_up",
            "unclear",
        ):
            data["signal_status"] = "no_signal"
            data["signal_status_comment"] = "Нет подтверждённого пробоя и объёма, поэтому сигнал понижен до no_signal"
            data["risk_management"] = {"primary": {"sl": None, "tp1": None, "tp2": None, "tp3": None, "rr": None}, "alternative": {"sl": None, "tp1": None, "tp2": None, "tp3": None, "rr": None}}
            data["risk_management_comment"] = "Не применяется (сигнал не подтверждён)"

    if trend_structure == "unknown":
        if htf_structure == "balance" and ltf_structure in ("correction_down", "correction_up"):
            data["trend_structure"] = "balance"
            data["trend_structure_comment"] = "Баланс на старших ТФ с локальной коррекцией на младшем ТФ"

    return data


async def run_hourly_analysis(
    bot: Bot,
    symbol_filter: str = "",
    llm_api_key: str = "",
    llm_base_url: str = "",
    llm_model: str = "",
) -> None:
    symbols = _get_symbols()
    if symbol_filter:
        symbols = [s for s in symbols if s == symbol_filter]
    timeframes = sort_timeframes(_get_timeframes())
    filter_active = get_setting("filter_mode", True)

    # P3-3: multi-symbol context — one API call per cycle, shared across symbols
    import time as _time
    cycle_id = str(_time.time())
    invalidate_multi_cache()
    multi_symbol_ctx = get_multi_symbol_context("BTCUSDT", cache_buster=cycle_id)

    for symbol_id in symbols:
        try:
            chart_bytes_list: list[bytes] = []
            all_metrics: dict[str, dict] = {}

            for tf in timeframes:
                chart_bytes, metrics = fetch_and_plot(symbol=symbol_id, timeframe=tf, limit=100)
                chart_bytes_list.append(chart_bytes)
                all_metrics[tf] = metrics

            m_htf = all_metrics[timeframes[0]]
            m_ltf = all_metrics[timeframes[-1]]

            live_price = m_ltf.get("current_price", m_ltf.get("last_closed_price", 0))
            last_closed_price = m_ltf.get("last_closed_price", live_price)

            fib = m_ltf.get("fib_context", {"50%": "N/A", "61.8%": "N/A", "38.2%": "N/A", "rule": ""})

            tf_zones = {}
            h1_reference = None

            for tf in timeframes:
                zone = all_metrics[tf].get("zone", {})
                tf_zones[tf] = zone

                tf_norm = str(tf).lower()
                if tf_norm in ("1h", "1", "h1"):
                    h1_reference = {
                        "upper": zone.get("upper"),
                        "lower": zone.get("lower"),
                    }

            # H1-first логика: если M15 слишком узкий, расширяем его до H1 reference
            if h1_reference and "15m" in tf_zones:
                m15_zone = tf_zones["15m"]
                if isinstance(m15_zone, dict):
                    m15_upper = m15_zone.get("upper")
                    m15_lower = m15_zone.get("lower")
                    h1_upper = h1_reference.get("upper")
                    h1_lower = h1_reference.get("lower")

                    try:
                        m15_upper_f = float(m15_upper) if m15_upper is not None else None
                        m15_lower_f = float(m15_lower) if m15_lower is not None else None
                        h1_upper_f = float(h1_upper) if h1_upper is not None else None
                        h1_lower_f = float(h1_lower) if h1_lower is not None else None

                        atr_raw = m_ltf.get("atr", 0)
                        atr_f = float(atr_raw) if atr_raw is not None else 0.0
                        min_width = max(float(live_price) * 0.0025, 2.0 * atr_f)

                        if (
                            m15_upper_f is not None
                            and m15_lower_f is not None
                            and h1_upper_f is not None
                            and h1_lower_f is not None
                            and (m15_upper_f - m15_lower_f) < min_width
                        ):
                            tf_zones["15m"] = {
                                "upper": max(m15_upper_f, h1_upper_f),
                                "lower": min(m15_lower_f, h1_lower_f),
                            }
                    except (TypeError, ValueError):
                        pass

            prev_trend, current_substructure = _build_prev_trend_and_substructure(m_htf, m_ltf, tf_zones)

            tf_context = (
                f"[HTF] {m_htf.get('phase', 'N/A')} | Упор: {m_htf.get('resistance', 'N/A')} | Поддержка: {m_htf.get('support', 'N/A')} | "
                f"[{timeframes[-1]}] {m_ltf.get('phase', 'N/A')} | Текущая цена: {live_price} | "
                f"Объём: {m_ltf.get('vol_ratio', 1.0)}x ({m_ltf.get('vol_trend', 'N/A')}) | "
                f"Фибо: 50%={fib['50%']} | 61.8%={fib['61.8%']} | 38.2%={fib['38.2%']}"
            )

            metrics_str = (
                f"Текущая цена: {live_price} | Последняя закрытая: {last_closed_price} | ATR: {m_ltf.get('atr', 'N/A')} | "
                f"RSI: {m_ltf.get('rsi', 'N/A')} | Сессия: {m_ltf.get('session', 'N/A')}"
            )

            # Binance market context: funding rate, open interest, order book imbalance
            try:
                binance_ctx = fetch_binance_metrics(symbol_id)
                metrics_str += f"\n{binance_ctx}"
            except Exception as e:
                logger.warning("binance_metrics failed for %s: %s", symbol_id, e)

            zigzag_context = _build_zigzag_context(symbol=symbol_id, timeframes=timeframes)

            # Собираем volume_context с младшего ТФ
            ltf_volume = all_metrics[timeframes[-1]].get("volume_context", {})
            if not isinstance(ltf_volume, dict):
                ltf_volume = {}

            # Liquidity heatmap (лёгкий текстовый контекст)
            heatmap_data = {}
            ltf_df = None
            try:
                from core.liquidity_heatmap import build_liquidity_context_text, build_liquidity_heatmap
                from core.data_provider import OhlcvDataProvider
                provider = OhlcvDataProvider()
                ltf_tf = timeframes[-1]
                try:
                    ltf_df = provider.read_current_csv(symbol_id, ltf_tf)
                    hm = build_liquidity_heatmap(ltf_df, current_price=live_price, symbol=symbol_id, timeframe=ltf_tf)
                    heatmap_text = build_liquidity_context_text(hm)
                    heatmap_data = hm  # структура для enforce_risk_rules
                except FileNotFoundError:
                    heatmap_text = "Liquidity heatmap: CSV недоступен."
            except Exception as e:
                heatmap_text = f"Liquidity heatmap: ошибка ({type(e).__name__})."

            # BUG 2 FIX: period_high/period_low — abs max/min свечей между сканами
            # для детекции intrabar sweep (ложный пробой внутри свечи).
            # Берём последние ~6 свечей M15 (≈90 мин ≈ интервал автоскана 30 мин × 2-3 цикла).
            period_high = None
            period_low = None
            if ltf_df is not None and hasattr(ltf_df, "columns"):
                try:
                    cols = {c.lower(): c for c in ltf_df.columns}
                    hi_col = cols.get("high", "high")
                    lo_col = cols.get("low", "low")
                    tail = ltf_df.tail(6)
                    period_high = float(tail[hi_col].max())
                    period_low = float(tail[lo_col].min())
                except Exception:
                    period_high = None
                    period_low = None

            # Добавляем heatmap в metrics
            metrics_str += f"\n{heatmap_text}"

            # Формируем liquidity_pools для _pick_tp_levels из heatmap levels
            liquidity_pools = {}
            if isinstance(heatmap_data.get("levels"), list):
                resistance_pools = [
                    {"level": z["level"], "strength": z.get("strength", 0)}
                    for z in heatmap_data["levels"]
                    if z.get("kind") == "resistance" and z.get("level") is not None
                ]
                support_pools = [
                    {"level": z["level"], "strength": z.get("strength", 0)}
                    for z in heatmap_data["levels"]
                    if z.get("kind") == "support" and z.get("level") is not None
                ]
                liquidity_pools = {
                    "resistance_pools": resistance_pools,
                    "support_pools": support_pools,
                }

            # Load previous state and build state_context BEFORE LLM call
            # so the LLM sees what changed vs last analysis
            _prev_state = load_state(symbol_id, timeframes[-1])
            _state_diff = compare_state(_prev_state, {"price": live_price, "tf_zones": tf_zones},
                                        period_high=period_high, period_low=period_low)
            _state_context = build_state_context(_state_diff, {"price": live_price, "tf_zones": tf_zones}, _prev_state)

            prev_ctx = {
                "metrics": metrics_str,
                "tf_context": tf_context,
                "backtest": get_backtest_context(symbol_id),
                "tf_zones": tf_zones,
                "zigzag_context": zigzag_context,
                "volume_context": ltf_volume,
                "heatmap_context": heatmap_text,
                "liquidity_pools": liquidity_pools,
                "current_price": live_price,
                "last_closed_price": last_closed_price,
                "prev_trend": prev_trend,
                "current_substructure": current_substructure,
                "tf_span_map": zigzag_context.get("stack", {}).get("tf_span_map", {}),
                "confluence_levels": zigzag_context.get("confluence_levels", []),
                "state_context": _state_context,
                "multi_symbol": get_multi_symbol_context(symbol_id, cache_buster=cycle_id),
            }

            parsed = await analyze_multi_images(
                chart_bytes_list, prev_analysis=prev_ctx,
                llm_api_key=llm_api_key, llm_base_url=llm_base_url, llm_model=llm_model,
            )
            parsed = normalize_analysis(parsed)

            # -----------------------------
            # State tracker integration
            # -----------------------------
            parsed = update_and_save_state(
                symbol=symbol_id,
                timeframe=timeframes[-1],
                current=parsed,
                period_high=period_high,
                period_low=period_low,
            )

            if isinstance(parsed, dict):
                parsed["price"] = parsed.get("price") or live_price or last_closed_price
                parsed["current_price"] = live_price
                parsed["last_closed_price"] = last_closed_price
                parsed["prev_trend"] = prev_trend
                parsed["current_substructure"] = current_substructure
                parsed["confluence_levels"] = zigzag_context.get("confluence_levels", [])
                parsed["tf_span_map"] = zigzag_context.get("stack", {}).get("tf_span_map", {})

                tf_zones_clean = {}
                key_map = {"1d": "1D", "4h": "4H", "1h": "1H", "15m": "15M", "5m": "5M"}

                # Сначала записываем зоны из metrics (auto_chart), затем LLM-зоны
                # перезаписывают их — LLM имеет приоритет
                for k, v in tf_zones.items():
                    norm_k = key_map.get(str(k).strip().lower(), str(k).strip().upper())
                    tf_zones_clean[norm_k] = v

                llm_zones = parsed.get("tf_zones") or {}
                if isinstance(llm_zones, dict):
                    for k, v in llm_zones.items():
                        norm_k = key_map.get(str(k).strip().lower(), str(k).strip().upper())
                        # LLM-зона перезаписывает metrics-зону только если она не пустая
                        if isinstance(v, dict):
                            upper = v.get("upper")
                            lower = v.get("lower")
                            # FIX: LLM возвращает "range": [low, high] вместо upper/lower
                            if upper is None and lower is None and "range" in v:
                                rng = v["range"]
                                if isinstance(rng, list) and len(rng) >= 2:
                                    lower = rng[0]
                                    upper = rng[1]
                            if upper is not None or lower is not None:
                                # Нормализуем: upper = max, lower = min
                                if upper is not None and lower is not None:
                                    orig_upper, orig_lower = float(upper), float(lower)
                                    upper = max(orig_upper, orig_lower)
                                    lower = min(orig_upper, orig_lower)
                                v_norm = dict(v)
                                v_norm["upper"] = upper
                                v_norm["lower"] = lower
                                tf_zones_clean[norm_k] = v_norm

                parsed["tf_zones"] = tf_zones_clean

                if parsed.get("price") in (None, "", "null"):
                    parsed["price"] = live_price or last_closed_price

                # Phase 3 MT5: кэшируем результат для /api/signals
                _last_analysis_cache[symbol_id] = {
                    "tf_zones": tf_zones_clean,
                    "live_price": float(live_price or last_closed_price or 0),
                    "signal_status": parsed.get("signal_status", "unknown"),
                    "signal_direction": parsed.get("signal_direction", ""),
                    "phase": parsed.get("phase", ""),
                    "metrics": {tf: {"vol_ratio": m.get("vol_ratio"), "atr": m.get("atr")}
                                for tf, m in all_metrics.items()},
                    "timestamp": datetime.utcnow().isoformat(),
                }

                parsed = enforce_risk_rules(parsed)

                # P3-1: сохранить прогноз в backtest
                try:
                    save_signal_log(parsed, symbol_id, timeframes, prompt_variant=PROMPT_VARIANT)
                except Exception as bt_err:
                    logger.warning("save_signal_log failed: %s", bt_err)

            if isinstance(parsed, dict) and parsed.get("error"):
                logger.error(f"Ошибка анализа {symbol_id}: {parsed.get('message')}")
                await bot.send_message(MY_CHAT_ID, f"⚠️ Ошибка анализа {format_symbol(symbol_id)}: {parsed.get('message')}")
                continue

            status = str(parsed.get("signal_status", "unknown")).lower()

            # AUTO_SIGNAL_ONLY: в авто-цикле отправляем только при подтверждённом сигнале
            if AUTO_SIGNAL_ONLY:
                send_to_tg = status in ACTIONABLE_SIGNALS
            else:
                send_to_tg = True

            # ── Phase 3: Level alerts + breakout detection ──────────────
            # KX-style: подход к уровню + фиксация пробоя. Отправляются ВСЕГДА.
            ltf_metrics = all_metrics.get(timeframes[-1], {})
            vol_ratio_ltf = ltf_metrics.get("vol_ratio", 1.0)
            atr_ltf = ltf_metrics.get("atr")

            alert_text, new_breakouts = _build_level_alerts(
                symbol_id=symbol_id,
                tf_zones=parsed.get("tf_zones", tf_zones) if isinstance(parsed, dict) else tf_zones,
                live_price=float(live_price or last_closed_price or 0),
                vol_ratio=vol_ratio_ltf,
                atr=atr_ltf,
                tf_metrics=all_metrics,
            )

            # Сохраняем новые пробои в DB
            for br in new_breakouts:
                try:
                    save_breakout_event(
                        symbol=br["symbol"], timeframe=br["timeframe"],
                        level_type=br["level_type"], level_price=br["level_price"],
                        breakout_dir=br["breakout_dir"], volume_ratio=br["volume_ratio"],
                    )
                except Exception as e:
                    logger.warning("save_breakout_event failed: %s", e)

            # Подтверждаем/опровергаем старые pending breakouts
            try:
                pending = get_pending_breakout_events(symbol_id)
                for p in pending:
                    level = p["level_price"]
                    direction = p["breakout_dir"]
                    if direction == "up" and live_price and float(live_price) > level:
                        confirm_breakout_event(p["id"], confirmed=1, outcome="continued")
                    elif direction == "down" and live_price and float(live_price) < level:
                        confirm_breakout_event(p["id"], confirmed=1, outcome="continued")
                    else:
                        confirm_breakout_event(p["id"], confirmed=-1, outcome="reversed")
            except Exception as e:
                logger.warning("breakout confirmation failed: %s", e)

            msg_text = format_json_for_tg(parsed)

            # ── Отправка в TG ─────────────────────────────────────────────
            # 1. Если есть level alerts — отправляем ВСЕГДА (мимо AUTO_SIGNAL_ONLY)
            if alert_text:
                try:
                    await bot.send_message(MY_CHAT_ID, alert_text)
                except Exception as e:
                    logger.warning("level alert send failed: %s", e)

            # 2. LLM сигнал — по фильтру AUTO_SIGNAL_ONLY
            if send_to_tg:
                msg = await bot.send_message(MY_CHAT_ID, msg_text)

                if status in ("aggressive_breakout", "retest"):
                    trend = "Long" if (
                        "long" in status.lower()
                        or "восходящий" in str(parsed.get("fact_feedback", "")).lower()
                    ) else "Short"

                    rm = parsed.get("risk_management", {})
                    primary = rm.get("primary", {}) if isinstance(rm, dict) else {}
                    target = primary.get("tp1") or primary.get("tp2") or (
                        round(float(live_price) * 1.02, 2) if trend == "Long" else round(float(live_price) * 0.98, 2)
                    )
                    save_forecast(symbol_id, trend, float(live_price), target, msg.message_id)

            else:
                logger.debug(f"🔇 Фильтр пропустил сигнал {symbol_id}: {status}")

        except Exception as e:
            logger.error(f"Ошибка {symbol_id}: {e}")
            await bot.send_message(MY_CHAT_ID, f"⚠️ Не удалось проанализировать {format_symbol(symbol_id)}: {type(e).__name__}")
            continue
async def update_prices_and_reschedule(bot: Bot) -> None:
    try:
        symbols = _get_symbols()
        prices = {}
        for sym in symbols:
            try:
                t = await fetch_ticker_safe(sym)
                prices[sym] = float(t.get("last") or 0)
            except Exception:
                pass
        if prices:
            update_actual_prices(prices)
            # P3-1: проверить накопленные прогнозы
            try:
                checked = check_pending_forecasts(prices)
                if checked:
                    logger.info("backtest: checked %d forecasts", checked)
            except Exception as bt_err:
                logger.warning("check_pending_forecasts failed: %s", bt_err)
    except Exception as e:
        logger.error(f"Ошибка обновления цен: {e}")

    await run_hourly_analysis(bot)

def start_scheduler(bot: Bot) -> None:
    init_all_tables()
    init_backtest_table()
    init_breakout_events_table()
    raw_mins = get_setting("interval_minutes", 60)
    current_minutes = int(raw_mins) if raw_mins is not None else 60

    scheduler.add_job(
        update_prices_and_reschedule,
        "interval",
        minutes=current_minutes,
        args=[bot],
        id="analysis_job",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info(
        f"🕒 Планировщик запущен (интервал: {current_minutes} мин, инструменты: {_get_symbols()}, ТФ: {sort_timeframes(_get_timeframes())})"
    )


def update_timer(new_minutes: int) -> None:
    set_setting("interval_minutes", new_minutes)
    try:
        scheduler.reschedule_job("analysis_job", trigger="interval", minutes=new_minutes)
        return True, f"✅ Таймер изменён на {new_minutes} минут."
    except Exception as e:
        return False, f"❌ Ошибка: {e}"
