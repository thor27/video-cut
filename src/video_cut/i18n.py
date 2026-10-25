"""Internationalization (i18n) service for Video Cut."""

import gettext
import locale
import os
from pathlib import Path

DOMAIN = "video-cut"
LOCALE_DIR = Path(__file__).parent / "locale"
SUPPORTED_LOCALES = ["en_US", "es", "pt_BR", "fr"]

_active_translation: gettext.NullTranslations = gettext.NullTranslations()
_current_locale: str = "en_US"


def _normalize_lang(code: str | None) -> str | None:
    """Normalize a locale/language identifier to one of the supported codes."""
    if not code:
        return None
    # Strip encoding e.g. pt_BR.UTF-8 -> pt_BR
    code = code.split(".")[0].split("@")[0].strip()
    code = code.replace("-", "_")

    # Match exact
    for sup in SUPPORTED_LOCALES:
        if code.lower() == sup.lower():
            return sup

    # Match language prefix
    lang = code.split("_")[0].lower()
    if lang == "pt":
        return "pt_BR"
    elif lang == "es":
        return "es"
    elif lang == "fr":
        return "fr"
    elif lang == "en":
        return "en_US"

    return None


def detect_system_locale() -> str:
    """Detect the user's preferred language from environment and locale settings."""
    # 1. Environment variables in order of standard Unix gettext precedence
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        val = os.environ.get(var)
        if val:
            # LANGUAGE can be a colon-separated list like "pt_BR:pt:en"
            for item in val.split(":"):
                matched = _normalize_lang(item)
                if matched:
                    return matched

    # 2. Python locale module
    try:
        loc = locale.getlocale(locale.LC_MESSAGES)[0] if hasattr(locale, "LC_MESSAGES") else None
        matched = _normalize_lang(loc)
        if matched:
            return matched
    except Exception:
        pass

    try:
        loc = locale.getlocale()[0]
        matched = _normalize_lang(loc)
        if matched:
            return matched
    except Exception:
        pass

    return "en_US"


def init_i18n(lang: str | None = None) -> None:
    """Initialize gettext translation catalog based on system language or explicit override."""
    global _active_translation, _current_locale

    target_lang = _normalize_lang(lang) if lang else detect_system_locale()
    _current_locale = target_lang or "en_US"

    # Languages list for fallback resolution: e.g. ["pt_BR", "pt", "en_US"]
    candidate_langs: list[str] = [_current_locale]
    if "_" in _current_locale:
        candidate_langs.append(_current_locale.split("_")[0])
    if "en_US" not in candidate_langs:
        candidate_langs.append("en_US")

    try:
        if LOCALE_DIR.exists():
            _active_translation = gettext.translation(
                domain=DOMAIN,
                localedir=str(LOCALE_DIR),
                languages=candidate_langs,
                fallback=True,
            )
        else:
            _active_translation = gettext.NullTranslations()
    except Exception:
        _active_translation = gettext.NullTranslations()


def set_language(lang_code: str) -> None:
    """Explicitly switch the active language."""
    init_i18n(lang_code)


def get_current_locale() -> str:
    """Return the active locale code (e.g. 'pt_BR', 'es', 'fr', 'en_US')."""
    return _current_locale


def get_supported_locales() -> list[str]:
    """Return the list of supported locales."""
    return list(SUPPORTED_LOCALES)


def _(message: str) -> str:
    """Translate a message string using the active gettext catalog."""
    return _active_translation.gettext(message)


def ngettext(singular: str, plural: str, n: int) -> str:
    """Translate a singular/plural message based on quantity n."""
    return _active_translation.ngettext(singular, plural, n)


# Auto-initialize on module import
init_i18n()
