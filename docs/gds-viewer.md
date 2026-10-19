# GDS layout viewers

## Online V2 layout explorer

[Open the interactive V2 layout explorer](https://mcranny.github.io/protocol-emulator-asic/layout/).
It displays the selected `447b3df` layout directly from GitHub Pages and works
without a local service. Drag to pan, scroll or pinch to zoom, select layers,
or choose **Inspect geometry** to move from the overview into detailed geometry.

The overview is a raster preview. Subpixel wires may be invisible at that scale;
it is not a geometry or connectivity check. At detailed zoom, the browser loads
checksum-verified tiles containing original transformed polygon coordinates.
The export retains paths, arrays and every hierarchy level, with no polygon
simplification or small-feature filter. Tile-boundary duplicates are clipped
at display time. Text labels are available in the original GDS but are not drawn
in the online viewer. Geometry cannot establish connectivity; use LVS reports.

All layer/datatype pairs are available, identified by their GDS numbers. The
export independently compares KLayout and gdstk expanded polygon counts per
layer. A manifest records the GDS checksum, counts, bounds and tile hashes.
The browser keeps at most 12 geometry tiles and uses a raster preview at wider
views. Modern browsers need Canvas, Web Crypto and gzip DecompressionStream.
The original GDS can always be downloaded from the viewer.

## Local service with arbitrary viewport rendering

The local viewer renders the original GDS through a KLayout service. The browser
holds one current viewport bitmap rather than millions of SVG elements. Every
settled zoom or pan requests a new render from the source geometry. Wires, vias,
paths, array instances and all hierarchy levels remain available at any zoom.
Literal geometry has no simplification or minimum feature-size filter. Auto mode
uses an explicitly labeled density overview when individual features are subpixel.

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
return to the overview. **Detail** chooses a scale at which typical selected
features occupy about four screen pixels. Press **F** while the canvas is focused
to fit. All layers start enabled. Checkboxes select any combination; **Solo**
isolates one layer. Pass `--layer-properties /path/to/technology.lyp` to show
verified PDK names such as Metal2 and Via2 alongside their GDS layer/datatype.
Without that optional file, source names or layer numbers remain available.

## Overview, geometry and zoom limits

**Auto** uses an area-density overview when the median characteristic feature
size of the selected layers is below 1.5 CSS pixels. Above that threshold it
renders literal geometry. The mode selector can force either **Density overview**
or **Literal geometry**, and the header and status identify the actual result.
All geometry and every hierarchy level remain available in literal mode; the
GDS and full SVG export are not altered by LOD.

Density is computed per layer in at most 200 by 200 screen-space buckets using
KLayout's `Region.rasterize` area integration. A wire narrower than a screen
pixel still contributes its area. Buckets snap to database-unit coordinates.
Overlaps contribute repeatedly: this is **overlap-weighted polygon area**, not
union coverage, routing congestion, or a DRC measurement. A square-root/log
transfer makes sparse features visible, and colors blend according to density.
The service caches only the current viewport's per-layer density arrays.
See the [KLayout rasterizer documentation](https://www.klayout.de/doc/code/class_Region.html#method199).

Literal geometry uses translucent solid fills and one-pixel, lighter outlines.
The former bright stipple dots are gone. Broad structural/boundary layers receive
lower weight in multi-layer views so they do not cover routing; solo views use
full weight. Blending is independent of layer order. There is no screen-space
inflation of wire widths. The background stays dark in both rendering modes.

Zoom-out stops at the actual design bounds plus a 4% margin; panning is constrained
to that envelope. Maximum magnification shows a span of four times the smallest
measured feature on the viewport's shorter side. Feature measurements include
path width, rectangle short side, and a conservative 2A/P characteristic width
for other polygons, with instance magnification and array multiplicity accounted
for. These are display heuristics, not PDK minimum-width rules. The limits adapt
to the actual GDS and viewport aspect ratio, including after resize.

The native placement grid is disabled. The optional **Reference grid (faint)**
is off by default and adds low-contrast neutral coordinate dots. Its spacing
adapts to zoom; it is not a claimed reconstruction of placement sites or routing
tracks, since those definitions are not supplied by the GDS.

The status says **Refreshing** while an old image is repositioned for interaction,
and **Ready** once the new view has rendered. Zoom into literal geometry to
inspect individual features. An image cannot certify connectivity: use LVS and
the physical signoff reports.

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

The local export's `index.html` is a small service client. Opening it as a file, or serving it
with a plain static HTTP server, shows setup instructions. Interactive navigation in this local client
requires `tools/view_gds.py`. The separate static exporter supplies the online
GitHub Pages viewer described above.

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
.venv/bin/python -m pytest -q tests/tooling/test_gds_render.py tests/tooling/test_gds_viewer.py
```

These cover transformed 1 nm vias, nested arrays, paths, overlapping boundary
layers, subpixel area contributions, density orientation and clipping, feature
scaling, LOD selection, cache bounds, the complete SVG inventory, source checksums
and bounded requests.
For browser checks, start the viewer as above, then in another terminal:

```sh
.venv/bin/python -m pip install -r requirements-viewer-browser.txt
PLAYWRIGHT_BROWSERS_PATH="$PWD/build/playwright" .venv/bin/python -m playwright install chromium
PLAYWRIGHT_BROWSERS_PATH="$PWD/build/playwright" .venv/bin/python tools/check_gds_viewer_browser.py --output build/viewer-browser-check
```

The browser check exercises repeated zooms, pan, wheel navigation, layer changes,
rapid inputs, resize, both zoom limits, automatic/manual LOD selection, layer
isolation, the reference-grid toggle and recovery from a failed render. It saves screenshots and
Chromium memory measurements. Process RSS is summed and can double-count shared
pages; it is not a measurement of unique physical memory. Browser version,
platform, display size and layout affect the result. The optional browser tools
are separate from the ASIC flow dependencies.

A saved development GDS is suitable for viewer tests but is not accepted v2
signoff evidence. Final v2 rendering must use the exact GDS accepted by physical
signoff and precheck.

## Publishing the documentation site

The **GitHub Pages** workflow builds strict Sphinx HTML plus the static viewer.
Pull requests build the site without deploying; only `main` can deploy through
the `github-pages` environment. All Pages actions and Python dependencies are
pinned. The site uses the repository path `/protocol-emulator-asic/`; viewer
assets use relative paths so they also work under that prefix.

`docs/layout-source.json` selects the released layout archive and both archive
and GDS checksums. A checksum mismatch fails the build. Updating documentation
does not silently switch the physical layout to another revision. The original
GDS, generated tiles and previews are build artifacts rather than Git source.
The complete evidence archive remains linked from its existing host.

To reproduce in a separate virtual environment:

```sh
python -m pip install -r requirements-pages.txt
python -m tools.build_pages --output build/pages-new
python -m http.server --directory build/pages-new 8000
```

Open `http://localhost:8000/` for Sphinx and `/layout/` for the viewer. Use
`--archive /path/to/the/release-archive.tar.gz` to reuse the exact pinned archive
offline. A site over 900 MB fails the build before deployment.
