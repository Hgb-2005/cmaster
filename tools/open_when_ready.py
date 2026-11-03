"""Open only the verified CMaster instance, after HTTP startup succeeds."""
import sys
import time
import urllib.request
import webbrowser

if __name__ == '__main__':
    url = 'http://127.0.0.1:%d' % int(sys.argv[1])
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(60):
        try:
            with opener.open(url + '/healthz', timeout=1) as response:
                if response.headers.get('X-CMaster-App') == 'CMaster':
                    webbrowser.open(url)
                    break
        except OSError:
            pass
        time.sleep(.5)
