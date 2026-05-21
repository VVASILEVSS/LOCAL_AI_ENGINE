import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot

from core.db import init_all_tables, save_forecast, update_actual_prices, get_setting, set_setting
from core.auto_chart import fetch_and_plot
from core.ollama_client import analyze_multi_images, format_json_for_tg
from core.config import MY_CHAT_ID
from core.utils import fetch_ticker_safe, format_symbol, sort_timeframes

# NEW: ZigZag compact context
from core.zigzag.benchmark_zigzag import run_benchmark

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()


def _get_timeframes() -> list:
    val = get_setting("timeframes", ["1h", "4h", "1D"])
    return val if isinstance(val, list) else ["1h", "4h", "1D"]


def _get_symbols() -> list:
    return ["BTCUSDT", "XAUTUSDT"]


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
        return {
            "symbol": benchmark.get("symbol", symbol),
            "normalized_symbol": benchmark.get("normalized_symbol", symbol),
            "stack": benchmark.get("stack", {}),
            "timeframes": benchmark.get("timeframes", {}),
            "confluence_levels": benchmark.get("confluence_levels", []),
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
            data["risk_management"] = {"sl": None, "tp1": None, "tp2": None, "rr": None}
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

            fib = m_ltf.get("fib_context", {"50%": "N/A", "61.8%": "N/A", "38.2%": "N/A", "rule": ""})
            tf_zones = {tf: all_metrics[tf]["zone"] for tf in timeframes}

            tf_context = (
                f"[HTF] {m_htf.get('phase', 'N/A')} | Упор: {m_htf.get('resistance', 'N/A')} | Поддержка: {m_htf.get('support', 'N/A')} | "
                f"[{timeframes[-1]}] {m_ltf.get('phase', 'N/A')} | Текущая цена: {live_price} | "
                f"Объём: {m_ltf.get('vol_ratio', 1.0)}x ({m_ltf.get('vol_trend', 'N/A')}) | "
                f"Фибо: 50%={fib['50%']} | 61.8%={fib['61.8%']} | 38.2%={fib['38.2%']}"
            )

            metrics_str = (
                f"Текущая цена: {live_price} | ATR: {m_ltf.get('atr', 'N/A')} | RSI: {m_ltf.get('rsi', 'N/A')} | "
                f"Сессия: {m_ltf.get('session', 'N/A')}"
            )

            zigzag_context = _build_zigzag_context(symbol=symbol_id, timeframes=timeframes)

            prev_ctx = {
                "metrics": metrics_str,
                "tf_context": tf_context,
                "backtest": "Статистика формируется в фоне.",
                "tf_zones": tf_zones,
                "zigzag_context": zigzag_context,
            }

            parsed = await analyze_multi_images(chart_bytes_list, prev_analysis=prev_ctx)
            parsed = normalize_analysis(parsed)

            if isinstance(parsed, dict) and "tf_zones" in parsed:
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
                    target = rm.get("tp1") or rm.get("tp2") or (
                        round(live_price * 1.02, 2) if trend == "Long" else round(live_price * 0.98, 2)
                    )
                    save_forecast(symbol_id, trend, live_price, target, msg.message_id)

            else:
                logger.debug(f"🔇 Фильтр пропустил сигнал {symbol_id}: {status}")

        except Exception as e:
            logger.error(f"Ошибка {symbol_id}: {e}")
            await bot.send_message(MY_CHAT_ID, f"⚠️ Не удалось проанализировать {format_symbol(symbol_id)}: {type(e).__name__}")


async def update_prices_and_reschedule(bot: Bot):
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


def start_scheduler(bot: Bot):
    init_all_tables()
    raw_mins = get_setting("interval_minutes", 60)
    current_minutes = int(raw_mins) if raw_mins is not None else 60

    scheduler.add_job(update_prices_and_reschedule, "interval", minutes=current_minutes, args=[bot], id="analysis_job")
    scheduler.start()
    logger.info(
        f"🕒 Планировщик запущен (интервал: {current_minutes} мин, инструменты: {_get_symbols()}, ТФ: {sort_timeframes(_get_timeframes())})"
    )


def update_timer(new_minutes: int):
    set_setting("interval_minutes", new_minutes)
    try:
        scheduler.reschedule_job("analysis_job", trigger="interval", minutes=new_minutes)
        return True, f"✅ Таймер изменён на {new_minutes} минут."
    except Exception as e:
        return False, f"❌ Ошибка: {e}"