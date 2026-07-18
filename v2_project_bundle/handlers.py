import io
import re
import logging
from aiogram import Router, types
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InaccessibleMessage,
    BufferedInputFile
)

from core.ollama_client import analyze_multi_images, format_json_for_tg, enforce_risk_rules
from core.config import USER_ANALYSIS_CACHE
from core.auto_chart import fetch_and_plot
from core.volume_filters import analyze_volume_hierarchy, compute_signal_score
from core.state_tracker import update_and_save_state
from core.db import get_backtest_stats, get_history_df, get_setting, set_setting
from core.scheduler import update_timer
from core.utils import validate_symbol, fetch_ticker_safe, format_symbol, is_futures, sort_timeframes
from core.symbol_setup import onboard_symbol_async, needs_setup, get_setup_status
from core.data_provider import OhlcvDataProvider, OhlcvRequest
import asyncio

logger = logging.getLogger(__name__)

router = Router()
USER_PHOTO_BUFFER: dict[int, list[bytes]] = {}


def clean_analysis_report(text: str) -> str:
    """Исправляет логические противоречия в ТП/СЛ и форматирует вывод."""
    if "НАПРАВЛЕНИЕ:" in text:
        direction = "Long" if "Long" in text.split("НАПРАВЛЕНИЕ:")[1].split("|")[0] else "Short"
        price_match = re.search(r"Текущая цена:\s*([\d.]+)", text)
        if price_match:
            current = float(price_match.group(1))
            tp1_match = re.search(r"TP1:\s*([\d.]+)", text)
            if tp1_match:
                tp1 = float(tp1_match.group(1))
                if direction == "Long" and tp1 <= current:
                    text = text.replace(f"TP1: {tp1}", "TP1: Уже отработан")
                elif direction == "Short" and tp1 >= current:
                    text = text.replace(f"TP1: {tp1}", "TP1: Уже отработан")
    return text


def _get_timeframes() -> list[str]:
    val = get_setting("timeframes", ["1h"])
    return val if isinstance(val, list) else ["1h"]


def _get_symbols() -> list[str]:
    val = get_setting("symbols", ["BTCUSDT", "XAUTUSDT"])
    return val if isinstance(val, list) else ["BTCUSDT", "XAUTUSDT"]


def _format_symbol(symbol_id: str) -> str:
    """XAGUSDT -> XAG/USDT, BTCUSDT -> BTC/USDT"""
    if "/" in symbol_id:
        return symbol_id
    for quote in ["USDT", "BUSD", "USDC", "EUR", "TRY", "BTC", "ETH", "BNB", "DAI", "GBP", "AUD"]:
        if symbol_id.endswith(quote):
            return f"{symbol_id[:-len(quote)]}/{quote}"
    return symbol_id


def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Быстрый анализ", callback_data="menu_scan"),
         InlineKeyboardButton(text="📷 Анализ скриншотов", callback_data="menu_screenshots")],
        [InlineKeyboardButton(text="⚙️ Инструменты", callback_data="menu_instruments"),
         InlineKeyboardButton(text="⏱ Таймер", callback_data="menu_timer")],
        [InlineKeyboardButton(text="📈 Таймфреймы", callback_data="menu_timeframes"),
         InlineKeyboardButton(text="📊 Экспорт + Бэктест", callback_data="menu_export")],
        [InlineKeyboardButton(text="📋 Настройки", callback_data="menu_settings"),
         InlineKeyboardButton(text="ℹ️ О боте", callback_data="menu_about")]
    ])


def get_tf_keyboard() -> InlineKeyboardMarkup:
    selected = _get_timeframes()
    keyboard = []
    row = []
    for tf in ["15m", "1h", "4h", "1D"]:
        icon = "✅" if tf in selected else "⬜"
        row.append(InlineKeyboardButton(text=f"{icon} {tf}", callback_data=f"tf_toggle_{tf}"))
    keyboard.append(row)
    keyboard.append([InlineKeyboardButton(text="❌ Закрыть", callback_data="close_tf")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


@router.message(Command("start"))
@router.message(Command("menu"))
async def show_main_menu(message: types.Message):
    await message.answer(
        "👋 Гибридный режим активен. Все данные обрабатываются локально.\n\n"
        "Выберите действие ниже или используйте команды в чате:",
        reply_markup=get_main_menu_keyboard()
    )


@router.message(Command("timeframes"))
async def cmd_timeframes(message: types.Message):
    selected = _get_timeframes()
    txt = f"📊 ТЕКУЩИЕ ТАЙМФРЕЙМЫ:\n{', '.join(selected)}\n\n"
    txt += "Нажмите на ТФ, чтобы добавить/удалить.\n"
    txt += "Все выбранные ТФ применяются к `/scan` и авто-отчётам."
    await message.answer(txt, reply_markup=get_tf_keyboard())


@router.callback_query(lambda c: c.data is not None and (c.data.startswith("tf_toggle_") or c.data == "close_tf"))
async def tf_callback(callback: types.CallbackQuery):
    await callback.answer()

    if not callback.data:
        return

    if callback.data == "close_tf":
        if callback.message and not isinstance(callback.message, InaccessibleMessage):
            await callback.message.edit_text("⚙️ Меню таймфреймов закрыто.")
        return

    tf = callback.data.replace("tf_toggle_", "")
    current = _get_timeframes()

    if tf in current:
        current.remove(tf)
    else:
        current.append(tf)

    set_setting("timeframes", current)

    if callback.message and not isinstance(callback.message, InaccessibleMessage):
        await callback.message.edit_text(
            f"📊 ТЕКУЩИЕ ТАЙМФРЕЙМЫ:\n{', '.join(current)}\n\n"
            "Нажмите на ТФ, чтобы добавить/удалить.\n"
            "Все выбранные ТФ применяются к `/scan` и авто-отчётам.",
            reply_markup=get_tf_keyboard()
        )


def clean_tp_sl(text: str, current_price: float, direction: str) -> str:
    """Исправляет логические ошибки ТП/СЛ."""
    if direction == "Long":
        for match in re.finditer(r"TP\d+:\s*([\d.]+)", text):
            tp = float(match.group(1))
            if tp <= current_price * 0.999:
                text = text.replace(match.group(0), "TP: Уже отработан / Не рассчитан")
    elif direction == "Short":
        for match in re.finditer(r"TP\d+:\s*([\d.]+)", text):
            tp = float(match.group(1))
            if tp >= current_price * 1.001:
                text = text.replace(match.group(0), "TP: Уже отработан / Не рассчитан")
    return text


@router.message(Command("scan"))
async def cmd_scan(message: types.Message):
    if message.text is None:
        return

    args = message.text.split()

    raw_sym = args[1].upper().replace("/", "").replace("USDT", "") if len(args) > 1 else "BTC"
    symbol = f"{raw_sym}USDT"

    timeframes = sort_timeframes(_get_timeframes())

    await message.answer(f"📡 Загружаю {format_symbol(symbol)} по ТФ: {', '.join(timeframes)}...")

    # === OHLCV auto-refresh: обновляем данные перед сканированием ===
    try:
        provider = OhlcvDataProvider()
        market_type = 'future' if is_futures(symbol) else 'spot'

        refresh_msg = await message.answer(f"📥 Обновляю OHLCV {format_symbol(symbol)}...")

        # Run sync refresh in thread to avoid blocking event loop
        def _do_refresh():
            return provider.refresh_many(
                symbols=[symbol],
                timeframes=timeframes,
                limit=500,
                market_type=market_type,
                force_refresh=True,
            )

        loop = asyncio.get_running_loop()
        paths = await loop.run_in_executor(None, _do_refresh)
        tf_marks = ", ".join(f"{tf}=✅" for tf in timeframes)
        await refresh_msg.edit_text(f"📥 OHLCV: {tf_marks}")
        logger.info(f"[{symbol}] OHLCV refreshed for {len(paths)} TFs before scan")

    except Exception as e:
        logger.warning(f"[{symbol}] OHLCV refresh failed (using cached): {e}")
        await message.answer(f"⚠️ OHLCV refresh failed, using cached data: {e}")

    try:
        chart_bytes_list: list[bytes] = []
        all_metrics: dict[str, dict] = {}

        for tf in timeframes:
            chart_bytes, metrics = fetch_and_plot(symbol=symbol, timeframe=tf, limit=120)
            chart_bytes_list.append(chart_bytes)
            all_metrics[tf] = metrics

        m_htf = all_metrics[timeframes[0]]
        m_ltf = all_metrics[timeframes[-1]]

        fib = m_ltf.get("fib_context", {"50%": "N/A", "61.8%": "N/A", "38.2%": "N/A", "rule": ""})
        tf_zones = {tf: all_metrics[tf]["zone"] for tf in timeframes}

        live_price = m_ltf.get("current_price", m_ltf.get("last_closed_price", 0))

        # === FIX: Получить живую цену с биржи ===
        try:
            ticker = await fetch_ticker_safe(symbol)
            if ticker and hasattr(ticker, 'last_price'):
                ticker_price = float(ticker.last_price)
                if ticker_price > 0:
                    logger.info(f"[{symbol}] /scan: ticker_price={ticker_price} vs ohlc={live_price}")
                    live_price = ticker_price
        except Exception as e:
            logger.debug(f"[{symbol}] /scan: fetch_ticker failed: {e}")

        tf_context = (
            f"[HTF] {m_htf.get('phase', 'N/A')} | Упор: {m_htf.get('resistance', 'N/A')} | Поддержка: {m_htf.get('support', 'N/A')} | "
            f"[{timeframes[-1]}] {m_ltf.get('phase', 'N/A')} | Текущая цена: {live_price} | "
            f"Объём: {m_ltf.get('vol_ratio', 1.0)}x ({m_ltf.get('vol_trend', 'N/A')}) | "
            f"Фибо: 50%={fib['50%']} | 61.8%={fib['61.8%']} | 38.2%={fib['38.2%']}"
        )

        stats = get_backtest_stats()
        metrics_str = (
            f"Текущая цена: {live_price} | Последняя закрытая: {m_ltf.get('last_closed_price', 'N/A')} | "
            f"ATR: {m_ltf.get('atr', 'N/A')} | RSI: {m_ltf.get('rsi', 'N/A')} | Сессия: {m_ltf.get('session', 'N/A')}"
        )

        # NEW: ZigZag compact context
        try:
            from core.zigzag.benchmark_zigzag import run_benchmark

            zigzag_benchmark = run_benchmark(
                symbol=symbol,
                market_type="future",
                timeframes=timeframes,
                limit=200,
                mode="hybrid_atr",
                confirmation_mode="close",
                debug=False,
                output=None,
                output_mode="compact",
            )

            zigzag_context = {
                "symbol": zigzag_benchmark.get("symbol", symbol),
                "normalized_symbol": zigzag_benchmark.get("normalized_symbol", symbol),
                "stack": zigzag_benchmark.get("stack", {}),
                "timeframes": zigzag_benchmark.get("timeframes", {}),
                "confluence_levels": zigzag_benchmark.get("confluence_levels", []),
            }
        except Exception as e:
            zigzag_context = {
                "error": True,
                "message": f"ZigZag context error: {type(e).__name__}",
                "symbol": symbol,
                "stack": {},
                "timeframes": {},
                "confluence_levels": [],
            }

        # === FIX: полный prev_ctx с divergence, price, zones ===
        # Volume hierarchy: собираем volume_context со всех ТФ
        per_tf_volume = {tf: all_metrics[tf].get("volume_context", {}) for tf in timeframes}
        volume_hierarchy = analyze_volume_hierarchy(per_tf_volume)

        # === Signal score per TF ===
        signal_scores = {}
        for tf in timeframes:
            try:
                vc = all_metrics[tf].get("volume_context", {})
                mt = all_metrics[tf]
                signal_scores[tf] = compute_signal_score(vc, mt)
            except Exception:
                pass

        # Best score across TFs (for summary)
        best_tf = max(signal_scores, key=lambda t: signal_scores[t].get("signal_score", 0)) if signal_scores else "N/A"
        best_score = signal_scores.get(best_tf, {}).get("signal_score", 0)
        best_quality = signal_scores.get(best_tf, {}).get("signal_quality", "N/A")

        # === Score-based filter recommendation ===
        score_filter = "PASS"
        score_comment = ""
        if best_score < 30:
            score_filter = "IGNORE"
            score_comment = f"Signal quality too low (score={best_score}), signal should be ignored."
        elif best_score < 50:
            score_filter = "WEAK"
            score_comment = f"Low quality signal (score={best_score}), reduced confidence."
        elif best_score < 70:
            score_filter = "MEDIUM"
            score_comment = f"Medium quality signal (score={best_score}), normal confidence."
        else:
            score_filter = "STRONG"
            score_comment = f"Strong quality signal (score={best_score}), high confidence."

        prev_ctx = {
            "metrics": metrics_str,
            "backtest_score_filter": f"[{score_filter}] {score_comment}",
            "tf_context": tf_context,
            "backtest": f"Win Rate: {stats['win_rate']}%, MAE: {stats['mae_pct']}%",
            "tf_zones": tf_zones,
            "zigzag_context": zigzag_context,
            "current_price": live_price,
            "last_closed_price": m_ltf.get("last_closed_price", live_price),
            "divergence_symbols": [symbol],
            "divergence_timeframes": timeframes,
            "tf_span_map": zigzag_context.get("stack", {}).get("tf_span_map", {}),
            "confluence_levels": zigzag_context.get("confluence_levels", []),
            # A/D volume hierarchy across TFs
            "volume_context": volume_hierarchy,
            "volume_contexts_per_tf": per_tf_volume,
            # Signal quality scores per TF
            "signal_scores": {tf: s.get("signal_score", 0) for tf, s in signal_scores.items()},
            "signal_qualities": {tf: s.get("signal_quality", "N/A") for tf, s in signal_scores.items()},
            "best_signal_tf": best_tf,
            "best_signal_score": best_score,
            "best_signal_quality": best_quality,
            "signal_filter": score_filter,
        }

        raw_result = await analyze_multi_images(chart_bytes_list, prev_analysis=prev_ctx)

        # === FIX: применить enforce_risk_rules и исправить цену ===
        if isinstance(raw_result, dict):
            raw_result["price"] = raw_result.get("price") or live_price
            raw_result["current_price"] = live_price
            raw_result = enforce_risk_rules(raw_result)

        parsed_result = raw_result
        if isinstance(parsed_result, dict):
            parsed_result = update_and_save_state(symbol, timeframes[-1], parsed_result)

        if isinstance(parsed_result, dict) and "tf_zones" in parsed_result:
            tf_zones_clean = {}
            key_map = {"1d": "1D", "4h": "4H", "1h": "1H", "15m": "15M", "5m": "5M"}

            llm_zones = parsed_result.get("tf_zones") or {}
            if isinstance(llm_zones, dict):
                for k, v in llm_zones.items():
                    norm_k = key_map.get(k.strip().lower(), k.strip().upper())
                    tf_zones_clean[norm_k] = v

            for k, v in tf_zones.items():
                norm_k = key_map.get(k.strip().lower(), k.strip().upper())
                tf_zones_clean[norm_k] = v

            parsed_result["tf_zones"] = tf_zones_clean

        if isinstance(parsed_result, dict) and parsed_result.get("error"):
            await message.answer(f"⚠️ Ошибка анализа: {parsed_result.get('message')}")
            return

        if isinstance(parsed_result, dict):
            final_text = format_json_for_tg(parsed_result)
        else:
            final_text = str(parsed_result)

        if message.from_user:
            USER_ANALYSIS_CACHE[message.from_user.id] = final_text

        await message.answer(f"📊 Анализ {format_symbol(symbol)}:\n\n{final_text}")

    except Exception as e:
        await message.answer(f"⚠️ Ошибка: {e}")


@router.message(Command("add"))
async def cmd_add(message: types.Message):
    if message.text is None:
        return
    parts = message.text.split()
    if len(parts) < 2:
        return await message.answer("❌ Укажите ID тикера: `/add XAGUSDT`")

    raw_sym = parts[1]
    result = await validate_symbol(raw_sym)

    if not result["valid"]:
        return await message.answer(result["error"])

    current = _get_symbols()
    if result["id"] in current:
        return await message.answer(f"⚠️ `{format_symbol(result['id'])}` уже в списке.")

    current.append(result["id"])
    set_setting("symbols", current)

    display = [f"`{format_symbol(s)}`" for s in current]
    await message.answer(f"✅ `{format_symbol(result['id'])}` добавлен ({result['type']}).\n📋 Список: {', '.join(display)}")


@router.message(Command("remove"))
async def cmd_remove(message: types.Message):
    if message.text is None:
        return
    parts = message.text.split()
    if len(parts) < 2:
        return await message.answer("❌ Укажите ID тикера: `/remove XAGUSDT`")

    raw_sym = parts[1].strip().upper()
    current = _get_symbols()

    found_idx = next((i for i, s in enumerate(current) if s == raw_sym or s.replace("/", "") == raw_sym), None)
    if found_idx is None:
        return await message.answer(f"⚠️ `{parts[1]}` не найден в списке.")

    current.pop(found_idx)
    set_setting("symbols", current)

    display = [f"`{format_symbol(s)}`" for s in current]
    await message.answer(f"✅ Удалён. Осталось: {', '.join(display) if current else 'нет'}")


@router.message(Command("settings"))
async def cmd_settings(message: types.Message):
    symbols = _get_symbols()
    timer = get_setting("interval_minutes", 60)
    timeframes = _get_timeframes()

    spot = [format_symbol(s) for s in symbols if not is_futures(s)]
    futures = [format_symbol(s) for s in symbols if is_futures(s)]

    txt = "⚙️ ТЕКУЩАЯ КОНФИГУРАЦИЯ:\n\n"
    txt += f" СПОТ: {', '.join(f'`{s}`' for s in spot) if spot else 'Нет'}\n"
    txt += f"🔴 ФЬЮЧЕРСЫ: {', '.join(f'`{s}`' for s in futures) if futures else 'Нет'}\n\n"
    txt += f"⏱ Интервал отчётов: {timer} минут\n"
    txt += f"📈 Таймфреймы: {', '.join(timeframes) if timeframes else 'Нет'}\n\n"
    txt += "🔹 `/add XAGUSDT` — добавить инструмент\n"
    txt += "🔹 `/remove XAGUSDT` — удалить инструмент\n"
    txt += "🔹 `/timer 30` — изменить интервал (мин 5)\n"
    txt += "🔹 `/timeframes` — выбрать ТФ"
    await message.answer(txt)


@router.message(Command("timer"))
async def cmd_timer(message: types.Message):
    if message.text is None:
        return
    parts = message.text.split()
    if len(parts) < 2:
        return await message.answer("❌ Укажите интервал в минутах: `/timer 30`")
    try:
        mins = int(parts[1])
        if mins < 5:
            return await message.answer("⚠️ Минимальный интервал — 5 минут.")
    except ValueError:
        return await message.answer("❌ Введите число.")
    success, resp = update_timer(mins)
    await message.answer(resp)


@router.message(Command("filter"))
async def cmd_filter(message: types.Message):
    if message.text is None:
        return
    args = message.text.split()
    current = get_setting("filter_mode", True)

    if len(args) > 1:
        state = args[1].lower() in ("on", "вкл", "true", "1")
        set_setting("filter_mode", state)
        status = "✅ ВКЛЮЧЁН" if state else "❌ ВЫКЛЮЧЁН"
        await message.answer(
            f"⚙️ Фильтр сигналов {status}.\n\n"
            f"🔹 ВКЛ: только подтверждённые пробои/ретесты + предупреждения о подходе к уровням.\n"
            f"🔹 ВЫКЛ: все отчёты без фильтрации."
        )
    else:
        status = "✅ ВКЛЮЧЁН" if current else "❌ ВЫКЛЮЧЁН"
        await message.answer(f"⚙️ Фильтр сигналов: {status}\nИспользуйте: `/filter on` или `/filter off`")


@router.message(Command("export"))
async def cmd_export(message: types.Message):
    stats = get_backtest_stats()
    csv_data = get_history_df()
    filename = f"forecasts_{stats['total']}_win{stats['win_rate']}%.csv"
    await message.answer_document(
        BufferedInputFile(csv_data.encode("utf-8-sig"), filename=filename),
        caption=f"📈 Бэктест: Всего {stats['total']}, Win: {stats['win_rate']}%, MAE: {stats['mae_pct']}%"
    )


@router.message(lambda msg: msg.photo)
async def collect_photos(message: types.Message):
    assert message.from_user is not None
    assert message.bot is not None

    user_id = message.from_user.id
    if user_id not in USER_PHOTO_BUFFER:
        USER_PHOTO_BUFFER[user_id] = []

    if len(USER_PHOTO_BUFFER[user_id]) >= 5:
        await message.answer("⚠️ Максимум 5 фото. Введите `/analyze_all`.")
        return

    if not message.photo:
        return

    bio = io.BytesIO()
    file_info = await message.bot.get_file(message.photo[-1].file_id)
    if file_info.file_path:
        await message.bot.download_file(file_info.file_path, destination=bio)
        USER_PHOTO_BUFFER[user_id].append(bio.getvalue())
        await message.answer(f"✅ Фото сохранено ({len(USER_PHOTO_BUFFER[user_id])}/5).")
    else:
        await message.answer("❌ Не удалось скачать фото.")


@router.message(Command("analyze_all"))
async def cmd_analyze_all(message: types.Message):
    assert message.from_user is not None
    user_id = message.from_user.id

    if user_id not in USER_PHOTO_BUFFER or not USER_PHOTO_BUFFER[user_id]:
        await message.answer("❌ Сначала отправьте фото.")
        return

    images = USER_PHOTO_BUFFER.pop(user_id)
    prev = USER_ANALYSIS_CACHE.get(user_id)

    await message.answer("🧠 Анализирую...")
    try:
        result = await analyze_multi_images(images, prev_analysis=prev)
        final_text = format_json_for_tg(result)

        USER_ANALYSIS_CACHE[user_id] = final_text
        await message.answer(final_text)

    except Exception as e:
        await message.answer(f"⚠️ Ошибка: {e}")

@router.message(Command("analyze_ad"))
async def cmd_analyze_ad(message: types.Message):
    """A/D divergence анализ — показывает последние divergence-сигналы для пары."""
    if message.text is None:
        return

    args = message.text.split()
    if len(args) < 2:
        return await message.answer(
            "❌ Формат: `/analyze_ad BTCUSDT 1h`\n"
            "Пример: `/analyze_ad ETHUSDT 4h`\n"
            "Без ТФ — показывает по всем доступным."
        )

    raw_sym = args[1].upper().replace("/", "").replace("USDT", "")
    symbol = f"{raw_sym}USDT"
    tf_arg = args[2].strip().lower() if len(args) > 2 else None

    await message.answer(f"🔍 A/D анализ {format_symbol(symbol)}...")

    try:
        from core.divergence_context import (
            load_candidates, get_multi_context,
            discover_available_tfs, discover_available_symbols,
        )

        available = discover_available_symbols()
        if symbol not in available:
            return await message.answer(
                f"⚠️ Нет данных дивергенций для {format_symbol(symbol)}.\n"
                f"Доступные пары: {', '.join(available) or 'нет'}"
            )

        if tf_arg:
            signals, csv_path = load_candidates(symbol, tf_arg)
            if not signals:
                return await message.answer(
                    f"⚠️ Нет кандидатов для {format_symbol(symbol)} | {tf_arg.upper()}.\n"
                    f"Доступные ТФ: {', '.join(discover_available_tfs(symbol))}"
                )

            bull = [s for s in signals if s.div_type == "bull"]
            bear = [s for s in signals if s.div_type == "bear"]
            hidden = [s for s in signals if s.hidden]

            txt = f"📊 A/D Divergence {format_symbol(symbol)} | {tf_arg.upper()}\n\n"
            txt += f"Всего сигналов: {len(signals)}\n"
            txt += f"🟢 Bull: {len(bull)} | 🔴 Bear: {len(bear)}\n"
            txt += f"👁 Hidden: {len(hidden)}\n\n"

            top5 = sorted(signals, key=lambda s: s.strength, reverse=True)[:5]
            txt += "🏆 Топ-5 по силе:\n"
            for i, s in enumerate(top5, 1):
                emoji = "🟢" if s.div_type == "bull" else "🔴"
                txt += f"  {i}. {emoji} {s.label} | Str={s.strength:.1f} | Bias={s.bias_label} | Vol={s.vol_ratio:.1f}x"
                if s.time_str and "-" in s.time_str:
                    txt += f" | {s.time_str[:13].replace('T',' ')}"
                txt += "\n"

            llm_ctx = get_multi_context(symbol, [tf_arg], lookback_hours=168, max_per_tf=10)
            if len(llm_ctx) > 200:
                txt += f"\n📋 Контекст для LLM ({len(llm_ctx)} символов):\n```\n{llm_ctx}\n```"

            await message.answer(txt)
        else:
            tfs = discover_available_tfs(symbol)
            if not tfs:
                return await message.answer(f"⚠️ Нет данных для {format_symbol(symbol)}.")

            txt = f"📊 A/D Divergence {format_symbol(symbol)} | ТФ: {', '.join(tfs)}\n\n"

            for tf in tfs:
                signals, _ = load_candidates(symbol, tf)
                bull = len([s for s in signals if s.div_type == "bull"])
                bear = len([s for s in signals if s.div_type == "bear"])
                top = sorted(signals, key=lambda s: s.strength, reverse=True)[:3]

                txt += f"--- {tf.upper()} ({len(signals)} сигналов: {bull} bull, {bear} bear) ---\n"
                for s in top:
                    emoji = "🟢" if s.div_type == "bull" else "🔴"
                    txt += f"  {emoji} Str={s.strength:.1f} | {s.label} | Bias={s.bias_label}\n"
                txt += "\n"

            await message.answer(txt)

    except Exception as e:
        logger.exception(f"analyze_ad error: {e}")
        await message.answer(f"⚠️ Ошибка: {e}")


@router.message(Command("run_ad"))
async def cmd_run_ad(message: types.Message):
    """Запуск A/D скана по всем парам из настроек."""
    symbols = _get_symbols()

    await message.answer(f"🔍 A/D скан: {', '.join(format_symbol(s) for s in symbols)}...")

    try:
        from core.divergence_context import (
            load_candidates, discover_available_tfs, discover_available_symbols,
        )

        available = discover_available_symbols()
        found_any = False

        for symbol in symbols:
            if symbol not in available:
                continue

            tfs = discover_available_tfs(symbol)
            if not tfs:
                continue

            found_any = True
            parts = []

            for tf in tfs:
                signals, _ = load_candidates(symbol, tf)
                bull = len([s for s in signals if s.div_type == "bull"])
                bear = len([s for s in signals if s.div_type == "bear"])
                hidden = len([s for s in signals if s.hidden])

                if signals:
                    top = sorted(signals, key=lambda s: s.strength, reverse=True)[0]
                    parts.append(
                        f"  {tf.upper()}: {len(signals)} ({bull}B/{bear}S) | "
                        f"Top: {top.label} Str={top.strength:.1f}"
                    )
                else:
                    parts.append(f"  {tf.upper()}: нет сигналов")

            txt = f"📊 {format_symbol(symbol)}:\n" + "\n".join(parts)
            await message.answer(txt)

        if not found_any:
            await message.answer("📊 Нет divergence-данных для пар из настроек.")
        else:
            await message.answer(f"✅ Сканирование завершено.")

    except Exception as e:
        logger.exception(f"run_ad error: {e}")
        await message.answer(f"⚠️ Ошибка: {e}")


@router.message(Command("setup"))
async def cmd_setup(message: types.Message):
    """Запустить/пересоздать полную настройку для тикера."""
    if message.text is None:
        return

    args = message.text.split()
    if len(args) < 2:
        return await message.answer(
            "❌ Формат: `/setup XAUTUSDT`\n"
            "Перескачивает OHLCV, автотюн, A/D кандидаты и Pine экспорт."
        )

    raw_sym = args[1].upper().replace("/", "").replace("USDT", "")
    symbol = f"{raw_sym}USDT"

        # Use bot's timeframes directly (normalize: "1D"→"1d")
    bot_tfs = _get_timeframes()
    tfs_for_setup = [tf.strip().lower() for tf in bot_tfs]

    # Check for --refresh flag
    force_refresh = "--refresh" in args

    await message.answer(
        f"🔧 Настройка {format_symbol(symbol)} | {', '.join(tfs_for_setup)}...\n"
        f"{'🔄 Полная перезагрузка' if force_refresh else '📥 Инкрементальное обновление'}\n"
        f"⏳ Это может занять несколько минут (автотюн)."
    )

    def progress(msg: str, *_args):
        try:
            asyncio.ensure_future(message.answer(msg))
        except Exception:
            pass

    try:
        setup_result = await onboard_symbol_async(
            symbol=symbol,
            timeframes=tfs_for_setup,
            force_refresh=force_refresh,
            progress_cb=progress,
        )

        total_cand = sum(setup_result.get("candidates", {}).values())
        pine_n = setup_result.get("pine_exports", 0)
        errors = setup_result.get("errors", [])

        summary = f"✅ Настройка {format_symbol(symbol)} завершена!\n\n"
        ohlcv_marks = ", ".join(f"{tf}={'✅' if p else '❌'}" for tf, p in setup_result.get("ohlcv", {}).items())
        summary += f"📥 OHLCV: {ohlcv_marks}\n"
        summary += f"⚙️ Автотюн: {len(setup_result.get('autotune', {}))} ТФ\n"
        summary += f"📊 A/D кандидатов: {total_cand}\n"
        summary += f"📌 Pine экспорт: {pine_n} файлов\n"

        if errors:
            summary += f"⚠️ Ошибки: {'; '.join(errors[:3])}"

        await message.answer(summary)

    except Exception as e:
        logger.exception(f"setup {symbol} error")
        await message.answer(f"⚠️ Ошибка настройки: {e}")


@router.message(Command("status"))
async def cmd_status(message: types.Message):
    """Показать статус настройки для всех символов."""
    symbols = _get_symbols()
    timeframes = sort_timeframes(_get_timeframes())

    txt = "📊 СТАТУС НАСТРОЙКИ СИМВОЛОВ:\n\n"

    for symbol in symbols:
        display_sym = format_symbol(symbol)
        status = get_setup_status(symbol, timeframes)

        tf_details = []
        for tf, st in status.items():
            if st == "ok":
                tf_details.append(f"  ✅ {tf}")
            elif st == "missing_ohlcv":
                tf_details.append(f"  ❌ {tf} (нет OHLCV)")
            elif st == "missing_candidates":
                tf_details.append(f"  ⚠️ {tf} (нет A/D)")
            elif st == "no_autotune":
                tf_details.append(f"  ⚠️ {tf} (нет автотюна)")
            else:
                tf_details.append(f"  ❓ {tf} ({st})")

        all_ok = all(s == "ok" for s in status.values())
        icon = "✅" if all_ok else "🔧"
        txt += f"{icon} {display_sym}:\n" + "\n".join(tf_details) + "\n\n"

    txt += (
        f"💡 `/setup XAUTUSDT` — пересоздать настройку\n"
        f"💡 `/add SOLUSDT` — добавить с авто-настройкой"
    )

    await message.answer(txt)

@router.callback_query()
async def callbacks_handler(callback: types.CallbackQuery):
    await callback.answer()
    if not callback.message or isinstance(callback.message, InaccessibleMessage):
        return
    if not callback.data:
        return

    if callback.data == "menu_scan":
        await callback.message.answer("📊 Введите пару для анализа:\n`/scan BTC` или `/scan ETH/USDT`")
    elif callback.data == "menu_screenshots":
        await callback.message.answer("📷 Отправьте 1–5 скриншотов графиков, затем введите `/analyze_all`")
    elif callback.data == "menu_instruments":
        await callback.message.answer("⚙️ Управление портфелем:\n`/add SOL/USDT` — добавить\n`/remove ETH/USDT` — удалить\n`/settings` — текущий список")
    elif callback.data == "menu_timer":
        await callback.message.answer("⏱ Введите интервал авто-отчётов в минутах:\n`/timer 15` (минимум 5)")
    elif callback.data == "menu_timeframes":
        await cmd_timeframes(callback.message)
    elif callback.data == "menu_export":
        await cmd_export(callback.message)
    elif callback.data == "menu_settings":
        await cmd_settings(callback.message)
    elif callback.data == "menu_about":
        await callback.message.edit_text(
            "🤖 Локальный ИИ-бот на qwen2.5-vl-7b\n"
            "🔒 Скриншоты и данные не покидают вашу сеть\n"
            "📡 Источник: Binance Public API (без ключей)\n"
            "🛠 Стек: aiogram 3.27 | LM Studio | SQLite | APScheduler"
        )
    elif callback.data == "analyze_all_btn":
        if not callback.from_user:
            return
        user_id = callback.from_user.id

        if user_id in USER_PHOTO_BUFFER and USER_PHOTO_BUFFER[user_id]:
            images = USER_PHOTO_BUFFER.pop(user_id)
            await callback.message.edit_text("🧠 Анализирую...")
            try:
                result = await analyze_multi_images(images, prev_analysis=USER_ANALYSIS_CACHE.get(user_id))
                final_text = format_json_for_tg(result)
                USER_ANALYSIS_CACHE[user_id] = final_text
                await callback.message.answer(final_text)
            except Exception as e:
                await callback.message.answer(f"⚠️ Ошибка: {e}")
        else:
            await callback.message.answer("❌ Сначала отправьте скриншоты.")
    elif callback.data == "export_history":
        stats = get_backtest_stats()
        csv_data = get_history_df()
        await callback.message.answer_document(
            BufferedInputFile(csv_data.encode("utf-8-sig"), filename="forecasts.csv"),
            caption=f"📈 Бэктест: Win {stats['win_rate']}%, MAE {stats['mae_pct']}%"
        )
    elif callback.data == "settings_menu":
        await callback.message.edit_text("⚙️ Используйте команды: /settings, /add, /remove, /timer, /timeframes")
    elif callback.data == "about":
        await callback.message.edit_text("🤖 Локальный ИИ + Binance API. Данные не покидают сеть.")
