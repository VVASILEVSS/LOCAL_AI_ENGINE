# unified_dataset_cleaned.csv: Чек-лист полей и их семантика

| column              | type   | description                                        |
|---------------------|--------|----------------------------------------------------|
| symbol              | cat    | тикер/символ                                       |
| tf                  | cat    | timeframe: 15m, 1h, 4h, 1d...                      |
| candidate_idx       | int    | индекс точки исходя из последовательности данных    |
| time_iso            | dt     | ISO8601 метка времени кандидата                    |
| label_time          | dt     | ISO8601 label event time                           |
| label_price         | float  | цена для метки                                      |
| prev_price          | float  | предыдущая цена                                    |
| curr_price          | float  | текущая цена                                       |
| prev_flow           | float  | предыдущий A/D / CMF flow                          |
| curr_flow           | float  | текущий flow                                       |
| flow_abs_change     | float  | абсолютное изменение flow                          |
| flow_pct_change     | float  | относительное изменение flow                       |
| price_move_pct      | float  | относительное движение цены                        |
| atr                 | float  | ATR                                                |
| flow_scale          | float  | flow scale feature                                 |
| pivot_left          | int    | pivot params                                       |
| pivot_right         | int    | --                                                 |
| context_start_idx   | int    | начало окна контекста                              |
| context_end_idx     | int    | конец окна контекста                               |
| top_price           | float  | верхняя цена окна                                  |
| bottom_price        | float  | нижняя цена окна                                   |
| mid_price           | float  | медианная цена окна                                |
| delta_volume        | float  | доп/объем (может быть "" если нету)                |
| momentum            | float  | моментум (может быть "" если нету)                 |
| ratio               | float  | сугубо техническое, если есть                      |
| strength            | str    | strong/medium/weak/empty                           |
| action              | str    | длинный/короткий/none/empty                        |
| comment             | str    | любой текст                                        |
| llm_feedback        | str    | фидбек LLM, если есть                              |
| context_ohlcv_json  | json   | json с контекстом окон                             |