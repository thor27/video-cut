"""Unit tests for the internationalization (i18n) module."""

from video_cut.i18n import (
    _,
    _normalize_lang,
    detect_system_locale,
    get_current_locale,
    set_language,
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

    assert _("Open...") == "Abrir..."
    assert _("No Media Loaded") == "Nenhuma mídia carregada"
    assert _("Split at Cursor") == "Dividir no Cursor"
    assert _("Crop & Rotate (C)") == "Cortar e Girar (C)"
    assert _("Delete clip (Delete)") == "Excluir clipe (Delete)"
    assert _("Undo (Ctrl+Z)") == "Desfazer (Ctrl+Z)"
    assert _("Redo (Ctrl+Shift+Z or Ctrl+Y)") == "Refazer (Ctrl+Shift+Z ou Ctrl+Y)"
    assert _("Crop & Rotate — {name}").format(name="clip.mp4") == "Cortar e Girar — clip.mp4"
    assert _("Aspect Ratio:") == "Proporção:"
    assert _("Free") == "Livre"
    assert _("Original") == "Original"
    assert _("Freeform (unconstrained)") == "Livre (sem restrição)"
    assert _("Original aspect ratio") == "Proporção original"
    assert _("New Window") == "Nova janela"
    assert _("Toggle Fullscreen (F11)") == "Alternar tela cheia (F11)"
    assert _("Main Menu") == "Menu principal"
    assert _("Export...") == "Exportar..."
    assert _("Export with default options (Ctrl+E)") == "Exportar com opções predefinidas (Ctrl+E)"
    assert _("Export with Default Options") == "Exportar com opções predefinidas"
    assert _("Export Video") == "Exportar vídeo"
    assert _("Import") == "Importar"
    assert _("Video will be generated with no audio") == "O vídeo será gerado sem áudio"
    assert _("3:4 Vertical Standard") == "3:4 Vertical Padrão"
    assert _("9:21 Vertical Ultrawide") == "9:21 Ultrawide Vertical"
    assert _("Overlays & Drawings (O)") == "Sobreposições e Desenhos (O)"
    assert _("Add Text") == "Adicionar Texto"
    assert _("Add Rectangle") == "Adicionar Retângulo"
    assert _("Add Circle / Ellipse") == "Adicionar Círculo / Elipse"
    assert _("Add Emoji") == "Adicionar Emoji"
    assert _("Properties") == "Propriedades"
    assert _("No element selected") == "Nenhum elemento selecionado"
    assert _("Keep proportions") == "Manter proporções"
    assert _("Allow audio") == "Permitir áudio"
    assert _("Audio Track") == "Faixa de áudio"
    assert _("No elements") == "Nenhum elemento"
    assert _("Lossless (Stream Copy)") == "Sem perdas (Cópia direta)"
    assert _("What is Lossless Cutting?") == "O que é o Corte sem Perdas?"
    assert _("Disadvantages of Lossless Cutting") == "Desvantagens do Corte sem Perdas"
    assert _("Indexing Keyframes") == "Indexando quadros-chave"
    assert _("Export Failed") == "Falha na exportação"
    assert _("Copy Details") == "Copiar detalhes"
    assert _("FFmpeg Operation Failed") == "Falha na operação do FFmpeg"
    assert _("FFmpeg Source") == "Origem do FFmpeg"
    assert _("Auto (Recommended)") == "Automático (Recomendado)"
    assert _("Container (Bundled)") == "Contêiner (Integrado)"
    assert _("App Bundle (Bundled)") == "Pacote do App (Integrado)"
    assert _("App Bundle") == "Pacote do App"
    assert _("Manual (Custom Path)") == "Manual (Caminho Personalizado)"


def test_translations_es():
    set_language("es")
    assert get_current_locale() == "es"

    assert _("Open...") == "Abrir..."
    assert _("No Media Loaded") == "Ningún medio cargado"
    assert _("Split at Cursor") == "Dividir en el Cursor"
    assert _("Crop & Rotate (C)") == "Recortar y Girar (C)"
    assert _("Delete clip (Delete)") == "Eliminar clip (Supr)"
    assert _("Undo (Ctrl+Z)") == "Deshacer (Ctrl+Z)"
    assert _("Redo (Ctrl+Shift+Z or Ctrl+Y)") == "Rehacer (Ctrl+Shift+Z o Ctrl+Y)"
    assert _("Crop & Rotate — {name}").format(name="clip.mp4") == "Recortar y Girar — clip.mp4"
    assert _("Aspect Ratio:") == "Relación de aspecto:"
    assert _("Free") == "Libre"
    assert _("Original") == "Original"
    assert _("Freeform (unconstrained)") == "Libre (sin restricciones)"
    assert _("Original aspect ratio") == "Proporción original"
    assert _("New Window") == "Nueva ventana"
    assert _("Toggle Fullscreen (F11)") == "Alternar pantalla completa (F11)"
    assert _("Main Menu") == "Menú principal"
    assert _("Export...") == "Exportar..."
    assert _("Export with default options (Ctrl+E)") == "Exportar con opciones predeterminadas (Ctrl+E)"
    assert _("Export with Default Options") == "Exportar con opciones predeterminadas"
    assert _("Export Video") == "Exportar video"
    assert _("Import") == "Importar"
    assert _("Video will be generated with no audio") == "El video se generará sin audio"
    assert _("Drag header to reorder clip") == "Arrastre el encabezado para reordenar el clip"
    assert _("3:4 Vertical Standard") == "3:4 Vertical Estándar"
    assert _("9:21 Vertical Ultrawide") == "9:21 Ultrawide Vertical"
    assert _("Overlays & Drawings (O)") == "Superposiciones y Dibujos (O)"
    assert _("Add Text") == "Añadir Texto"
    assert _("Add Rectangle") == "Añadir Rectángulo"
    assert _("Properties") == "Propiedades"
    assert _("Lossless (Stream Copy)") == "Sin pérdidas (Copia directa)"
    assert _("What is Lossless Cutting?") == "¿Qué es el Corte sin Pérdidas?"
    assert _("Disadvantages of Lossless Cutting") == "Desventajas del Corte sin Pérdidas"
    assert _("Indexing Keyframes") == "Indexando fotogramas clave"
    assert _("Export Failed") == "Error al exportar"
    assert _("Copy Details") == "Copiar detalles"
    assert _("FFmpeg Operation Failed") == "Error en la operación de FFmpeg"
    assert _("FFmpeg Source") == "Origen de FFmpeg"
    assert _("Auto (Recommended)") == "Automático (Recomendado)"
    assert _("Container (Bundled)") == "Contenedor (Integrado)"
    assert _("App Bundle (Bundled)") == "Paquete de la App (Integrado)"
    assert _("App Bundle") == "Paquete de la App"
    assert _("Manual (Custom Path)") == "Manual (Ruta Personalizada)"


def test_translations_fr():
    set_language("fr")
    assert get_current_locale() == "fr"

    assert _("Open...") == "Ouvrir..."
    assert _("No Media Loaded") == "Aucun média chargé"
    assert _("Split at Cursor") == "Diviser au Curseur"
    assert _("Crop & Rotate (C)") == "Rogner et Pivoter (C)"
    assert _("Delete clip (Delete)") == "Supprimer le clip (Suppr)"
    assert _("Undo (Ctrl+Z)") == "Annuler (Ctrl+Z)"
    assert _("Redo (Ctrl+Shift+Z or Ctrl+Y)") == "Rétablir (Ctrl+Shift+Z ou Ctrl+Y)"
    assert _("Crop & Rotate — {name}").format(name="clip.mp4") == "Rogner et Pivoter — clip.mp4"
    assert _("Aspect Ratio:") == "Format d'image :"
    assert _("Free") == "Libre"
    assert _("Original") == "Original"
    assert _("Freeform (unconstrained)") == "Libre (sans contrainte)"
    assert _("Original aspect ratio") == "Format d'origine"
    assert _("New Window") == "Nouvelle fenêtre"
    assert _("Toggle Fullscreen (F11)") == "Basculer en plein écran (F11)"
    assert _("Main Menu") == "Menu principal"
    assert _("Export...") == "Exporter..."
    assert _("Export with default options (Ctrl+E)") == "Exporter avec les options par défaut (Ctrl+E)"
    assert _("Export with Default Options") == "Exporter avec les options par défaut"
    assert _("Overlays & Drawings (O)") == "Superpositions et Dessins (O)"
    assert _("Add Text") == "Ajouter du texte"
    assert _("Add Rectangle") == "Ajouter un rectangle"
    assert _("Properties") == "Propriétés"

    assert _("Export Video") == "Exporter la vidéo"
    assert _("Import") == "Importer"
    assert _("Video will be generated with no audio") == "La vidéo sera générée sans audio"
    assert _("Drag header to reorder clip") == "Glissez l'en-tête pour réorganiser le clip"
    assert _("3:4 Vertical Standard") == "3:4 Standard Vertical"
    assert _("9:21 Vertical Ultrawide") == "9:21 Ultra-large Vertical"
    assert _("Lossless (Stream Copy)") == "Sans perte (Copie directe)"
    assert _("What is Lossless Cutting?") == "Qu'est-ce que le Découpage sans Perte ?"
    assert _("Disadvantages of Lossless Cutting") == "Inconvénients du Découpage sans Perte"
    assert _("Indexing Keyframes") == "Indexation des images clés"
    assert _("Export Failed") == "Échec de l'exportation"
    assert _("Copy Details") == "Copier les détails"
    assert _("FFmpeg Operation Failed") == "Échec de l'opération FFmpeg"
    assert _("FFmpeg Source") == "Source de FFmpeg"
    assert _("Auto (Recommended)") == "Automatique (Recommandé)"
    assert _("Container (Bundled)") == "Conteneur (Intégré)"
    assert _("App Bundle (Bundled)") == "Paquet d'application (Intégré)"
    assert _("App Bundle") == "Paquet d'application"
    assert _("Manual (Custom Path)") == "Manuel (Chemin Personnalisé)"


def test_translations_en_us():
    set_language("en_US")
    assert get_current_locale() == "en_US"

    assert _("Open...") == "Open..."
    assert _("No Media Loaded") == "No Media Loaded"
    assert _("Split at Cursor") == "Split at Cursor"
    assert _("Aspect Ratio:") == "Aspect Ratio:"
    assert _("Free") == "Free"
    assert _("Original") == "Original"
    assert _("Freeform (unconstrained)") == "Freeform (unconstrained)"
    assert _("Original aspect ratio") == "Original aspect ratio"
    assert _("New Window") == "New Window"
    assert _("Toggle Fullscreen (F11)") == "Toggle Fullscreen (F11)"
    assert _("Main Menu") == "Main Menu"
    assert _("Export...") == "Export..."
    assert _("Export with default options (Ctrl+E)") == "Export with default options (Ctrl+E)"
    assert _("Export with Default Options") == "Export with Default Options"
    assert _("Export Video") == "Export Video"
    assert _("Import") == "Import"
    assert _("Video will be generated with no audio") == "Video will be generated with no audio"
    assert _("Drag header to reorder clip") == "Drag header to reorder clip"
    assert _("3:4 Vertical Standard") == "3:4 Vertical Standard"
    assert _("9:21 Vertical Ultrawide") == "9:21 Vertical Ultrawide"
    assert _("Lossless (Stream Copy)") == "Lossless (Stream Copy)"
    assert _("What is Lossless Cutting?") == "What is Lossless Cutting?"
    assert _("Disadvantages of Lossless Cutting") == "Disadvantages of Lossless Cutting"
    assert _("Indexing Keyframes") == "Indexing Keyframes"
    assert _("Export Failed") == "Export Failed"
    assert _("Copy Details") == "Copy Details"
    assert _("FFmpeg Operation Failed") == "FFmpeg Operation Failed"
    assert _("FFmpeg Source") == "FFmpeg Source"
    assert _("Auto (Recommended)") == "Auto (Recommended)"
    assert _("Container (Bundled)") == "Container (Bundled)"
    assert _("App Bundle (Bundled)") == "App Bundle (Bundled)"
    assert _("App Bundle") == "App Bundle"
    assert _("Manual (Custom Path)") == "Manual (Custom Path)"


def test_fallback_unsupported_language():
    # If set to an unsupported language, it defaults to en_US and does not crash
    set_language("de_DE")
    assert _("Open...") == "Open..."

    # Untranslated string returns original
    assert _("A brand new unknown string") == "A brand new unknown string"
