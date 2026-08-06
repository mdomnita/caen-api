import re
import unicodedata


def strip_diacritics(value: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFD", value)
        if unicodedata.category(char) != "Mn"
    )


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def normalize_search(value: str) -> str:
    return strip_diacritics(normalize_whitespace(value)).upper()
