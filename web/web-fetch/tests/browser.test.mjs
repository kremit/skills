import { describe, test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import puppeteer from 'puppeteer-core';
import { launchOptions } from '../lib/browser.mjs';
import { fetchPage } from '../lib/fetch.mjs';
import { startServer } from './fixtures/server.mjs';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function filesUnder(root) {
    let out = [];
    for (const e of readdirSync(root, { withFileTypes: true })) {
        if (e.isSymbolicLink()) continue; // Firefox's own profile "lock" is a symlink to a nonexistent target
        const p = join(root, e.name);
        if (e.isDirectory()) out = out.concat(filesUnder(p));
        else if (e.isFile()) out.push(p);
    }
    return out;
}

for (const engine of ['chrome', 'firefox']) {
    describe(`fetchPage (${engine})`, () => {
        let srv;
        let browser;
        let dir;
        const go = (path, opts = {}) => fetchPage(browser, srv.base + path, { engine, timeoutMs: 20000, ...opts });

        before(async () => {
            srv = await startServer();
            dir = mkdtempSync(join(tmpdir(), `wf-${engine}-`));
            browser = await puppeteer.launch(launchOptions({ engine, profileDir: dir, viewport: '1600x1000' }));
        });
        after(async () => {
            await browser?.close().catch(() => {});
            await srv?.close();
            if (dir) rmSync(dir, { recursive: true, force: true });
        });

        test('article extracts the main content as markdown', async () => {
            const r = await go('/article.html');
            assert.equal(r.status, 200);
            assert.equal(r.title, 'The Fixture Article');
            assert.match(r.content, /UNIQUEARTICLEPHRASE/);
            assert.doesNotMatch(r.content, /NAVLINK|FOOTERTEXT/);
        });

        test('sentinel waits for lazy content', async () => {
            const r = await go('/lazy.html', { sentinels: ['LAZYLOADED'], noExtract: true });
            assert.deepEqual(r.sentinel.missing, []);
            assert.match(r.content, /LAZYLOADED/);
        });

        test('missing sentinel and dropped-by-extraction note', async () => {
            const r = await go('/article.html', { sentinels: ['NOT-ON-PAGE', 'NAVLINK', 'foo_bar_baz'], timeoutMs: 4000 });
            assert.deepEqual(r.sentinel.missing, ['NOT-ON-PAGE', 'NAVLINK']);
            assert.deepEqual(r.sentinel.notes, [{ phrase: 'NAVLINK', note: 'in page, dropped by extraction' }]);
        });

        test('challenge that clears is followed', async () => {
            const r = await go('/challenge.html');
            assert.equal(r.challenge, 'interstitial');
            assert.equal(r.blocked, false);
            assert.match(r.content, /UNIQUEARTICLEPHRASE/);
        });

        test('challenge that never clears is blocked', async () => {
            const r = await go('/challenge-stuck.html', { challengeWaitMs: 3000 });
            assert.deepEqual([r.blocked, r.challenge], [true, 'interstitial']);
        });

        test('403 is blocked', async () => {
            const r = await go('/forbidden');
            assert.deepEqual([r.status, r.blocked], [403, true]);
        });

        test('up to 3 HTTP redirects are followed, a 4th is refused', async () => {
            const ok = await go('/r/3');
            assert.equal(ok.error, null);
            assert.match(ok.content, /UNIQUEARTICLEPHRASE/);
            const tooMany = await go('/r/4');
            assert.match(tooMany.error, /^navigation: too many redirects \(4 > 3\)/);
            assert.equal(tooMany.content, '');
        });

        test('busy page that never goes idle still loads', async () => {
            const started = Date.now();
            const r = await go('/busy.html');
            assert.match(r.content, /BUSYCONTENT/);
            assert.equal(r.error, null);
            assert.ok(Date.now() - started < 15000);
        });

        test('JSON comes back as raw text', async () => {
            const r = await go('/data.json');
            assert.match(r.content_type, /json/);
            assert.deepEqual(JSON.parse(r.content), { hello: 'JSONVALUE', n: 1 });
        });

        test('AMD loader page still extracts markdown', async () => {
            const r = await go('/amd.html');
            assert.match(r.content, /UNIQUEARTICLEPHRASE/);
            assert.equal(r.error, null);
        });

        test('Trusted Types page still extracts markdown', async () => {
            const r = await go('/tt.html');
            assert.match(r.content, /UNIQUEARTICLEPHRASE/);
            assert.equal(r.error, null);
        });

        test('raw text responses follow blocked, truncation and xml rules', async () => {
            const limited = await go('/limited.json');
            assert.deepEqual([limited.status, limited.blocked], [429, true]);

            const big = await go('/big.txt', { maxChars: 100 });
            assert.equal(big.truncated, true);
            assert.equal(big.content.length, 100);

            const feed = await go('/feed.xml');
            assert.match(feed.content, /FEEDTITLE/);
            assert.match(feed.content, /<rss/);
        });

        test('binary responses are unsupported and never downloaded', async () => {
            const marker = Buffer.from('\u0000\u0001binary', 'utf8');
            const downloadsDir = join(dir, 'downloads');
            const started = Date.now();
            const r = await go('/bin', { downloadsDir });
            assert.match(r.error, /^unsupported content-type: application\/octet-stream/);
            assert.ok(Date.now() - started < 10000);
            if (engine === 'firefox') {
                // Firefox writes its own profile files lazily, so scan content rather than diff the file set
                for (const f of filesUnder(dir).filter((f) => !f.startsWith(downloadsDir))) assert.ok(!readFileSync(f).includes(marker), f);
                assert.ok(!existsSync(downloadsDir) || readdirSync(downloadsDir).length === 0, 'downloads/ is purged');
            }
        });

        if (engine === 'chrome') {
            test('Chrome never writes to the real HOME Downloads directory', async () => {
                const home = mkdtempSync(join(tmpdir(), 'wfh-'));
                const probeDir = mkdtempSync(join(tmpdir(), `wf-${engine}-probe-`));
                let probeBrowser;
                try {
                    const opts = launchOptions({ engine, profileDir: probeDir, viewport: '1600x1000' });
                    opts.env = { ...opts.env, HOME: home };
                    probeBrowser = await puppeteer.launch(opts);
                    await fetchPage(probeBrowser, srv.base + '/bin', { engine, timeoutMs: 20000 });
                    await fetchPage(probeBrowser, srv.base + '/doc.pdf', { engine, timeoutMs: 20000 });
                    await sleep(2000);
                    const downloads = join(home, 'Downloads');
                    assert.ok(!existsSync(downloads) || readdirSync(downloads).length === 0, 'no file landed in HOME/Downloads');
                } finally {
                    await probeBrowser?.close().catch(() => {});
                    rmSync(home, { recursive: true, force: true });
                    rmSync(probeDir, { recursive: true, force: true });
                }
            });
        }

        test('a stalled subresource does not fail the page', async () => {
            const started = Date.now();
            const r = await go('/stalled.html');
            assert.match(r.content, /UNIQUEARTICLEPHRASE/);
            assert.equal(r.error, null);
            assert.ok(Date.now() - started < 15000);
        });

        test('viewport matches the requested size', async () => {
            const page = await browser.newPage();
            await page.goto(srv.base + '/article.html');
            assert.equal(await page.evaluate(() => window.innerWidth), 1600);
            if (engine === 'chrome') {
                const ua = await browser.userAgent();
                await page.setUserAgent(ua.replace('HeadlessChrome', 'Chrome'));
                await page.goto(srv.base + '/article.html');
                assert.doesNotMatch(await page.evaluate(() => navigator.userAgent), /HeadlessChrome/);
            }
            await page.close();
        });
    });
}
