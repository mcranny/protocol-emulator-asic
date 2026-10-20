"""Strict, offline-capable documentation build."""
project = "Protocol Emulator ASIC"
author = "Protocol Emulator ASIC contributors"
extensions = ["myst_parser"]
source_suffix = {".md": "markdown"}
root_doc = "index"
exclude_patterns = ["_build"]
html_theme = "alabaster"
html_title = "Protocol Emulator ASIC — V3"
html_theme_options = {"description": "48 MHz time-cursor protocol emulator"}
myst_heading_anchors = 4
nitpicky = True

html_baseurl = "https://mcranny.github.io/protocol-emulator-asic/"
