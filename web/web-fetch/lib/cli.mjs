import { join, resolve } from 'node:path';
import { parseArgs } from 'node:util';
import { openBrowser, parseViewport } from './browser.mjs';
import { startDaemon, stopDaemon } from './daemon.mjs';
import { fetchPage } from './fetch.mjs';
import { checkUrl } from './guard.mjs';
import { dirs } from './paths.mjs';
import { cloneProfile, defaultCloneSource, resolveProfile } from './profile.mjs';
import { checkRobots } from './robots.mjs';
import { acquireLock, listStates, readState } from './state.mjs';
import { parseDuration, UsageError } from './util.mjs';

const USAGE = `usage:
  web-fetch URL [--browser chrome|firefox] [--profile NAME|PATH] [--format md|text|html]
                [--selector CSS] [--no-extract] [--sentinel PHRASE]... [--wait load|domcontentloaded|networkidle|CSS]
                [--timeout 30s] [--scroll N] [--screenshot PATH] [--max-chars N] [--headed] [--display D]
                [--viewport WxH] [--respect-robots] [--allow-private] [--no-daemon] [--raw]
  web-fetch --daemonize [--browser B] [--profile P] [--idle 30m] [--headed] [--display D] [--viewport WxH]
  web-fetch --status
  web-fetch --stop [--browser B] [--profile P]
  web-fetch --clone-profile [SRC] [--browser B] [--profile NAME]
exit: 0 ok, 3 sentinel missing, 4 blocked, 5 error, 6 robots, 7 private target, 64 usage
`;

const OPTIONS = {
    browser: { type: 'string', default: 'chrome' },
    profile: { type: 'string', default: 'default' },
    format: { type: 'string', default: 'md' },
    selector: { type: 'string' },
    'no-extract': { type: 'boolean', default: false },
    sentinel: { type: 'string', multiple: true, default: [] },
    wait: { type: 'string', default: 'networkidle' },
    timeout: { type: 'string', default: '30s' },
    scroll: { type: 'string', default: '0' },
    screenshot: { type: 'string' },
    'max-chars': { type: 'string', default: '100000' },
    headed: { type: 'boolean', default: false },
    display: { type: 'string' },
    viewport: { type: 'string' },
    'respect-robots': { type: 'boolean', default: false },
    'allow-private': { type: 'boolean', default: false },
    'no-daemon': { type: 'boolean', default: false },
    raw: { type: 'boolean', default: false },
    daemonize: { type: 'boolean', default: false },
    idle: { type: 'string', default: '30m' },
    status: { type: 'boolean', default: false },
    stop: { type: 'boolean', default: false },
    'clone-profile': { type: 'boolean', default: false },
    help: { type: 'boolean', short: 'h', default: false },
};

export function parseCli(argv) {
    let parsed;
    try {
        parsed = parseArgs({ args: argv, options: OPTIONS, allowPositionals: true, strict: true });
    } catch (e) {
        throw new UsageError(e.message);
    }
    const v = parsed.values;
    const int = (name) => {
        const n = Number(v[name]);
        if (!Number.isInteger(n) || n < 0) throw new UsageError(`--${name} must be a non-negative integer`);
        return n;
    };
    if (!['chrome', 'firefox'].includes(v.browser)) throw new UsageError('--browser must be chrome or firefox');
    if (!['md', 'text', 'html'].includes(v.format)) throw new UsageError('--format must be md, text or html');
    const modes = ['daemonize', 'status', 'stop', 'clone-profile'].filter((m) => v[m]);
    if (modes.length > 1) throw new UsageError(`pick one of --${modes.join(', --')}`);
    if (v.viewport) parseViewport(v.viewport);
    const opts = {
        mode: modes[0] || 'fetch',
        engine: v.browser,
        profileArg: v.profile,
        format: v.format,
        selector: v.selector ?? null,
        noExtract: v['no-extract'],
        sentinels: v.sentinel,
        waitFor: v.wait,
        timeoutMs: parseDuration(v.timeout),
        scroll: int('scroll'),
        screenshot: v.screenshot ? resolve(v.screenshot) : null,
        maxChars: int('max-chars'),
        headed: v.headed,
        display: v.display ?? null,
        viewport: v.viewport ?? null,
        respectRobots: v['respect-robots'],
        allowPrivate: v['allow-private'],
        noDaemon: v['no-daemon'],
        raw: v.raw,
        idleMs: parseDuration(v.idle),
        help: v.help,
        positionals: parsed.positionals,
    };
    if (opts.mode === 'fetch' && !opts.help && opts.positionals.length !== 1) throw new UsageError('expected exactly one URL');
    if (opts.mode === 'clone-profile' && opts.positionals.length > 1) throw new UsageError('--clone-profile takes at most one SRC');
    return opts;
}

export function exitCodeFor(env) {
    if (env.error && /^(navigation|unsupported|wait|selector|extract|launch)/.test(env.error)) return 5;
    if (env.blocked) return 4;
    if (typeof env.status === 'number' && env.status >= 400) return 5;
    if (env.sentinel && env.sentinel.missing.length) return 3;
    return 0;
}

/** Await the write callback so output is flushed before the process can exit. */
async function write(stdout, data) {
    await new Promise((r) => stdout.write(data, r));
}

/** The envelope is always printed for exits 3-7; --raw only replaces a 0-exit body with the bare content. */
async function emit(env, code, opts, stdout) {
    const data = opts.raw && code === 0 ? `${env.content}\n` : `${JSON.stringify(env, null, 2)}\n`;
    await write(stdout, data);
    return code;
}

async function cmdFetch(opts, stdout) {
    const raw = opts.positionals[0];
    const url = /^[a-z][a-z0-9+.-]*:\/\//i.test(raw) ? raw : `https://${raw}`;
    const env = {
        url, final_url: null, status: null, content_type: null, title: null, fetched_at: new Date().toISOString(),
        browser: opts.engine, profile: opts.profileArg, daemon: false, browser_pid: null, elapsed_ms: null,
        blocked: false, challenge: null, sentinel: null, robots: null, truncated: false, screenshot: null,
        error: null, content: '',
    };
    const guard = await checkUrl(url, { allowPrivate: opts.allowPrivate });
    if (!guard.ok) {
        if (guard.kind === 'usage') throw new UsageError(guard.reason);
        env.error = `private address ${guard.address}`;
        return await emit(env, 7, opts, stdout);
    }
    const profile = resolveProfile(opts.engine, opts.profileArg);
    const started = Date.now();
    const unlock = await acquireLock(opts.engine, `${profile.key}.cold`, opts.timeoutMs + 30000);
    try {
        let handle;
        try {
            handle = await openBrowser({ engine: opts.engine, profile, headed: opts.headed, display: opts.display, viewport: opts.viewport, noDaemon: opts.noDaemon });
        } catch (e) {
            if (e instanceof UsageError || /cannot reach the .* daemon/.test(e.message)) throw e;
            env.error = `launch: ${e.message}`;
            env.elapsed_ms = Date.now() - started;
            return await emit(env, 5, opts, stdout);
        }
        env.daemon = handle.viaDaemon;
        env.browser_pid = handle.pid;
        try {
            const ua = (await handle.browser.userAgent()).replace('HeadlessChrome', 'Chrome');
            env.robots = await checkRobots(url, { userAgent: ua, cacheDir: join(dirs().cache, 'robots') });
            if (opts.respectRobots && env.robots.allowed === false) {
                env.error = 'disallowed by robots.txt';
                env.elapsed_ms = Date.now() - started;
                return await emit(env, 6, opts, stdout);
            }
            const r = await fetchPage(handle.browser, url, { ...opts, downloadsDir: join(profile.dir, 'downloads') });
            for (const k of ['final_url', 'status', 'content_type', 'title', 'blocked', 'challenge', 'sentinel', 'truncated', 'screenshot', 'error', 'content']) {
                env[k] = r[k];
            }
        } finally {
            await handle.close();
        }
    } finally {
        await unlock();
    }
    env.elapsed_ms = Date.now() - started;
    if (env.final_url && env.final_url !== url) {
        const again = await checkUrl(env.final_url, { allowPrivate: opts.allowPrivate });
        if (!again.ok && again.kind === 'private') {
            env.content = '';
            env.error = `redirected to private address ${again.address}`;
            return await emit(env, 7, opts, stdout);
        }
    }
    return await emit(env, exitCodeFor(env), opts, stdout);
}

async function cmdDaemonize(opts, stdout) {
    const profile = resolveProfile(opts.engine, opts.profileArg);
    const { state, started } = await startDaemon({ engine: opts.engine, profile, headed: opts.headed, display: opts.display, viewport: opts.viewport, idleMs: opts.idleMs });
    await write(stdout, `${JSON.stringify({ started, browser: state.engine, profile: state.profile, headed: state.headed, browser_pid: state.browserPid, idle_ms: state.idleMs }, null, 2)}\n`);
    return 0;
}

async function cmdStatus(stdout) {
    const states = (await listStates()).map((s) => ({
        browser: s.engine, profile: s.profile, headed: s.headed, browser_pid: s.browserPid,
        supervisor_pid: s.supervisorPid, started_at: s.startedAt, idle_ms: s.idleMs,
    }));
    await write(stdout, `${JSON.stringify(states, null, 2)}\n`);
    return 0;
}

async function cmdStop(opts, stdout) {
    const profile = resolveProfile(opts.engine, opts.profileArg);
    const stopped = await stopDaemon(opts.engine, profile.key);
    await write(stdout, `${JSON.stringify({ stopped, browser: opts.engine, profile: profile.name })}\n`);
    return 0;
}

async function cmdClone(opts, stdout) {
    const profile = resolveProfile(opts.engine, opts.profileArg);
    if (await readState(opts.engine, profile.key)) throw new UsageError(`profile ${profile.name} has a running daemon; --stop it first`);
    const src = opts.positionals[0] ? resolve(opts.positionals[0]) : await defaultCloneSource(opts.engine);
    const r = await cloneProfile(opts.engine, src, profile);
    await write(stdout, `${JSON.stringify({ cloned: true, browser: opts.engine, profile: profile.name, ...r }, null, 2)}\n`);
    return 0;
}

export async function main(argv, { stdout = process.stdout, stderr = process.stderr } = {}) {
    let opts;
    try {
        opts = parseCli(argv);
    } catch (e) {
        await write(stderr, `web-fetch: ${e.message}\n${USAGE}`);
        return 64;
    }
    if (opts.help) {
        await write(stdout, USAGE);
        return 0;
    }
    try {
        if (opts.mode === 'status') return await cmdStatus(stdout);
        if (opts.mode === 'stop') return await cmdStop(opts, stdout);
        if (opts.mode === 'daemonize') return await cmdDaemonize(opts, stdout);
        if (opts.mode === 'clone-profile') return await cmdClone(opts, stdout);
        return await cmdFetch(opts, stdout);
    } catch (e) {
        await write(stderr, `web-fetch: ${e.message}\n`);
        return e instanceof UsageError ? 64 : 5;
    }
}
