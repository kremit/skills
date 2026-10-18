import { createHash } from 'node:crypto';
import { realpathSync } from 'node:fs';
import { cp, mkdir, readFile, rm, stat, writeFile } from 'node:fs/promises';
import os from 'node:os';
import { basename, dirname, join, resolve } from 'node:path';
import { dirs } from './paths.mjs';
import { UsageError } from './util.mjs';

const EXCLUDE = new Set([
    'Cache', 'Code Cache', 'GPUCache', 'CacheStorage', 'DawnCache', 'GrShaderCache', 'ShaderCache',
    'cache2', 'startupCache', 'parent.lock', 'lock', '.parentlock',
    'Login Data', 'Login Data-journal', 'Login Data For Account', 'Login Data For Account-journal',
    'Web Data', 'Web Data-journal', 'logins.json', 'logins-backup.json', 'key4.db', 'formhistory.sqlite',
]);

function liveRoots() {
    const h = os.homedir();
    return [join(h, '.config/google-chrome'), join(h, '.config/chromium'), join(h, '.mozilla/firefox')];
}

function real(p) {
    try {
        return realpathSync(p);
    } catch {
        return resolve(p);
    }
}

export function isLiveProfile(dir) {
    const d = real(dir);
    return liveRoots().some((r) => d === real(r) || d.startsWith(real(r) + '/'));
}

/** NAME maps to a managed dir; anything with a slash is a custom path. */
export function resolveProfile(browser, arg = 'default') {
    if (arg.includes('/')) {
        const dir = resolve(arg.replace(/^~(?=\/)/, os.homedir()));
        if (isLiveProfile(dir)) {
            throw new UsageError(`refusing live browser profile ${dir}; use --clone-profile to work on a copy`);
        }
        const key = 'path-' + createHash('sha1').update(dir).digest('hex').slice(0, 8);
        return { name: basename(dir), key, dir, managed: false };
    }
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(arg)) throw new UsageError(`bad profile name: ${arg}`);
    return { name: arg, key: arg, dir: join(dirs().profiles, browser, arg), managed: true };
}

export function firefoxDefaultFromIni(ini, base) {
    const sections = [];
    let cur = null;
    for (const raw of ini.split(/\r?\n/)) {
        const line = raw.trim();
        const sec = /^\[(.+)\]$/.exec(line);
        if (sec) {
            cur = { name: sec[1], kv: {} };
            sections.push(cur);
            continue;
        }
        const kv = /^([^=]+)=(.*)$/.exec(line);
        if (kv && cur) cur.kv[kv[1]] = kv[2];
    }
    const install = sections.find((s) => s.name.startsWith('Install') && s.kv.Default);
    if (install) return join(base, install.kv.Default);
    const prof = sections.find((s) => s.name.startsWith('Profile') && s.kv.Default === '1')
        || sections.find((s) => s.name.startsWith('Profile'));
    if (!prof) throw new UsageError('no Firefox profile found in profiles.ini');
    return prof.kv.IsRelative === '0' ? prof.kv.Path : join(base, prof.kv.Path);
}

export async function defaultCloneSource(browser) {
    const h = os.homedir();
    if (browser === 'chrome') return join(h, '.config/google-chrome/Default');
    const base = join(h, '.mozilla/firefox');
    return firefoxDefaultFromIni(await readFile(join(base, 'profiles.ini'), 'utf8'), base);
}

const keep = (p) => {
    const n = basename(p);
    return !EXCLUDE.has(n) && !n.startsWith('Singleton');
};

/** Snapshot a real profile into a managed one. The source is only read. */
export async function cloneProfile(browser, src, profile) {
    if (!profile.managed) throw new UsageError('--clone-profile needs a profile NAME, not a path');
    const root = real(join(dirs().profiles, browser));
    if (!real(profile.dir).startsWith(root + '/')) throw new UsageError(`refusing to clone outside ${root}`);
    try {
        await stat(src);
    } catch {
        throw new UsageError(`clone source not found: ${src}`);
    }
    await rm(profile.dir, { recursive: true, force: true });
    await mkdir(profile.dir, { recursive: true });
    if (browser === 'chrome') {
        await cp(src, join(profile.dir, 'Default'), { recursive: true, filter: keep });
        try {
            await cp(join(dirname(src), 'Local State'), join(profile.dir, 'Local State'));
        } catch {
            // Local State is optional; cookies decrypt from the keyring on Linux
        }
    } else {
        await cp(src, profile.dir, { recursive: true, filter: keep });
    }
    await writeFile(join(profile.dir, '.web-fetch-clone.json'), JSON.stringify({ source: src, cloned_at: new Date().toISOString() }));
    return { source: src, dir: profile.dir };
}
