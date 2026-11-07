import { createServer } from 'node:http';

const para = (s) => `<p>${s} The fixture article keeps going with ordinary prose so that Readability treats it as the main content of the page, which needs a few hundred characters of text.</p>`;

const ARTICLE = `<!doctype html><html><head><title>The Fixture Article</title></head><body>
<nav><a href="/">Home NAVLINK</a> <a href="/about">About</a></nav>
<div class="cookie-banner">We use cookies BANNERTEXT</div>
<article><h1>The Fixture Article</h1>
${para('UNIQUEARTICLEPHRASE starts here.')}
${para('A second paragraph mentions foo_bar_baz and keeps the text long.')}
${para('A third paragraph exists to pad the article further.')}
</article>
<footer>FOOTERTEXT copyright</footer></body></html>`;

const AMD = ARTICLE.replace('<head>', '<head><script>window.define = function () {}; window.define.amd = {};</script>');

const STALLED = ARTICLE.replace('<article>', '<article><img src="/hang">');

const LAZY = `<!doctype html><html><head><title>Lazy</title></head><body><h1>Lazy page</h1><div id="slot"></div>
<script>setTimeout(() => { const p = document.createElement('p'); p.textContent = 'LAZYLOADED content arrived'; document.getElementById('slot').appendChild(p); }, 1500);</script>
</body></html>`;

const CHALLENGE = `<!doctype html><html><head><title>Just a moment...</title></head><body>Checking your browser
<script>setTimeout(() => location.replace('/article.html'), 2000);</script></body></html>`;

const STUCK = '<!doctype html><html><head><title>Just a moment...</title></head><body>Checking your browser</body></html>';

const BUSY = `<!doctype html><html><head><title>Busy</title></head><body><h1>Busy page</h1><p>BUSYCONTENT is here.</p>
<script>setInterval(() => fetch('/ping?' + Date.now()).catch(() => {}), 200);</script></body></html>`;

const CITED = ARTICLE.replace('<head>', `<head><meta charset="utf-8"><meta property="og:site_name" content="Fixture Times"><link rel="canonical" href="/cited.html">
<script type="application/ld+json">${JSON.stringify({ '@graph': [{ '@type': 'NewsArticle', headline: 'A fixture about café data', datePublished: '2026-03-02T23:30:00-05:00', dateModified: '2026-03-03T01:00:00Z', description: 'Short summary.', author: [{ name: 'Ada Lovelace' }, { name: 'Grace Hopper' }] }] })}</script>`);

const HIDDEN_CAPTCHA = ARTICLE.replace('<article>', '<iframe src="/google.com/recaptcha/api2/aframe" style="display:none"></iframe><article>');

const VISIBLE_CAPTCHA = ARTICLE.replace('<article>', '<iframe src="/google.com/recaptcha/api2/anchor?size=normal" width="300" height="80"></iframe><article>');

const ROUTES = {
    '/cited.html': [200, 'text/html', CITED],
    '/captcha-hidden.html': [200, 'text/html', HIDDEN_CAPTCHA],
    '/captcha-visible.html': [200, 'text/html', VISIBLE_CAPTCHA],
    '/article.html': [200, 'text/html', ARTICLE],
    '/private/page.html': [200, 'text/html', ARTICLE],
    '/lazy.html': [200, 'text/html', LAZY],
    '/challenge.html': [200, 'text/html', CHALLENGE],
    '/challenge-stuck.html': [200, 'text/html', STUCK],
    '/busy.html': [200, 'text/html', BUSY],
    '/ping': [204, 'text/plain', ''],
    '/forbidden': [403, 'text/html', '<!doctype html><html><head><title>Forbidden</title></head><body>no</body></html>'],
    '/data.json': [200, 'application/json', JSON.stringify({ hello: 'JSONVALUE', n: 1 })],
    '/robots.txt': [200, 'text/plain', 'User-agent: *\nDisallow: /private/\n'],
    '/amd.html': [200, 'text/html', AMD],
    '/tt.html': [200, 'text/html', ARTICLE, { 'content-security-policy': "require-trusted-types-for 'script'" }],
    '/limited.json': [429, 'application/json', '{"error":"slow down"}'],
    '/big.txt': [200, 'text/plain', 'BIGTEXT '.repeat(500)],
    '/feed.xml': [200, 'text/xml', '<?xml version="1.0"?><rss><channel><title>FEEDTITLE</title></channel></rss>'],
    '/bin': [200, 'application/octet-stream', '\u0000\u0001binary'],
    '/stalled.html': [200, 'text/html', STALLED],
    '/timemap': [200, 'application/link-format', '<https://example.com/a>; rel="original",\n<https://archive.example/20240101/https://example.com/a>; rel="memento"; datetime="Mon, 01 Jan 2024 00:00:00 GMT" LINKFORMATVALUE\n'],
    '/events.ndjson': [200, 'application/x-ndjson', '{"n":1,"v":"NDJSONVALUE"}\n{"n":2}\n'],
    '/sheet.xlsx': [200, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'PK\u0003\u0004binary'],
    '/doc.pdf': [200, 'application/pdf', '%PDF-1.4\n%fixture only, not a real PDF\n'],
};

export function startServer() {
    return new Promise((done) => {
        const server = createServer((req, res) => {
            const path = new URL(req.url, 'http://x').pathname;
            if (path === '/hang') {
                // simulate a stalled subresource: never respond
                return;
            }
            const hop = /^\/r\/(\d+)$/.exec(path);
            if (hop) {
                // /r/N redirects N times, then lands on the article
                const n = Number(hop[1]);
                res.writeHead(302, { location: n > 1 ? `/r/${n - 1}` : '/article.html' });
                res.end();
                return;
            }
            if (path === '/binary-redirect') {
                // Google's Lens upload link answers like this
                res.writeHead(303, { location: '/article.html', 'content-type': 'application/binary' });
                res.end();
                return;
            }
            const hit = ROUTES[path];
            if (!hit) {
                res.writeHead(404, { 'content-type': 'text/plain' });
                res.end('not found');
                return;
            }
            const [status, type, body, headers] = hit;
            res.writeHead(status, { 'content-type': type, ...(headers || {}) });
            res.end(body);
        });
        server.listen(0, '127.0.0.1', () => done({
            base: `http://127.0.0.1:${server.address().port}`,
            close: () => new Promise((r) => {
                server.closeAllConnections();
                server.close(r);
            }),
        }));
    });
}
