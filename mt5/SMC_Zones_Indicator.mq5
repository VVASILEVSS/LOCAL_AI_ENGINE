//+------------------------------------------------------------------+
//|                                        SMC_Zones_Indicator.mq5    |
//|                  LOCAL_AI_ENGINE — MT5 Bridge Indicator           |
//|                  Рисует зоны (upper/lower) + стрелки пробоев      |
//|                  Данные: http://localhost:5000/api/signals        |
//+------------------------------------------------------------------+
#property copyright "LOCAL_AI_ENGINE"
#property version   "1.00"
#property indicator_chart_window
#property indicator_plots 0

// --- Входные параметры ---
input string  ServerURL    = "http://localhost:5000/api/signals";  // URL API
input string  TargetSymbol = "BTCUSDT";                              // Символ (как в боте)
input int     PollSeconds  = 30;                                     // Частота опроса (сек)
input bool    ShowAllTF    = true;                                   // Показать все ТФ
input string  ShowTFs      = "15m,1h,4h,1D";                         // Какие ТФ рисовать
input color   ColorUpper   = clrRed;                                 // Цвет resistance
input color   ColorLower   = clrGreen;                               // Цвет support
input color   ColorBreak   = clrGold;                                // Цвет стрелок пробоя
input int     LineWidth    = 1;                                      // Толщина линий
input bool    ShowPrice    = true;                                   // Показать цену
input bool    ShowLabel    = true;                                   // Показать текстовые метки

// --- Глобальные ---
string PREFIX = "SMC_";  // Префикс объектов на графике
datetime lastPoll = 0;

//+------------------------------------------------------------------+
//| Структура зоны                                                    |
//+------------------------------------------------------------------+
struct ZoneData {
   string  tf;
   double  upper;
   double  lower;
};

//+------------------------------------------------------------------+
//| Инициализация                                                    |
//+------------------------------------------------------------------+
int OnInit() {
   EventSetTimer(PollSeconds);
   Print("SMC Zones Indicator: старт. URL=", ServerURL, " Symbol=", TargetSymbol);
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Деинициализация — удаляем все объекты                             |
//+------------------------------------------------------------------+
void OnDeinit(const int reason) {
   EventKillTimer();
   CleanupObjects();
   Print("SMC Zones Indicator: остановка. reason=", reason);
}

//+------------------------------------------------------------------+
//| Таймер — опрашиваем API                                          |
//+------------------------------------------------------------------+
void OnTimer() {
   if(TimeCurrent() - lastPoll < PollSeconds) return;
   lastPoll = TimeCurrent();
   PollSignals();
}

//+------------------------------------------------------------------+
//| Главная функция отрисовки                                        |
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
//| Опрос API и отрисовка                                            |
//+------------------------------------------------------------------+
void PollSignals() {
   string response = "";
   string headers = "";
   char   data[] = {0};
   char   result[] = {0};

   // WebRequest к Flask
   string url = ServerURL;
   ResetLastError();
   int status = WebRequest("GET", url, "", "", 30000, data, 0, result, headers);

   if(status == -1) {
      Print("SMC: WebRequest failed — разрешите URL в MT5: Tools → Options → Expert Advisors → Allow WebRequest. err=", GetLastError());
      return;
   }
   if(status != 200) {
      Print("SMC: HTTP error ", status);
      return;
   }

   // Парсим response как строку
   response = CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);

   // Ищем наш символ
   string symKey = "\"" + TargetSymbol + "\":";
   int symPos = StringFind(response, symKey);
   if(symPos < 0) {
      // Нет данных по этому символу — скан ещё не делался
      return;
   }

   // Извлекаем блок символа (от symPos до следующего " BTCUSDT" или конец symbols)
   int blockEnd = StringFind(response, "},", symPos);
   if(blockEnd < 0) blockEnd = StringLen(response);
   string symBlock = StringSubstr(response, symPos, blockEnd - symPos + 1);

   // --- Цена ---
   double price = 0.0;
   double val = ExtractDouble(symBlock, "\"price\":");
   if(val > 0) price = val;

   // --- signal_status ---
   string sigStatus = ExtractString(symBlock, "\"signal_status\":");

   // --- phase ---
   string phase = ExtractString(symBlock, "\"phase\":");

   // --- Зоны по ТФ ---
   int zonesStart = StringFind(symBlock, "\"zones\":");
   if(zonesStart < 0) return;

   string zonesBlock = StringSubstr(symBlock, zonesStart);

   // Парсим каждый ТФ
   string tfs[];
   int tfCount = StringSplit(ShowTFs, ',', tfs);

   // Сначала удаляем старые объекты
   CleanupObjects();

   // Рисуем цену (если включено)
   if(ShowPrice && price > 0) {
      string name = PREFIX + "PRICE";
      if(ObjectFind(0, name) < 0)
         ObjectCreate(0, name, OBJ_HLINE, 0, 0, price);
      ObjectSetDouble(0, name, OBJPROP_PRICE, price);
      ObjectSetInteger(0, name, OBJPROP_COLOR, clrDodgerBlue);
      ObjectSetInteger(0, name, OBJPROP_STYLE, STYLE_DOT);
      ObjectSetString(0, name, OBJPROP_TEXT, "Цена: " + DoubleToString(price, _Digits));
   }

   // Рисуем зоны
   for(int i = 0; i < tfCount; i++) {
      string tf = tfs[i];
      StringTrimLeft(tf);
      StringTrimRight(tf);

      string tfKey = "\"" + tf + "\":";
      int tfPos = StringFind(zonesBlock, tfKey);
      if(tfPos < 0) continue;

      double upper = ExtractDoubleFromPos(zonesBlock, tfPos, "\"upper\":");
      double lower = ExtractDoubleFromPos(zonesBlock, tfPos, "\"lower\":");

      // Resistance (upper)
      if(upper > 0) {
         string name = PREFIX + "R_" + tf;
         if(ObjectFind(0, name) < 0)
            ObjectCreate(0, name, OBJ_HLINE, 0, 0, upper);
         ObjectSetDouble(0, name, OBJPROP_PRICE, upper);
         ObjectSetInteger(0, name, OBJPROP_COLOR, ColorUpper);
         ObjectSetInteger(0, name, OBJPROP_STYLE, STYLE_SOLID);
         ObjectSetInteger(0, name, OBJPROP_WIDTH, LineWidth);
         if(ShowLabel)
            ObjectSetString(0, name, OBJPROP_TEXT, "R " + tf + ": " + DoubleToString(upper, _Digits));

         // Стрелка пробоя если цена выше resistance
         if(price > upper && price > 0) {
            DrawBreakoutArrow("BRK_R_" + tf, upper, true, "ПРОБОЙ R " + tf);
         }
      }

      // Support (lower)
      if(lower > 0) {
         string name = PREFIX + "S_" + tf;
         if(ObjectFind(0, name) < 0)
            ObjectCreate(0, name, OBJ_HLINE, 0, 0, lower);
         ObjectSetDouble(0, name, OBJPROP_PRICE, lower);
         ObjectSetInteger(0, name, OBJPROP_COLOR, ColorLower);
         ObjectSetInteger(0, name, OBJPROP_STYLE, STYLE_SOLID);
         ObjectSetInteger(0, name, OBJPROP_WIDTH, LineWidth);
         if(ShowLabel)
            ObjectSetString(0, name, OBJPROP_TEXT, "S " + tf + ": " + DoubleToString(lower, _Digits));

         // Стрелка пробоя если цена ниже support
         if(price < lower && price > 0) {
            DrawBreakoutArrow("BRK_S_" + tf, lower, false, "ПРОБОЙ S " + tf);
         }
      }
   }

   // Инфо-бейдж в углу
   if(ShowLabel) {
      string badge = PREFIX + "BADGE";
      if(ObjectFind(0, badge) < 0)
         ObjectCreate(0, badge, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, badge, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, badge, OBJPROP_XDISTANCE, 10);
      ObjectSetInteger(0, badge, OBJPROP_YDISTANCE, 20);
      ObjectSetString(0, badge, OBJPROP_TEXT,
         "SMC Zones | " + TargetSymbol +
         " | цена: " + DoubleToString(price, _Digits) +
         " | статус: " + sigStatus +
         " | фаза: " + phase);
      ObjectSetInteger(0, badge, OBJPROP_COLOR, clrWhite);
      ObjectSetInteger(0, badge, OBJPROP_FONTSIZE, 9);
   }

   ChartRedraw();
}

//+------------------------------------------------------------------+
//| Рисование стрелки пробоя                                         |
//+------------------------------------------------------------------+
void DrawBreakoutArrow(string name, double price, bool isUp, string text) {
   if(ObjectFind(0, name) >= 0) return;  // уже есть

   datetime t = TimeCurrent();
   ObjectCreate(0, name, OBJ_ARROW, 0, t, price);
   ObjectSetInteger(0, name, OBJPROP_ARROWCODE, isUp ? 233 : 234);
   ObjectSetInteger(0, name, OBJPROP_COLOR, ColorBreak);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, 2);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
}

//+------------------------------------------------------------------+
//| Удаление всех объектов индикатора                                |
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
//| Вспомогательные: извлечение чисел и строк из JSON строки          |
//+------------------------------------------------------------------+
double ExtractDouble(string text, string key) {
   int pos = StringFind(text, key);
   if(pos < 0) return 0.0;

   int valStart = pos + StringLen(key);
   string numStr = "";
   for(int i = valStart; i < StringLen(text); i++) {
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
   double val = StringToDouble(numStr);
   // null → 0
   if(val < 0.0001 && StringFind(numStr, "null") >= 0) return 0.0;
   return val;
}

double ExtractDoubleFromPos(string text, int startPos, string key) {
   int pos = StringFind(text, key, startPos);
   if(pos < 0) return 0.0;
   return ExtractDouble(text, key);
}

string ExtractString(string text, string key) {
   int pos = StringFind(text, key);
   if(pos < 0) return "";

   int valStart = pos + StringLen(key);
   // Пропускаем кавычки
   while(valStart < StringLen(text) && StringSubstr(text, valStart, 1) == " ")
      valStart++;
   if(StringSubstr(text, valStart, 1) == "\"") {
      valStart++;
      int end = StringFind(text, "\"", valStart);
      if(end < 0) return "";
      return StringSubstr(text, valStart, end - valStart);
   }
   // null/true/false/number без кавычек
   string val = "";
   for(int i = valStart; i < StringLen(text); i++) {
      string ch = StringSubstr(text, i, 1);
      if(ch == "," || ch == "}" || ch == " ") break;
      val += ch;
   }
   return val;
}
//+------------------------------------------------------------------+
