import { readdir, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { extractContent, pageText } from './extract.mjs';
import { missingPhrases } from './util.mjs';

const CHALLENGE_TITLE = 'just a moment|attention required|verify you are human|are you a robot|access denied|checking your browser';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
// HTTP 30x hops on the initial navigation; JS and meta redirects from challenges are not counted
const MAX_REDIRECTS = 3;

const isHtml = (ct) => /html/i.test(ct);
const isRawText = (ct) => !isHtml(ct) && /^text\/|json|javascript|xml/i.test(ct);

export async function detectChallenge(page) {
    try {
        return await page.evaluate((re) => {
            if (new RegExp(re, 'i').test(document.title)) return 'interstitial';
            const srcs = Array.from(document.querySelectorAll('iframe')).map((f) => f.src || '');
            if (srcs.some((s) => s.includes('challenges.cloudflare.com'))) return 'cloudflare';
            if (srcs.some((s) => s.includes('hcaptcha.com'))) return 'hcaptcha';
            if (srcs.some((s) => s.includes('google.com/recaptcha'))) return 'recaptcha';
            return null;
        }, CHALLENGE_TITLE);
    } catch {
        return 'navigating';
    }
}

async function safeText(page) {
    try {
        return await pageText(page);
    } catch {
        return '';
    }
}

async function rawBody(page, response) {
    try {
        return await response.text();
    } catch {
        return page.evaluate(() => {
            const pre = document.querySelector('pre');
            return pre ? pre.innerText : (document.body ? document.body.innerText : '');
        });
    }
}

/**
 * Chrome's own PDF-viewer extension can leave a page wedged mid-navigation so
 * page.close() never resolves (observed for application/pdf with downloads
 * denied); race it so one wedged page can never hang a whole fetch. The browser's
 * own close (cold launch) or the daemon's lifetime eventually reclaims it.
 */
async function closePage(page) {
    await Promise.race([page.close().catch(() => {}), sleep(5000)]);
}

/** Removes anything a browser dropped into its managed downloads dir; keeps the dir itself. */
async function purgeDownloads(downloadsDir) {
    if (!downloadsDir) return;
    let names;
    try {
        names = await readdir(downloadsDir);
    } catch {
        return;
    }
    for (const name of names) {
        await rm(join(downloadsDir, name), { recursive: true, force: true }).catch(() => {});
    }
}

/** Navigate, wait, handle challenges, check sentinels, extract. */
export async function fetchPage(browser, url, opts = {}) {
    const {
        engine = 'chrome', waitFor = 'networkidle', timeoutMs = 30000, scroll = 0, sentinels = [],
        format = 'md', selector = null, noExtract = false, maxChars = 100000, screenshot = null,
        headed = false, challengeWaitMs = 15000, downloadsDir = null,
    } = opts;
    const deadline = Date.now() + timeoutMs;
    const left = () => Math.max(1, deadline - Date.now());
    const result = {
        final_url: url, status: null, content_type: null, title: null, blocked: false, challenge: null,
        sentinel: null, truncated: false, screenshot: null, content: '', error: null,
    };
    const page = await browser.newPage();
    let lastStatus = null;
    let navResponse = null;
    let unsupportedResolve;
    const unsupported = new Promise((resolve) => {
        unsupportedResolve = resolve;
    });
    page.on('response', (r) => {
        try {
            if (r.request().isNavigationRequest() && r.frame() === page.mainFrame()) {
                lastStatus = r.status();
                navResponse = r;
                const ct = r.headers()['content-type'] || '';
                if (ct && !isHtml(ct) && !isRawText(ct)) unsupportedResolve(ct);
            }
        } catch {
            // frame detached mid-navigation
        }
    });
    try {
        if (engine === 'chrome') {
            const ua = await browser.userAgent();
            if (ua.includes('HeadlessChrome')) await page.setUserAgent(ua.replace('HeadlessChrome', 'Chrome'));
        }
        let response;
        try {
            // domcontentloaded always: a non-renderable response never fires load,
            // and the unsupported race below catches it before that can hang.
            response = await Promise.race([
                page.goto(url, { waitUntil: 'domcontentloaded', timeout: left() }),
                unsupported.then((ct) => {
                    throw Object.assign(new Error(ct), { unsupportedType: ct });
                }),
            ]);
        } catch (e) {
            result.final_url = page.url();
            if (!e.unsupportedType && /ERR_ABORTED/.test(e.message)) {
                // a non-renderable response aborts the load; its content-type event can trail the abort
                const ct = await Promise.race([unsupported, sleep(1000).then(() => null)]);
                if (ct) e.unsupportedType = ct;
            }
            if (e.unsupportedType) {
                result.content_type = e.unsupportedType;
                result.status = navResponse ? navResponse.status() : null;
                result.error = `unsupported content-type: ${e.unsupportedType}`;
            } else {
                result.error = `navigation: ${e.message}`;
            }
            return result;
        }
        const hops = response ? response.request().redirectChain().length : 0;
        if (hops > MAX_REDIRECTS) {
            result.final_url = page.url();
            result.status = response.status();
            result.error = `navigation: too many redirects (${hops} > ${MAX_REDIRECTS})`;
            return result;
        }
        const ct = (response && response.headers()['content-type']) || '';
        result.content_type = ct || null;
        if (ct && isRawText(ct)) {
            result.status = response.status();
            result.final_url = page.url();
            result.content = await rawBody(page, response);
            if ([401, 403, 429].includes(result.status)) result.blocked = true;
            if (result.content.length > maxChars) {
                result.content = result.content.slice(0, maxChars);
                result.truncated = true;
            }
            if (sentinels.length) result.sentinel = { phrases: sentinels, missing: missingPhrases(sentinels, result.content), notes: [] };
            return result;
        }
        if (ct && !isHtml(ct)) {
            // safety net: a type that slipped past the pre-goto race
            result.status = response.status();
            result.final_url = page.url();
            result.error = `unsupported content-type: ${ct}`;
            return result;
        }
        const settleEnd = Date.now() + Math.min(10000, left());
        if (waitFor !== 'domcontentloaded') {
            try {
                await page.waitForFunction(() => document.readyState === 'complete', { timeout: Math.max(1, settleEnd - Date.now()) });
            } catch {
                // best effort
            }
        }
        if (waitFor === 'networkidle') {
            try {
                await page.waitForNetworkIdle({ idleTime: 500, concurrency: 2, timeout: Math.max(1, settleEnd - Date.now()) });
            } catch {
                // busy page: proceed with what loaded
            }
        } else if (!['load', 'domcontentloaded', 'networkidle'].includes(waitFor)) {
            try {
                await page.waitForSelector(waitFor, { timeout: Math.max(1, settleEnd - Date.now()) });
            } catch {
                result.error = `wait: selector not found: ${waitFor}`;
            }
        }
        let kind = await detectChallenge(page);
        if (kind === 'navigating') {
            await sleep(500);
            kind = await detectChallenge(page);
        }
        if (kind) {
            if (kind !== 'navigating') result.challenge = kind;
            const end = Date.now() + (headed ? left() : Math.min(challengeWaitMs, left()));
            while (kind && Date.now() < end) {
                await sleep(500);
                kind = await detectChallenge(page);
                if (kind && kind !== 'navigating') result.challenge = kind;
            }
            if (kind && kind !== 'navigating') {
                result.blocked = true;
            } else {
                try {
                    await page.waitForNetworkIdle({ idleTime: 500, timeout: Math.min(5000, left()) });
                } catch {
                    // best effort
                }
            }
        }
        if (headed && !result.challenge) {
            // headed: keep the window open for a manual login or retry until the deadline
            const isWaitError = () => Boolean(result.error && result.error.startsWith('wait:'));
            while (Date.now() < deadline && ([401, 403, 429].includes(lastStatus) || isWaitError())) {
                await sleep(500);
                if (isWaitError() && await page.$(waitFor).catch(() => null)) result.error = null;
            }
        }
        result.status = lastStatus ?? (response ? response.status() : null);
        if (!result.blocked && [401, 403, 429].includes(result.status)) result.blocked = true;
        const prep = async () => {
            if (!result.blocked) {
                for (let i = 0; i < scroll && Date.now() < deadline; i += 1) {
                    await page.evaluate(() => window.scrollTo(0, document.body ? document.body.scrollHeight : 0));
                    await sleep(500);
                }
                while (sentinels.length && Date.now() < deadline && missingPhrases(sentinels, await safeText(page)).length) {
                    await sleep(250);
                }
            }
            // freeze the page like the stop button: pending requests end, the DOM stays
            await page.evaluate(() => window.stop()).catch(() => {});
            return await extractContent(page, { format, selector, noExtract });
        };
        let ex;
        try {
            ex = await prep();
        } catch {
            // a navigation was in flight: let it land, then retry once
            await page.waitForNavigation({ timeout: Math.min(5000, left()) }).catch(() => {});
            try {
                ex = await prep();
            } catch (e2) {
                result.error = result.error || `extract: ${e2.message}`;
                result.final_url = page.url();
                return result;
            }
        }
        if (ex.error) result.error = ex.error;
        result.title = ex.title ?? null;
        let content = ex.content ?? '';
        if (content.length > maxChars) {
            content = content.slice(0, maxChars);
            result.truncated = true;
        }
        if (sentinels.length) {
            const missing = missingPhrases(sentinels, content, { markdown: format === 'md' });
            const stillMissingInPage = new Set(missingPhrases(missing, await safeText(page)));
            result.sentinel = {
                phrases: sentinels,
                missing,
                notes: missing.filter((p) => !stillMissingInPage.has(p)).map((p) => ({ phrase: p, note: 'in page, dropped by extraction' })),
            };
        }
        result.content = content;
        if (screenshot) {
            try {
                await page.screenshot({ path: screenshot, fullPage: true });
                result.screenshot = screenshot;
            } catch (e) {
                if (!result.error) result.error = `screenshot: ${e.message}`;
            }
        }
        result.final_url = page.url();
        return result;
    } finally {
        await closePage(page);
        await purgeDownloads(downloadsDir);
    }
}
