# core/scheduler.py
# Назначение: периодический анализ рынка и сбор контекста для LLM.
# Отвечает за: запуск auto-analysis, передачу цен, зон, ZigZag-контекста и нормализацию ответа.
# Связан с: ollama_client.py, auto_chart.py, db.py, utils.py.

import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot

from core.db import init_all_tables, save_forecast, update_actual_prices, get_setting, set_setting
from core.auto_chart import fetch_and_plot
from core.state_tracker import update_and_save_state
from core.ollama_client import analyze_multi_images, format_json_for_tg, enforce_risk_rules
from core.config import MY_CHAT_ID
from core.utils import fetch_ticker_safe, format_symbol, sort_timeframes
from core.zigzag.benchmark_zigzag import run_benchmark
from core.data_provider import OhlcvDataProvider
import asyncio
import sys
import datetime
import os
from pathlib import Path


logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()


def _get_timeframes() -> list:
    val = get_setting("timeframes", ["1h", "4h", "1D"])
    return val if isinstance(val, list) else ["1h", "4h", "1D"]


def _get_symbols() -> list:
    return ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XAUTUSDT"]


def _build_warning_message(symbol_id: str, timeframe: str, upper: float | None, lower: float | None, live_price: float) -> str | None:
    warning_threshold = 0.005
    if upper and abs(live_price - upper) / upper < warning_threshold:
        return (
            f"🔔 ПОДХОД К УРОВНЮ ({format_symbol(symbol_id)})\n"
            f"💹 Цена в 0.5% от верхней границы {timeframe}: {upper}"
        )
    if lower and abs(live_price - lower) / lower < warning_threshold:
        return (
            f"🔔 ПОДХОД К УРОВНЮ ({format_symbol(symbol_id)})\n"
            f"💹 Цена в 0.5% от нижней границы {timeframe}: {lower}"
        )
    return None


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
                "zones": data.get("levels", {}),
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

    abc_risk = str(data.get("abc_risk", "")).lower()
    wave_comment = str(data.get("wave_phase_comment", "")).lower()

    if "abc вверх" in wave_comment or "abc-коррекции вверх" in wave_comment:
        data["abc_risk"] = "abc_risk_up"
        if not data.get("abc_risk_comment"):
            data["abc_risk_comment"] = "Риск ABC вверх по волновой фазе"
    elif "abc вниз" in wave_comment or "abc-коррекции вниз" in wave_comment:
        data["abc_risk"] = "abc_risk_down"
        if not data.get("abc_risk_comment"):
            data["abc_risk_comment"] = "Риск ABC вниз по волновой фазе"
    elif wave_phase == "correction_down" and abc_risk == "none":
        data["abc_risk"] = "abc_risk_up"
        data["abc_risk_comment"] = "Риск ABC вверх после коррекции вниз"
    elif wave_phase == "correction_up" and abc_risk == "none":
        data["abc_risk"] = "abc_risk_down"
        data["abc_risk_comment"] = "Риск ABC вниз после коррекции вверх"

    return data


async def run_hourly_analysis(bot: Bot):
    symbols = _get_symbols()
    timeframes = sort_timeframes(_get_timeframes())
    filter_active = get_setting("filter_mode", True)

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

            zigzag_context = _build_zigzag_context(symbol=symbol_id, timeframes=timeframes)

            prev_ctx = {
                "metrics": metrics_str,
                "tf_context": tf_context,
                "backtest": "Статистика формируется в фоне.",
                "tf_zones": tf_zones,
                "zigzag_context": zigzag_context,
                "current_price": live_price,
                "last_closed_price": last_closed_price,
                "prev_trend": prev_trend,
                "current_substructure": current_substructure,
                "tf_span_map": zigzag_context.get("stack", {}).get("tf_span_map", {}),
                "confluence_levels": zigzag_context.get("confluence_levels", []),
            }

            parsed = await analyze_multi_images(chart_bytes_list, prev_analysis=prev_ctx)
            parsed = normalize_analysis(parsed)

            # -----------------------------
            # State tracker integration
            # -----------------------------
            parsed = update_and_save_state(
                symbol=symbol_id,
                timeframe=timeframes[-1],
                current=parsed,
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

                llm_zones = parsed.get("tf_zones") or {}
                if isinstance(llm_zones, dict):
                    for k, v in llm_zones.items():
                        norm_k = key_map.get(str(k).strip().lower(), str(k).strip().upper())
                        tf_zones_clean[norm_k] = v

                for k, v in tf_zones.items():
                    norm_k = key_map.get(str(k).strip().lower(), str(k).strip().upper())
                    tf_zones_clean[norm_k] = v

                parsed["tf_zones"] = tf_zones_clean

                if parsed.get("price") in (None, "", "null"):
                    parsed["price"] = live_price or last_closed_price

                parsed["symbol"] = symbol_id
                parsed["symbol_id"] = symbol_id
                # parsed = enforce_risk_rules(parsed)  # DUPLICATE: already called inside analyze_multi_images

            if isinstance(parsed, dict) and parsed.get("error"):
                logger.error(f"Ошибка анализа {symbol_id}: {parsed.get('message')}")
                await bot.send_message(MY_CHAT_ID, f"⚠️ Ошибка анализа {format_symbol(symbol_id)}: {parsed.get('message')}")
                continue

            status = str(parsed.get("signal_status", "unknown"))
            send_to_tg = True

            ltf_zone = tf_zones.get(timeframes[-1], {})
            upper = ltf_zone.get("upper")
            lower = ltf_zone.get("lower")
            warning_msg = _build_warning_message(symbol_id, timeframes[-1], upper, lower, live_price)

            msg_text = format_json_for_tg(parsed)
            if warning_msg:
                msg_text = warning_msg + "\n\n" + msg_text

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
            
async def update_prices_and_reschedule(bot: Bot):
    # === OHLCV auto-refresh: обновляем данные для всех символов/ТФ ===
    try:
        symbols = _get_symbols()
        timeframes = sort_timeframes(_get_timeframes())
        provider = OhlcvDataProvider()

        def _do_refresh():
            refreshed = []
            for sym in symbols:
                market_type = "future" if sym.endswith("USDT") else "spot"
                paths = provider.refresh_many(
                    symbols=[sym],
                    timeframes=timeframes,
                    limit=500,
                    market_type=market_type,
                    force_refresh=True,
                )
                refreshed.extend(paths)
            return refreshed

        loop = asyncio.get_running_loop()
        paths = await loop.run_in_executor(None, _do_refresh)
        logger.info(f"OHLCV auto-refresh: {len(paths)} TFs updated for {symbols}")

    except Exception as e:
        logger.warning(f"OHLCV auto-refresh failed (using cached): {e}")

    # Update live ticker prices
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
    except Exception as e:
        logger.error(f"Ошибка обновления цен: {e}")

    await run_hourly_analysis(bot)



# ============================================================================
# AUTOTUNE DAILY (06:00 UTC, before London session)
# ============================================================================

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_RESULTS_DIR = _PROJECT_ROOT / "results"
_AUTOTUNE_LOCK_FILE = _RESULTS_DIR / ".autotune_last_run"


def _autotune_ran_today() -> bool:
    """Check if autotune already ran today (UTC)."""
    if not _AUTOTUNE_LOCK_FILE.exists():
        return False
    try:
        mtime = datetime.datetime.utcfromtimestamp(_AUTOTUNE_LOCK_FILE.stat().st_mtime)
        today = datetime.datetime.utcnow().date()
        return mtime.date() == today
    except Exception:
        return False


def _mark_autotune_done():
    """Write timestamp to lock file."""
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    _AUTOTUNE_LOCK_FILE.touch()


async def _run_autotune_daily(bot: Bot):
    """
    Run autotune for all symbols:
    1. Fetch fresh OHLCV
    2. Grid search for best zigzag params
    3. Generate A/D candidates
    4. Update backtest labels + stats
    """
    if _autotune_ran_today():
        logger.info("Autotune already ran today, skipping.")
        return

    symbols = _get_symbols()
    tfs = ["15m", "1h", "4h", "1d"]

    try:
        await bot.send_message(
            MY_CHAT_ID,
            "Starting autotune for " + str(len(symbols)) + " symbols (" + ", ".join(tfs) + ")..."
        )
    except Exception:
        pass

    total_tuned = 0
    total_cands = 0

    for symbol in symbols:
        try:
            logger.info("[autotune] Starting " + symbol + "...")

            from core.symbol_setup import fetch_ohlcv, run_autotune, run_candidates
            loop = asyncio.get_running_loop()

            def _fetch(sym=symbol):
                return fetch_ohlcv(sym, tfs, "future", force_refresh=True)

            ohlcv_map = await loop.run_in_executor(None, _fetch)

            def _autotune(sym=symbol, om=ohlcv_map):
                return run_autotune(sym, om, target_min=3, target_max=20)

            autotune_params = await loop.run_in_executor(None, _autotune)
            total_tuned += len(autotune_params)

            def _candidates(sym=symbol, om=ohlcv_map, ap=autotune_params):
                return run_candidates(sym, om, ap)

            cand_result = await loop.run_in_executor(None, _candidates)
            total_cands += cand_result.get("total_candidates", 0) if cand_result else 0

            n_cands = cand_result.get("total_candidates", 0) if cand_result else 0
            logger.info("[autotune] Done " + symbol + ": " + str(len(autotune_params)) + " TFs, " + str(n_cands) + " candidates")

        except Exception as e:
            logger.error("[autotune] Error " + symbol + ": " + str(e))
            try:
                await bot.send_message(MY_CHAT_ID, "Autotune " + symbol + " error: " + str(e))
            except Exception:
                pass

    # Step 4: Update labels + backtest stats
    try:
        import subprocess
        loop = asyncio.get_running_loop()

        def _refresh_labels():
            result = subprocess.run(
                [sys.executable, str(_PROJECT_ROOT / "tools" / "label_refresher.py")],
                capture_output=True, text=True, cwd=str(_PROJECT_ROOT), timeout=120
            )
            return result.stdout

        out = await loop.run_in_executor(None, _refresh_labels)
        logger.info("[autotune] Label refresh done")

        def _gen_stats():
            result = subprocess.run(
                [sys.executable, str(_PROJECT_ROOT / "tools" / "backtest_stats_generator.py")],
                capture_output=True, text=True, cwd=str(_PROJECT_ROOT), timeout=60
            )
            return result.stdout

        out = await loop.run_in_executor(None, _gen_stats)
        logger.info("[autotune] Backtest stats done")

    except Exception as e:
        logger.warning("[autotune] Label/stats update error: " + str(e))

    _mark_autotune_done()

    summary = (
        "Autotune complete. "
        + "Symbols: " + str(total_tuned) + ", "
        + "Candidates: " + str(total_cands) + ". "
        + "Labels + backtest updated."
    )
    logger.info(summary)

    try:
        await bot.send_message(MY_CHAT_ID, summary)
    except Exception:
        pass


def start_scheduler(bot: Bot):
    init_all_tables()
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

    # Daily autotune at 06:00 UTC (before London open)
    scheduler.add_job(
        _run_autotune_daily,
        "cron",
        hour=6,
        minute=0,
        timezone="UTC",
        args=[bot],
        id="autotune_daily_job",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    scheduler.start()
    logger.info(
        f"🕒 Планировщик запущен (интервал: {current_minutes} мин, инструменты: {_get_symbols()}, ТФ: {sort_timeframes(_get_timeframes())})"
    )

    # First-run: if autotune has not run today, trigger it now
    if not _autotune_ran_today():
        logger.info("First run: triggering autotune (has not run today)...")
        asyncio.create_task(_run_autotune_daily(bot))


def update_timer(new_minutes: int):
    set_setting("interval_minutes", new_minutes)
    try:
        scheduler.reschedule_job("analysis_job", trigger="interval", minutes=new_minutes)
        return True, f"✅ Таймер изменён на {new_minutes} минут."
    except Exception as e:
        return False, f"❌ Ошибка: {e}"
