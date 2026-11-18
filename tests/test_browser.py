# Browser acceptance test: launches the Flask app and uses Playwright to verify upload, dashboard navigation, and timestamp seeking.

"""Real Chrome acceptance checks. Run with WORKSIGHT_BROWSER_TESTS=1."""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.request import urlopen


@unittest.skipUnless(os.environ.get('WORKSIGHT_BROWSER_TESTS') == '1', 'Opt-in real browser acceptance test')
class BrowserTests(unittest.TestCase):
    """Check the main upload and evidence-seeking flow in a real browser."""
    def test_import_replace_empty_and_seek(self):
        import cv2
        import numpy as np
        from playwright.sync_api import sync_playwright
        from worksight.media import convert_browser_video
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / 'original.mp4'
            clip = root / 'clip.mp4'
            writer = cv2.VideoWriter(str(original), cv2.VideoWriter_fourcc(*'mp4v'), 10, (320, 240))
            for _ in range(40):
                writer.write(np.zeros((240, 320, 3), dtype=np.uint8))
            writer.release()
            convert_browser_video(original, clip)
            csv = root / 'first.csv'
            csv.write_text('Start seconds,Track,Signal,Duration (s),Confidence\n1.25,7,candidate low activity,1,0.9\n')
            replacement = root / 'second.csv'
            replacement.write_text('Time,Track,Signal,Duration (s),Confidence\n00:00,99,work area presence,1,0.9\n')
            empty = root / 'empty.csv'
            empty.write_text('Time,Track,Signal,Duration (s),Confidence\n')
            env = {**os.environ, 'WORKSIGHT_DATA_DIR': str(root / 'data'), 'WORKSIGHT_DB': str(root / 'data/db.sqlite3'), 'WORKSIGHT_PORT': '5015'}
            server = subprocess.Popen([sys.executable, '-m', 'backend.app'], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                for _ in range(100):
                    try:
                        urlopen('http://127.0.0.1:5015/api/health').close()
                        break
                    except OSError:
                        time.sleep(.1)
                with sync_playwright() as p:
                    chrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
                    browser = p.chromium.launch(headless=True, executable_path=chrome if Path(chrome).exists() else None)
                    page = browser.new_page()
                    failures = []
                    page.on('pageerror', lambda error: failures.append(str(error)))
                    page.goto('http://127.0.0.1:5015')
                    def load(file):
                        page.get_by_role('button', name='Load Analysis', exact=True).first.click()
                        page.get_by_label('Analysis video', exact=True).set_input_files(str(clip))
                        page.get_by_label('Events CSV', exact=True).set_input_files(str(file))
                        page.get_by_role('button', name='Load Analysis', exact=True).last.click()
                        page.get_by_role('heading', name='Operations overview', exact=True).wait_for()
                    load(csv)
                    page.locator('.slowdown-timestamp-list button').first.click()
                    page.wait_for_function('document.querySelector("video").currentTime >= 1.2')
                    self.assertIn('Person ID 7', page.locator('.slowdown-panel').inner_text())
                    page.get_by_role('button', name='Change files', exact=True).last.click()
                    load(replacement)
                    self.assertNotIn('Person ID 7', page.locator('.slowdown-panel').inner_text())
                    self.assertIn('No slowdown events', page.locator('.slowdown-panel').inner_text())
                    page.get_by_role('button', name='Change files', exact=True).last.click()
                    load(empty)
                    self.assertIn('0 events', page.locator('.compact-event-panel').inner_text())
                    # Exercise the other workflow through the actual worker subprocess.
                    page.get_by_role('button', name='Change files', exact=True).last.click()
                    page.get_by_label('Original video', exact=True).set_input_files(str(clip))
                    page.get_by_role('button', name='Upload and draw zones', exact=True).click()
                    editor = page.locator('svg.zone-editor')
                    editor.wait_for()
                    box = editor.bounding_box()
                    def draw(points):
                        for x, y in points:
                            editor.click(position={'x': x * box['width'], 'y': y * box['height']})
                    draw([(.1, .1), (.9, .1), (.9, .4)])
                    page.get_by_role('button', name='Working area · blue', exact=True).click()
                    draw([(.1, .5), (.9, .5), (.9, .9)])
                    page.get_by_role('button', name='Process Video', exact=True).click()
                    try:
                        page.wait_for_function('document.querySelector(".page-heading h1") || document.querySelector(".job-status b")?.textContent.includes("failed")', timeout=120000)
                        self.assertTrue(page.locator('.page-heading h1').count(), page.locator('body').inner_text())
                    except Exception:
                        print(page.locator('body').inner_text())
                        for log in (root / 'data' / 'logs').glob('*.log'):
                            print(log.read_text()[-4000:])
                        raise
                    self.assertIn('0 events', page.locator('.compact-event-panel').inner_text())
                    self.assertEqual(failures, [])
                    browser.close()
            finally:
                server.terminate()
                server.wait(timeout=10)
