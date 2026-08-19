"""Shared PyInstaller scope for the lightweight CSImage viewer builds."""

VIEWER_ENTRYPOINT = "csimage_viewer.py"

# These modules remain in the repository for later development, but the current
# product exposes only the Qt viewer, quick-markup and lightweight image tools.
HIDDEN_DEVELOPMENT_MODULES = (
    "light_image_editor.accessibility",
    "light_image_editor.annotations",
    "light_image_editor.batch",
    "light_image_editor.files",
    "light_image_editor.presets",
    "light_image_editor.preview",
    "light_image_editor.privacy",
    "light_image_editor.projects",
    "light_image_editor.renderer",
    "light_image_editor.session",
    "light_image_editor.theme",
    "light_image_editor.update_security",
    "light_image_editor.updater",
    "light_image_editor.windows_drop",
    "light_image_editor.windows_effects",
    "light_image_editor.domain",
    "light_image_editor.providers",
    "light_image_editor.services",
    "light_image_editor.shell",
    "light_image_editor.ui",
)

# The viewer uses QtCore, QtGui, QtWidgets and QtSvg. PySide's package metadata
# advertises every installed binding, so PyInstaller otherwise collects large
# QML, Quick, WebEngine, PDF, multimedia and developer-only libraries.
UNUSED_QT_MODULES = (
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DRender",
    "PySide6.QtAxContainer",
    "PySide6.QtBluetooth",
    "PySide6.QtCanvasPainter",
    "PySide6.QtCharts",
    "PySide6.QtConcurrent",
    "PySide6.QtDBus",
    "PySide6.QtDataVisualization",
    "PySide6.QtDesigner",
    "PySide6.QtGraphs",
    "PySide6.QtGraphsWidgets",
    "PySide6.QtHelp",
    "PySide6.QtHttpServer",
    "PySide6.QtLocation",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtNetwork",
    "PySide6.QtNetworkAuth",
    "PySide6.QtNfc",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning",
    "PySide6.QtPrintSupport",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickControls2",
    "PySide6.QtQuickTest",
    "PySide6.QtQuickWidgets",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSensors",
    "PySide6.QtSerialBus",
    "PySide6.QtSerialPort",
    "PySide6.QtSpatialAudio",
    "PySide6.QtSql",
    "PySide6.QtStateMachine",
    "PySide6.QtSvgWidgets",
    "PySide6.QtTest",
    "PySide6.QtTextToSpeech",
    "PySide6.QtUiTools",
    "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebSockets",
    "PySide6.QtWebView",
    "PySide6.QtXml",
)

VIEWER_EXCLUDES = (
    "tkinter",
    "_tkinter",
    *HIDDEN_DEVELOPMENT_MODULES,
    *UNUSED_QT_MODULES,
)

# Qt's deployment hook copies a few optional plugins even when their Python
# bindings are excluded. None are used by the raster viewer; removing each
# plugin together with its private dependency chain avoids orphan DLLs.
EXCLUDED_BUNDLE_PATHS = frozenset(
    {
        "pyside6/opengl32sw.dll",
        "pyside6/qt6network.dll",
        "pyside6/qt6opengl.dll",
        "pyside6/qt6pdf.dll",
        "pyside6/qt6qml.dll",
        "pyside6/qt6qmlmeta.dll",
        "pyside6/qt6qmlmodels.dll",
        "pyside6/qt6qmlworkerscript.dll",
        "pyside6/qt6quick.dll",
        "pyside6/qt6virtualkeyboard.dll",
        "pyside6/plugins/generic/qtuiotouchplugin.dll",
        "pyside6/plugins/iconengines/qsvgicon.dll",
        "pyside6/plugins/imageformats/qpdf.dll",
        "pyside6/plugins/imageformats/qsvg.dll",
        "pyside6/plugins/platforminputcontexts/qtvirtualkeyboardplugin.dll",
    }
)


def keep_viewer_bundle_entry(entry: tuple[str, str, str]) -> bool:
    normalized = entry[0].replace("\\", "/").casefold()
    return normalized not in EXCLUDED_BUNDLE_PATHS
