"""Exercise a running local viewer and record bounded browser resource use."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import urlsplit


def check(url, destination):
    from playwright.sync_api import expect, sync_playwright

    if urlsplit(url).hostname not in {'localhost', '127.0.0.1'}:
        raise ValueError('Browser checks require a local viewer URL')
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    measurements, errors, pending = [], [], set()
    peak_pending = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--enable-precise-memory-info'])
        page = browser.new_page(viewport={'width': 1440, 'height': 1000}, device_scale_factor=2)
        page.on('pageerror', lambda error: errors.append(str(error)))
        session = page.context.new_cdp_session(page)
        session.send('Performance.enable')
        browser_session = browser.new_browser_cdp_session()

        def requested(request):
            nonlocal peak_pending
            if '/api/render?' in request.url:
                pending.add(request)
                peak_pending = max(peak_pending, len(pending))

        page.on('request', requested)
        page.on('requestfinished', lambda request: pending.discard(request))
        page.on('requestfailed', lambda request: pending.discard(request))

        def ready():
            # Locator assertions work with the viewer's strict CSP. Avoid the
            # eval-based wait_for_function predicate, which that policy blocks.
            expect(page.locator('#status')).to_have_text(re.compile(r'^Ready'), timeout=120_000)

        def measure(label):
            metrics = {item['name']: item['value'] for item in session.send('Performance.getMetrics')['metrics']}
            pids = [str(item['id']) for item in browser_session.send('SystemInfo.getProcessInfo')['processInfo']]
            try:
                rss = sum(int(s) for s in subprocess.check_output(
                    ['ps', '-o', 'rss=', '-p', ','.join(pids)], text=True).split()) * 1024
            except (OSError, subprocess.CalledProcessError):
                rss = None  # JS heap and DOM checks also work without POSIX ps.
            canvas = page.locator('canvas').evaluate('(e) => [e.width, e.height]')
            assert max(canvas) <= 2048 and canvas[0] * canvas[1] <= 4_194_304
            measurements.append({'label': label, 'js_heap_used_bytes': metrics['JSHeapUsedSize'],
                                 'dom_nodes': metrics['Nodes'],
                                 'browser_process_rss_sum_bytes': rss, 'canvas': canvas})

        start = time.monotonic()
        page.goto(url)
        ready()
        load_seconds = time.monotonic() - start
        measure('initial')
        page.screenshot(path=str(destination / 'overview.png'))
        page.get_by_role('button', name='50 µm', exact=True).click()
        ready()
        measure('detail')
        page.screenshot(path=str(destination / 'detail.png'))
        for index in range(12):
            page.get_by_role('button', name='Zoom in', exact=True).click()
            ready()
            page.get_by_role('button', name='Zoom out', exact=True).click()
            ready()
            measure(f'zoom cycle {index}')
        page.get_by_role('button', name='None', exact=True).click()
        ready()
        expect(page.locator('#status')).to_contain_text('0 /')
        page.get_by_role('button', name='All', exact=True).click()
        ready()
        inputs = page.locator('[data-layer]')
        inputs.first.uncheck()
        ready()
        expect(page.locator('#status')).to_contain_text(f'{inputs.count()-1} / {inputs.count()}')
        inputs.first.check()
        ready()
        bounds = page.locator('canvas').bounding_box()
        x, y = bounds['x'] + bounds['width']/2, bounds['y'] + bounds['height']/2
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x+70, y+40, steps=8)
        page.mouse.up()
        ready()
        with page.expect_response('**/api/render?**', timeout=120_000):
            page.mouse.wheel(0, -150)
        ready()
        for _ in range(8):
            page.get_by_role('button', name='Zoom in', exact=True).click()
        page.get_by_role('button', name='Fit layout', exact=True).click()
        ready()
        measure('rapid changes')
        with page.expect_response('**/api/render?**', timeout=120_000):
            page.set_viewport_size({'width': 900, 'height': 700})
        ready()
        measure('resized')
        page.route('**/api/render?**', lambda route: route.fulfill(
            status=500, content_type='application/json', body='{"error":"Test failure"}'))
        page.get_by_role('button', name='Zoom in', exact=True).click()
        expect(page.locator('#status')).to_have_text('View not current')
        page.unroute('**/api/render?**')
        page.get_by_role('button', name='Fit layout', exact=True).click()
        ready()
        expect(page.locator('#message')).to_be_hidden()
        measure('recovered')
        assert page.locator('svg,polygon').count() == 0
        assert not errors, errors
        assert peak_pending == 1, peak_pending
        assert max(m['dom_nodes'] for m in measurements) < 1000
        assert max(m['js_heap_used_bytes'] for m in measurements) < 32*1024*1024
        report = {'url': url, 'browser_version': browser.version, 'load_seconds': load_seconds,
                  'errors': errors, 'peak_pending_render_requests': peak_pending,
                  'measurements': measurements}
        (destination / 'results.json').write_text(json.dumps(report, indent=2) + '\n')
        browser.close()
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8792')
    parser.add_argument('--output', type=Path, required=True, help='New evidence directory')
    args = parser.parse_args()
    result = check(args.url, args.output)
    print(json.dumps(result, indent=2))
