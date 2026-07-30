from typing import Dict, Any, List
import logging
from src.grading.base import CompareResult

logger = logging.getLogger(__name__)

class ScoringEngine:
    """Tính toán điểm số tổng hợp dựa trên trọng số cấu hình."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.scoring_config = config.get("scoring", {})
        self.scale = float(self.scoring_config.get("scale", 10.0))
        self.weights = self.scoring_config.get("weights", {})
        self.total_weight = sum(self.weights.values()) or 100.0

    def calculate_scores(self, module_results: Dict[str, CompareResult]) -> Dict[str, Any]:
        """Tính điểm cho từng module và tổng điểm bài làm."""
        module_scores = {}
        weighted_total = 0.0

        for mid, result in module_results.items():
            weight = float(self.weights.get(mid, 0.0))
            result.max_weight = weight
            
            # Module score normalized to max_weight
            module_score = result.match_ratio * weight
            result.score = round(module_score, 4)
            module_scores[mid] = result

            weighted_total += module_score

        # Final scaled score (e.g. out of 10)
        final_score = round((weighted_total / self.total_weight) * self.scale, 2)

        return {
            "final_score": final_score,
            "scale": self.scale,
            "weighted_total": round(weighted_total, 2),
            "total_weight": self.total_weight,
            "module_results": module_scores
        }
