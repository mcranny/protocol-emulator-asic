# Full-detail GDS viewer

The viewer renders the original GDS through a local KLayout service. The browser
holds one current viewport bitmap rather than millions of SVG elements. Every
settled zoom or pan requests a new render from the source geometry. Wires, vias,
paths, array instances and all hierarchy levels remain available at any zoom.
There is no geometry simplification or minimum feature-size filter.

## Start locally

From the repository root, with Python 3.11 or newer:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-viewer.txt
.venv/bin/python tools/view_gds.py /absolute/path/to/layout.gds
```

Open the printed address, normally <http://127.0.0.1:8792>. The service binds only
to this computer. Stop it with Ctrl-C. Use `--port` to select another port, and
`--top CELL_NAME` when the GDS contains multiple top cells. GDS is loaded at
startup: to inspect a different revision, restart the service with that file.
Use a completed output or a saved copy, not a file still being written by a flow.

Drag to pan, use the wheel or double-click to zoom, and select **Fit layout** to
return to the overview. **50 µm** shows a region 50 micrometres across at the
current centre. Press **F** while the canvas is focused to fit. All layers start
enabled; layer checkboxes let you inspect individual conductors. Sparse fills
and transparent overlap are display styles that expose underlying geometry;
polygon edges are still drawn. Overlapping features can be distinguished most
clearly by isolating their layers.

The status says **Refreshing** while an old image is being repositioned for
interaction, and **Ready** once the requested geometry has been freshly rendered.
Screen pixels necessarily limit the overview. Zoom in to resolve individual
features; the viewer does not enlarge a fixed overview as its final detail image.
An image cannot certify connectivity: use LVS and the physical signoff reports.

## Keep the complete vector export

The existing full SVG export and independent expanded polygon inventory remain
available. Install the test requirements for `gdstk`, then run:

```sh
.venv/bin/python -m pip install -r test/requirements.txt
.venv/bin/python tools/render_gds.py /absolute/path/to/layout.gds --output build/layout-render
.venv/bin/python tools/view_gds.py /absolute/path/to/layout.gds --render-dir build/layout-render
```

The optional bundle enables downloads of the complete SVG and geometry manifest.
Its GDS and SVG checksums must match before the service starts. The GDS itself is
always downloadable. Downloads stream from disk without loading the entire file
into the browser page. Opening the complete SVG directly can still consume large
amounts of memory; the new HTML does not embed it.

`index.html` is now a small service client. Opening it as a file, or serving it
with a plain static HTTP server, shows setup instructions. Interactive navigation
requires `tools/view_gds.py`; it cannot run as a standalone static site.

## Resource limits and local verification

Each viewport is at most 2048 by 2048 pixels (16 MiB of raw RGBA pixels).
The browser coalesces interactions, permits one rendering request at a time,
discards stale responses and closes replaced image bitmaps. The current image,
canvas, incoming image and browser compositor can coexist, so 16 MiB is not a
total browser memory cap. The service uses one drawing worker and retains the
original GDS in native memory; its memory still scales with layout size. Multiple
open tabs consume additional browser memory and share the serial service.

Run the geometry and service regression tests:

```sh
.venv/bin/python -m pytest -q tests/test_gds_render.py tests/test_gds_viewer.py
```

These cover transformed 1 nm vias, nested arrays, paths, overlapping boundary
layers, the complete SVG inventory, source checksums and bounded requests.
For browser checks, start the viewer as above, then in another terminal:

```sh
.venv/bin/python -m pip install -r requirements-viewer-browser.txt
PLAYWRIGHT_BROWSERS_PATH="$PWD/build/playwright" .venv/bin/python -m playwright install chromium
PLAYWRIGHT_BROWSERS_PATH="$PWD/build/playwright" .venv/bin/python tools/check_gds_viewer_browser.py --output build/viewer-browser-check
```

The browser check exercises repeated zooms, pan, wheel navigation, layer changes,
rapid inputs, resize and recovery from a failed render. It saves screenshots and
Chromium memory measurements. Process RSS is summed and can double-count shared
pages; it is not a measurement of unique physical memory. Browser version,
platform, display size and layout affect the result. The optional browser tools
are separate from the ASIC flow dependencies.

A saved development GDS is suitable for viewer tests but is not accepted v2
signoff evidence. Final v2 rendering must use the exact GDS accepted by physical
signoff and precheck.
