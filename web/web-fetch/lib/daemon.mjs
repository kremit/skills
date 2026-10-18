import { spawn } from 'node:child_process';
import { closeSync, openSync, statSync } from 'node:fs';
import { mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer-core';
import { ENGINES, launchOptions } from './browser.mjs';
import { dirs } from './paths.mjs';
import { acquireLock, alive, isDaemonBrowser, isSupervisor, readState, removeState, statePath, writeState } from './state.mjs';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const logPath = (engine, key) => statePath(engine, key).replace(/\.json$/, '.log');

/** Fork a detached supervisor holding a warm browser; no-op if one is live. */
export async function startDaemon({ engine, profile, headed = false, display = null, viewport = null, idleMs = 1800000 }) {
    // serializes concurrent starts for the same profile so only one supervisor is ever spawned
    const release = await acquireLock(engine, `${profile.key}.start`, 45000);
    try {
        const existing = await readState(engine, profile.key);
        if (existing) return { state: existing, started: false };
        launchOptions({ engine, profileDir: profile.dir, headed, display, viewport });
        await mkdir(dirs().run, { recursive: true, mode: 0o700 });
        await mkdir(profile.dir, { recursive: true });
        const log = openSync(logPath(engine, profile.key), 'a');
        const config = JSON.stringify({ engine, profile, headed, display, viewport, idleMs });
        const child = spawn(process.execPath, [fileURLToPath(import.meta.url), '--supervise', config], {
            detached: true,
            stdio: ['ignore', log, log],
        });
        closeSync(log);
        child.unref();
        const end = Date.now() + 30000;
        while (Date.now() < end) {
            const s = await readState(engine, profile.key);
            if (s) return { state: s, started: true };
            if (child.exitCode !== null) break;
            await sleep(250);
        }
        throw new Error(`daemon did not start; see ${logPath(engine, profile.key)}`);
    } finally {
        await release();
    }
}

export async function stopDaemon(engine, key) {
    const s = await readState(engine, key);
    if (!s) return false;
    if (await isSupervisor(s)) {
        try {
            process.kill(s.supervisorPid, 'SIGTERM');
        } catch {
            // already exiting
        }
    }
    const end = Date.now() + 10000;
    while (Date.now() < end && (alive(s.supervisorPid) || alive(s.browserPid))) await sleep(200);
    if (await isDaemonBrowser(s)) {
        try {
            process.kill(s.browserPid, 'SIGKILL');
        } catch {
            // gone
        }
    }
    await removeState(engine, key);
    return true;
}

async function supervise({ engine, profile, headed, display, viewport, idleMs }) {
    const browser = await puppeteer.launch(launchOptions({ engine, profileDir: profile.dir, headed, display, viewport, ownSignals: false }));
    const browserPid = browser.process().pid;
    const state = {
        engine, key: profile.key, profile: profile.name, profileDir: profile.dir,
        supervisorPid: process.pid, browserPid, wsEndpoint: browser.wsEndpoint(),
        protocol: ENGINES[engine].protocol, headed: Boolean(headed), idleMs, startedAt: new Date().toISOString(),
    };
    await browser.disconnect();
    await writeState(engine, profile.key, state);
    let stopping = false;
    const shutdown = async () => {
        if (stopping) return;
        stopping = true;
        try {
            process.kill(browserPid, 'SIGTERM');
        } catch {
            // gone
        }
        const end = Date.now() + 5000;
        while (Date.now() < end && alive(browserPid)) await sleep(100);
        if (alive(browserPid)) {
            try {
                process.kill(browserPid, 'SIGKILL');
            } catch {
                // gone
            }
        }
        await removeState(engine, profile.key);
        process.exit(0);
    };
    process.on('SIGTERM', shutdown);
    process.on('SIGINT', shutdown);
    setInterval(() => {
        if (!alive(browserPid)) return shutdown();
        let mtime;
        try {
            mtime = statSync(statePath(engine, profile.key)).mtimeMs;
        } catch {
            return shutdown();
        }
        if (Date.now() - mtime > idleMs) return shutdown();
        return undefined;
    }, 5000);
}

if (process.argv[2] === '--supervise') {
    supervise(JSON.parse(process.argv[3])).catch((e) => {
        console.error(e);
        process.exit(1);
    });
}
