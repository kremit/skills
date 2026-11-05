import { writeFile } from 'node:fs/promises';
import { PDFDict, PDFDocument, PDFHexString, PDFName } from 'pdf-lib';
import { printableArticle } from './extract.mjs';

const MONTHS = ['Jan.', 'Feb.', 'Mar.', 'Apr.', 'May', 'June', 'July', 'Aug.', 'Sept.', 'Oct.', 'Nov.', 'Dec.'];
const MINOR = new Set('a an and as at but by for from if in into nor of on or per so than the to up via vs with yet'.split(' '));
const SUFFIX = /^(?:jr|sr|ii|iii|iv|phd|md)\.?$/i;

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const mlaDate = (y, m, d) => [d, m === undefined ? null : MONTHS[m], y].filter((x) => x !== null && x !== undefined).join(' ');

/** Publisher's own calendar date from an ISO or date-like string, or null. */
export function mlaPublished(value) {
    if (!value) return null;
    const iso = /^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?/.exec(value);
    if (iso) return mlaDate(Number(iso[1]), iso[2] ? Number(iso[2]) - 1 : undefined, iso[3] ? Number(iso[3]) : undefined);
    const t = new Date(value);
    return Number.isNaN(t.getTime()) ? null : mlaDate(t.getFullYear(), t.getMonth(), t.getDate());
}

/** MLA title case: capitalizes words only, so AI and iPhone stay as written. */
export function titleCase(title) {
    const words = String(title).trim().split(/\s+/);
    return words.map((w, i) => {
        const first = i === 0 || /[:?!]$/.test(words[i - 1]);
        const bare = w.replace(/^[^\p{L}\p{N}]+/u, '');
        if (!bare || /\p{Lu}/u.test(bare) || /\d/.test(bare)) return w;
        if (!first && i !== words.length - 1 && MINOR.has(bare.toLowerCase())) return w.toLowerCase();
        const at = w.length - bare.length;
        return w.slice(0, at) + w[at].toUpperCase() + w.slice(at + 1);
    }).join(' ');
}

function invertName(name) {
    const parts = name.trim().split(/\s+/);
    if (parts.length < 2 || parts.length > 4 || /\d/.test(name)) return name.trim();
    const suffix = SUFFIX.test(parts[parts.length - 1]) ? parts.pop() : null;
    const last = parts.pop();
    return `${last}, ${parts.join(' ')}${suffix ? `, ${suffix}` : ''}`;
}

function mlaAuthors(authors) {
    if (!authors.length) return '';
    let s = invertName(authors[0]);
    if (authors.length === 2) s += `, and ${authors[1].trim()}`;
    if (authors.length > 2) s += ', et al';
    return s.endsWith('.') ? s : `${s}.`;
}

/** MLA 9 web citation as plain text, plus HTML with the container in italics. */
export function mlaCitation(meta, accessed) {
    const title = titleCase(meta.title || meta.site || meta.url);
    const quoted = `"${title}${/[.?!]$/.test(title) ? '' : '.'}"`;
    const url = meta.url.replace(/^https?:\/\//i, '').replace(/\/$/, '');
    const date = mlaPublished(meta.published);
    const tail = [date, url].filter(Boolean).join(', ');
    const seen = mlaDate(accessed.getFullYear(), accessed.getMonth(), accessed.getDate());
    const author = mlaAuthors(meta.authors);
    const accessedOn = `Accessed ${seen}.`;
    return {
        text: [author, quoted, `${meta.site ? `${meta.site}, ` : ''}${tail}.`, accessedOn].filter(Boolean).join(' '),
        html: [esc(author), esc(quoted), `${meta.site ? `<i>${esc(meta.site)}</i>, ` : ''}${esc(tail)}.`, accessedOn].filter(Boolean).join(' '),
    };
}

/** The clean layout for --pdf-style reader. Runs in a blank page with scripts off. */
function readerHtml(article, meta, citation, capturedAt) {
    const when = mlaPublished(meta.published);
    const byline = [meta.authors.join(', '), when].filter(Boolean).join(' · ');
    return `<!doctype html><html lang="${esc(article.lang || 'en')}"><head><meta charset="utf-8"><title>${esc(meta.title)}</title>
<base href="${esc(meta.url)}"><style>
body { font: 11pt/1.5 Georgia, 'Times New Roman', serif; color: #111; margin: 0; }
.site { font: 600 9pt/1.2 Helvetica, Arial, sans-serif; letter-spacing: .08em; text-transform: uppercase; color: #555; }
h1 { font: 700 22pt/1.2 Helvetica, Arial, sans-serif; margin: .3em 0 .25em; }
.byline { font: 9.5pt/1.3 Helvetica, Arial, sans-serif; color: #444; margin-bottom: 1.5em; }
h2, h3, h4 { font-family: Helvetica, Arial, sans-serif; line-height: 1.25; break-after: avoid; }
p, li, blockquote { orphans: 3; widows: 3; }
img, svg { max-width: 100%; height: auto; display: block; margin: .5em auto; break-inside: avoid; }
figure { margin: 1em 0; break-inside: avoid; }
figcaption { font: 8.5pt/1.3 Helvetica, Arial, sans-serif; color: #555; }
blockquote { margin: 1em 0; padding-left: 1em; border-left: 2px solid #bbb; }
pre, code { font: 9pt/1.4 'DejaVu Sans Mono', monospace; white-space: pre-wrap; overflow-wrap: anywhere; }
table { border-collapse: collapse; width: 100%; font-size: 9.5pt; }
th, td { border: 1px solid #bbb; padding: .25em .4em; text-align: left; }
a { color: inherit; }
.cite { margin-top: 2em; padding-top: .8em; border-top: 1px solid #999; font: 9pt/1.4 Georgia, serif; break-inside: avoid; }
.cite p { margin: .4em 0; overflow-wrap: anywhere; }
.cite .work { padding-left: 2em; text-indent: -2em; }
</style></head><body>
<div class="site">${esc(meta.site)}</div><h1>${esc(meta.title)}</h1><div class="byline">${esc(byline)}</div>
<main>${article.html}</main>
<div class="cite"><p><b>Work Cited</b></p><p class="work">${citation.html}</p>
<p>Captured ${esc(capturedAt)} from ${esc(meta.url)}</p></div></body></html>`;
}

const footer = (url) => `<div style="font: 7pt Helvetica, Arial, sans-serif; color: #555; width: 100%; margin: 0 .5in; display: flex; justify-content: space-between;">
<span style="overflow: hidden; white-space: nowrap; text-overflow: ellipsis; max-width: 80%;">${esc(url)}</span>
<span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span></div>`;

function xmp(meta, citation, capturedAt) {
    const li = (s) => `<rdf:li>${esc(s)}</rdf:li>`;
    const lang = (s) => `<rdf:Alt><rdf:li xml:lang="x-default">${esc(s)}</rdf:li></rdf:Alt>`;
    const tag = (name, value, body = esc(value)) => (value ? `<${name}>${body}</${name}>` : '');
    return `<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmlns:pdf="http://ns.adobe.com/pdf/1.3/">
${tag('dc:title', meta.title, lang(meta.title))}
${tag('dc:description', meta.description, lang(meta.description))}
${meta.authors.length ? `<dc:creator><rdf:Seq>${meta.authors.map(li).join('')}</rdf:Seq></dc:creator>` : ''}
${meta.site ? `<dc:publisher><rdf:Bag>${li(meta.site)}</rdf:Bag></dc:publisher>` : ''}
${tag('dc:source', meta.url)}${tag('dc:identifier', meta.url)}
${meta.published ? `<dc:date><rdf:Seq>${li(meta.published)}</rdf:Seq></dc:date>` : ''}
${tag('dcterms:issued', meta.published)}${tag('dcterms:modified', meta.modified)}
${tag('dcterms:created', capturedAt)}${tag('dcterms:bibliographicCitation', citation.text)}
${tag('xmp:CreateDate', capturedAt)}${tag('xmp:ModifyDate', capturedAt)}${tag('xmp:MetadataDate', capturedAt)}
<xmp:CreatorTool>web-fetch</xmp:CreatorTool><pdf:Producer>web-fetch</pdf:Producer>
</rdf:Description></rdf:RDF></x:xmpmeta>
<?xpacket end="w"?>`;
}

/** Replaces the browser's thin Info dict and adds an XMP packet plus custom Info keys. */
export async function stampMetadata(bytes, meta, citation, capturedAt) {
    const doc = await PDFDocument.load(bytes, { updateMetadata: false });
    const when = new Date(capturedAt);
    doc.setTitle(meta.title || meta.url, { showInWindowTitleBar: true });
    doc.setAuthor(meta.authors.join('; '));
    doc.setSubject(meta.description || citation.text);
    doc.setKeywords([meta.site, meta.url].filter(Boolean));
    doc.setCreator('web-fetch');
    doc.setProducer('web-fetch');
    doc.setCreationDate(when);
    doc.setModificationDate(when);
    const info = doc.context.lookup(doc.context.trailerInfo.Info, PDFDict);
    const custom = {
        SourceURL: meta.url, RequestedURL: meta.requested, CapturedAt: capturedAt, PublishedAt: meta.published,
        ModifiedAt: meta.modified, SiteName: meta.site, Citation: citation.text, CitationStyle: 'MLA 9',
    };
    for (const [k, v] of Object.entries(custom)) {
        if (v) info.set(PDFName.of(k), PDFHexString.fromText(v));
    }
    const stream = doc.context.stream(new TextEncoder().encode(xmp(meta, citation, capturedAt)), { Type: 'Metadata', Subtype: 'XML' });
    doc.catalog.set(PDFName.of('Metadata'), doc.context.register(stream));
    return { bytes: await doc.save({ useObjectStreams: false, updateFieldAppearances: false }), pages: doc.getPageCount() };
}

/** Adds the citation after the page's own content for --pdf-style page. */
async function appendCitation(page, citation) {
    await page.evaluate((html) => {
        const div = document.createElement('div');
        div.style.cssText = 'margin:2em 1em;padding-top:.8em;border-top:1px solid #999;font:9pt/1.4 Georgia,serif;color:#111;background:#fff';
        div.innerHTML = html;
        document.body.append(div);
    }, `<b>Work Cited</b><br>${citation.html}`);
}

/** Prints the loaded page, stamps its metadata and writes it to path. */
export async function renderPdf(browser, page, { path, style = 'reader', selector = null, finalUrl, requestedUrl, fetchedAt, timeoutMs = 30000 }) {
    const capturedAt = fetchedAt || new Date().toISOString();
    const { meta: found, article } = await printableArticle(page, { selector });
    const meta = { ...found, requested: requestedUrl };
    meta.url = found.canonical || finalUrl;
    const citation = mlaCitation(meta, new Date(capturedAt));
    const used = style === 'reader' && article ? 'reader' : 'page';
    const options = {
        format: 'letter', printBackground: true, timeout: timeoutMs, displayHeaderFooter: true, headerTemplate: '<span></span>',
        footerTemplate: footer(meta.url), margin: { top: '0.7in', bottom: '0.7in', left: '0.8in', right: '0.8in' },
    };
    let raw;
    if (used === 'reader') {
        const blank = await browser.newPage();
        try {
            await blank.setJavaScriptEnabled(false).catch(() => {});
            await blank.setContent(readerHtml(article, meta, citation, capturedAt), { waitUntil: 'load', timeout: Math.min(timeoutMs, 20000) }).catch(() => {});
            raw = await blank.pdf(options);
        } finally {
            await blank.close().catch(() => {});
        }
    } else {
        await appendCitation(page, citation);
        raw = await page.pdf(options);
    }
    const out = await stampMetadata(raw, meta, citation, capturedAt);
    await writeFile(path, out.bytes);
    return {
        path, style: used, pages: out.pages, bytes: out.bytes.length, title: meta.title, authors: meta.authors, site: meta.site,
        published: meta.published, modified: meta.modified, source_url: meta.url, captured_at: capturedAt, citation: citation.text,
    };
}
