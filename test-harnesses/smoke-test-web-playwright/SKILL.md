---
name: smoke-test-web-playwright
description: Repeatable interactive browser smoke test for a browser-driven web app with a dev server (Vite, Parcel, webpack-dev-server, etc.), using a headless-Chromium REPL when available and a Playwright fallback (Chromium, Firefox, or WebKit; JS or Python driver) when it isn't. Drives real navigation, clicks, and console/error capture instead of just confirming the entrypoint resolves. Also usable standalone as a cross-browser headless screenshot/render utility. Use before merging a build-tooling change, a dependency swap, or any refactor of code that runs in the browser, when you want proof the app actually works, not just that it typechecks or builds.
---

# Smoke-Test a Browser-Driven Web App with Playwright

## When to use this

You changed something that only shows up at runtime in a browser — a bundler
migration, a dependency swap (especially anything touching URL/query-string
handling, polyfills, or global objects), a refactor of DOM-wiring code — and
you want to *prove* the app still works, not just that the build succeeds.
Building headless-browser screenshots + a captured console log is cheap
insurance against "it compiled, therefore it works."

This is a **verification recipe**, not a permanent CI test suite. It leaves
no artifacts in the target repo. Run it, look at the screenshots, read the
console dump, decide, done. It also works standalone as a headless
render/screenshot utility, independent of testing anything — see
"Beyond smoke testing" below.

## Arguments

Invoked as `/smoke-test-web-playwright [key=value ...]`. Parse space-separated
`key=value` pairs out of the args string; anything unrecognized, ignore.

| Key | Values | Default | Effect |
|---|---|---|---|
| `browser` | `chromium` \| `firefox` \| `webkit` | `chromium` | Which engine to drive. See Step 2 - only `chromium` gets the no-download system-binary shortcut. |
| `lang` | `js` \| `python` | `js` | Which Playwright binding to write the driver in. See Step 4. |

Examples: `/smoke-test-web-playwright browser=firefox`,
`/smoke-test-web-playwright browser=webkit lang=python`.

## Step 0: Check for `chromium-cli` first

Only applies when `browser=chromium` (the default) - `chromium-cli` is
Chromium-only.

```bash
which chromium-cli
```

If present, use it — it's a headless-Chromium REPL purpose-built for this,
no driver script needed:

```bash
chromium-cli --session smoke <<'EOF'
nav http://localhost:5173/
wait-for text=SomeKnownPageText
screenshot
click button:has-text("Some Action")
wait-for text=ExpectedResultText
screenshot
console --errors
EOF
```

Screenshots land in `chromium_cli/sessions/smoke/screenshots/`. Full command
reference: `chromium-cli` skill, or `help` at the prompt. **If this works and
`browser=chromium`, skip everything below.** For `browser=firefox` or
`browser=webkit`, go straight to Step 1 - there is no CLI-REPL equivalent for
those engines here.

## Step 1: If `chromium-cli` isn't available (or you need firefox/webkit), use Playwright directly

`npx playwright` will often refuse to auto-install in a sandboxed/CI-like
environment ("canceled due to missing packages and no YES option"). Don't
fight it — install the JS bindings only, into a scratch directory, without
touching the target repo's `package.json`:

```bash
mkdir -p /tmp/smoke-test-pw && cd /tmp/smoke-test-pw
npm install playwright@1.61.1 --no-save
```

This is fast (a few seconds) because it's just the JS package, not a browser
binary download. (For `lang=python`, see Step 4's Python variant instead -
it uses a separate `pip install playwright` step.)

## Step 2: Get a browser engine

**`browser=chromium` (default):** Playwright's own managed browser cache is
frequently version-mismatched with whatever got installed (`Executable
doesn't exist at .../headless_shell...`). Rather than fighting that download,
point Playwright at a real, already-installed system browser - this works
because Chrome/Chromium speak the standard DevTools Protocol Playwright
drives against, so any reasonably recent build works:

```bash
which google-chrome chromium chromium-browser 2>/dev/null
```

```js
const browser = await chromium.launch({
  executablePath: '/usr/bin/google-chrome', // or whatever `which` found
  args: ['--no-sandbox'],
});
```

**`browser=firefox` or `browser=webkit`: the system-binary shortcut does NOT
work.** Playwright requires its own specially-patched Firefox/WebKit builds
(Firefox needs Playwright's Juggler protocol patches; regular system Firefox
doesn't have them, and `executablePath` pointing at a stock Firefox is
unsupported and frequently just fails to attach). There's no shortcut here -
you need Playwright's own browser install:

```bash
cd /tmp/smoke-test-pw
npx playwright install firefox   # or: npx playwright install webkit
```

This downloads a browser binary and needs network access - if the sandbox
blocks it, `browser=firefox`/`webkit` genuinely isn't available in that
environment; fall back to `browser=chromium`, or report the limitation
rather than faking a result. Once installed, no `executablePath` override is
needed - just `firefox.launch({ args: ['--no-sandbox'] })` /
`webkit.launch({ args: ['--no-sandbox'] })`.

`--no-sandbox` is required in most containerized/CI environments regardless
of engine (the sandbox needs privileges those environments don't have).

## Step 3: Launch the dev server and poll, don't sleep

```bash
npm run dev > /tmp/dev-server.log 2>&1 &
echo $! > /tmp/dev-server.pid
timeout 30 bash -c 'until curl -sf http://localhost:5173/ >/dev/null; do sleep 1; done'
```

Vite/webpack-dev-server compile-on-demand means the *first* navigation can
still take 10s+ even after the port answers — `wait-for` the actual DOM
content you need in the driver script, don't assume "port is up" means "page
is ready."

## Step 4: Drive it — nav, wait, act, screenshot, check console

The only thing that changes per `browser=` value is which Playwright browser
type you import and launch - `goto`/`waitForSelector`/`click`/`screenshot`/
console capture are identical across all three engines, that uniform API is
Playwright's main advantage over Puppeteer (Chromium-only by default) or
Selenium (a much heavier, less ergonomic WebDriver-protocol API) for this
kind of ad hoc, throwaway verification script.

### JS driver (`lang=js`, default)

```js
// swap require line + .launch() call per `browser=`:
//   chromium -> const { chromium } = require(...); chromium.launch({ executablePath: ... })
//   firefox  -> const { firefox }  = require(...); firefox.launch({ args: ['--no-sandbox'] })
//   webkit   -> const { webkit }   = require(...); webkit.launch({ args: ['--no-sandbox'] })
const { chromium } = require('/tmp/smoke-test-pw/node_modules/playwright');

(async () => {
  const browser = await chromium.launch({ executablePath: '/usr/bin/google-chrome', args: ['--no-sandbox'] });
  const page = await browser.newPage();

  const consoleLines = [];
  const pageErrors = [];
  page.on('console', (msg) => consoleLines.push(msg.text()));
  page.on('pageerror', (err) => pageErrors.push(String(err)));

  await page.goto('http://localhost:5173/');
  await page.waitForSelector('h1'); // replace with real "app is up" signal
  await page.screenshot({ path: '/tmp/smoke-test-pw/screenshot-1-initial.png' });

  // Exercise the specific code path(s) the change actually touched -
  // not the whole app. One or two representative interactions.
  await page.click('#some-action-button');
  await page.waitForSelector('#expected-result');
  await page.screenshot({ path: '/tmp/smoke-test-pw/screenshot-2-after-action.png' });

  console.log('pageerror count:', pageErrors.length);
  console.log('console lines matching regression pattern:',
    consoleLines.filter((l) => /pattern-that-would-indicate-the-old-bug/i.test(l)));

  await browser.close();
})();
```

Run it with `node /tmp/smoke-test-pw/driver.js`.

### Python driver (`lang=python`)

Same shape, sync API (simplest for a one-off script - use `async_api` only
if you need concurrency):

```bash
pip install --user playwright
python -m playwright install firefox   # skip for chromium if using the system-binary shortcut
```

```python
from playwright.sync_api import sync_playwright

console_lines = []
page_errors = []

with sync_playwright() as p:
    # chromium -> p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    # firefox  -> p.firefox.launch(args=["--no-sandbox"])
    # webkit   -> p.webkit.launch(args=["--no-sandbox"])
    browser = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    page = browser.new_page()
    page.on("console", lambda msg: console_lines.append(msg.text))
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    page.goto("http://localhost:5173/")
    page.wait_for_selector("h1")
    page.screenshot(path="/tmp/smoke-test-pw/screenshot-1-initial.png")

    page.click("#some-action-button")
    page.wait_for_selector("#expected-result")
    page.screenshot(path="/tmp/smoke-test-pw/screenshot-2-after-action.png")

    print("pageerror count:", len(page_errors))
    print("matching console lines:",
          [l for l in console_lines if "pattern-that-would-indicate-the-old-bug" in l.lower()])

    browser.close()
```

**Check the captured console/pageerror output before declaring success.** A
page can render its shell while every data fetch silently 500s or a
background handler throws.

## Step 5: Tear down

```bash
kill "$(cat /tmp/dev-server.pid)" 2>/dev/null || pkill -f "vite$"
curl -sf http://localhost:5173/ && echo "still up!" || echo "confirmed down"
```

## Beyond smoke testing

The same nav→screenshot loop, with no click/assert steps at all, is a
perfectly good generic **headless cross-browser renderer** - e.g. generating
a preview image of a page, checking how a layout renders in Firefox vs
Chromium without installing either as a desktop app, or capturing a PDF
(`page.pdf(...)`, Chromium only). Nothing about Steps 1-4 is
testing-specific; "click a button and assert on the result" is just one use
of "drive a real browser headlessly and get output back."

## Gotchas that recur

- **React/Vue controlled inputs.** Setting `.value` directly via `page.evaluate`
  doesn't fire the framework's change handler. Use `page.fill()`/`page.type()` -
  they go through the real input event pipeline.
- **Slow first paint.** Vite/webpack compile routes on demand; `waitForSelector`
  handles it, a raw `sleep` doesn't - and don't shorten the default timeout
  below ~10s for the first navigation.
- **Screenshot cropping.** For a diff scoped to one component, screenshot just
  that element (`page.locator(sel).screenshot(...)`) instead of the full page -
  easier to eyeball.
- **`--no-sandbox` isn't optional** in most container/CI sandboxes - Chrome's
  sandbox syscalls need privileges those environments don't grant, and the
  launch will hang or fail without it.
- **Firefox/WebKit can't reuse a system browser install.** Unlike Chromium,
  don't waste time hunting for a system Firefox/Safari binary to point
  `executablePath` at - Playwright needs its own patched build for those two
  engines, full stop.

## Worked example

Validated during a Parcel→Vite migration on a vanilla-JS OpenLayers map app
(no framework, no existing test suite), `browser=chromium`: `chromium-cli`
wasn't present, `npx playwright` refused to auto-install, Playwright's own
browser cache didn't match what was requested - the working path was exactly
Steps 1-2 above (scratch `npm install playwright@1.61.1 --no-save`, launch
against `/usr/bin/google-chrome`). The driver navigated the dev server,
exercised two rewritten code paths via real clicks, took 3 screenshots, and
scanned all captured console lines for a regex matching the class of
regression the migration could have introduced. Zero `pageerror`s, zero
matches, confirmed real production API calls succeeded in the screenshots.
Total driver script was ~40 lines; total wall-clock cost was a few minutes,
dominated by the scratch `npm install`, not browser interaction.
