"""agent 输出的 JSON 结构。所有属性都列为 required（结构化输出要求），不适用的字段填空字符串。"""

ACTION_TYPES = [
    "negate_search_term",  # 否定搜索词
    "add_keyword_exact",  # 收割为精准词
    "raise_bid",
    "lower_bid",
    "raise_budget",
    "lower_budget",
    "pause_target",  # 暂停投放词 / 广告
    "listing_change",  # 修改标题、五点、图片、A+
    "price_change",
    "coupon_promo",  # 优惠券 / 促销
    "restock",
    "investigate",  # 需要人工进一步排查
    "other",
]

_str = {"type": "string"}

FINDING = {
    "type": "object",
    "properties": {
        "severity": {"type": "string", "enum": ["critical", "warning", "opportunity", "info"]},
        "title": _str,
        "detail": _str,
        "shop_id": _str,
        "asin": _str,
    },
    "required": ["severity", "title", "detail", "shop_id", "asin"],
    "additionalProperties": False,
}

RECOMMENDATION = {
    "type": "object",
    "properties": {
        "action_type": {"type": "string", "enum": ACTION_TYPES},
        "shop_id": _str,
        "asin": _str,
        "campaign_id": _str,
        "target": {"type": "string", "description": "操作对象：关键词 / 搜索词 / 广告活动名 / listing 字段等"},
        "current_value": _str,
        "proposed_value": _str,
        "reason": _str,
        "expected_impact": _str,
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "risk": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": [
        "action_type", "shop_id", "asin", "campaign_id", "target", "current_value",
        "proposed_value", "reason", "expected_impact", "confidence", "risk",
    ],
    "additionalProperties": False,
}

REVIEW_OUTPUT = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "3-5 句话的总体结论，老板一眼能看懂"},
        "findings": {"type": "array", "items": FINDING},
        "recommendations": {"type": "array", "items": RECOMMENDATION},
        "questions_for_owner": {
            "type": "array",
            "items": _str,
            "description": "需要老板补充的信息或需要老板拍板的问题",
        },
    },
    "required": ["summary", "findings", "recommendations", "questions_for_owner"],
    "additionalProperties": False,
}
