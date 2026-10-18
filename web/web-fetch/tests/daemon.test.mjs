import { describe, test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, existsSync, rmSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { execSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { startDaemon, stopDaemon } from '../lib/daemon.mjs';
import { openBrowser } from '../lib/browser.mjs';
import { fetchPage } from '../lib/fetch.mjs';
import { resolveProfile } from '../lib/profile.mjs';
import { alive, readState, statePath } from '../lib/state.mjs';
import { startServer } from './fixtures/server.mjs';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

for (const engine of ['chrome', 'firefox']) {
    describe(`daemon (${engine})`, () => {
        let srv;
        let profile;
        before(async () => {
            process.env.WEB_FETCH_HOME = mkdtempSync(join(tmpdir(), 'wf-daemon-'));
            srv = await startServer();
            profile = resolveProfile(engine, 'd1');
        });
        after(async () => {
            if (profile) await stopDaemon(engine, profile.key).catch(() => {});
            await srv?.close();
            rmSync(process.env.WEB_FETCH_HOME, { recursive: true, force: true });
        });

        test('start, reuse across fetches including concurrent ones, stop', async () => {
            const { state, started } = await startDaemon({ engine, profile, idleMs: 60000, viewport: '1600x1000' });
            assert.equal(started, true);
            assert.ok(alive(state.browserPid));
            assert.equal((await startDaemon({ engine, profile, idleMs: 60000 })).started, false, 'second start is a no-op');

            const once = async () => {
                const h = await openBrowser({ engine, profile });
                try {
                    const r = await fetchPage(h.browser, `${srv.base}/article.html`, { engine, timeoutMs: 20000 });
                    return [h.viaDaemon, h.pid, /UNIQUEARTICLEPHRASE/.test(r.content)];
                } finally {
                    await h.close();
                }
            };
            const results = await Promise.all([once(), once()]);
            for (const r of results) assert.deepEqual(r, [true, state.browserPid, true]);

            assert.equal(await stopDaemon(engine, profile.key), true);
            assert.ok(!existsSync(statePath(engine, profile.key)));
            assert.ok(!alive(state.browserPid));
        });

        test('concurrent --daemonize on one profile starts exactly one daemon', async () => {
            const profile2 = resolveProfile(engine, 'd2');
            let a;
            let b;
            try {
                [a, b] = await Promise.all([
                    startDaemon({ engine, profile: profile2, idleMs: 60000 }),
                    startDaemon({ engine, profile: profile2, idleMs: 60000 }),
                ]);
                assert.equal([a.started, b.started].filter(Boolean).length, 1);
                assert.equal(a.state.browserPid, b.state.browserPid);
            } finally {
                await stopDaemon(engine, profile2.key);
            }
            const out = execSync("pgrep -f 'daemon.mjs --supervise' || true").toString().trim();
            const pids = out ? out.split('\n') : [];
            for (const pid of pids) {
                const cmdline = await readFile(`/proc/${pid}/cmdline`, 'utf8').catch(() => '');
                assert.ok(!cmdline.includes(process.env.WEB_FETCH_HOME));
            }
            assert.ok(!alive(a.state.browserPid));
            assert.ok(!alive(a.state.supervisorPid));
        });

        test("a killed supervisor's browser is reaped on next read", async () => {
            const profile3 = resolveProfile(engine, 'd3');
            const { state } = await startDaemon({ engine, profile: profile3, idleMs: 60000 });
            try {
                process.kill(state.supervisorPid, 'SIGKILL');
                await sleep(300);
                assert.equal(await readState(engine, profile3.key), null);
                const end = Date.now() + 6000;
                while (Date.now() < end && alive(state.browserPid)) await sleep(100);
                assert.ok(!alive(state.browserPid));
            } finally {
                await stopDaemon(engine, profile3.key);
            }
        });
    });
}
