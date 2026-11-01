"""Project category buckets from authoritative merchant metadata, never free text."""

# Narrow project MCC catalogue; unlisted codes use the saved category mapping.
_MCC = {"7997": "健身", "5812": "餐饮"}
_CATEGORY = {
    "餐饮": "餐饮",
    "外卖": "餐饮",
    "零食": "餐饮",
    "茶饮": "餐饮",
    "交通": "交通",
    "出行": "交通",
    "购物": "购物",
    "零售": "购物",
    "生鲜": "购物",
    "娱乐": "娱乐",
    "视频娱乐": "娱乐",
    "音乐": "娱乐",
    "生活服务": "生活服务",
    "日用": "生活服务",
    "数码": "数码",
    "存储": "数码",
    "健身": "健身",
    "培训": "健身",
    "其他": "其他",
}


def merchant_category(mcc: str | None, category: str | None) -> str:
    return _MCC.get(mcc, _CATEGORY.get(category, "其他"))
