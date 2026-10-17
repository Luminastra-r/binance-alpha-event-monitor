"""Text normalization pipeline.

Order: Unicode NFC -> full-width ASCII to half-width -> punctuation mapping ->
whitespace collapse -> lowercase. Applied before keyword matching.
"""

import re
import unicodedata

_PUNCT_MAP = str.maketrans(
    {
        "\u201c": '"',  # "
        "\u201d": '"',  # "
        "\u2018": "'",
        "\u2019": "'",
        "\u2014": "-",
        "\u2013": "-",
        "\u2015": "-",
        "\u00b7": " ",
    }
)


def normalize_text(text: str) -> str:
    """Normalize a raw message for deterministic keyword matching."""
    if text is None:
        return ""
    text = unicodedata.normalize("NFC", str(text))

    out = []
    for ch in text:
        o = ord(ch)
        if o == 0x3000:  # ideographic space
            out.append(" ")
        elif 0xFF01 <= o <= 0xFF5E:  # full-width ASCII -> half-width
            out.append(chr(o - 0xFEE0))
        else:
            out.append(ch)
    text = "".join(out).translate(_PUNCT_MAP)
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()
