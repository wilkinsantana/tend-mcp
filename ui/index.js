// Native schema-2 entry point. No host storage, proxy or external runtime code.
const messages = {
  en: {
    title: 'Tend MCP', language: 'Language', refresh: 'Refresh', status: 'Status',
    ready: 'Ready', unavailable: 'Not ready', endpoint: 'Assistant endpoint', version: 'Version',
    scope: 'Read-only: app summary and deployment status for explicitly selected apps only. Sibling apps and server-wide health are denied. No writes or payments. Use a separate MCP bearer credential, never panel cookies or personal access tokens.',
    guidance: 'Enrollment requires a recent administrator sign-in. If refused, sign in again in the panel and retry. An uncertain enrollment may have succeeded: refresh inventory, revoke the named assistant, then enroll again.',
    add: 'Add assistant', name: 'Assistant name', apps: 'Allowed apps (select explicitly)', days: 'Expiry (days)',
    consent: 'I approve these read-only permissions for the selected apps.', create: 'Create credential',
    inventory: 'Assistants', empty: 'No assistants.', expires: 'Expires', revoke: 'Revoke',
    confirm: 'Type the assistant name to revoke access', cancel: 'Cancel', close: 'Dismiss and clear',
    once: 'Credential shown once. Save it securely now. After dismissal it cannot be recovered; revoke and enroll again if lost. Configure a Streamable HTTP MCP client with this endpoint and bearer token.',
    copy: 'Copy credential', copied: 'Copied. Your clipboard is outside this component; clear it after use.',
    error: 'Request failed. Refresh and check your administrator session and component readiness.',
    copyError: 'Copy failed. Select and copy the credential manually.', loading: 'Loading…', permissions: 'Permissions',
  },
  es: {
    title: 'Tend MCP', language: 'Idioma', refresh: 'Actualizar', status: 'Estado',
    ready: 'Listo', unavailable: 'No disponible', endpoint: 'Endpoint del asistente', version: 'Versión',
    scope: 'Solo lectura: resumen de aplicaciones y estado de despliegues únicamente para las aplicaciones seleccionadas. Se deniegan las aplicaciones hermanas y la salud global del servidor. Sin escrituras ni pagos. Usa una credencial MCP independiente, nunca cookies del panel ni tokens de acceso personal.',
    guidance: 'El registro requiere un inicio de sesión reciente del administrador. Si se rechaza, vuelve a iniciar sesión en el panel. Un registro incierto puede haber tenido éxito: actualiza el inventario, revoca el asistente por nombre y regístralo otra vez.',
    add: 'Añadir asistente', name: 'Nombre del asistente', apps: 'Aplicaciones permitidas (selección explícita)', days: 'Caducidad (días)',
    consent: 'Autorizo estos permisos de solo lectura para las aplicaciones seleccionadas.', create: 'Crear credencial',
    inventory: 'Asistentes', empty: 'No hay asistentes.', expires: 'Caduca', revoke: 'Revocar',
    confirm: 'Escribe el nombre del asistente para revocar el acceso', cancel: 'Cancelar', close: 'Cerrar y borrar',
    once: 'La credencial se muestra una sola vez. Guárdala ahora de forma segura. Al cerrar no se puede recuperar; revoca y registra de nuevo si se pierde. Configura un cliente MCP Streamable HTTP con este endpoint y token bearer.',
    copy: 'Copiar credencial', copied: 'Copiada. El portapapeles está fuera del componente; bórralo después de usarla.',
    error: 'La solicitud falló. Actualiza y comprueba la sesión de administrador y el estado del componente.',
    copyError: 'No se pudo copiar. Selecciona y copia la credencial manualmente.', loading: 'Cargando…', permissions: 'Permisos',
  },
};

export default function activate() {
  let root, controller, dialog, opener, secret = '', generation = 0;
  let locale = document.documentElement.lang.startsWith('es') ? 'es' : 'en';
  let status = null, apps = [], clients = [], busy = false;
  const t = (key) => messages[locale][key];
  const element = (tag, text, parent, attrs = {}) => {
    const node = document.createElement(tag);
    if (text !== null) node.textContent = text;
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    if (parent) parent.append(node);
    return node;
  };
  const button = (text, parent, action) => {
    const node = element('button', text, parent, { type: 'button' });
    node.addEventListener('click', action);
    return node;
  };
  function clearDialog() {
    secret = '';
    if (dialog) {
      for (const node of dialog.querySelectorAll('input, textarea')) node.value = '';
      dialog.replaceChildren();
      dialog.close();
      dialog.remove();
      dialog = null;
      if (opener?.isConnected) opener.focus();
      opener = null;
    }
  }
  function modal(title) {
    clearDialog();
    opener = document.activeElement;
    dialog = element('dialog', null, root, { 'aria-labelledby': 'mcp-dialog-title' });
    element('h2', title, dialog, { id: 'mcp-dialog-title' });
    element('p', '', dialog, { role: 'status', 'aria-live': 'polite' });
    dialog.addEventListener('cancel', (event) => {
      event.preventDefault();
      if (!busy) clearDialog();
    });
    // Callers populate before showModal; native focus trap, no backdrop handler.
    return dialog;
  }
  async function request(path, body) {
    const signal = controller.signal;
    const options = { credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal };
    if (body !== undefined) {
      const auth = await fetch('/api/auth/me', options);
      if (!auth.ok) throw new Error('request');
      const { csrf_token } = await auth.json();
      if (typeof csrf_token !== 'string' || !csrf_token) throw new Error('request');
      options.method = 'POST';
      options.headers = { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf_token };
      options.body = JSON.stringify(body);
    }
    const response = await fetch(path, options);
    if (!response.ok) throw new Error('request');
    return response.json();
  }
  function notice(text) {
    const node = dialog?.querySelector('[role="status"]') || root?.querySelector('[role="status"]');
    if (node) node.textContent = text;
  }
  async function refresh() {
    if (busy || !root) return;
    const current = generation;
    busy = true;
    notice(t('loading'));
    try {
      const results = await Promise.all([
        request('/api/mcp/status'), request('/api/mcp/apps'), request('/api/mcp/clients'),
      ]);
      if (current !== generation) return;
      [status] = results;
      apps = results[1].apps;
      // Whitelist inventory fields. Never retain or render an unexpected token.
      clients = results[2].clients.map(({ client_id, name, expires_at, permissions }) =>
        ({ client_id, name, expires_at, permissions }));
      render();
    } catch {
      if (current === generation) {
        status = null; apps = []; clients = [];
        render(); notice(t('error'));
      }
    } finally { if (current === generation) busy = false; }
  }
  function field(parent, title, type = 'text') {
    const label = element('label', title, parent);
    return element('input', null, label, { type });
  }
  async function submit(action, form) {
    if (busy || !form.reportValidity()) return;
    const current = generation;
    busy = true;
    const controls = [...dialog.querySelectorAll('button, input, select')];
    controls.forEach((node) => { node.disabled = true; });
    try { await action(current); }
    catch { if (current === generation) notice(t('error')); }
    finally {
      if (current === generation) {
        busy = false;
        controls.forEach((node) => { node.disabled = false; });
      }
    }
  }
  function enroll() {
    if (busy || !status?.ready) return;
    const box = modal(t('add'));
    element('p', t('guidance'), box);
    const form = element('form', null, box);
    const name = field(form, t('name'));
    name.required = true; name.maxLength = 80;
    const selection = element('fieldset', null, form);
    element('legend', t('apps'), selection);
    const choices = apps.map((app) => ({ id: app.id, node: field(selection, app.name, 'checkbox') }));
    const days = field(form, t('days'), 'number');
    days.min = '1'; days.max = '30'; days.step = '1'; days.value = '7'; days.required = true;
    const consent = field(form, t('consent'), 'checkbox'); consent.required = true;
    const create = element('button', t('create'), form, { type: 'submit' });
    create.disabled = true;
    selection.addEventListener('change', () => { create.disabled = !choices.some(({ node }) => node.checked); });
    button(t('cancel'), form, () => { if (!busy) clearDialog(); });
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      const app_ids = choices.filter(({ node }) => node.checked).map(({ id }) => id);
      if (!name.value.trim() || !app_ids.length || !consent.checked) return;
      void submit(async (current) => {
        const result = await request('/api/mcp/clients', {
          name: name.value.trim(), app_ids, expires_in_days: Number(days.value), accepted: true,
        });
        if (current !== generation) { result.token = ''; return; }
        if (typeof result.token !== 'string' || !result.token.startsWith('tend_mcp_')) throw new Error('request');
        const box = modal(t('once'));
        secret = result.token; result.token = '';
        element('p', result.name, box);
        element('p', result.endpoint, box);
        const value = element('textarea', null, box, { readonly: '', 'aria-label': t('create'), spellcheck: 'false' });
        value.value = secret;
        button(t('copy'), box, async () => {
          if (!secret) return;
          try { await navigator.clipboard.writeText(secret); if (current === generation && dialog === box) feedback.textContent = t('copied'); }
          catch { if (current === generation && dialog === box) feedback.textContent = t('copyError'); }
        });
        const feedback = element('p', '', box, { role: 'status' });
        button(t('close'), box, () => { clearDialog(); void refresh(); });
        box.showModal(); value.focus();
      }, form);
    });
    box.showModal(); name.focus();
  }
  function revoke(client) {
    if (busy) return;
    if (typeof client.client_id !== 'string' || !client.client_id || client.client_id.length > 256 || ['.', '..'].includes(client.client_id)) {
      notice(t('error')); return;
    }
    const box = modal(t('revoke'));
    element('p', client.name, box);
    const form = element('form', null, box);
    const confirmation = field(form, t('confirm')); confirmation.required = true;
    element('button', t('revoke'), form, { type: 'submit' });
    button(t('cancel'), form, () => { if (!busy) clearDialog(); });
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      if (confirmation.value !== client.name) { confirmation.setCustomValidity(t('confirm')); confirmation.reportValidity(); return; }
      void submit(async (current) => {
        await request(`/api/mcp/clients/${encodeURIComponent(client.client_id)}/revoke`, { confirm_name: confirmation.value });
        if (current !== generation) return;
        clearDialog();
        clients = clients.filter((item) => item.client_id !== client.client_id);
        render();
      }, form);
    });
    confirmation.addEventListener('input', () => confirmation.setCustomValidity(''));
    box.showModal(); confirmation.focus();
  }
  function render() {
    const restoreFocus = root.contains(document.activeElement);
    clearDialog(); root.replaceChildren(); root.lang = locale;
    element('style', '.tend-mcp-ui{padding:1rem;overflow:auto;max-height:100%;font:inherit;color:inherit}.tend-mcp-ui label{display:block;margin:.75rem 0}.tend-mcp-ui input:not([type=checkbox]),.tend-mcp-ui textarea{display:block;max-width:100%;width:28rem}.tend-mcp-ui button,.tend-mcp-ui select{font:inherit;padding:.4rem .7rem;margin:.25rem}.tend-mcp-ui dialog{max-width:min(40rem,90vw);max-height:85vh;overflow:auto;color:inherit;background:var(--bg-primary,Canvas)}.tend-mcp-ui dialog::backdrop{background:#0008}.tend-mcp-ui p,.tend-mcp-ui li{overflow-wrap:anywhere}.tend-mcp-ui :focus-visible{outline:3px solid Highlight;outline-offset:2px}', root);
    element('h1', t('title'), root);
    const language = element('label', t('language'), root);
    const select = element('select', null, language);
    for (const [code, label] of [['en', 'English'], ['es', 'Español']]) element('option', label, select, { value: code });
    select.value = locale;
    select.addEventListener('change', () => { locale = select.value; render(); });
    element('p', t('scope'), root);
    element('p', t('guidance'), root);
    element('p', `${t('status')}: ${status?.ready ? t('ready') : t('unavailable')}`, root);
    element('p', `${t('status')}: ${status?.phase || '—'}`, root);
    element('p', `${t('version')}: ${status?.version || '—'}`, root);
    element('p', `${t('endpoint')}: ${status?.endpoint || '—'}`, root);
    element('p', '', root, { role: 'status', 'aria-live': 'polite' });
    const refreshButton = button(t('refresh'), root, () => void refresh());
    if (restoreFocus) refreshButton.focus();
    button(t('add'), root, enroll).disabled = !status?.ready;
    element('h2', t('inventory'), root);
    if (!clients.length) element('p', t('empty'), root);
    const list = element('ul', null, root);
    for (const client of clients) {
      const item = element('li', null, list);
      element('h3', client.name, item);
      element('p', `${t('expires')}: ${client.expires_at}`, item);
      const permissions = element('ul', null, item, { 'aria-label': t('permissions') });
      for (const permission of client.permissions) element('li', `${permission.capability} · ${permission.resource_id}`, permissions);
      button(`${t('revoke')}: ${client.name}`, item, () => revoke(client));
    }
  }
  return {
    mount(container) {
      generation++; controller = new AbortController(); busy = false;
      root = element('section', null, container, { 'aria-label': 'Tend MCP', class: 'tend-mcp-ui' });
      render(); void refresh();
    },
    unmount() {
      generation++; controller?.abort(); clearDialog();
      root?.remove(); root = null; status = null; apps = []; clients = []; busy = false;
    },
  };
}
