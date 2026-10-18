export class UsageError extends Error {}

const UNITS = { ms: 1, s: 1000, m: 60000, h: 3600000 };

/** "500ms", "30s", "3m", "2h" or plain milliseconds. */
export function parseDuration(value) {
    if (typeof value === 'number') return value;
    const m = /^(\d+(?:\.\d+)?)(ms|s|m|h)?$/.exec(String(value).trim());
    if (!m) throw new UsageError(`bad duration: ${value}`);
    return Math.round(Number(m[1]) * UNITS[m[2] || 'ms']);
}

/** Lowercase, collapse whitespace; for markdown also drop links, escapes and emphasis. */
export function normalizeForMatch(text, { markdown = false } = {}) {
    let t = String(text ?? '');
    if (markdown) {
        t = t
            .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1')
            .replace(/\\([\\`*_{}[\]()#+\-.!>|~])/g, '$1')
            .replace(/\*\*|__/g, '')
            .replace(/(^|[\s(])[*_]|[*_](?=[\s).,;:!?]|$)/g, '$1');
    }
    return t.replace(/\s+/g, ' ').trim().toLowerCase();
}

export function missingPhrases(phrases, text, { markdown = false } = {}) {
    const hay = normalizeForMatch(text, { markdown });
    return phrases.filter((p) => !hay.includes(normalizeForMatch(p)));
}
