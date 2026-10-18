import os from 'node:os';
import { join } from 'node:path';

/** Data directories; WEB_FETCH_HOME relocates all of them (tests). */
export function dirs() {
    const home = process.env.WEB_FETCH_HOME;
    if (home) {
        return { profiles: join(home, 'profiles'), cache: join(home, 'cache'), run: join(home, 'run') };
    }
    const h = os.homedir();
    const data = process.env.XDG_DATA_HOME || join(h, '.local/share');
    const cache = process.env.XDG_CACHE_HOME || join(h, '.cache');
    const run = process.env.XDG_RUNTIME_DIR || join(os.tmpdir(), `web-fetch-${process.getuid()}`);
    return { profiles: join(data, 'web-fetch/profiles'), cache: join(cache, 'web-fetch'), run: join(run, 'web-fetch') };
}
