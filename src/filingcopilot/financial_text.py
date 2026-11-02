"""Shared financial-token grammar for prose verification gates."""

import re


def financial_number_tokens(text: str) -> list[str]:
    """Recognize amounts and ratios without converting model text into facts."""
    text = text.replace("单一倍数", "").replace("一成不变", "")
    # In nominal compounds the 一 belongs to the determiner, not the ratio 成.
    # Keep genuine ratios such as 一成成本 or 利润增加一成 detectable.
    text = re.sub(r"(?:统一|单一|唯一|同一|这一|那一|另一|任一|某一)(?=成[因本果分效就品功])", "", text)
    digits = r"[零〇一二两三四五六七八九十百千万亿兆壹贰叁肆伍陆柒捌玖拾佰仟萬億半]+"
    number = rf"{digits}(?:[点點]{digits})?"
    units = (
        r"美元|港元|澳元|欧元|日元|加元|新加坡元|人民币|英镑|元|"
        r"倍|成|%|％|(?:个)?百分点|基点"
    )
    pattern = rf"[负負正]?(?:{number}\s*(?:{units})|(?:百分之|千分之|万分之){number})|翻(?:番|倍|{number}番)"
    return list(dict.fromkeys(m[0] for m in re.finditer(pattern, text)))


