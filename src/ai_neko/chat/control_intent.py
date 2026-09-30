"""Recognize only explicit whole-utterance controls from the current user input."""

from __future__ import annotations

import re
import unicodedata


def explicit_control(text: object) -> str | None:
    if not isinstance(text, str):
        return None
    value = unicodedata.normalize("NFKC", text).strip().rstrip("。.!！").strip()
    # Full matching excludes quotation, negation, hypothetical speech and text
    # embedded in a page. This recognizer never sees sources or model output.
    if re.fullmatch(r"(?:请)?(?:开始)?(?:新一局|新对局)(?:吧)?", value):
        return "new_match"
    if re.fullmatch(r"(?:请)?(?:就)?(?:按|采用|使用)(?:这份|这个|这篇)(?:攻略)?(?:来|吧)?", value):
        return "select_guide"
    if re.fullmatch(
        r"(?:请)?(?:把攻略)?(?:切换|换)(?:成|到|为)(?:这份|这个|这篇)(?:攻略)?(?:吧)?", value
    ):
        return "select_guide"
    return None
