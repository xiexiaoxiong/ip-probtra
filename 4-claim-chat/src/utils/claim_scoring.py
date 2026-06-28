import re
from typing import Any, Dict, List, Tuple


STOP_WORDS = ("所述",)
PUNCTUATION_PATTERN = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)
NUMBER_MARKER_PATTERN = re.compile(r"[\(\[（【]?\d+[\)\]）】]?")
ENGLISH_WORD_PATTERN = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
CHINESE_CHAR_PATTERN = re.compile(r"[\u4e00-\u9fff]")
SEGMENT_PATTERN = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?|[\u4e00-\u9fff]+")


def _strip_stop_words(text: str) -> str:
    cleaned = text
    for word in STOP_WORDS:
        cleaned = cleaned.replace(word, "")
    return cleaned


def normalize_claim_text(text: str) -> str:
    cleaned = str(text or "")
    cleaned = NUMBER_MARKER_PATTERN.sub("", cleaned)
    cleaned = _strip_stop_words(cleaned)
    cleaned = PUNCTUATION_PATTERN.sub(" ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def count_effective_units(text: str) -> int:
    normalized = normalize_claim_text(text)
    if not normalized:
        return 0

    english_words = ENGLISH_WORD_PATTERN.findall(normalized)
    remaining = ENGLISH_WORD_PATTERN.sub("", normalized)
    chinese_chars = CHINESE_CHAR_PATTERN.findall(remaining)
    return len(chinese_chars) + len(english_words)


def build_feature_segments(feature_text: str) -> List[Dict[str, Any]]:
    segments: List[Dict[str, Any]] = []
    for match in SEGMENT_PATTERN.finditer(str(feature_text or "")):
        raw_text = match.group(0)
        normalized_text = normalize_claim_text(raw_text)
        effective_length = count_effective_units(raw_text)
        if effective_length <= 0:
            continue
        segments.append({
            "text": raw_text,
            "normalized_text": normalized_text,
            "start": match.start(),
            "end": match.end(),
            "effective_length": effective_length,
        })
    return segments


def build_claim_totals(features: List[Dict[str, Any]]) -> Dict[str, int]:
    claim_totals: Dict[str, int] = {}
    for feature in features:
        claim_id = str(feature.get("claim_id", "")).strip() or "unknown"
        claim_totals[claim_id] = claim_totals.get(claim_id, 0) + int(feature.get("effective_length", 0) or 0)
    return claim_totals


def compute_feature_full_score(feature_effective_length: int, claim_total_effective_length: int) -> float:
    if feature_effective_length <= 0 or claim_total_effective_length <= 0:
        return 0.0
    return round((feature_effective_length / claim_total_effective_length) * 100, 2)


def cap_matched_effective_length(feature_effective_length: int, matched_effective_length: int) -> int:
    if feature_effective_length <= 0 or matched_effective_length <= 0:
        return 0
    return min(int(matched_effective_length), int(feature_effective_length))


def compute_feature_similarity_score(
    feature_effective_length: int,
    matched_effective_length: int,
    has_mismatch: bool,
) -> float:
    if has_mismatch or feature_effective_length <= 0:
        return 0.0
    capped_matched = cap_matched_effective_length(feature_effective_length, matched_effective_length)
    return round((capped_matched / feature_effective_length) * 100, 2)


def compute_feature_awarded_score(
    feature_full_score: float,
    feature_effective_length: int,
    matched_effective_length: int,
    has_mismatch: bool,
) -> float:
    if has_mismatch or feature_effective_length <= 0 or feature_full_score <= 0:
        return 0.0
    capped_matched = cap_matched_effective_length(feature_effective_length, matched_effective_length)
    ratio = capped_matched / feature_effective_length if feature_effective_length else 0
    return round(feature_full_score * ratio, 2)


def classify_unit_status(unit_status: str, reasoning_type: str = "") -> str:
    """
    把模型给出的 unit_status 归一为 match / mismatch / uncertain。

    核心原则：单元级状态必须由 LLM 对该单元的 unit_status 决定。
    当 unit_status 缺失、为空或为未识别值时，一律默认 ``uncertain``，禁止使用
    特征级 reasoning_type 推断单元级状态——否则会出现"特征级给出
    可判断不具有，但单元级其实匹配/待确认"的整条染红问题。

    reasoning_type 参数保留仅为兼容历史调用方，不再参与分类。
    """
    del reasoning_type  # 显式忽略，避免误用
    normalized = str(unit_status or "").strip().lower()
    if normalized in {"match", "matching", "相同", "相同特征", "明确相同"}:
        return "match"
    if normalized in {"mismatch", "not_match", "不相同", "不同", "明确不相同"}:
        return "mismatch"
    # 包括明确 "uncertain" 以及任何缺失/空/未识别值
    return "uncertain"


def compute_claim_score(features: List[Dict[str, Any]], claim_total_effective_length: int) -> Tuple[float, int, bool]:
    claim_score = 0.0
    matched_effective_length = 0
    zeroed_by_mismatch = False

    for feature in features:
        feature_effective_length = int(feature.get("feature_effective_length", 0) or 0)
        raw_feature_matched_effective_length = int(feature.get("matched_effective_length", 0) or 0)
        feature_matched_effective_length = cap_matched_effective_length(
            feature_effective_length,
            raw_feature_matched_effective_length,
        )
        matched_effective_length += feature_matched_effective_length
        if bool(feature.get("zeroed_by_mismatch")):
            zeroed_by_mismatch = True
        claim_score += float(feature.get("feature_awarded_score", 0.0) or 0.0)

    if zeroed_by_mismatch:
        return 0.0, matched_effective_length, True
    return round(min(claim_score, 100.0), 2), matched_effective_length, False


def compute_product_score(claim_scores: List[Dict[str, Any]]) -> float:
    if not claim_scores:
        return 0.0
    if any(bool(item.get("zeroed_by_mismatch")) for item in claim_scores):
        return 0.0
    total_score = sum(float(item.get("similarity_score", 0.0) or 0.0) for item in claim_scores)
    return round(min(total_score, 100.0), 2)


def score_band(
    score: float,
    has_mismatch: bool = False,
    matched_length: int = 0,
    total_length: int = 0,
) -> str:
    """
    把分数映射为展示分段。

    关键区分（必须依据单元级 unit_status 而非纯分数）：
    - has_mismatch=True → "明确不相同"（由前端显示为浅灰）
    - 其他按 70+/30-70/0-30 三段走；无明确不相同但 0 分也属于低相似
    """
    if has_mismatch:
        return "明确不相同"
    del matched_length, total_length
    if score > 70:
        return "明确相同"
    if score >= 30:
        return "中等相似"
    return "低相似"
