import { lookup } from 'node:dns/promises';
import { isIP } from 'node:net';

/** ::ffff:a.b.c.d or ::ffff:XXXX:YYYY to dotted IPv4; anything else unchanged. */
function unmapV4(a) {
    if (!a.startsWith('::ffff:')) return a;
    const rest = a.slice(7);
    if (isIP(rest) === 4) return rest;
    const m = /^([0-9a-f]{1,4}):([0-9a-f]{1,4})$/.exec(rest);
    if (!m) return a;
    const hi = parseInt(m[1], 16);
    const lo = parseInt(m[2], 16);
    return `${hi >> 8}.${hi & 255}.${lo >> 8}.${lo & 255}`;
}

export function isPrivateAddress(addr) {
    const a = unmapV4(String(addr).toLowerCase());
    if (isIP(a) === 4) {
        const [x, y] = a.split('.').map(Number);
        return x === 10 || x === 127 || x === 0
            || (x === 172 && y >= 16 && y <= 31)
            || (x === 192 && y === 168)
            || (x === 169 && y === 254)
            || (x === 100 && y >= 64 && y <= 127);
    }
    if (isIP(a) === 6) {
        return a === '::1' || a === '::' || /^f[cd]/.test(a) || /^fe[89ab]/.test(a);
    }
    return false;
}

/** Scheme check plus private-target check on every resolved address. */
export async function checkUrl(url, { allowPrivate = false, lookupImpl = lookup } = {}) {
    let u;
    try {
        u = new URL(url);
    } catch {
        return { ok: false, kind: 'usage', reason: `invalid url: ${url}` };
    }
    if (u.protocol !== 'http:' && u.protocol !== 'https:') {
        return { ok: false, kind: 'usage', reason: `unsupported scheme ${u.protocol}` };
    }
    if (allowPrivate) return { ok: true };
    const host = u.hostname.replace(/^\[|\]$/g, '');
    let addrs;
    if (isIP(host)) {
        addrs = [host];
    } else {
        try {
            addrs = (await lookupImpl(host, { all: true })).map((r) => r.address);
        } catch {
            return { ok: true };
        }
    }
    const bad = addrs.find(isPrivateAddress);
    return bad ? { ok: false, kind: 'private', reason: 'private address', address: bad } : { ok: true };
}
