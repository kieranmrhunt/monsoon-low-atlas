from __future__ import annotations

import unittest

from cmip6_pipeline.review import assess_historical_screen


def screen(event_ratio: float, class_ratio: float) -> dict:
    comparisons = {
        "event_frequency_ratio": event_ratio,
        "system_days_ratio": 1.0,
        "monthly_cycle_correlation": 0.8,
        "track_density_shape": {
            "pattern_correlation_nonempty_union": 0.75,
            "probability_overlap": 0.7,
        },
    }
    classification = {
        "comparisons": {
            "depression_or_stronger_frequency_ratio": class_ratio,
        }
    }
    return {
        "comparisons": comparisons,
        "classification_screen": classification,
    }


def paired_screen(event_ratio: float, class_ratio: float) -> dict:
    result = screen(event_ratio, class_ratio)
    jjas = screen(event_ratio, class_ratio)
    result["seasonal"] = {"jjas": jjas}
    result["classification_screen"]["seasonal"] = {
        "jjas": jjas["classification_screen"]
    }
    return result


class ReviewTest(unittest.TestCase):
    def test_track_realism_and_class_realism_are_separate(self) -> None:
        result = assess_historical_screen(paired_screen(1.0, 0.3))
        self.assertTrue(result["all_lps_headline_eligible"])
        self.assertFalse(result["absolute_class_headline_eligible"])
        self.assertEqual(result["disposition"], "headline-all-lps-only")

    def test_frequency_bias_makes_model_exploratory(self) -> None:
        result = assess_historical_screen(paired_screen(0.4, 1.0))
        self.assertFalse(result["all_lps_headline_eligible"])
        self.assertTrue(result["absolute_class_headline_eligible"])
        self.assertEqual(result["disposition"], "exploratory-only")


if __name__ == "__main__":
    unittest.main()
