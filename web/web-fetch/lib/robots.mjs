import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import robotsParser from 'robots-parser';

const TOKEN = 'web-fetch';

async function readCache(cacheDir, host, ttlMs) {
    try {
        const e = JSON.parse(await readFile(join(cacheDir, `${encodeURIComponent(host)}.json`), 'utf8'));
        return Date.now() - e.fetched_at < ttlMs ? e : null;
    } catch {
        return null;
    }
}

async function writeCache(cacheDir, host, entry) {
    await mkdir(cacheDir, { recursive: true });
    await writeFile(join(cacheDir, `${encodeURIComponent(host)}.json`), JSON.stringify({ ...entry, fetched_at: Date.now() }));
}

/** Advisory robots.txt report for one URL; never throws. */
export async function checkRobots(url, { userAgent, cacheDir, ttlMs = 3600000, fetchImpl = fetch } = {}) {
    const u = new URL(url);
    const robotsUrl = `${u.protocol}//${u.host}/robots.txt`;
    let entry = await readCache(cacheDir, u.host, ttlMs);
    if (!entry) {
        try {
            const res = await fetchImpl(robotsUrl, { headers: { 'user-agent': userAgent }, redirect: 'manual', signal: AbortSignal.timeout(5000) });
            if (res.status >= 300 && res.status < 400) {
                return { status: 'error', allowed: null, rule: null, crawl_delay: null, error: 'robots redirect not followed' };
            }
            entry = { status: res.status, body: res.ok ? await res.text() : '' };
        } catch (e) {
            return { status: 'error', allowed: null, rule: null, crawl_delay: null, error: String(e.message || e) };
        }
        await writeCache(cacheDir, u.host, entry);
    }
    if (entry.status >= 500) return { status: entry.status, allowed: null, rule: null, crawl_delay: null };
    if (entry.status >= 400 || !entry.body) return { status: entry.status, allowed: true, rule: null, crawl_delay: null };
    const robots = robotsParser(robotsUrl, entry.body);
    const line = robots.getMatchingLineNumber(url, TOKEN);
    return {
        status: entry.status,
        allowed: robots.isAllowed(url, TOKEN) !== false,
        rule: line > 0 ? entry.body.split(/\r?\n/)[line - 1].trim() : null,
        crawl_delay: robots.getCrawlDelay(TOKEN) ?? null,
    };
}
