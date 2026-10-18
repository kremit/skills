# web-fetch: Design

Date: 2026-10-17
Skill path: `web/web-fetch/` (symlinked to `~/.claude/skills/web-fetch`)

## Goal

This is a local page fetcher that Claude Code calls when the user asks. It loads a URL in a real headless Chrome or Firefox on the user's machine and home IP, waits until the page has actually loaded, and returns clean markdown with metadata. Sites that block cloud or agent browsers, or that gate content behind a JS check, load the way they would for a person.

The user runs it through their own commands in claude-cli. It is not a crawler.

## Non-goals

- Mass crawling, link following, sitemaps
- Automated CAPTCHA solving, fingerprint spoofing, stealth plugins, hiding `navigator.webdriver`
- Login automation (a cloned profile carries existing sessions instead)
- An MCP server or Firecrawl integration (later)

## Approach

Node 22 with `puppeteer-core`, driving the **system** browsers. Nothing is downloaded.

| Browser | Binary | Protocol |
|---------|--------|----------|
| chrome (default) | `/usr/bin/google-chrome` | CDP, `--headless=new` |
| firefox | `/usr/bin/firefox` | WebDriver BiDi (native since Firefox 129; no patched build, no geckodriver) |

One Puppeteer API covers both. Both support `puppeteer.connect()`, which the daemon relies on.

These alternatives were rejected:
- **Python Playwright plus Selenium/geckodriver:** two APIs, and Firefox needs geckodriver.
- **Playwright's patched Firefox:** it can't use the user's profile, and it's a separate download.

Extraction runs **inside the page**. The script injects Mozilla Readability and Turndown (plus the GFM plugin) through `page.evaluate`, so it works the same in both browsers. Protocol-level evaluate is not subject to the page's CSP.

## CLI

```
web-fetch URL [options]
web-fetch --daemonize [--browser B] [--profile P] [--idle 30m] [--headed]
web-fetch --status | --stop [--browser B] [--profile P]
web-fetch --clone-profile [SRC] [--browser B] [--profile NAME]
```

| Option | Default | Meaning |
|--------|---------|---------|
| `--browser chrome\|firefox` | chrome | engine |
| `--profile NAME\|PATH` | `default` | a NAME resolves to `~/.local/share/web-fetch/profiles/<browser>/<NAME>`; a PATH (has a `/`) is used as-is |
| `--format md\|text\|html` | md | content format |
| `--selector CSS` | none | extract only this element and skip Readability |
| `--no-extract` | off | convert the whole `<body>` and skip Readability |
| `--sentinel PHRASE` | none | repeatable; see Sentinel |
| `--wait load\|domcontentloaded\|networkidle\|CSS` | networkidle | readiness condition before extraction; `networkidle` maps to Puppeteer `networkidle2`, and any other value is a CSS selector |
| `--timeout DUR` | 30s | navigation plus wait budget |
| `--scroll N` | 0 | scroll to the bottom N times, pausing 500 ms each, for lazy content |
| `--screenshot PATH` | none | full-page PNG |
| `--max-chars N` | 100000 | truncate content and set `truncated` |
| `--headed` | off | open a visible window on the desktop so the user can clear a challenge by hand; see Headed mode |
| `--display DISPLAY` | auto | X or Wayland display used for `--headed` and screen-size detection |
| `--viewport WxH` | screen size | override the window and viewport size |
| `--respect-robots` | off | refuse disallowed URLs (exit 6) |
| `--allow-private` | off | permit private, loopback and link-local targets |
| `--no-daemon` | off | always cold-launch; errors (exit 64) if a daemon already holds the profile, since two browsers cannot share one profile |
| `--raw` | off | print only the content, not the JSON envelope |

Durations accept `500ms`, `30s` and `30m`.

### Output

By default the output is a JSON envelope on stdout:

```json
{
  "url": "...", "final_url": "...", "status": 200, "title": "...",
  "fetched_at": "2026-10-17T18:00:00Z", "browser": "chrome", "profile": "default",
  "daemon": true, "elapsed_ms": 2140,
  "blocked": false, "challenge": null,
  "sentinel": {"phrases": ["..."], "missing": []},
  "robots": {"status": 200, "allowed": true, "rule": null, "crawl_delay": null},
  "truncated": false, "screenshot": null,
  "content": "# Title\n\n..."
}
```

Diagnostics go to stderr.

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | ok |
| 3 | sentinel missing |
| 4 | blocked: a challenge did not clear, or HTTP 401, 403 or 429 |
| 5 | navigation error, timeout, or other HTTP 4xx/5xx |
| 6 | robots disallowed and `--respect-robots` set |
| 7 | private target blocked |
| 64 | usage error |

The envelope is still printed for exits 3 to 7, so the caller can inspect it.

## Sentinel

- `--sentinel` can be repeated, and every phrase must be found.
- Matching is case-insensitive, after collapsing whitespace on both sides. For `md` output, the comparison copy has markdown escapes and emphasis markers stripped (`\_`, `\*`, `**`, `_`), so `foo_bar` still matches Turndown's `foo\_bar`.
- **The sentinel is also a wait condition.** After `--wait` is satisfied, the page's `innerText` is polled every 250 ms until every phrase appears or the timeout runs out. Lazy and JS-rendered articles finish loading before extraction.
- **Then it is checked against the extracted content.** Each missing phrase is reported. If a phrase is in the page but not in the extracted content, the envelope says `"note": "in page, dropped by extraction"`. That points at a `--selector` or `--no-extract` fix, not a load failure.
- Any missing phrase means exit 3.

## Profiles

All profile data lives outside the user's real browser profiles.

- **Managed profile (default):** a persistent directory per browser and name. Cookies, storage and challenge clearances persist across fetches. It starts empty.
- **Custom path (`--profile /some/dir`):** used as-is. The tool **refuses** the browser's live default profile directories (`~/.config/google-chrome`, `~/.mozilla/firefox/*`), with a message pointing to `--clone-profile`, because:
  - Chrome 136+ ignores remote debugging on its default user-data-dir.
  - Both browsers lock a profile that's in use.
  - It protects the real profile from damage.
- **`--clone-profile [SRC]`:** snapshots a real profile into a managed one. Running it again refreshes the snapshot.
  - SRC defaults to Chrome `~/.config/google-chrome/Default` (plus its `Local State`) and the Firefox profile marked default in `profiles.ini`.
  - Excluded: caches (`Cache`, `Code Cache`, `GPUCache`, `Service Worker/CacheStorage`, `cache2`, `startupCache`) and lock files (`Singleton*`, `parent.lock`, `lock`).
  - Firefox SQLite files are copied together with their `-wal`/`-shm` siblings.
  - The source is only read.
- **Chrome cookie decryption:** Puppeteer passes `--password-store=basic` by default, which can't decrypt keyring-encrypted cookies from a cloned Linux profile. Chrome launches drop that default argument, so Chrome uses the desktop keyring like the real browser.
- **Risk (documented in SKILL.md):** a cloned profile carries the user's logged-in sessions. Any URL fetched with it is fetched as the user. Clone only when that is wanted. A dedicated profile name such as `--profile me` keeps cloned sessions apart from the anonymous `default`.

## Daemon

- `--daemonize` spawns a detached supervisor (`node lib/daemon.mjs`) that launches the browser and writes a state file at `$XDG_RUNTIME_DIR/web-fetch/<browser>-<profile>.json` (mode 0600) containing pid, browser pid, wsEndpoint, protocol, profile path, start time and headed flag.
- The browser listens on `127.0.0.1` only, on a random port: Chrome `--remote-debugging-port=0` (CDP) and Firefox `--remote-debugging-port=0` (BiDi).
- Fetches check for a live daemon that matches the browser and profile. If one is found, they `connect()`, open a **new page in the default context** (so profile cookies apply), and close the page afterwards. Otherwise they cold-launch and close the browser at the end.
- Each fetch touches the state file. The supervisor exits after `--idle` (default 30m) without a touch, and also when the browser exits.
- `--status` lists daemons. `--stop` kills one and removes its state file.
- Stale state files, where the pid is dead, are cleaned up on read.
- **Warning:** the debugging port on `127.0.0.1` is reachable by any local user on the host, not only the owner, while the browser runs. A daemon keeps it open until it stops, so never daemonize a cloned profile on a shared host. This is documented in SKILL.md.

## Readiness, challenges, identity

- **Wait order:** navigation, then `--wait`, then `--scroll`, then the sentinel poll, then `window.stop()` to freeze the page as loaded (like the stop button), then extraction.
- **Challenge detection.** A page counts as a challenge when either of these holds:
  - its title matches `just a moment|attention required|verify you are human|are you a robot|access denied|checking your browser`
  - it contains an iframe from `challenges.cloudflare.com`, `hcaptcha.com` or `google.com/recaptcha`

  On detection, wait up to 15s for a navigation that clears it. If it doesn't clear, the result is `blocked: true`, `challenge: "<kind>"`, exit 4. With `--headed`, the wait extends to the full timeout so the user can solve it by hand.
- **Identity:**
  - Chrome's headless UA contains `HeadlessChrome`. It's rewritten to the browser's normal `Chrome/<version>` string through `page.setUserAgent`.
  - Locale and timezone come from the system.
  - Window and viewport size match the user's real primary monitor: `xrandr --current` on the resolved display, primary output (1920x1200 here). Chrome gets `--window-size=W,H` and Firefox gets `--width/--height`, with Puppeteer `defaultViewport: null` so the page sees the real window. With no display, the fallback is 1920x1080, the most common desktop size. `--viewport` overrides both.
  - Nothing else is spoofed.

## Headed mode

- `--headed` launches the browser with a real window on the user's desktop while Claude Code waits. The user clears the challenge or logs in, and the fetch continues once the page passes the challenge check. The wait is capped by `--timeout`; use a longer one such as `--timeout 3m` for manual steps.
- Claude Code often runs without display variables, for example from a tty, tmux, ssh or remote control. This session has `XDG_SESSION_TYPE=tty` and no `DISPLAY`, while the desktop is Cinnamon on X11 `:0`. Display resolution order:
  1. `--display`
  2. inherited `WAYLAND_DISPLAY` or `DISPLAY`
  3. the user's active seat0 graphical session from `loginctl` (Type x11 gives `DISPLAY=:N` from `/tmp/.X11-unix/XN`, with `XAUTHORITY` from `~/.Xauthority` if present; Type wayland gives `WAYLAND_DISPLAY` from `$XDG_RUNTIME_DIR/wayland-*`)
  4. otherwise exit 64 with `no display for --headed`
- A headed daemon keeps its window open between fetches. Each fetch opens a tab and closes it when done.
- Headless fetches never need a display. They only use one, if found, to read the screen size.

## Robots

- For each fetch, `https://<host>/robots.txt` is requested with Node `fetch` and the browser's UA, then evaluated with `robots-parser` for UA token `web-fetch`, falling back to `*`.
- The result goes in `robots`, with status 404 meaning allowed.
- The robots fetch is cached per host for 1 hour in `~/.cache/web-fetch/robots/`.
- It is advisory unless `--respect-robots` is set.
- A robots fetch error never fails the page fetch; it's reported as `robots.status: "error"`.

## Private target guard

- The hostname is resolved before navigation. Loopback, RFC1918, CGNAT, link-local (including 169.254.169.254) and IPv6 ULA/link-local addresses are refused unless `--allow-private` is set (exit 7).
- `final_url` is checked again after redirects, with the same result.
- Subresource requests made by the page itself are not filtered. Normal browsing makes those too, and this is documented.
- **Why:** fetched pages can carry prompt injection that steers the model toward LAN services.

## Layout

```
web/web-fetch/
  SKILL.md
  package.json, package-lock.json   # puppeteer-core, @mozilla/readability, turndown, turndown-plugin-gfm, robots-parser
  bin/web-fetch                     # node entry (executable)
  lib/cli.mjs                       # arg parsing, envelope, exit codes
  lib/browser.mjs                   # launch/connect per engine, UA, daemon lookup
  lib/daemon.mjs                    # supervisor process
  lib/fetch.mjs                     # navigate, wait, scroll, sentinel, challenge
  lib/extract.mjs                   # in-page Readability + Turndown
  lib/profile.mjs                   # resolve, refuse live dirs, clone
  lib/robots.mjs                    # fetch, cache, evaluate
  lib/guard.mjs                     # private address checks
  tests/                            # node:test + fixture HTTP server
```

`node_modules/` is gitignored, and setup is `npm ci` in the skill dir. JS style is 4-space indentation and 1TBS, ESM `.mjs`, with short JSDoc where it helps.

## Testing

A lean suite using `node:test`, with a fixture HTTP server on 127.0.0.1 and `--allow-private` set for tests. Each case runs against **both browsers**.

| Fixture | Asserts |
|---------|---------|
| `article.html` (nav, footer, cookie banner around an article) | content has the article, not the nav or footer; title; exit 0 |
| `lazy.html` (text injected after 1.5s) | `--sentinel` waits and finds it; a wrong phrase gives exit 3 with `missing` filled |
| `challenge.html` (title "Just a moment...", redirect after 2s; a variant never redirects) | the clearing variant gives exit 0; the stuck variant gives exit 4, `blocked: true` |
| `/forbidden` (403) | exit 4 |
| `robots.txt` disallowing `/private/` | advisory report; `--respect-robots` gives exit 6 |
| guard | without `--allow-private`, 127.0.0.1 gives exit 7 |
| daemon | `--daemonize`, two fetches report `daemon: true` and the same browser pid, `--stop` cleans up the state file |
| profile | refuses `~/.config/google-chrome`; clone of a fixture "real" profile skips caches and locks |

Firefox download prefs point inside the managed profile; non-renderable types return unsupported content-type without downloading.

**Real profiles are never loaded.** Tests use a fixture fake profile only. The live clone check snapshots Chrome `Default` (read-only copy) and runs only on the copy. The user's Firefox profile is the daily driver: it is never cloned or launched during development, and Firefox cloning is verified against the fixture only.

**Live checks**, not in the suite:
- a JS-heavy public page per browser
- one challenge-protected site fetched with `--headed` once, then headless with the same profile
- a cloned Chrome profile, as a copy only, reading a logged-in page, to prove cookie decryption
- `--headed` on the Cinnamon X11 desktop (`:0`) from this tty-launched Claude Code session, through display auto-detection

## Risks to prove first (plan task 1, a spike)

1. Cookies from a cloned Chrome profile decrypt with `--password-store=basic` removed. This runs on a copy of `~/.config/google-chrome/Default`; the source is only read.
2. Puppeteer `connect()` to a daemonized Firefox over BiDi works with Firefox 155.
3. Readability and Turndown injected with `page.evaluate` run in Firefox through BiDi, and `networkidle2` waits work there.

If any of these fails, the fallback is decided before building on it:
- (1) a managed profile, with cookies imported in plaintext via CDP `Network.setCookies` from a decrypted export
- (2) Firefox cold-launch only
- (3) extraction in Node using `linkedom`
