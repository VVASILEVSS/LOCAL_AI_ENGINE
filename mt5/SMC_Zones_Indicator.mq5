//+------------------------------------------------------------------+
//|                                        SMC_Zones_Indicator.mq5    |
//|                  LOCAL_AI_ENGINE — MT5 Bridge Indicator           |
//|                  Рисует зоны (upper/lower) + стрелки пробоев      |
//|                  Данные: http://localhost:5000/api/signals        |
//+------------------------------------------------------------------+
#property copyright "LOCAL_AI_ENGINE"
#property version   "1.19"
#property indicator_chart_window
#property indicator_plots 0

// --- Входные параметры ---
input string  ServerURL    = "http://127.0.0.1:5000/api/signals";  // URL API (127.0.0.1 = этот ПК; для MT5 на другом ПК: http://192.168.43.235:5000/api/signals)
input string  TargetSymbol = "AUTO";                              // Символ (AUTO = по графику, или вручную: BTCUSDT, ETHUSDT, XAUTUSDT)
input int     PollSeconds  = 300;                                    // Опрос API (сек). Бот сканит каждые 15мин → 300сек=5мин достаточно. Min=60, Max=3600
input string  ShowTFs      = "15M,1H,4H,1D";                         // ТФ (uppercase как в API!)
input color   ColorUpper   = clrRed;                                 // Цвет resistance
input color   ColorLower   = clrGreen;                               // Цвет support
input color   ColorBreak   = clrGold;                                // Цвет стрелок пробоя
input int     LineWidth    = 1;                                      // Толщина линий
input bool    ShowPrice    = true;                                   // Показать цену
input bool    ShowLabel    = true;                                   // Показать текстовые метки
input bool    ShowZoneFill = true;                                   // Заливка зон прямоугольниками
input color   ColorFill1D  = clrDimGray;                             // Заливка 1D
input color   ColorFill4H  = clrDarkSlateGray;                       // Заливка 4H
input color   ColorFill1H  = clrSlateGray;                           // Заливка 1H
input color   ColorFill15M = clrGray;                                // Заливка 15M
input bool    AutoTextColor = true;                                  // Авто-цвет текста (белый на тёмном, тёмный на светлом)
input color   TextColorManual = clrWhite;                             // Цвет текста (если AutoTextColor=false)
input bool    EnableAlerts   = true;                                  // Включить алерты (звук + всплывающее окно)
input bool    AlertOnBreakout = true;                                 // Алерт при пробое зоны (цена за upper/lower)
input bool    AlertOnSignal  = true;                                 // Алерт при сигнале (aggressive_breakout / retest / reversal)
input bool    AlertOnProximity = true;                                // Алерт при приближении цены к зоне (%)
input double  ProximityPercent = 0.5;                                 // % близости цены к зоне для алерта (0.5%)
input bool    AlertSoundOnly = false;                                 // Только звук (без всплывающего окна)
input string  AlertSoundFile = "alert.wav";                           // Звуковой файл (alert.wav = стандартный)
input bool    AlertPushNotif = false;                                 // Push-уведомление на мобильное (нужен MetaQuotes ID)

// --- Глобальные ---
string PREFIX = "SMC_";
datetime lastPoll = 0;

// State-tracking: логируем только при изменении
string g_lastStateHash = "";      // хэш зон+цены+статуса
string g_lastErrorKey = "";      // ключ последней ошибки (чтобы не спамить)
int    g_pollCount = 0;           // счётчик поллов (для диагностики)

// Alert-tracking: чтобы не повторять один алерт
bool   g_alertedBreakoutR = false;  // уже алертили пробой resistance
bool   g_alertedBreakoutS = false;  // уже алертили пробой support
string g_alertedSignal    = "";     // последний сигнал-статус (для алерта)
string g_alertedProximity = "";     // последний proximity-ключ (TF+side)
string g_lastSigStatus    = "";     // для детекции смены сигнала

//+------------------------------------------------------------------+
//| Автоопределение символа графика → формат бота                    |
//| BTCUSD/BTCIUSD → BTCUSDT, ETHUSD → ETHUSDT, XAUUSD → XAUTUSDT   |
//+------------------------------------------------------------------+
string ResolveSymbol() {
   if(TargetSymbol != "AUTO") return TargetSymbol;
   string s = Symbol();
   StringToUpper(s);
   // Убираем возможные суффиксы брокера: BTCUSD.r, BTCUSD#, BTCUSD-i
   // Ищем ключевое слово в начале
   if(StringFind(s, "BTC") == 0) return "BTCUSDT";
   if(StringFind(s, "ETH") == 0) return "ETHUSDT";
   if(StringFind(s, "XAU") == 0) return "XAUTUSDT";   // золото в боте = XAUTUSDT
   if(StringFind(s, "XAG") == 0) return "XAGUSDT";
   if(StringFind(s, "SOL") == 0) return "SOLUSDT";
   // Не распозно — возвращаем как есть + добавляем USDT если нет
   if(StringFind(s, "USDT") < 0) s += "USDT";
   return s;
}

//+------------------------------------------------------------------+
//| Инициализация                                                    |
//+------------------------------------------------------------------+
int OnInit() {
   // Clamping: 60..3600 сек (1 мин .. 1 час)
   int poll = (int)PollSeconds;
   if(poll < 60)       poll = 60;
   else if(poll > 3600) poll = 3600;
   EventSetTimer(poll);
   string resolved = ResolveSymbol();
   Print("SMC Zones v1.19: старт. URL=", ServerURL, " Symbol=", TargetSymbol, "→", resolved,
         " (chart=", Symbol(), ") ShowTFs=", ShowTFs, " Poll=", poll, "сек (input=", PollSeconds, ")");
   PollSignals();
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Деинициализация                                                  |
//+------------------------------------------------------------------+
void OnDeinit(const int reason) {
   EventKillTimer();
   CleanupObjects();
   Print("SMC Zones: остановка. reason=", reason);
}

//+------------------------------------------------------------------+
//| Таймер                                                           |
//+------------------------------------------------------------------+
void OnTimer() {
   if(TimeCurrent() - lastPoll < PollSeconds) return;
   lastPoll = TimeCurrent();
   PollSignals();
}

//+------------------------------------------------------------------+
//| OnCalculate                                                      |
//+------------------------------------------------------------------+
int OnCalculate(const int rates_total,
                const int prev_calculated,
                const datetime &time[],
                const double &open[],
                const double &high[],
                const double &low[],
                const double &close[],
                const long &tick_volume[],
                const long &volume[],
                const int &spread[]) {
   return rates_total;
}

//+------------------------------------------------------------------+
//| Алерт (звук + всплывающее окно + push)                           |
//+------------------------------------------------------------------+
void FireAlert(string message) {
   if(!EnableAlerts) return;

   string fullMsg = "🔔 SMC " + Symbol() + ": " + message;

   if(AlertSoundOnly) {
      PlaySound(AlertSoundFile);
   } else {
      Alert(fullMsg);   // Alert() = звук + всплывающее окно
   }

   if(AlertPushNotif) {
      SendNotification(fullMsg);
   }

   Print("ALERT: ", message);
}

//+------------------------------------------------------------------+
//| Проверка proximity (близость цены к уровню)                       |
//+------------------------------------------------------------------+
void CheckProximityAlerts(double price, string zonesBlock, string &tfs[], int tfCount) {
   if(!EnableAlerts || !AlertOnProximity || price <= 0) return;

   for(int i = 0; i < tfCount; i++) {
      string tf = tfs[i];
      StringTrimLeft(tf);
      StringTrimRight(tf);
      StringToUpper(tf);

      string tfKey = "\"" + tf + "\":";
      int tfPos = StringFind(zonesBlock, tfKey);
      if(tfPos < 0) continue;

      double upper = ExtractDoubleFromPos(zonesBlock, tfPos, "\"upper\":");
      double lower = ExtractDoubleFromPos(zonesBlock, tfPos, "\"lower\":");

      if(upper > 0) {
         double dist = MathAbs(upper - price) / price * 100.0;
         string proxKey = tf + "_R";
         if(dist <= ProximityPercent && g_alertedProximity != proxKey) {
            FireAlert("Цена " + DoubleToString(price, _Digits) +
                     " в " + DoubleToString(dist, 2) + "% от resistance " +
                     DoubleToString(upper, _Digits) + " (" + tf + ")");
            g_alertedProximity = proxKey;
         }
      }

      if(lower > 0) {
         double dist = MathAbs(price - lower) / price * 100.0;
         string proxKey = tf + "_S";
         if(dist <= ProximityPercent && g_alertedProximity != proxKey) {
            FireAlert("Цена " + DoubleToString(price, _Digits) +
                     " в " + DoubleToString(dist, 2) + "% от support " +
                     DoubleToString(lower, _Digits) + " (" + tf + ")");
            g_alertedProximity = proxKey;
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Проверка сигнала (aggressive_breakout / retest / reversal)        |
//+------------------------------------------------------------------+
void CheckSignalAlert(string sigStatus, double price) {
   if(!EnableAlerts || !AlertOnSignal) return;
   if(sigStatus == "") return;

   // Только actionable signals
   bool isActionable = (sigStatus == "aggressive_breakout" ||
                        sigStatus == "retest" ||
                        sigStatus == "reversal" ||
                        sigStatus == "false_breakout");

   if(!isActionable) {
      g_alertedSignal = sigStatus;
      return;
   }

   // Алерт только при смене сигнала (не повторяем)
   if(g_alertedSignal != sigStatus) {
      string actionMsg = "";
      if(sigStatus == "aggressive_breakout") actionMsg = "🚀 AGGRESSIVE BREAKOUT";
      else if(sigStatus == "retest")         actionMsg = "🔄 RETEST";
      else if(sigStatus == "reversal")       actionMsg = "⚡ REVERSAL";
      else if(sigStatus == "false_breakout") actionMsg = "⚠️ FALSE BREAKOUT";

      FireAlert(actionMsg + " | цена=" + DoubleToString(price, _Digits) +
               " | status=" + sigStatus);
      g_alertedSignal = sigStatus;
   }
}

//+------------------------------------------------------------------+
//| Опрос API и отрисовка                                            |
//+------------------------------------------------------------------+
void PollSignals() {
   g_pollCount++;
   string sym = ResolveSymbol();
   string headers = "";
   char data[] = {0};
   char result[] = {0};

   string response = "";
   bool useFile = false;

   // --- Попытка 1: WebRequest ---
   ResetLastError();
   int status = WebRequest("GET", ServerURL, "", "", 30000, data, 0, result, headers);

   if(status == -1) {
      int err = GetLastError();
      LogErrorOnce("WR_FAIL", "WebRequest FAIL err=" + IntegerToString(err)
                   + " → fallback на файл signals_" + sym + ".json");
      // --- Попытка 2: Файловый fallback ---
      if(!TryReadFile(sym, response)) {
         return;
      }
      useFile = true;
   }
   else if(status != 200) {
      LogErrorOnce("HTTP_" + IntegerToString(status), "HTTP " + IntegerToString(status));
      if(!TryReadFile(sym, response)) {
         return;
      }
      useFile = true;
   }
   else {
      response = CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);
   }

   // Анти-спам: WR_FAIL логируем только раз — файловый fallback работает
   // (не сбрасываем g_lastErrorKey, иначе каждый poll будет спамить ошибкой)

   // Для файлового режима response = JSON одного символа (без symbols wrapper)
   string symKey = "\"" + sym + "\":";
   int symPos = StringFind(response, symKey);

   string zonesBlock;
   string symBlock;

   if(useFile || symPos < 0) {
      // Файловый режим: весь response = данные символа
      symBlock = response;
      int zb = StringFind(response, "\"zones\"");
      if(zb < 0) {
         LogErrorOnce("NO_ZONES_FILE", "'zones' не найдено в файле");
         return;
      }
      int braceStart = StringFind(response, "{", zb);
      if(braceStart < 0) return;
      int depth = 1, p = braceStart + 1, rlen = StringLen(response);
      while(p < rlen && depth > 0) {
         string ch = StringSubstr(response, p, 1);
         if(ch == "{") depth++;
         else if(ch == "}") depth--;
         p++;
      }
      zonesBlock = StringSubstr(response, braceStart, p - braceStart);
   }
   else {
      // WebRequest режим: ищем символ в symbols
      int zonesKeyPos = StringFind(response, "\"zones\"", symPos);
      if(zonesKeyPos < 0) {
         LogErrorOnce("NO_ZONES", "'zones' не найдено после символа");
         return;
      }
      int braceStart = StringFind(response, "{", zonesKeyPos);
      if(braceStart < 0) {
         LogErrorOnce("NO_BRACE", "'{' после zones не найдена");
         return;
      }
      int depth = 1, p = braceStart + 1, rlen = StringLen(response);
      while(p < rlen && depth > 0) {
         string ch = StringSubstr(response, p, 1);
         if(ch == "{") depth++;
         else if(ch == "}") depth--;
         p++;
      }
      zonesBlock = StringSubstr(response, braceStart, p - braceStart);
      symBlock = StringSubstr(response, symPos, braceStart - symPos);
   }

   // Цена: реальная цена графика MT5 (SymbolInfoDouble) вместо запаздывающей цены из JSON.
   // JSON price = цена на момент скана бота (15+ мин назад), что приводит к пропуску пробоев.
   double jsonPrice = ExtractDouble(symBlock, "\"price\":");
   double price = SymbolInfoDouble(Symbol(), SYMBOL_BID);
   if(price <= 0) price = SymbolInfoDouble(Symbol(), SYMBOL_ASK);
   if(price <= 0) price = jsonPrice;  // fallback на JSON price если MT5 цена недоступна
   string sigStatus = ExtractString(symBlock, "\"signal_status\":");
   string sigDir = ExtractString(symBlock, "\"signal_direction\":");
   string phase = ExtractString(symBlock, "\"phase\":");

   string tfs[];
   int tfCount = StringSplit(ShowTFs, ',', tfs);

   // Собираем state hash — только значимые поля (зоны + статус, БЕЗ цены)
   string stateHash = sigStatus + "|" + sigDir + "|" + phase + "|";
   for(int i = 0; i < tfCount; i++) {
      string tf = tfs[i];
      StringTrimLeft(tf);
      StringTrimRight(tf);
      StringToUpper(tf);
      string tfKey = "\"" + tf + "\":";
      int tfPos = StringFind(zonesBlock, tfKey);
      if(tfPos < 0) {
         stateHash += tf + ":MISS|";
         continue;
      }
      double upper = ExtractDoubleFromPos(zonesBlock, tfPos, "\"upper\":");
      double lower = ExtractDoubleFromPos(zonesBlock, tfPos, "\"lower\":");
      stateHash += tf + ":" + DoubleToString(upper, 1) + "/" + DoubleToString(lower, 1) + "|";
   }

   // Логируем только если состояние изменилось
   bool changed = (stateHash != g_lastStateHash);
   if(changed || g_pollCount == 1) {
      Print("SMC: состояние изменено (poll #", g_pollCount, ")");
      Print("  price=", DoubleToString(price, _Digits),
            " status=", sigStatus, " dir=", sigDir, " phase=", phase);
      for(int i = 0; i < tfCount; i++) {
         string tf = tfs[i];
         StringTrimLeft(tf);
         StringTrimRight(tf);
         StringToUpper(tf);
         string tfKey = "\"" + tf + "\":";
         int tfPos = StringFind(zonesBlock, tfKey);
         if(tfPos < 0) {
            Print("  ", tf, ": НЕ НАЙДЕН в API");
            continue;
         }
         double upper = ExtractDoubleFromPos(zonesBlock, tfPos, "\"upper\":");
         double lower = ExtractDoubleFromPos(zonesBlock, tfPos, "\"lower\":");
         Print("  ", tf, ": R=", DoubleToString(upper, _Digits),
               " S=", DoubleToString(lower, _Digits));
      }
      g_lastStateHash = stateHash;
   }
   // Анти-спам: WR_FAIL логируем только раз — файловый fallback работает
   // (не сбрасываем g_lastErrorKey, иначе каждый poll будет спамить ошибкой)

   CleanupZoneObjects();

   if(ShowPrice && price > 0) {
      DrawHLine(PREFIX + "PRICE", price, clrDodgerBlue, STYLE_DOT, 1, "Цена: " + DoubleToString(price, _Digits));
   }

   int zonesDrawn = 0;
   for(int i = 0; i < tfCount; i++) {
      string tf = tfs[i];
      StringTrimLeft(tf);
      StringTrimRight(tf);
      StringToUpper(tf);

      string tfKey = "\"" + tf + "\":";
      int tfPos = StringFind(zonesBlock, tfKey);
      if(tfPos < 0) continue;

      double upper = ExtractDoubleFromPos(zonesBlock, tfPos, "\"upper\":");
      double lower = ExtractDoubleFromPos(zonesBlock, tfPos, "\"lower\":");

      if(ShowZoneFill && upper > 0 && lower > 0 && upper != lower) {
         color fillColor = GetZoneColor(tf);
         DrawZoneFill(PREFIX + "FILL_" + tf, lower, upper, fillColor);
      }

      if(upper > 0) {
         DrawHLine(PREFIX + "R_" + tf, upper, ColorUpper, STYLE_SOLID, LineWidth,
                   "R " + tf + ": " + DoubleToString(upper, _Digits));
         zonesDrawn++;
         if(price > upper && price > 0) {
            DrawBreakoutArrow("BRK_R_" + tf, upper, true, "ПРОБОЙ R " + tf);
            // Алерт при пробое resistance
            if(EnableAlerts && AlertOnBreakout && !g_alertedBreakoutR) {
               FireAlert("🔴 ПРОБОЙ resistance " + DoubleToString(upper, _Digits) +
                        " (" + tf + ") | цена=" + DoubleToString(price, _Digits));
               g_alertedBreakoutR = true;
               g_alertedBreakoutS = false;  // сброс opposite
            }
         } else {
            g_alertedBreakoutR = false;  // цена вернулась — сброс
         }
      }

      if(lower > 0) {
         DrawHLine(PREFIX + "S_" + tf, lower, ColorLower, STYLE_SOLID, LineWidth,
                   "S " + tf + ": " + DoubleToString(lower, _Digits));
         zonesDrawn++;
         if(price < lower && price > 0) {
            DrawBreakoutArrow("BRK_S_" + tf, lower, false, "ПРОБОЙ S " + tf);
            // Алерт при пробое support
            if(EnableAlerts && AlertOnBreakout && !g_alertedBreakoutS) {
               FireAlert("🟢 ПРОБОЙ support " + DoubleToString(lower, _Digits) +
                        " (" + tf + ") | цена=" + DoubleToString(price, _Digits));
               g_alertedBreakoutS = true;
               g_alertedBreakoutR = false;  // сброс opposite
            }
         } else {
            g_alertedBreakoutS = false;  // цена вернулась — сброс
         }
      }
   }

   // Инфо-панель (разворачиваемая)
   if(ShowLabel) {
      DrawInfoPanel(price, sym, sigStatus, sigDir, phase, zonesBlock, tfs, tfCount);
   }

   // Алерты: proximity (близость к зоне) + signal (смена сигнала)
   CheckProximityAlerts(price, zonesBlock, tfs, tfCount);
   CheckSignalAlert(sigStatus, price);

   ChartRedraw();
}

//+------------------------------------------------------------------+
//| Разворачиваемая инфо-панель                                      |
//| Клик по заголовку — раскрыть/скрыть детали                       |
//+------------------------------------------------------------------+
bool g_panelExpanded = false;

void DrawInfoPanel(double price, string sym, string sigStatus, string sigDir, string phase,
                   string zonesBlock, string &tfs[], int tfCount) {
   string panelTag = PREFIX + "PANEL_TAG";
   string panelBody = PREFIX + "PANEL_BODY";
   color txtClr = GetTextColor();
   color accentClr = (sigStatus == "accumulation") ? clrGold :
                     (sigStatus == "bullish_breakout") ? clrLime :
                     (sigStatus == "bearish_breakout") ? clrRed : clrGray;

   string dirIcon = "";
   if(sigDir == "bullish") dirIcon = " ↑";
   else if(sigDir == "bearish") dirIcon = " ↓";

   // --- Заголовок (всегда виден) ---
   if(ObjectFind(0, panelTag) < 0)
      ObjectCreate(0, panelTag, OBJ_LABEL, 0, 0, 0);
   ObjectSetInteger(0, panelTag, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, panelTag, OBJPROP_XDISTANCE, 10);
   ObjectSetInteger(0, panelTag, OBJPROP_YDISTANCE, 20);
   ObjectSetString(0, panelTag, OBJPROP_TEXT,
      "SMC Zones " + sym + " | " + DoubleToString(price, _Digits) +
      " | " + sigStatus + dirIcon + (g_panelExpanded ? "  [v]" : "  [>]"));
   ObjectSetInteger(0, panelTag, OBJPROP_COLOR, accentClr);
   ObjectSetInteger(0, panelTag, OBJPROP_FONTSIZE, 10);
   ObjectSetInteger(0, panelTag, OBJPROP_SELECTABLE, true);
   ObjectSetInteger(0, panelTag, OBJPROP_SELECTED, false);

   // --- Тело панели (если развёрнута) ---
   if(g_panelExpanded) {
      if(ObjectFind(0, panelBody) < 0)
         ObjectCreate(0, panelBody, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, panelBody, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, panelBody, OBJPROP_XDISTANCE, 10);
      ObjectSetInteger(0, panelBody, OBJPROP_YDISTANCE, 42);

      string body = "";
      body += "Статус: " + sigStatus + dirIcon + "\n";
      body += "Фаза: " + phase + "\n";
      body += "Цена: " + DoubleToString(price, _Digits) + "\n";
      body += "---------------------------\n";
      body += "TF  |  R (upper)  |  S (lower)\n";
      for(int i = 0; i < tfCount; i++) {
         string tf = tfs[i];
         StringTrimLeft(tf);
         StringTrimRight(tf);
         StringToUpper(tf);
         string tfKey = "\"" + tf + "\":";
         int tfPos = StringFind(zonesBlock, tfKey);
         if(tfPos < 0) continue;
         double upper = ExtractDoubleFromPos(zonesBlock, tfPos, "\"upper\":");
         double lower = ExtractDoubleFromPos(zonesBlock, tfPos, "\"lower\":");
         body += tf + "  |  " + DoubleToString(upper, _Digits) +
                 "  |  " + DoubleToString(lower, _Digits) + "\n";
      }
      body += "---------------------------\n";
      body += "Click title to collapse";

      ObjectSetString(0, panelBody, OBJPROP_TEXT, body);
      ObjectSetInteger(0, panelBody, OBJPROP_COLOR, txtClr);
      ObjectSetInteger(0, panelBody, OBJPROP_FONTSIZE, 9);
      ObjectSetInteger(0, panelBody, OBJPROP_SELECTABLE, false);
   } else {
      ObjectDelete(0, panelBody);
   }
}

//+------------------------------------------------------------------+
//| Обработка клика по панели (ChartEvent)                          |
//+------------------------------------------------------------------+
void OnChartEvent(const int id, const long &lparam, const double &dparam, const string &sparam) {
   if(id == CHARTEVENT_OBJECT_CLICK) {
      if(sparam == PREFIX + "PANEL_TAG") {
         g_panelExpanded = !g_panelExpanded;
         // Перезапросим данные для отрисовки панели
         PollSignals();
         ChartRedraw();
      }
   }
}

//+------------------------------------------------------------------+
//| Чтение JSON из файла (fallback для MT5 без WebRequest)           |
//+------------------------------------------------------------------+
bool TryReadFile(string sym, string &content) {
   // Динамически генерируем имя файла из символа: signals_BTCUSDT.json
   string filename = "signals_" + sym + ".json";
   // FILE_COMMON = C:\Users\<user>\AppData\Roaming\MetaQuotes\Terminal\Common\Files\
   // FILE_SHARE_READ|WRITE — чтобы Flask мог писать, а индикатор читать одновременно
   int handle = FileOpen(filename, FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON | FILE_SHARE_READ | FILE_SHARE_WRITE);
   if(handle == INVALID_HANDLE) {
      LogErrorOnce("FILE_OPEN", "FileOpen FAIL path=" + filename + " err=" + IntegerToString(GetLastError()));
      return false;
   }
   content = FileReadString(handle);
   FileClose(handle);
   if(StringLen(content) < 10) {
      LogErrorOnce("FILE_EMPTY", "Файл пуст: " + filename);
      return false;
   }
   return true;
}

//+------------------------------------------------------------------+
//| Лог ошибки только при первом появлении (анти-спам)                |
//+------------------------------------------------------------------+
void LogErrorOnce(string key, string msg) {
   if(key != g_lastErrorKey) {
      Print("SMC: ", msg, " (poll #", g_pollCount, ")");
      g_lastErrorKey = key;
   }
}

//+------------------------------------------------------------------+
//| Автоопределение цвета текста по фону графика                     |
//| (clrWhite на тёмном фоне, clrBlack на светлом)                   |
//+------------------------------------------------------------------+
color GetTextColor() {
   if(!AutoTextColor) return TextColorManual;
   color bg = (color)ChartGetInteger(0, CHART_COLOR_BACKGROUND);
   // Читаем яркость фона: среднее RGB
   int r = (bg & 0xFF);
   int g = (bg >> 8) & 0xFF;
   int b = (bg >> 16) & 0xFF;
   int brightness = (r + g + b) / 3;
   if(brightness < 128) return clrWhite;   // тёмный фон → белый текст
   return clrBlack;                        // светлый фон → тёмный текст
}

//+------------------------------------------------------------------+
//| Рисование горизонтальной линии                                   |
//+------------------------------------------------------------------+
void DrawHLine(string name, double price, color clr, int style, int width, string text) {
   if(ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_HLINE, 0, 0, price);
   ObjectSetDouble(0, name, OBJPROP_PRICE, price);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_STYLE, style);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, width);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetInteger(0, name, OBJPROP_BACK, true);
   // Цвет текста метки = автоопределение по фону
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, 9);
   color txtClr = GetTextColor();
   ObjectSetInteger(0, name, OBJPROP_LEVELCOLOR, txtClr);
}

//+------------------------------------------------------------------+
//| Заливка зоны прямоугольником                                     |
//+------------------------------------------------------------------+
void DrawZoneFill(string name, double lower, double upper, color clr) {
   datetime t1 = TimeCurrent() - PeriodSeconds(PERIOD_CURRENT) * 50;
   datetime t2 = TimeCurrent() + PeriodSeconds(PERIOD_CURRENT) * 10;

   if(ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_RECTANGLE, 0, t1, lower, t2, upper);
   else {
      ObjectSetInteger(0, name, OBJPROP_TIME, 0, t1);
      ObjectSetDouble(0, name, OBJPROP_PRICE, 0, lower);
      ObjectSetInteger(0, name, OBJPROP_TIME, 1, t2);
      ObjectSetDouble(0, name, OBJPROP_PRICE, 1, upper);
   }
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_STYLE, STYLE_SOLID);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, 1);
   ObjectSetInteger(0, name, OBJPROP_BACK, true);
   ObjectSetInteger(0, name, OBJPROP_FILL, true);
}

//+------------------------------------------------------------------+
//| Цвет заливки по ТФ                                               |
//+------------------------------------------------------------------+
color GetZoneColor(string tf) {
   if(tf == "1D") return ColorFill1D;
   if(tf == "4H") return ColorFill4H;
   if(tf == "1H") return ColorFill1H;
   if(tf == "15M") return ColorFill15M;
   return clrGray;
}

//+------------------------------------------------------------------+
//| Стрелка пробоя                                                   |
//+------------------------------------------------------------------+
void DrawBreakoutArrow(string name, double price, bool isUp, string text) {
   if(ObjectFind(0, name) >= 0) return;  // уже есть — не перерисовываем

   datetime t = TimeCurrent();
   ObjectCreate(0, name, OBJ_ARROW, 0, t, price);
   ObjectSetInteger(0, name, OBJPROP_ARROWCODE, isUp ? 233 : 234);
   ObjectSetInteger(0, name, OBJPROP_COLOR, ColorBreak);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, 2);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
}

//+------------------------------------------------------------------+
//| Удаление линий зон и цены (НЕ стрелок пробоев)                    |
//+------------------------------------------------------------------+
void CleanupZoneObjects() {
   int total = ObjectsTotal(0, -1, -1);
   for(int i = total - 1; i >= 0; i--) {
      string name = ObjectName(0, i, -1, -1);
      if(StringFind(name, PREFIX) != 0) continue;
      // Не удаляем стрелки пробоев (BRK_) и бейдж (BADGE)
      if(StringFind(name, "BRK_") >= 0) continue;
      if(StringFind(name, "BADGE") >= 0) continue;
      ObjectDelete(0, name);
   }
}

//+------------------------------------------------------------------+
//| Полная очистка (при деинициализации)                             |
//+------------------------------------------------------------------+
void CleanupObjects() {
   int total = ObjectsTotal(0, -1, -1);
   for(int i = total - 1; i >= 0; i--) {
      string name = ObjectName(0, i, -1, -1);
      if(StringFind(name, PREFIX) == 0) {
         ObjectDelete(0, name);
      }
   }
}

//+------------------------------------------------------------------+
//| Извлечение числа после key (от начала text)                     |
//+------------------------------------------------------------------+
double ExtractDouble(string text, string key) {
   int pos = StringFind(text, key);
   if(pos < 0) return 0.0;
   return ExtractDoubleFromPos(text, pos, key);
}

//+------------------------------------------------------------------+
//| Извлечение числа после key, начиная с startPos                  |
//| FIX: раньше вызывал ExtractDouble от начала — баг!               |
//+------------------------------------------------------------------+
double ExtractDoubleFromPos(string text, int startPos, string key) {
   int pos = StringFind(text, key, startPos);
   if(pos < 0) return 0.0;

   int valStart = pos + StringLen(key);

   // Пропускаем пробелы после ключа (JSON: "price": 64997.8)
   int textLen = StringLen(text);
   while(valStart < textLen && StringSubstr(text, valStart, 1) == " ")
      valStart++;

   // Проверяем null
   string nextChars = StringSubstr(text, valStart, 4);
   if(nextChars == "null") return 0.0;

   string numStr = "";
   for(int i = valStart; i < textLen; i++) {
      string ch = StringSubstr(text, i, 1);
      if(ch == "-" || ch == "." || ch == "0" || ch == "1" || ch == "2" ||
         ch == "3" || ch == "4" || ch == "5" || ch == "6" || ch == "7" ||
         ch == "8" || ch == "9" || ch == "e" || ch == "E" || ch == "+") {
         numStr += ch;
      } else {
         break;
      }
   }

   if(numStr == "") return 0.0;
   return StringToDouble(numStr);
}

//+------------------------------------------------------------------+
//| Извлечение строки после key                                      |
//+------------------------------------------------------------------+
string ExtractString(string text, string key) {
   int pos = StringFind(text, key);
   if(pos < 0) return "";

   int valStart = pos + StringLen(key);
   int textLen = StringLen(text);

   // Пропускаем пробелы
   while(valStart < textLen && StringSubstr(text, valStart, 1) == " ")
      valStart++;

   // Строка в кавычках
   if(valStart < textLen && StringSubstr(text, valStart, 1) == "\"") {
      valStart++;
      int end = StringFind(text, "\"", valStart);
      if(end < 0) return "";
      return StringSubstr(text, valStart, end - valStart);
   }

   // null/true/false без кавычек
   string val = "";
   for(int i = valStart; i < textLen; i++) {
      string ch = StringSubstr(text, i, 1);
      if(ch == "," || ch == "}" || ch == " ") break;
      val += ch;
   }
   return val;
}
//+------------------------------------------------------------------+
