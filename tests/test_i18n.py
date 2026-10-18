"""Unit tests for the internationalization (i18n) module."""

import os
import pytest
from video_cut.i18n import (
    _,
    set_language,
    get_current_locale,
    get_supported_locales,
    detect_system_locale,
    _normalize_lang,
)


def test_normalize_lang():
    assert _normalize_lang("pt_BR.UTF-8") == "pt_BR"
    assert _normalize_lang("pt_BR") == "pt_BR"
    assert _normalize_lang("pt-br") == "pt_BR"
    assert _normalize_lang("pt") == "pt_BR"

    assert _normalize_lang("es_ES.UTF-8") == "es"
    assert _normalize_lang("es_MX") == "es"
    assert _normalize_lang("es") == "es"

    assert _normalize_lang("fr_FR.UTF-8") == "fr"
    assert _normalize_lang("fr_CA") == "fr"
    assert _normalize_lang("fr") == "fr"

    assert _normalize_lang("en_US.UTF-8") == "en_US"
    assert _normalize_lang("en_GB") == "en_US"
    assert _normalize_lang("en") == "en_US"

    assert _normalize_lang("de_DE") is None
    assert _normalize_lang(None) is None


def test_detect_system_locale(monkeypatch):
    # Test LANGUAGE environment variable
    monkeypatch.setenv("LANGUAGE", "pt_BR:en")
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    monkeypatch.delenv("LANG", raising=False)
    assert detect_system_locale() == "pt_BR"

    # Test LC_ALL
    monkeypatch.delenv("LANGUAGE", raising=False)
    monkeypatch.setenv("LC_ALL", "es_ES.UTF-8")
    assert detect_system_locale() == "es"

    # Test LANG
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.setenv("LANG", "fr_FR.UTF-8")
    assert detect_system_locale() == "fr"


def test_translations_pt_br():
    set_language("pt_BR")
    assert get_current_locale() == "pt_BR"

    assert _("Open") == "Abrir"
    assert _("Export") == "Exportar"
    assert _("No Media Loaded") == "Nenhuma mídia carregada"
    assert _("Split at Cursor") == "Dividir no Cursor"
    assert _("Crop & Rotate (C)") == "Cortar e Girar (C)"
    assert _("Delete clip (Delete)") == "Excluir clipe (Delete)"
    assert _("Undo (Ctrl+Z)") == "Desfazer (Ctrl+Z)"
    assert _("Redo (Ctrl+Shift+Z or Ctrl+Y)") == "Refazer (Ctrl+Shift+Z ou Ctrl+Y)"
    assert _("Crop & Rotate — {name}").format(name="clip.mp4") == "Cortar e Girar — clip.mp4"


def test_translations_es():
    set_language("es")
    assert get_current_locale() == "es"

    assert _("Open") == "Abrir"
    assert _("Export") == "Exportar"
    assert _("No Media Loaded") == "Ningún medio cargado"
    assert _("Split at Cursor") == "Dividir en el Cursor"
    assert _("Crop & Rotate (C)") == "Recortar y Girar (C)"
    assert _("Delete clip (Delete)") == "Eliminar clip (Supr)"
    assert _("Undo (Ctrl+Z)") == "Deshacer (Ctrl+Z)"
    assert _("Redo (Ctrl+Shift+Z or Ctrl+Y)") == "Rehacer (Ctrl+Shift+Z o Ctrl+Y)"
    assert _("Crop & Rotate — {name}").format(name="clip.mp4") == "Recortar y Girar — clip.mp4"


def test_translations_fr():
    set_language("fr")
    assert get_current_locale() == "fr"

    assert _("Open") == "Ouvrir"
    assert _("Export") == "Exporter"
    assert _("No Media Loaded") == "Aucun média chargé"
    assert _("Split at Cursor") == "Diviser au Curseur"
    assert _("Crop & Rotate (C)") == "Rogner et Pivoter (C)"
    assert _("Delete clip (Delete)") == "Supprimer le clip (Suppr)"
    assert _("Undo (Ctrl+Z)") == "Annuler (Ctrl+Z)"
    assert _("Redo (Ctrl+Shift+Z or Ctrl+Y)") == "Rétablir (Ctrl+Shift+Z ou Ctrl+Y)"
    assert _("Crop & Rotate — {name}").format(name="clip.mp4") == "Rogner et Pivoter — clip.mp4"


def test_translations_en_us():
    set_language("en_US")
    assert get_current_locale() == "en_US"

    assert _("Open") == "Open"
    assert _("Export") == "Export"
    assert _("No Media Loaded") == "No Media Loaded"
    assert _("Split at Cursor") == "Split at Cursor"


def test_fallback_unsupported_language():
    # If set to an unsupported language, it defaults to en_US and does not crash
    set_language("de_DE")
    assert _("Open") == "Open"

    # Untranslated string returns original
    assert _("A brand new unknown string") == "A brand new unknown string"
