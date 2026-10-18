import { execFileSync } from 'node:child_process';
import { existsSync, readdirSync } from 'node:fs';
import os from 'node:os';
import { join } from 'node:path';

function run(cmd, args, env = {}) {
    try {
        return execFileSync(cmd, args, { encoding: 'utf8', env: { ...process.env, ...env }, stdio: ['ignore', 'pipe', 'ignore'], timeout: 3000 });
    } catch {
        return '';
    }
}

function withXauth(env) {
    const xa = process.env.XAUTHORITY || join(os.homedir(), '.Xauthority');
    return existsSync(xa) ? { ...env, XAUTHORITY: xa } : env;
}

function fromLoginctl() {
    const user = os.userInfo().username;
    const sessions = run('loginctl', ['list-sessions', '--no-legend'])
        .split('\n')
        .map((l) => l.trim().split(/\s+/))
        .filter((f) => f.length >= 4 && f[2] === user && f[3] === 'seat0')
        .map((f) => Object.fromEntries(
            run('loginctl', ['show-session', f[0], '-p', 'Type', '-p', 'Display', '-p', 'Active'])
                .split('\n').filter(Boolean).map((l) => l.split(/=(.*)/s).slice(0, 2)),
        ))
        .sort((a, b) => (b.Active === 'yes') - (a.Active === 'yes'));
    for (const s of sessions) {
        if (s.Type === 'x11' && s.Display) return withXauth({ DISPLAY: s.Display });
        if (s.Type === 'wayland') {
            const rt = process.env.XDG_RUNTIME_DIR || `/run/user/${process.getuid()}`;
            const sock = existsSync(rt) ? readdirSync(rt).find((n) => /^wayland-\d+$/.test(n)) : null;
            if (sock) return { WAYLAND_DISPLAY: sock, XDG_RUNTIME_DIR: rt };
        }
    }
    return null;
}

/** Display env for headed launches and screen-size reads, or null. */
export function resolveDisplay(explicit) {
    if (explicit) return explicit.startsWith('wayland') ? { WAYLAND_DISPLAY: explicit } : withXauth({ DISPLAY: explicit });
    if (process.env.WAYLAND_DISPLAY) return { WAYLAND_DISPLAY: process.env.WAYLAND_DISPLAY };
    if (process.env.DISPLAY) return withXauth({ DISPLAY: process.env.DISPLAY });
    return fromLoginctl();
}

export function parseXrandr(text) {
    const lines = String(text).split('\n').filter((l) => / connected /.test(l));
    const pick = lines.find((l) => / primary /.test(l)) || lines[0];
    const m = pick && /(\d+)x(\d+)\+\d+\+\d+/.exec(pick);
    return m ? { width: Number(m[1]), height: Number(m[2]) } : null;
}

export function screenSize(env) {
    return env ? parseXrandr(run('xrandr', ['--current'], env)) : null;
}
