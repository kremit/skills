import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import os from 'node:os';
import { join } from 'node:path';
import { spawn } from 'node:child_process';
import { parseDuration, normalizeForMatch, missingPhrases, UsageError } from '../lib/util.mjs';
import { isPrivateAddress, checkUrl } from '../lib/guard.mjs';
import { checkRobots } from '../lib/robots.mjs';
import { parseXrandr } from '../lib/display.mjs';
import { resolveProfile, cloneProfile, firefoxDefaultFromIni } from '../lib/profile.mjs';
import { statePath, acquireLock } from '../lib/state.mjs';

test('parseDuration units and errors', () => {
    assert.equal(parseDuration('500ms'), 500);
    assert.equal(parseDuration('30s'), 30000);
    assert.equal(parseDuration('3m'), 180000);
    assert.equal(parseDuration('250'), 250);
    assert.throws(() => parseDuration('soon'), UsageError);
});

test('sentinel normalization handles case, whitespace and markdown', () => {
    const md = 'Read **Bold** text and foo\\_bar\\_baz, then click [here](https://x.test/a)  now.';
    assert.equal(normalizeForMatch('  A\n\tB  '), 'a b');
    assert.deepEqual(missingPhrases(['bold text', 'foo_bar_baz', 'click here now', 'absent'], md, { markdown: true }), ['absent']);
});

test('isPrivateAddress classifies ranges', () => {
    for (const ip of ['127.0.0.1', '10.2.3.4', '172.16.0.1', '192.168.70.53', '169.254.169.254', '100.64.0.1', '::1', 'fd00::1', 'fe80::1', '::ffff:10.0.0.1', '::ffff:7f00:1', '::ffff:a9fe:a9fe']) {
        assert.equal(isPrivateAddress(ip), true, ip);
    }
    for (const ip of ['8.8.8.8', '172.32.0.1', '2606:4700::1111', '::ffff:808:808']) {
        assert.equal(isPrivateAddress(ip), false, ip);
    }
});

test('checkUrl: scheme, literal and resolved private targets', async () => {
    assert.equal((await checkUrl('ftp://x.test/')).kind, 'usage');
    assert.equal((await checkUrl('http://127.0.0.1:8080/')).kind, 'private');
    assert.equal((await checkUrl('http://[::ffff:169.254.169.254]/')).kind, 'private');
    const fakeLookup = async () => [{ address: '192.168.1.5' }];
    const r = await checkUrl('https://intranet.test/', { lookupImpl: fakeLookup });
    assert.deepEqual([r.ok, r.kind, r.address], [false, 'private', '192.168.1.5']);
    assert.equal((await checkUrl('http://127.0.0.1/', { allowPrivate: true })).ok, true);
});

test('checkRobots: disallow, 404, error and cache', async () => {
    const cacheDir = mkdtempSync(join(tmpdir(), 'wf-robots-'));
    let calls = 0;
    const body = 'User-agent: *\nDisallow: /private/\nCrawl-delay: 2\n';
    const ok = async () => { calls += 1; return { status: 200, ok: true, text: async () => body }; };
    const dis = await checkRobots('https://a.test/private/x', { userAgent: 'UA', cacheDir, fetchImpl: ok });
    assert.deepEqual([dis.allowed, dis.rule, dis.crawl_delay], [false, 'Disallow: /private/', 2]);
    const allow = await checkRobots('https://a.test/public', { userAgent: 'UA', cacheDir, fetchImpl: ok });
    assert.equal(allow.allowed, true);
    assert.equal(calls, 1, 'second lookup served from cache');
    const nf = await checkRobots('https://b.test/x', { userAgent: 'UA', cacheDir, fetchImpl: async () => ({ status: 404, ok: false, text: async () => '' }) });
    assert.deepEqual([nf.status, nf.allowed], [404, true]);
    const err = await checkRobots('https://c.test/x', { userAgent: 'UA', cacheDir, fetchImpl: async () => { throw new Error('boom'); } });
    assert.deepEqual([err.status, err.allowed], ['error', null]);
    const redir = await checkRobots('https://d.test/x', { userAgent: 'UA', cacheDir, fetchImpl: async () => ({ status: 302, ok: false, text: async () => '' }) });
    assert.deepEqual([redir.status, redir.allowed], ['error', null]);
});

test('parseXrandr picks the primary output', () => {
    const out = 'Screen 0: minimum 8 x 8\nDP-0 connected primary 1920x1200+0+0 (normal) 518mm x 324mm\n   1920x1200 59.95*+\nDP-2 connected 2560x1440+1920+0 (normal)\n';
    assert.deepEqual(parseXrandr(out), { width: 1920, height: 1200 });
    assert.deepEqual(parseXrandr('HDMI-1 connected 1680x1050+0+0 (normal)\n'), { width: 1680, height: 1050 });
    assert.equal(parseXrandr(''), null);
});

test('resolveProfile: names, paths, and refusal of live profiles', () => {
    process.env.WEB_FETCH_HOME = mkdtempSync(join(tmpdir(), 'wf-home-'));
    const p = resolveProfile('chrome', 'me');
    assert.deepEqual([p.name, p.key, p.managed], ['me', 'me', true]);
    assert.equal(p.dir, join(process.env.WEB_FETCH_HOME, 'profiles/chrome/me'));
    const custom = resolveProfile('firefox', '/tmp/some/dir');
    assert.equal(custom.managed, false);
    assert.match(custom.key, /^path-[0-9a-f]{8}$/);
    assert.throws(() => resolveProfile('chrome', join(os.homedir(), '.config/google-chrome')), UsageError);
    assert.throws(() => resolveProfile('firefox', join(os.homedir(), '.mozilla/firefox/abc.default')), UsageError);
    assert.throws(() => resolveProfile('chrome', 'bad name!'), UsageError);
    for (const bad of ['.', '..', '.hidden']) assert.throws(() => resolveProfile('chrome', bad), UsageError);
});

test('cloneProfile copies a fixture profile, skipping caches and locks', async () => {
    process.env.WEB_FETCH_HOME = mkdtempSync(join(tmpdir(), 'wf-home-'));
    const src = mkdtempSync(join(tmpdir(), 'wf-src-'));
    mkdirSync(join(src, 'Default/Cache'), { recursive: true });
    mkdirSync(join(src, 'Default/Code Cache'), { recursive: true });
    writeFileSync(join(src, 'Default/Cookies'), 'x');
    writeFileSync(join(src, 'Default/Cookies-journal'), 'x');
    writeFileSync(join(src, 'Default/Cache/data_0'), 'x');
    writeFileSync(join(src, 'Default/SingletonLock'), 'x');
    writeFileSync(join(src, 'Default/Login Data'), 'x');
    writeFileSync(join(src, 'Local State'), '{}');
    const prof = resolveProfile('chrome', 'clone1');
    await cloneProfile('chrome', join(src, 'Default'), prof);
    assert.ok(existsSync(join(prof.dir, 'Default/Cookies')));
    assert.ok(existsSync(join(prof.dir, 'Default/Cookies-journal')));
    assert.ok(existsSync(join(prof.dir, 'Local State')));
    assert.ok(existsSync(join(prof.dir, '.web-fetch-clone.json')));
    assert.ok(!existsSync(join(prof.dir, 'Default/Cache')));
    assert.ok(!existsSync(join(prof.dir, 'Default/Code Cache')));
    assert.ok(!existsSync(join(prof.dir, 'Default/SingletonLock')));
    assert.ok(!existsSync(join(prof.dir, 'Default/Login Data')), 'saved logins not cloned');
    assert.ok(existsSync(join(src, 'Default/Cache/data_0')), 'source untouched');

    const ff = mkdtempSync(join(tmpdir(), 'wf-ffsrc-'));
    mkdirSync(join(ff, 'cache2'));
    for (const f of ['cookies.sqlite', 'cookies.sqlite-wal', 'parent.lock', 'lock', 'logins.json']) writeFileSync(join(ff, f), 'x');
    const fprof = resolveProfile('firefox', 'clone1');
    await cloneProfile('firefox', ff, fprof);
    assert.ok(existsSync(join(fprof.dir, 'cookies.sqlite-wal')));
    assert.ok(!existsSync(join(fprof.dir, 'cache2')));
    assert.ok(!existsSync(join(fprof.dir, 'parent.lock')));
    assert.ok(!existsSync(join(fprof.dir, 'logins.json')), 'saved logins not cloned');
    await assert.rejects(cloneProfile('chrome', join(src, 'Default'), resolveProfile('chrome', '/tmp/x')), UsageError);
    const rootProfile = { name: 'x', key: 'x', dir: join(process.env.WEB_FETCH_HOME, 'profiles'), managed: true };
    await assert.rejects(cloneProfile('chrome', join(src, 'Default'), rootProfile), UsageError);
    assert.ok(existsSync(join(prof.dir, 'Default/Cookies')), 'existing clone untouched');
});

test('acquireLock is pid-owned', async () => {
    process.env.WEB_FETCH_HOME = mkdtempSync(join(tmpdir(), 'wf-home-'));
    const release = await acquireLock('firefox', 'k', 2000);
    await assert.rejects(acquireLock('firefox', 'k', 500));

    const lock = `${statePath('firefox', 'k')}.lock`;
    const deadPid = await new Promise((resolve) => {
        const child = spawn(process.execPath, ['-e', '']);
        child.on('exit', () => resolve(child.pid));
    });
    writeFileSync(join(lock, 'pid'), String(deadPid));

    const r2 = await acquireLock('firefox', 'k', 2000);
    await release();
    assert.ok(existsSync(lock), 'releasing the stale lock must not drop r2\'s lock');
    await r2();
    assert.ok(!existsSync(lock));
});

test('firefoxDefaultFromIni prefers the Install default', () => {
    const ini = '[Profile0]\nName=default\nIsRelative=1\nPath=aaa.default\nDefault=1\n\n[Install4F96D1932A9F858E]\nDefault=bbb.default-release\nLocked=1\n';
    assert.equal(firefoxDefaultFromIni(ini, '/b'), '/b/bbb.default-release');
    assert.equal(firefoxDefaultFromIni('[Profile0]\nIsRelative=1\nPath=aaa.default\nDefault=1\n', '/b'), '/b/aaa.default');
});
