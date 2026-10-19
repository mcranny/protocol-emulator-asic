"""Exercise the static layout site at a project-path URL in Chromium."""
import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def check(url, output):
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for name, viewport in [('desktop', {'width':1440,'height':1000}), ('mobile', {'width':390,'height':844})]:
            context = browser.new_context(viewport=viewport, has_touch=name=='mobile')
            page = context.new_page(); errors=[]
            page.on('pageerror', lambda e:errors.append(str(e)))
            page.goto(url, wait_until='networkidle', timeout=120000)
            canvas=page.locator('#view')
            page.wait_for_function("document.querySelector('#view').dataset.mode === 'preview'")
            page.screenshot(path=str(output/f'{name}-overview.png'), full_page=True)
            initial=canvas.evaluate('(c)=>c.toDataURL()')
            saturation=canvas.evaluate('''c=>{const p=c.getContext('2d').getImageData(0,0,c.width,c.height).data;let white=0;for(let i=0;i<p.length;i+=4)if(p[i]>245&&p[i+1]>245&&p[i+2]>245)white++;return white/(p.length/4)}''')
            assert saturation < .03, 'overview layers saturate to white'
            page.get_by_role('button',name='Inspect geometry',exact=True).click()
            page.wait_for_function("document.querySelector('#view').dataset.mode === 'geometry'",timeout=120000)
            assert initial!=canvas.evaluate('(c)=>c.toDataURL()')
            page.screenshot(path=str(output/f'{name}-geometry.png'),full_page=True)
            page.get_by_role('button',name='None',exact=True).click()
            page.wait_for_function("document.querySelector('#view').dataset.mode === 'geometry'")
            page.wait_for_timeout(150)
            blank=canvas.evaluate('(c)=>c.toDataURL()')
            page.get_by_role('button',name='Solo layer 10/0',exact=True).click()
            page.wait_for_timeout(250)
            assert blank!=canvas.evaluate('(c)=>c.toDataURL()')
            assert page.locator('#layers input:checked').count()==1
            canvas.focus(); page.keyboard.press('+');page.keyboard.press('ArrowRight')
            page.wait_for_function("document.querySelector('#view').dataset.mode === 'geometry'",timeout=120000)
            page.get_by_role('button',name='All',exact=True).click()
            page.get_by_role('button',name='Fit layout',exact=True).click()
            page.wait_for_function("document.querySelector('#view').dataset.mode === 'preview'")
            assert page.locator('#layers input:checked').count()==16
            page.get_by_role('link',name='← Documentation',exact=True).click()
            assert page.title().startswith('Protocol Emulator ASIC')
            assert not errors, errors
            results.append({'viewport':name,'passed':True,'browser_errors':errors})
            context.close()
        context=browser.new_context();page=context.new_page()
        page.route('**/tiles/*.jsonl.gz',lambda route:route.fulfill(status=200,body=b'invalid tile'))
        page.goto(url,wait_until='networkidle',timeout=120000)
        page.get_by_role('button',name='Inspect geometry',exact=True).click()
        page.wait_for_function("document.querySelector('#view').dataset.mode === 'error'",timeout=30000)
        assert 'checksum mismatch' in page.locator('#status').inner_text()
        results.append({'case':'corrupt-tile-rejected','passed':True})
        context.close();browser.close()
    (output/'results.json').write_text(json.dumps(results,indent=2)+'\n')
    return results


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(check(args.url,args.output),indent=2))
