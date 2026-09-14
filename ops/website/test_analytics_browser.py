"""Run against the built site; Matomo traffic is intercepted, never sent live."""
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import threading
import unittest

ROOT = os.environ.get('SPAWNWP_ANALYTICS_TEST_ROOT')

@unittest.skipUnless(ROOT, 'Set SPAWNWP_ANALYTICS_TEST_ROOT to the generated public site')
class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        class QuietHandler(SimpleHTTPRequestHandler):
            def log_message(self, *args):pass
        cls.server = ThreadingHTTPServer(('127.0.0.1',0), functools.partial(QuietHandler,directory=ROOT))
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.base=f'http://127.0.0.1:{cls.server.server_port}'
        cls.pw=sync_playwright().start()
        cls.browser=cls.pw.chromium.launch(args=['--no-sandbox'])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.pw.stop();cls.server.shutdown();cls.server.server_close();cls.thread.join()

    def setUp(self):
        self.context=self.browser.new_context()
        def route_request(route):
            if route.request.url == self.base+'/docs/sitemap.xml':
                route.fulfill(content_type='application/xml', body=(Path(ROOT)/'docs/sitemap.xml').read_text().replace('https://spawnwp.com',self.base))
            elif route.request.url.startswith(self.base):route.continue_()
            else:route.abort()
        self.context.route('**/*',route_request)
        self.page=self.context.new_page()
        self.errors=[];self.page.on('pageerror',lambda error:self.errors.append(str(error)))
        self.page.add_init_script("""window.copyAttempts=[];
            Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{
                window.copyAttempts.push(text);if(window.copyFails)throw new Error('denied');
            }}});""")

    def tearDown(self):self.context.close()

    def load(self,path='/'):
        self.page.goto(self.base+path)
        self.page.wait_for_function('window._paq && window._paq.some(x=>x[0]==="trackPageView")')
        self.page.wait_for_load_state('networkidle')

    def events(self,action):
        return self.page.evaluate('(action)=>window._paq.filter(x=>x[0]==="trackEvent"&&x[2]===action)',action)

    def test_home_cta_and_successful_copy_without_tracker(self):
        self.load()
        self.page.locator('a[data-seo-funnel="open_install_section"]').click()
        self.assertEqual(len(self.events('open_install_section')),1)
        self.assertEqual(self.page.evaluate('window._paq.filter(x=>x[0]==="trackPageView").length'),1)
        self.page.locator('[data-copy-command]').last.click()
        self.page.wait_for_function('window._paq.some(x=>x[2]==="copy_install_command")')
        self.assertEqual(self.events('copy_install_command'),[['trackEvent','SEO Funnel','copy_install_command','/']])
        self.assertEqual(self.page.evaluate('window.copyAttempts.length'),1)
        self.assertEqual(self.errors,[])

    def test_copy_failures_and_fallback(self):
        self.load();self.page.evaluate('window.copyFails=true')
        self.page.locator('[data-copy-command]').first.click()
        self.assertEqual(self.events('copy_install_command'),[])
        self.page.wait_for_function('!document.querySelector("[data-copy-command]").dataset.copyPending')
        self.page.evaluate("""Object.defineProperty(navigator,'clipboard',{configurable:true,value:undefined});window.execCalls=0;
            document.execCommand=(command)=>{if(command==='copy')window.execCalls++;return true;}""")
        self.page.locator('[data-copy-command]').first.click()
        self.page.wait_for_function('window.execCalls===1')
        self.assertEqual(len(self.events('copy_install_command')),1)

    def test_docs_instant_navigation_and_copy(self):
        self.load('/docs/requirements/')
        self.page.evaluate('window.documentIdentity="original"')
        self.page.locator('a[href$="/installation/"]').first.click()
        self.page.wait_for_url('**/docs/installation/')
        self.page.wait_for_function('window._paq.filter(x=>x[0]==="trackPageView").length===2')
        self.assertEqual(self.page.evaluate('window.documentIdentity'),'original')
        self.assertEqual(self.events('visit_installation'),[['trackEvent','SEO Funnel','visit_installation','/docs/requirements/']])
        self.page.locator('.spawnwp-install-command [data-md-type="copy"]').first.click()
        self.page.wait_for_function('window._paq.some(x=>x[2]==="copy_install_command")')
        self.assertEqual(self.events('copy_install_command')[0][3],'/docs/installation/')
        self.assertEqual(self.page.evaluate('window.copyAttempts.length'),1)
        self.page.evaluate("window.location.hash='measurement-test'")
        self.page.wait_for_url('**#measurement-test')
        self.assertEqual(self.page.evaluate('window._paq.filter(x=>x[0]==="trackPageView").length'),2)
        self.page.go_back()  # hash navigation
        self.page.go_back()  # document navigation
        self.page.wait_for_url('**/docs/requirements/')
        self.page.wait_for_function('window._paq.filter(x=>x[0]==="trackPageView").length===3')
        self.page.go_forward();self.page.wait_for_url('**/docs/installation/')
        self.page.wait_for_function('window._paq.filter(x=>x[0]==="trackPageView").length===4')
        self.assertEqual(self.errors,[])

    def test_other_code_blocks_are_not_installation_copies(self):
        self.load('/docs/installation/')
        self.page.locator('.highlight:not(.spawnwp-install-command) [data-md-type="copy"]').first.click()
        self.assertEqual(self.events('copy_install_command'),[])
        self.assertEqual(self.errors,[])

    def test_video_deduplicates_and_does_not_count_seeking_as_watched(self):
        self.load()
        self.page.evaluate("""() => {
            const v=document.querySelector('video');window.ranges=[];
            Object.defineProperty(v,'duration',{value:100});
            Object.defineProperty(v,'played',{get:()=>({length:window.ranges.length,
                start:i=>window.ranges[i][0],end:i=>window.ranges[i][1]})});
            v.dispatchEvent(new Event('playing'));v.dispatchEvent(new Event('pause'));
            v.dispatchEvent(new Event('playing'));v.dispatchEvent(new Event('seeking'));
            v.dispatchEvent(new Event('seeked'));v.dispatchEvent(new Event('timeupdate'));
        }""")
        self.assertEqual(len(self.events('demo_video_start')),1)
        self.assertEqual(self.events('demo_video_50'),[])
        self.page.evaluate("""() => {
            const v=document.querySelector('video');window.ranges=[[0,20],[50,80]];
            v.dispatchEvent(new Event('timeupdate'));v.dispatchEvent(new Event('timeupdate'));
            v.dispatchEvent(new Event('ended'));v.dispatchEvent(new Event('ended'));
        }""")
        self.assertEqual(len(self.events('demo_video_50')),1)
        self.assertEqual(len(self.events('demo_video_complete')),1)
        self.assertEqual(self.errors,[])

    @unittest.skipUnless(os.environ.get('SPAWNWP_MATOMO_TEST_JS'),'Optional actual Matomo tracker contract check')
    def test_actual_tracker_request_payload(self):
        requests=[]
        def intercept(route):
            if route.request.url.endswith('/matomo.js'):
                route.fulfill(content_type='text/javascript',body=Path(os.environ['SPAWNWP_MATOMO_TEST_JS']).read_text())
            else:
                requests.append(route.request.url+' '+(route.request.post_data or ''))
                route.fulfill(status=204)
        self.context.route('https://stats.presenzaweb.net/**',intercept)
        self.page.goto(self.base+'/')
        self.page.wait_for_function('window.Matomo && window.Matomo.getAsyncTracker')
        self.page.locator('[data-copy-command]').first.click()
        self.page.wait_for_timeout(1000)
        self.assertTrue(any('copy_install_command' in r and 'idsite=6' in r for r in requests),requests)
        self.assertEqual(self.context.cookies(),[])

if __name__=='__main__':unittest.main()
