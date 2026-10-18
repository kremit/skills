import { existsSync } from 'node:fs';
import { mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import puppeteer from 'puppeteer-core';
import { resolveDisplay, screenSize } from './display.mjs';
import { acquireLock, readState, touchState } from './state.mjs';
import { UsageError } from './util.mjs';

export const ENGINES = {
    chrome: { product: 'chrome', protocol: 'cdp', executable: () => process.env.WEB_FETCH_CHROME || '/usr/bin/google-chrome' },
    firefox: { product: 'firefox', protocol: 'webDriverBiDi', executable: () => process.env.WEB_FETCH_FIREFOX || '/usr/bin/firefox' },
};

const FALLBACK_SIZE = { width: 1920, height: 1080 };

export function parseViewport(v) {
    const m = /^(\d+)x(\d+)$/.exec(v || '');
    if (!m) throw new UsageError(`bad --viewport ${v}, expected WxH`);
    return { width: Number(m[1]), height: Number(m[2]) };
}

/** Puppeteer launch options for one engine, profile and display. */
export function launchOptions({ engine, profileDir, headed = false, display = null, viewport = null, ownSignals = true }) {
    const spec = ENGINES[engine];
    if (!spec) throw new UsageError(`unknown browser ${engine}`);
    const displayEnv = resolveDisplay(display);
    if (headed && !displayEnv) throw new UsageError('no display for --headed');
    const size = viewport ? parseViewport(viewport) : (screenSize(displayEnv) || FALLBACK_SIZE);
    const env = { ...process.env, ...(displayEnv || {}) };
    const bus = `/run/user/${process.getuid()}/bus`;
    if (!env.DBUS_SESSION_BUS_ADDRESS && existsSync(bus)) env.DBUS_SESSION_BUS_ADDRESS = `unix:path=${bus}`;
    const opts = {
        browser: spec.product,
        protocol: spec.protocol,
        executablePath: spec.executable(),
        headless: !headed,
        userDataDir: profileDir,
        defaultViewport: null,
        downloadBehavior: { policy: 'deny' },
        env,
        handleSIGINT: ownSignals,
        handleSIGTERM: ownSignals,
        handleSIGHUP: ownSignals,
    };
    if (engine === 'chrome') {
        opts.args = [`--window-size=${size.width},${size.height}`, '--no-first-run', '--no-default-browser-check'];
        opts.ignoreDefaultArgs = ['--password-store=basic', '--use-mock-keychain'];
    } else {
        opts.args = [`--width=${size.width}`, `--height=${size.height}`];
        opts.extraPrefsFirefox = {
            'devtools.jsonview.enabled': false,
            'browser.download.folderList': 2,
            'browser.download.dir': join(profileDir, 'downloads'),
            'browser.download.useDownloadDir': true,
            'browser.download.start_downloads_in_tmp_dir': true,
        };
    }
    return opts;
}

/** Connect to a live daemon for the profile, or cold-launch. */
export async function openBrowser({ engine, profile, headed = false, display = null, viewport = null, noDaemon = false }) {
    const state = await readState(engine, profile.key);
    if (state) {
        if (noDaemon) throw new UsageError(`profile ${profile.name} is in use by a ${engine} daemon; --stop it or drop --no-daemon`);
        if (headed && !state.headed) throw new UsageError(`the ${engine} daemon for profile ${profile.name} is headless; --stop it or drop --headed`);
        const release = engine === 'firefox' ? await acquireLock(engine, profile.key) : async () => {};
        let browser;
        try {
            browser = await puppeteer.connect({
                browserWSEndpoint: state.wsEndpoint, protocol: state.protocol, defaultViewport: null,
                downloadBehavior: { policy: 'deny' },
            });
        } catch (e) {
            await release();
            throw new Error(`cannot reach the ${engine} daemon for profile ${profile.name} (${e.message}); run web-fetch --stop --browser ${engine} --profile ${profile.name}`);
        }
        await touchState(engine, profile.key);
        return {
            browser,
            viaDaemon: true,
            pid: state.browserPid,
            close: async () => {
                try {
                    await browser.disconnect();
                } finally {
                    await touchState(engine, profile.key);
                    await release();
                }
            },
        };
    }
    await mkdir(profile.dir, { recursive: true });
    const browser = await puppeteer.launch(launchOptions({ engine, profileDir: profile.dir, headed, display, viewport }));
    return { browser, viaDaemon: false, pid: browser.process()?.pid ?? null, close: () => browser.close() };
}
