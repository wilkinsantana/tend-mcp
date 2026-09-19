// Disposable real-browser native contract check of the independently packaged ZIP.
// APIs are intercepted; this is not live panel, worker, or core HostApi acceptance.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdtemp, rm } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const temporary = await mkdtemp(join(tmpdir(), 'mcp-native-browser-'));
let server, browser;
try {
  const python = process.env.PYTHON_BIN || 'python3';
  const archive = join(temporary, 'ui.zip');
  execFileSync(python, ['scripts/pack-native-ui.py', '--output', archive], { cwd: root, stdio: 'pipe' });
  const { manifest, module } = JSON.parse(execFileSync(python, ['-c',
    'import json,sys,zipfile\nwith zipfile.ZipFile(sys.argv[1]) as z:\n assert sorted(z.namelist()) == ["extension.json", "index.js"]\n print(json.dumps({"manifest":json.loads(z.read("extension.json")),"module":z.read("index.js").decode()}))',
    archive], { encoding: 'utf8', maxBuffer: 2 * 1024 * 1024 }));
  assert.equal(manifest.id, 'host.tend.mcp');
  assert.deepEqual(manifest.permissions, ['network']);
  assert.equal(manifest.integrity['index.js'], `sha256-${createHash('sha256').update(module).digest('base64')}`);
  server = createServer((request, response) => {
    response.setHeader('Cache-Control', 'no-store');
    if (request.url === '/') {
      response.setHeader('Content-Type', 'text/html');
      response.end('<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><button id="outside">Outside</button><main></main><script type="module">import activate from "/component.js";window.component=activate({});component.mount(document.querySelector("main"));</script>');
    } else if (request.url === '/component.js') {
      response.setHeader('Content-Type', 'text/javascript'); response.end(module);
    } else { response.writeHead(404); response.end(); }
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
  browser = await chromium.launch({ headless: true });
  for (const locale of ['en', 'es']) for (const width of [375, 1024]) {
    const context = await browser.newContext({ viewport: { width, height: 900 } });
    const page = await context.newPage();
    const errors = [], posts = [], unexpected = [];
    let clients = [], unavailable = false, uncertain = false;
    const token = 'tend_mcp_browser_fixture_only';
    const ui = locale === 'en'
      ? { add: 'Add assistant', name: 'Assistant name', consent: 'I approve these read-only permissions for the selected apps.', create: 'Create credential', copy: 'Copy credential', close: 'Dismiss and clear', revoke: 'Revoke', confirm: 'Type the assistant name to revoke access', refresh: 'Refresh', cancel: 'Cancel', error: 'Request failed.' }
      : { add: 'Añadir asistente', name: 'Nombre del asistente', consent: 'Autorizo estos permisos de solo lectura para las aplicaciones seleccionadas.', create: 'Crear credencial', copy: 'Copiar credencial', close: 'Cerrar y borrar', revoke: 'Revocar', confirm: 'Escribe el nombre del asistente para revocar el acceso', refresh: 'Actualizar', cancel: 'Cancelar', error: 'La solicitud falló.' };
    await page.addInitScript(({ locale }) => {
      document.addEventListener('DOMContentLoaded', () => { document.documentElement.lang = locale; }, { once: true });
      window.copies = [];
      Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async (text) => { window.copies.push(text); } } });
    }, { locale });
    page.on('pageerror', (error) => errors.push(error.message));
    await page.route('**/*', async (route) => {
      const request = route.request(), url = new URL(request.url());
      if (url.origin !== origin) { unexpected.push('external'); await route.abort(); return; }
      if (!url.pathname.startsWith('/api/')) { await route.continue(); return; }
      if (request.method() === 'POST') {
        assert.equal(request.headers()['x-csrf-token'], 'browser-fixture-csrf');
        posts.push({ path: url.pathname, body: request.postDataJSON() });
      }
      let body;
      if (url.pathname === '/api/auth/me') body = { csrf_token: 'browser-fixture-csrf' };
      else if (url.pathname === '/api/mcp/status') body = { ready: !unavailable, installed: true, phase: 'ready', version: '0.1.0', endpoint: `${origin}/mcp` };
      else if (url.pathname === '/api/mcp/apps') body = { apps: [{ id: 'app-a', name: '<b>App A</b>' }, { id: 'app-b', name: 'App B' }] };
      else if (url.pathname === '/api/mcp/clients' && request.method() === 'GET') body = { clients };
      else if (url.pathname === '/api/mcp/clients' && request.method() === 'POST') {
        const input = request.postDataJSON();
        assert.ok(input.name.length <= 64);
        assert.deepEqual(input.app_ids, ['app-a']); assert.equal(input.accepted, true);
        clients = [{ client_id: 'fixture-client', name: input.name, expires_at: 123, permissions: [] }];
        body = { ...clients[0], endpoint: `${origin}/mcp`, token };
        if (uncertain) { await route.abort('failed'); return; }
      } else if (url.pathname === '/api/mcp/clients/fixture-client/revoke') {
        assert.equal(request.postDataJSON().confirm_name, clients[0].name);
        clients = []; body = { ok: true };
      } else { unexpected.push(url.pathname); await route.abort(); return; }
      await route.fulfill({ json: body, headers: { 'Cache-Control': 'no-store' } });
    });
    await page.goto(origin);
    // Select explicitly; the HostApi has no locale field and document may start unset.
    await page.getByRole('combobox').selectOption(locale);
    await page.getByRole('button', { name: ui.add, exact: true }).click();
    const dialog = page.getByRole('dialog');
    await page.getByLabel(ui.name, { exact: true }).waitFor();
    assert.equal(await page.getByLabel(ui.name, { exact: true }).getAttribute('maxlength'), '64');
    assert.equal(await page.getByRole('button', { name: ui.create, exact: true }).isDisabled(), true);
    assert.equal(await dialog.locator('input:checked').count(), 0);
    assert.equal(await page.locator('b').count(), 0, 'app names stay text');
    await page.mouse.click(1, 1);
    assert.equal(await dialog.count(), 1, 'backdrop cannot dismiss');
    await page.getByLabel(ui.name, { exact: true }).focus();
    await page.keyboard.press('Shift+Tab');
    assert.equal(await page.evaluate(() => document.activeElement.id === 'outside'), false, 'background stays inert');
    await page.getByLabel(ui.name, { exact: true }).fill('a'.repeat(64));
    await page.getByLabel('<b>App A</b>', { exact: true }).check();
    await page.getByLabel(ui.consent, { exact: true }).check();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'no document overflow');
    assert.equal(await dialog.evaluate((node) => node.scrollWidth <= node.clientWidth + 1), true, 'no dialog overflow');
    await page.getByRole('button', { name: ui.create, exact: true }).click();
    await page.locator('textarea').waitFor();
    assert.equal(posts.length, 1);
    assert.equal(posts[0].body.name.length, 64);
    assert.equal(await page.evaluate(() => window.copies.length), 0);
    await page.getByRole('button', { name: ui.copy, exact: true }).click();
    await page.waitForFunction(() => window.copies.length === 1);
    assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
    const secretInput = await page.locator('textarea').elementHandle();
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.querySelector('dialog'));
    assert.equal(await secretInput.evaluate((node) => node.value), '');
    await page.getByRole('button', { name: ui.refresh, exact: true }).click();
    await page.getByRole('button', { name: `${ui.revoke}: ${'a'.repeat(64)}`, exact: true }).click();
    await page.getByLabel(ui.confirm, { exact: true }).fill('wrong');
    await dialog.getByRole('button', { name: ui.revoke, exact: true }).click();
    assert.equal(posts.length, 1);
    await page.getByLabel(ui.confirm, { exact: true }).fill('a'.repeat(64));
    await dialog.getByRole('button', { name: ui.revoke, exact: true }).click();
    await page.waitForFunction(() => !document.querySelector('dialog'));
    assert.equal(posts.length, 2);
    // A lost POST response must not automatically retry; recover via inventory.
    uncertain = true;
    await page.getByRole('button', { name: ui.add, exact: true }).click();
    await page.getByLabel(ui.name, { exact: true }).fill('uncertain-fixture');
    await page.getByLabel('<b>App A</b>', { exact: true }).check();
    await page.getByLabel(ui.consent, { exact: true }).check();
    await page.getByRole('button', { name: ui.create, exact: true }).click();
    await dialog.getByRole('status').filter({ hasText: ui.error }).waitFor();
    assert.equal(posts.length, 3);
    await dialog.getByRole('button', { name: ui.cancel, exact: true }).click();
    unavailable = true;
    await page.getByRole('button', { name: ui.refresh, exact: true }).click();
    await page.waitForFunction((label) => [...document.querySelectorAll('button')].some((b) => b.textContent === label && b.disabled), ui.add);
    assert.equal(posts.length, 3);
    await page.evaluate(() => window.component.unmount());
    assert.equal(await page.locator('dialog, textarea, .tend-mcp-ui').count(), 0);
    assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
    await context.close();
  }
  console.log('PASS: packaged native UI EN/ES desktop/mobile, name64, explicit app consent, native dialog/backdrop, one-time credential/clipboard/clearing, named revoke, uncertain-result/no-retry and unavailable state. API fixtures only; no live panel/worker acceptance claimed.');
} finally {
  await browser?.close();
  if (server) await new Promise((resolve) => server.close(resolve));
  await rm(temporary, { recursive: true, force: true });
}
