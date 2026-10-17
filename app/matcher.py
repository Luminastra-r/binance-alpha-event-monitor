"""Deterministic Alpha airdrop keyword rules (v1: no LLM).

A message matches iff it contains at least one Alpha-identity keyword AND at
least one airdrop/claim intent keyword. "Binance Alpha" alone is NOT a match
(e.g. "Binance Alpha Trading Competition" must NOT match).
"""

# Alpha identity keywords
ALPHA_IDENTITY_KEYWORDS = [
    "binance alpha",
    "binancealpha",
    "币安 alpha",
    "币安alpha",
    "alpha points",
    "alpha events",
    "alpha airdrop",
    "alpha debut",
    "alpha box",
]

# Airdrop / claim intent keywords
AIRDROP_INTENT_KEYWORDS = [
    "airdrop",
    "claim",
    "claim the airdrop",
    "claim an airdrop",
    "claim the token",
    "claim their airdrop",
    "claiming the airdrop",
    "alpha points",
    "get ready to claim",
    "first platform to feature",
    "now live on binance alpha",
    "空投",
    "领取",
    "积分",
    "先到先得",
]


def alpha_identity_hit(normalized: str) -> str | None:
    for kw in ALPHA_IDENTITY_KEYWORDS:
        if kw in normalized:
            return kw
    return None


def airdrop_intent_hit(normalized: str) -> str | None:
    for kw in AIRDROP_INTENT_KEYWORDS:
        if kw in normalized:
            return kw
    return None


def matches_alpha_airdrop(normalized: str) -> bool:
    """Rule: Alpha identity AND airdrop/claim intent, both required."""
    return alpha_identity_hit(normalized) is not None and airdrop_intent_hit(normalized) is not None


def match_reason(normalized: str) -> dict:
    return {
        "matched": matches_alpha_airdrop(normalized),
        "identity": alpha_identity_hit(normalized),
        "intent": airdrop_intent_hit(normalized),
    }
