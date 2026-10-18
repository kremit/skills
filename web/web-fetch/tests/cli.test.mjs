import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { execFile, spawn } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startServer } from './fixtures/server.mjs';

const BIN = join(dirname(fileURLToPath(import.meta.url)), '..', 'bin', 'web-fetch');
const HOME = mkdtempSync(join(tmpdir(), 'wf-cli-'));
let srv;

function cli(args) {
    return new Promise((done) => {
        execFile(BIN, args, { env: { ...process.env, WEB_FETCH_HOME: HOME }, timeout: 90000 }, (err, stdout, stderr) => {
            done({ code: err ? err.code : 0, stdout, stderr, json: (() => { try { return JSON.parse(stdout); } catch { return null; } })() });
        });
    });
}

before(async () => {
    srv = await startServer();
});
after(async () => {
    await srv.close();
});

test('usage errors exit 64', async () => {
    assert.equal((await cli([])).code, 64);
    assert.equal((await cli(['--browser', 'safari', 'https://x.test'])).code, 64);
});

test('private targets exit 7 unless allowed', async () => {
    const r = await cli([`${srv.base}/article.html`]);
    assert.equal(r.code, 7);
    assert.match(r.json.error, /private address 127\.0\.0\.1/);

    const rawPriv = await cli(['--raw', `${srv.base}/article.html`]);
    assert.equal(rawPriv.code, 7);
    assert.match(rawPriv.json.error, /private address/);
});

for (const engine of ['chrome', 'firefox']) {
    test(`${engine}: envelope, sentinel pass and fail, robots advisory and enforced`, async () => {
        const ok = await cli(['--browser', engine, '--allow-private', '--sentinel', 'UNIQUEARTICLEPHRASE', `${srv.base}/article.html`]);
        assert.equal(ok.code, 0, ok.stderr);
        assert.equal(ok.json.browser, engine);
        assert.equal(ok.json.daemon, false);
        assert.deepEqual(ok.json.sentinel.missing, []);
        assert.equal(ok.json.robots.allowed, true);

        const miss = await cli(['--browser', engine, '--allow-private', '--timeout', '5s', '--sentinel', 'NOT-ON-PAGE', `${srv.base}/article.html`]);
        assert.equal(miss.code, 3);

        const adv = await cli(['--browser', engine, '--allow-private', `${srv.base}/private/page.html`]);
        assert.deepEqual([adv.code, adv.json.robots.allowed, adv.json.robots.rule], [0, false, 'Disallow: /private/']);

        const enforced = await cli(['--browser', engine, '--allow-private', '--respect-robots', `${srv.base}/private/page.html`]);
        assert.equal(enforced.code, 6);
        assert.equal(enforced.json.robots.allowed, false);
        assert.equal(typeof enforced.json.elapsed_ms, 'number');
    });
}

test('--raw prints markdown, 403 exits 4', async () => {
    const raw = await cli(['--allow-private', '--raw', `${srv.base}/article.html`]);
    assert.equal(raw.code, 0);
    assert.equal(raw.json, null);
    assert.match(raw.stdout, /UNIQUEARTICLEPHRASE/);
    assert.doesNotMatch(raw.stdout, /^\s*\{/);
    assert.equal((await cli(['--allow-private', `${srv.base}/forbidden`])).code, 4);

    const sel = await cli(['--allow-private', '--raw', '--selector', '#does-not-exist', `${srv.base}/article.html`]);
    assert.equal(sel.code, 5);
    assert.match(sel.json.error, /selector not found/);
});

test('scheme-less URL is treated as https', async () => {
    const r = await cli(['--allow-private', '--timeout', '10s', srv.base.replace('http://', '') + '/article.html']);
    assert.equal(r.json.url, srv.base.replace('http://', 'https://') + '/article.html');
    assert.equal(r.code, 5);
});

test('unreachable daemon state exits 5 and names --stop', async () => {
    // The brief's version of this test reuses the test runner's own pid for both
    // pids. Under the cmdline-verified liveness guard added after the plan was
    // written, that state is stale (readState checks /proc/<pid>/cmdline, and the
    // test runner's cmdline contains neither --supervise nor the profile dir), so
    // the CLI would treat it as gone and cold-launch successfully instead of
    // hitting the unreachable-daemon path. To keep the test's intent -- an
    // unreachable but live-looking daemon exits 5 and names --stop -- spawn two
    // harmless long-lived processes whose cmdlines satisfy the guard.
    const profileDir = join(HOME, 'profiles', 'chrome', 'stale');
    mkdirSync(profileDir, { recursive: true });
    const sup = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1e9)', '--', '--supervise'], { stdio: 'ignore' });
    const br = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1e9)', '--', profileDir], { stdio: 'ignore' });
    try {
        const run = join(HOME, 'run');
        mkdirSync(run, { recursive: true });
        const statefile = join(run, 'chrome-stale.json');
        writeFileSync(statefile, JSON.stringify({
            engine: 'chrome', key: 'stale', profile: 'stale', profileDir, supervisorPid: sup.pid, browserPid: br.pid,
            wsEndpoint: 'ws://127.0.0.1:1/devtools/browser/none', protocol: 'cdp', headed: false,
        }));
        const r = await cli(['--allow-private', '--profile', 'stale', `${srv.base}/article.html`]);
        assert.equal(r.code, 5);
        assert.match(r.stderr, /--stop/);
    } finally {
        sup.kill('SIGKILL');
        br.kill('SIGKILL');
    }
});
