import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const NM = join(dirname(fileURLToPath(import.meta.url)), '..', 'node_modules');
const SOURCES = [
    '@mozilla/readability/Readability.js',
    'turndown/lib/turndown.browser.umd.js',
    'turndown-plugin-gfm/dist/turndown-plugin-gfm.js',
].map((p) => readFileSync(join(NM, p), 'utf8')).join('\n;\n');

/* Runs inside the page after SOURCES; Readability, TurndownService and turndownPluginGfm are in scope. */
function inPage(opts) {
    let title = document.title;
    let node;
    let html;
    let text;
    const bodyNode = () => (document.body ? document.body.cloneNode(true) : document.createElement('div'));
    const bodyStrings = () => [document.body ? document.body.innerHTML : '', document.body ? document.body.innerText : ''];
    if (opts.selector) {
        const el = document.querySelector(opts.selector);
        if (!el) return { title, content: '', error: `selector not found: ${opts.selector}` };
        node = el.cloneNode(true);
        html = el.outerHTML;
        text = el.innerText || el.textContent || '';
    } else if (opts.noExtract) {
        node = bodyNode();
        [html, text] = bodyStrings();
    } else {
        const article = new Readability(document.cloneNode(true), { serializer: (el) => el }).parse();
        if (article && article.content) {
            node = article.content;
            html = node.innerHTML;
            text = article.textContent;
            title = article.title || title;
        } else {
            node = bodyNode();
            [html, text] = bodyStrings();
        }
    }
    if (opts.format === 'html') return { title, content: html };
    if (opts.format === 'text') return { title, content: String(text).replace(/\n{3,}/g, '\n\n').trim() };
    try {
        const td = new TurndownService({ headingStyle: 'atx', codeBlockStyle: 'fenced', bulletListMarker: '-' });
        td.use(turndownPluginGfm.gfm);
        td.remove(['script', 'style', 'noscript', 'iframe', 'form', 'nav', 'footer', 'button']);
        return { title, content: td.turndown(node).trim() };
    } catch (e) {
        return { title, content: String(text).trim(), error: `md conversion failed (${e.message}); returned text` };
    }
}

/* Runs inside the page after SOURCES: bibliographic fields, and the article HTML cleaned for printing. */
function inPagePrint(opts) {
    const list = (v) => (v === undefined || v === null ? [] : Array.isArray(v) ? v : [v]);
    const metas = (...names) => names.flatMap((n) => Array.from(document.querySelectorAll(`meta[name="${n}" i], meta[property="${n}" i]`)).map((m) => (m.content || '').trim())).filter(Boolean);
    const objs = [];
    const walk = (o) => {
        if (Array.isArray(o)) o.forEach(walk);
        else if (o && typeof o === 'object') {
            objs.push(o);
            if (o['@graph']) walk(o['@graph']);
        }
    };
    for (const el of document.querySelectorAll('script[type="application/ld+json"]')) {
        try {
            walk(JSON.parse(el.textContent));
        } catch {
            // publishers ship broken JSON-LD often enough
        }
    }
    const ld = objs.find((o) => list(o['@type']).some((t) => /Article|BlogPosting|Report/.test(t))) || {};
    const names = (v) => list(v).map((a) => (typeof a === 'string' ? a : a && a.name)).filter(Boolean);
    const parsed = new Readability(document.cloneNode(true), { serializer: (el) => el }).parse();
    let node = parsed && parsed.content;
    if (opts.selector) {
        const el = document.querySelector(opts.selector);
        node = el ? el.cloneNode(true) : null;
    }
    const byline = parsed && parsed.byline ? parsed.byline.replace(/^by\s+/i, '').split(/\s*(?:,|;|\band\b|&)\s*/).filter(Boolean) : [];
    const site = ld.publisher && names(ld.publisher)[0] || metas('og:site_name', 'citation_journal_title', 'dc.publisher')[0] || (parsed && parsed.siteName) || location.hostname.replace(/^www\./, '');
    const seen = new Set();
    const authors = [...names(ld.author), ...metas('citation_author', 'author', 'parsely-author', 'dc.creator'), ...byline]
        .map((a) => a.trim()).filter((a) => !/^https?:/.test(a) && a.toLowerCase() !== String(site).toLowerCase());
    const unique = authors.filter((a) => !seen.has(a.toLowerCase()) && seen.add(a.toLowerCase()));
    const canonical = document.querySelector('link[rel="canonical"]');
    const meta = {
        title: ld.headline || metas('citation_title')[0] || (document.querySelector('h1') && document.querySelector('h1').innerText.trim()) || metas('og:title')[0] || (parsed && parsed.title) || document.title,
        authors: names(ld.author).length ? names(ld.author) : unique,
        site,
        published: ld.datePublished || metas('article:published_time', 'citation_publication_date', 'citation_date', 'datePublished', 'dc.date', 'date')[0] || (parsed && parsed.publishedTime) || null,
        modified: ld.dateModified || metas('article:modified_time', 'dateModified')[0] || null,
        description: ld.description || metas('description', 'og:description')[0] || (parsed && parsed.excerpt) || null,
        canonical: canonical && canonical.href && new URL(canonical.href).host === location.host ? canonical.href : null,
    };
    if (!node) return { meta, article: null };
    node.querySelectorAll('script, style, noscript, iframe, form, nav, button, object, embed, video, audio, source, link, meta, base, [aria-hidden="true"]').forEach((el) => el.remove());
    for (const el of [node, ...node.querySelectorAll('*')]) {
        for (const a of Array.from(el.attributes)) {
            if (/^on|^style$|^class$|^id$|^loading$|^data-/i.test(a.name) || /^\s*javascript:/i.test(a.value)) el.removeAttribute(a.name);
        }
    }
    return { meta, article: { html: node.innerHTML, lang: document.documentElement.lang || null } };
}

/** Bibliographic metadata plus sanitized article HTML; article is null when nothing readable was found. */
export async function printableArticle(page, { selector = null } = {}) {
    return page.evaluate(`(function () {\nvar define, module, exports;\n${SOURCES}\n;return (${inPagePrint.toString()})(${JSON.stringify({ selector })});\n})()`);
}

/** Scrolls through the page once so lazy images load, then waits briefly for them. */
export async function loadLazyImages(page, maxMs) {
    await page.evaluate(async (limit) => {
        const end = Date.now() + limit;
        const pause = (ms) => new Promise((r) => setTimeout(r, ms));
        document.querySelectorAll('img[loading="lazy"]').forEach((img) => img.setAttribute('loading', 'eager'));
        for (let i = 0; i < 40 && Date.now() < end && window.scrollY + window.innerHeight < document.documentElement.scrollHeight; i += 1) {
            window.scrollBy(0, window.innerHeight);
            await pause(120);
        }
        window.scrollTo(0, 0);
        while (Date.now() < end && Array.from(document.images).some((img) => !img.complete)) await pause(100);
    }, maxMs);
}

export async function extractContent(page, { format = 'md', selector = null, noExtract = false } = {}) {
    const opts = JSON.stringify({ format, selector, noExtract });
    return page.evaluate(`(function () {\nvar define, module, exports;\n${SOURCES}\n;return (${inPage.toString()})(${opts});\n})()`);
}

export async function pageText(page) {
    return page.evaluate(() => (document.body ? document.body.innerText : ''));
}
