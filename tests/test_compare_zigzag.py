import json
import unittest
from unittest.mock import patch

from core.zigzag.compare_zigzag import run_compare


def _mock_benchmark(symbol: str):
    if symbol == "BTC/USDT":
        return {
            "symbol": symbol,
            "market_type": "future",
            "normalized_symbol": "BTCUSDT",
            "settings": {
                "mode": "hybrid_atr",
                "length": None,
                "percent": None,
                "confirmation_mode": "close",
                "limit": 300,
                "debug": False,
            },
            "timeframes": {
                "15m": {
                    "swing_direction": "bearish",
                    "pattern_tags": ["no_clear_pattern"],
                    "pivot_count": 18,
                    "zones": {"resistance": [80478.0, 81043.2], "support": [79507.6, 80084.3]},
                },
                "1h": {
                    "swing_direction": "bullish",
                    "pattern_tags": ["no_clear_pattern"],
                    "pivot_count": 7,
                    "zones": {"resistance": [80590.2, 82460.5], "support": [78128.3, 79137.4]},
                },
                "4h": {
                    "swing_direction": "bearish",
                    "pattern_tags": ["bullish_structure"],
                    "pivot_count": 13,
                    "zones": {"resistance": [78300.0, 82828.7], "support": [73669.0, 74868.0]},
                },
                "1d": {
                    "swing_direction": "bearish",
                    "pattern_tags": ["bearish_structure"],
                    "pivot_count": 8,
                    "zones": {"resistance": [116380.2, 126208.5], "support": [80600.0, 101516.5]},
                },
            },
            "stack": {
                "stack_bias": "bearish",
                "alignment": "mixed",
                "dominant_tf": "1h",
                "directions": {
                    "15m": "bearish",
                    "1h": "bullish",
                    "4h": "bearish",
                    "1d": "bearish",
                },
                "summary": "mock",
            },
        }

    if symbol == "XAUT/USDT":
        return {
            "symbol": symbol,
            "market_type": "future",
            "normalized_symbol": "XAUTUSDT",
            "settings": {
                "mode": "hybrid_atr",
                "length": None,
                "percent": None,
                "confirmation_mode": "close",
                "limit": 300,
                "debug": False,
            },
            "timeframes": {
                "15m": {
                    "swing_direction": "bullish",
                    "pattern_tags": ["bearish_structure"],
                    "pivot_count": 11,
                    "zones": {"resistance": [4698.0, 4716.94], "support": [4643.36, 4655.3]},
                },
                "1h": {
                    "swing_direction": "bullish",
                    "pattern_tags": ["no_clear_pattern"],
                    "pivot_count": 6,
                    "zones": {"resistance": [4634.77, 4747.56], "support": [4501.05, 4557.64]},
                },
                "4h": {
                    "swing_direction": "bullish",
                    "pattern_tags": ["no_clear_pattern"],
                    "pivot_count": 32,
                    "zones": {"resistance": [4634.77, 4747.56], "support": [4501.05, 4557.64]},
                },
                "1d": {
                    "swing_direction": "bullish",
                    "pattern_tags": ["insufficient_pivots"],
                    "pivot_count": 2,
                    "zones": {"resistance": [4863.68], "support": [4352.21]},
                },
            },
            "stack": {
                "stack_bias": "bullish",
                "alignment": "aligned",
                "dominant_tf": "1h",
                "directions": {
                    "15m": "bullish",
                    "1h": "bullish",
                    "4h": "bullish",
                    "1d": "bullish",
                },
                "summary": "mock",
            },
        }

    return {
        "symbol": symbol,
        "market_type": "future",
        "normalized_symbol": "ETHUSDT",
        "settings": {
            "mode": "hybrid_atr",
            "length": None,
            "percent": None,
            "confirmation_mode": "close",
            "limit": 300,
            "debug": False,
        },
        "timeframes": {
            "15m": {
                "swing_direction": "bullish",
                "pattern_tags": ["bearish_structure"],
                "pivot_count": 17,
                "zones": {"resistance": [2318.3, 2324.51], "support": [2310.01, 2317.02]},
            },
            "1h": {
                "swing_direction": "bearish",
                "pattern_tags": ["no_clear_pattern"],
                "pivot_count": 7,
                "zones": {"resistance": [2346.48, 2398.88], "support": [2218.83, 2313.05]},
            },
            "4h": {
                "swing_direction": "bullish",
                "pattern_tags": ["no_clear_pattern"],
                "pivot_count": 14,
                "zones": {"resistance": [2423.0, 2403.99], "support": [2218.83, 2283.31]},
            },
            "1d": {
                "swing_direction": "bearish",
                "pattern_tags": ["no_clear_pattern"],
                "pivot_count": 24,
                "zones": {"resistance": [3403.77, 2385.78], "support": [1736.02, 1936.54]},
            },
        },
        "stack": {
            "stack_bias": "mixed",
            "alignment": "mixed",
            "dominant_tf": "1h",
            "directions": {
                "15m": "bullish",
                "1h": "bearish",
                "4h": "bullish",
                "1d": "bearish",
            },
            "summary": "mock",
        },
    }


class TestCompareZigZag(unittest.TestCase):
    @patch("core.zigzag.compare_zigzag.run_benchmark")
    def test_run_compare_structure(self, mock_run_benchmark):
        mock_run_benchmark.side_effect = lambda **kwargs: _mock_benchmark(kwargs["symbol"])

        payload = run_compare(
            symbols=["BTC/USDT", "XAUT/USDT", "ETH/USDT"],
            market_type="future",
            mode="hybrid_atr",
            limit=300,
            debug=False,
            output=None,
        )

        self.assertIn("settings", payload)
        self.assertIn("summary_rows", payload)
        self.assertIn("results", payload)
        self.assertIn("telegram_summary", payload)
        self.assertIn("telegram_human_summary", payload)

        self.assertEqual(len(payload["summary_rows"]), 3)
        self.assertEqual(set(payload["results"].keys()), {"BTC/USDT", "XAUT/USDT", "ETH/USDT"})

        for symbol, data in payload["results"].items():
            self.assertIn("scores", data)
            self.assertIn("early_reversal", data)
            self.assertIn("confluence", data)
            self.assertIn("telegram_confluence", data)
            self.assertIn("pattern_mismatch", data)
            self.assertIn("pattern_conflict", data)
            self.assertIn("signal_quality_state", data)
            self.assertIn("signal_quality_reason", data)
            self.assertIn("action_state", data)
            self.assertIn("human_verdict", data)
            self.assertIn("telegram_summary", data)

            self.assertLessEqual(len(data["telegram_confluence"]), 5)
            self.assertIn(data["signal_quality_state"], {"strong", "moderate", "weak", "avoid"})

    @patch("core.zigzag.compare_zigzag.run_benchmark")
    def test_telegram_json_is_serializable(self, mock_run_benchmark):
        mock_run_benchmark.side_effect = lambda **kwargs: _mock_benchmark(kwargs["symbol"])

        payload = run_compare(
            symbols=["BTC/USDT", "XAUT/USDT", "ETH/USDT"],
            market_type="future",
            mode="hybrid_atr",
            limit=300,
            debug=False,
            output=None,
        )

        json.dumps(payload, ensure_ascii=False, indent=2)
        json.dumps(payload["results"]["BTC/USDT"], ensure_ascii=False, indent=2)
        json.dumps(payload["results"]["XAUT/USDT"], ensure_ascii=False, indent=2)
        json.dumps(payload["results"]["ETH/USDT"], ensure_ascii=False, indent=2)

    @patch("core.zigzag.compare_zigzag.run_benchmark")
    def test_signal_routing_fields(self, mock_run_benchmark):
        mock_run_benchmark.side_effect = lambda **kwargs: _mock_benchmark(kwargs["symbol"])

        payload = run_compare(
            symbols=["BTC/USDT", "XAUT/USDT", "ETH/USDT"],
            market_type="future",
            mode="hybrid_atr",
            limit=300,
            debug=False,
            output=None,
        )

        btc = payload["results"]["BTC/USDT"]
        xaut = payload["results"]["XAUT/USDT"]
        eth = payload["results"]["ETH/USDT"]

        self.assertEqual(xaut["signal_quality_state"], "strong")
        self.assertEqual(xaut["action_state"], "strong_bullish")
        self.assertEqual(xaut["human_verdict"], "clean bullish continuation")
        self.assertIn("aligned", xaut["signal_quality_reason"])

        self.assertEqual(btc["signal_quality_state"], "weak")
        self.assertEqual(btc["human_verdict"], "weak / noisy setup")
        self.assertIn(btc["action_state"], {"caution", "avoid"})
        self.assertIn("weak", btc["signal_quality_reason"])

        self.assertEqual(eth["signal_quality_state"], "weak")
        self.assertEqual(eth["human_verdict"], "weak / noisy setup")
        self.assertIn(eth["action_state"], {"caution", "avoid"})
        self.assertIn("weak", eth["signal_quality_reason"])


if __name__ == "__main__":
    unittest.main()