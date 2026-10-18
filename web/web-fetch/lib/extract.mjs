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

export async function extractContent(page, { format = 'md', selector = null, noExtract = false } = {}) {
    const opts = JSON.stringify({ format, selector, noExtract });
    return page.evaluate(`(function () {\nvar define, module, exports;\n${SOURCES}\n;return (${inPage.toString()})(${opts});\n})()`);
}

export async function pageText(page) {
    return page.evaluate(() => (document.body ? document.body.innerText : ''));
}
