import { randomBytes } from 'node:crypto';
import { mkdir, readdir, readFile, rename, rm, utimes, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { dirs } from './paths.mjs';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export function statePath(engine, key) {
    return join(dirs().run, `${engine}-${key}.json`);
}

export function alive(pid) {
    if (!pid) return false;
    try {
        process.kill(pid, 0);
        return true;
    } catch (e) {
        return e.code === 'EPERM';
    }
}

export async function writeState(engine, key, state) {
    await mkdir(dirs().run, { recursive: true, mode: 0o700 });
    const p = statePath(engine, key);
    await writeFile(`${p}.tmp`, JSON.stringify(state, null, 2), { mode: 0o600 });
    await rename(`${p}.tmp`, p);
}

/** Raw /proc cmdline for a pid, NUL separators turned into spaces; '' on error. */
export async function procCmdline(pid) {
    try {
        return (await readFile(`/proc/${pid}/cmdline`, 'utf8')).replace(/\0/g, ' ').trim();
    } catch {
        return '';
    }
}

/** True when supervisorPid is alive and really running the supervisor entry. */
export async function isSupervisor(s) {
    return alive(s.supervisorPid) && (await procCmdline(s.supervisorPid)).includes('--supervise');
}

/** True when browserPid is alive and really holding this state's profile dir. */
export async function isDaemonBrowser(s) {
    return alive(s.browserPid) && (await procCmdline(s.browserPid)).includes(s.profileDir);
}

/** Live daemon state, or null; stale and orphaned state files are removed. */
export async function readState(engine, key) {
    const p = statePath(engine, key);
    let s;
    try {
        s = JSON.parse(await readFile(p, 'utf8'));
    } catch {
        await rm(p, { force: true });
        return null;
    }
    if ((await isSupervisor(s)) && (await isDaemonBrowser(s))) return s;
    if (await isDaemonBrowser(s)) {
        // supervisor died without cleaning up; reap its orphaned browser
        try {
            process.kill(s.browserPid, 'SIGTERM');
        } catch {
            // already gone
        }
        const end = Date.now() + 5000;
        while (Date.now() < end && alive(s.browserPid)) await sleep(100);
        if (alive(s.browserPid)) {
            try {
                process.kill(s.browserPid, 'SIGKILL');
            } catch {
                // already gone
            }
        }
    }
    await rm(p, { force: true });
    return null;
}

export async function removeState(engine, key) {
    await rm(statePath(engine, key), { force: true });
}

export async function touchState(engine, key) {
    const now = new Date();
    try {
        await utimes(statePath(engine, key), now, now);
    } catch {
        // daemon already gone
    }
}

export async function listStates() {
    let names;
    try {
        names = await readdir(dirs().run);
    } catch {
        return [];
    }
    const out = [];
    for (const n of names) {
        const m = /^(chrome|firefox)-(.+)\.json$/.exec(n);
        if (!m) continue;
        const s = await readState(m[1], m[2]);
        if (s) out.push(s);
    }
    return out;
}

/**
 * mkdir lock owned by a pid, reclaimed only when the owner is dead.
 * The pid file holds "pid:token": the token disambiguates two acquisitions
 * by the same pid (e.g. a reclaim within one supervisor process), so an
 * old release() can never drop a lock a later acquisition now holds.
 */
export async function acquireLock(engine, key, timeoutMs = 60000) {
    const lock = `${statePath(engine, key)}.lock`;
    await mkdir(dirs().run, { recursive: true, mode: 0o700 });
    const end = Date.now() + timeoutMs;
    for (;;) {
        const token = `${process.pid}:${randomBytes(4).toString('hex')}`;
        try {
            await mkdir(lock);
            await writeFile(join(lock, 'pid'), token);
            return async () => {
                try {
                    if ((await readFile(join(lock, 'pid'), 'utf8')) === token) await rm(lock, { recursive: true, force: true });
                } catch {
                    // already gone
                }
            };
        } catch (e) {
            if (e.code !== 'EEXIST') throw e;
            let owner = 0;
            try {
                owner = Number((await readFile(join(lock, 'pid'), 'utf8')).split(':')[0]);
            } catch {
                // owner still writing its pid, or lock just released
            }
            if (owner && !alive(owner)) {
                const stale = `${lock}.stale.${process.pid}.${Date.now()}`;
                try {
                    await rename(lock, stale);
                    await rm(stale, { recursive: true, force: true });
                } catch {
                    // another waiter reclaimed it first
                }
                continue;
            }
            if (Date.now() > end) throw new Error(`timed out waiting for the ${engine} daemon lock`);
            await sleep(200);
        }
    }
}
