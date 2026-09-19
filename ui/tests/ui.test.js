import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import activate from '../index.js';

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
async function setup(overrides = {}) {
  const dom = new JSDOM('<html lang="en"><body><main></main></body></html>', { url: 'https://panel.invalid' });
  globalThis.document = dom.window.document;
  dom.window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  dom.window.HTMLDialogElement.prototype.close = function () { this.open = false; };
  const calls = [], copies = [];
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { clipboard: { writeText: async (value) => copies.push(value) } } });
  const inventory = { client_id: 'opaque/id', name: '<img src=x onerror=alert(1)>', expires_at: 123, permissions: [{ capability: 'mcp.apps.summary.read', resource_id: 'app-a', server_id: 'srv' }], token: 'inventory-must-not-render' };
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    if (overrides.fetch) return overrides.fetch(url, options);
    const values = {
      '/api/auth/me': { csrf_token: 'csrf-test' },
      '/api/mcp/status': { installed: true, ready: true, phase: 'ready', version: '0.1.0', endpoint: 'https://panel.invalid/mcp' },
      '/api/mcp/apps': { apps: [{ id: 'app-a', name: '<b>App A</b>' }, { id: 'app-b', name: 'App B' }] },
      '/api/mcp/clients': options.method === 'POST' ? { ...inventory, endpoint: 'https://panel.invalid/mcp', token: 'tend_mcp_test_only' } : { clients: [inventory] },
      '/api/mcp/clients/opaque%2Fid/revoke': { ok: true },
    };
    return { ok: true, json: async () => values[url] };
  };
  const component = activate({}); component.mount(document.querySelector('main'));
  await tick();
  const find = (text) => [...document.querySelectorAll('button')].find((node) => node.textContent === text);
  const change = (node) => node.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
  const submit = () => document.querySelector('dialog form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }));
  return { dom, component, calls, copies, find, change, submit };
}

function fill(ctx) {
  ctx.find('Add assistant').click();
  const inputs = document.querySelectorAll('dialog input');
  inputs[0].value = 'Assistant A'; inputs[1].checked = true; ctx.change(inputs[1]);
  inputs[4].checked = true;
}

test('fixed same-origin GETs, text-only inventory, explicit selection, CSRF and one-time secret', async () => {
  const ctx = await setup();
  assert.equal(document.querySelector('img'), null);
  assert.ok(!document.body.textContent.includes('inventory-must-not-render'));
  fill(ctx);
  assert.equal(document.querySelectorAll('dialog input:checked').length, 2);
  ctx.submit(); await tick();
  const post = ctx.calls.find(({ options }) => options.method === 'POST');
  assert.deepEqual(JSON.parse(post.options.body), { name: 'Assistant A', app_ids: ['app-a'], expires_in_days: 7, accepted: true });
  assert.equal(post.options.headers['X-CSRF-Token'], 'csrf-test');
  assert.ok(ctx.calls.every(({ options }) => options.cache === 'no-store' && options.credentials === 'same-origin' && options.redirect === 'error'));
  assert.equal(ctx.copies.length, 0);
  const textarea = document.querySelector('textarea');
  assert.equal(textarea.value, 'tend_mcp_test_only');
  ctx.find('Copy credential').click(); await tick();
  assert.deepEqual(ctx.copies, ['tend_mcp_test_only']);
  document.querySelector('dialog').click();
  assert.ok(document.querySelector('dialog').open, 'backdrop clicks do not close');
  ctx.find('Dismiss and clear').click(); await tick();
  assert.equal(textarea.value, '');
  assert.equal(document.querySelector('dialog'), null);
  ctx.component.unmount();
});

test('assistant name matches the core 64-character boundary even for programmatic submission', async () => {
  const ctx = await setup(); fill(ctx);
  const name = document.querySelector('dialog input');
  assert.equal(name.maxLength, 64);
  name.value = 'a'.repeat(65); ctx.submit(); await tick();
  assert.ok(!ctx.calls.some(({ options }) => options.method === 'POST'));
  name.value = 'a'.repeat(64); ctx.submit(); await tick();
  const post = ctx.calls.find(({ options }) => options.method === 'POST');
  assert.equal(JSON.parse(post.options.body).name, 'a'.repeat(64));
  ctx.component.unmount();
});

test('named revocation encodes opaque IDs, mismatch never posts', async () => {
  const ctx = await setup();
  [...document.querySelectorAll('button')].find((n) => n.textContent.startsWith('Revoke:')).click();
  const input = document.querySelector('dialog input');
  input.value = 'wrong'; ctx.submit(); await tick();
  assert.ok(!ctx.calls.some(({ options }) => options.method === 'POST'));
  input.value = '<img src=x onerror=alert(1)>';
  input.dispatchEvent(new ctx.dom.window.Event('input'));
  ctx.submit(); await tick();
  assert.equal(ctx.calls.at(-1).url, '/api/mcp/clients/opaque%2Fid/revoke');
  assert.deepEqual(JSON.parse(ctx.calls.at(-1).options.body), { confirm_name: input.defaultValue || '<img src=x onerror=alert(1)>' });
  ctx.component.unmount();
});

test('locale choice and unavailable/error fail closed with fixed messages', async () => {
  const ctx = await setup({ fetch: async () => ({ ok: false, json: async () => ({ detail: 'private-error' }) }) });
  assert.ok(ctx.find('Add assistant').disabled);
  assert.ok(!document.body.textContent.includes('private-error'));
  const select = document.querySelector('select'); select.value = 'es'; ctx.change(select);
  assert.ok(document.body.textContent.includes('Solo lectura'));
  ctx.component.unmount();
});

test('unmount fences late successful enrollment and clears memory/DOM', async () => {
  const ctx = await setup(); fill(ctx);
  let finish;
  const previous = globalThis.fetch;
  globalThis.fetch = async (url, options) => options.method === 'POST'
    ? new Promise((resolve) => { finish = resolve; }) : previous(url, options);
  ctx.submit(); await tick();
  ctx.component.unmount();
  finish({ ok: true, json: async () => ({ token: 'tend_mcp_late' }) }); await tick();
  assert.equal(document.querySelector('dialog'), null);
  assert.equal(document.querySelector('textarea'), null);
  assert.equal(ctx.copies.length, 0);
});

test('unmount clears a displayed credential; Escape clears without backdrop handling', async () => {
  for (const escape of [false, true]) {
    const ctx = await setup(); fill(ctx); ctx.submit(); await tick();
    const textarea = document.querySelector('textarea');
    if (escape) document.querySelector('dialog').dispatchEvent(new ctx.dom.window.Event('cancel', { cancelable: true }));
    else ctx.component.unmount();
    assert.equal(textarea.value, '');
    assert.equal(document.querySelector('dialog'), null);
    ctx.component.unmount();
  }
});
