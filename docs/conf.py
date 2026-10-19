"""Strict, offline-capable V2 documentation build."""
project = "Protocol Emulator ASIC"
author = "Protocol Emulator ASIC contributors"
extensions = ["myst_parser"]
source_suffix = {".md": "markdown"}
root_doc = "index"
exclude_patterns = ["_build"]
html_theme = "alabaster"
html_title = "Protocol Emulator ASIC — V2"
html_theme_options = {"description": "Dual timing engines and triggered capture"}
myst_heading_anchors = 4
nitpicky = True

html_baseurl = "https://mcranny.github.io/protocol-emulator-asic/"
