// Functions injected into pages with chrome.scripting.executeScript. Each must be self-contained (no closures).
// Snapshot format matches browser_svc /snapshot so the AgentOS browser graph works unchanged.

export function snapshotFn() {
  const sel = 'a,button,input,select,textarea,summary,[role],[onclick],[contenteditable="true"],[tabindex]:not([tabindex="-1"])';
  document.querySelectorAll('[data-aos-ref]').forEach(e => e.removeAttribute('data-aos-ref'));
  const out = []; let n = 0;
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect(); const st = getComputedStyle(el);
    if (r.width < 1 || r.height < 1 || st.visibility === 'hidden' || st.display === 'none') continue;
    if (n >= 300) break;
    const ref = String(++n); el.setAttribute('data-aos-ref', ref);
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute('role') || ({a: 'link', button: 'button', select: 'combobox', textarea: 'textbox', summary: 'button'}[tag])
      || (tag === 'input' ? ((el.type === 'checkbox' || el.type === 'radio') ? el.type : (el.type === 'submit' || el.type === 'button') ? 'button' : 'textbox') : tag);
    const name = (el.getAttribute('aria-label') || el.getAttribute('alt') || el.getAttribute('title') ||
      el.getAttribute('placeholder') || el.getAttribute('name') || '').trim();
    const text = (el.innerText || el.value || '').trim().replace(/\s+/g, ' ').slice(0, 120);
    const item = {ref, role, name: name.slice(0, 120), tag, text};
    if (tag === 'a' && el.href) item.href = el.href.slice(0, 200);
    if (tag === 'input') item.value = String(el.value || '').slice(0, 80);
    out.push(item);
  }
  const text = (document.body ? document.body.innerText : '').replace(/\n{3,}/g, '\n\n').slice(0, 8000);
  return {url: location.href, title: document.title, elements: out, text};
}

// action runner: {action, ref, selector, text, key, dy, submit, value}
export function actFn(a) {
  const pick = () => {
    if (a.ref) return document.querySelector(`[data-aos-ref="${String(a.ref).replace(/[^0-9]/g, '')}"]`);
    if (a.selector) return document.querySelector(a.selector);
    return document.activeElement;
  };
  const el = pick();
  const fire = (t, type) => t.dispatchEvent(new Event(type, {bubbles: true}));
  switch (a.action) {
    case 'click':
      if (!el) return {ok: false, error: 'element not found (take a new snapshot)'};
      el.scrollIntoView({block: 'center'}); el.click(); return {ok: true};
    case 'type': {
      if (!el) return {ok: false, error: 'element not found'};
      el.focus();
      if (el.isContentEditable) el.textContent = a.text || '';
      else { const proto = Object.getPrototypeOf(el); const d = Object.getOwnPropertyDescriptor(proto, 'value');
             d && d.set ? d.set.call(el, a.text || '') : (el.value = a.text || ''); }
      fire(el, 'input'); fire(el, 'change');
      if (a.submit) { const f = el.form; if (f) (f.requestSubmit ? f.requestSubmit() : f.submit());
                      else el.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true})); }
      return {ok: true};
    }
    case 'select':
      if (!el) return {ok: false, error: 'element not found'};
      el.value = a.value || ''; fire(el, 'change'); return {ok: true};
    case 'press':
      (el || document.body).dispatchEvent(new KeyboardEvent('keydown', {key: a.key || 'Enter', bubbles: true}));
      (el || document.body).dispatchEvent(new KeyboardEvent('keyup', {key: a.key || 'Enter', bubbles: true}));
      return {ok: true};
    case 'scroll': window.scrollBy(0, a.dy == null ? 600 : a.dy); return {ok: true};
    case 'back': history.back(); return {ok: true};
    case 'set_code': {
      // MAIN-world access is needed for Monaco; this runs in the page's MAIN world (see background.js)
      try { const m = window.monaco && window.monaco.editor.getModels(); if (m && m.length) { m[0].setValue(a.text || ''); return {ok: true, editor: 'monaco'}; } } catch (e) {}
      try { const cm = document.querySelector('.CodeMirror'); if (cm && cm.CodeMirror) { cm.CodeMirror.setValue(a.text || ''); return {ok: true, editor: 'codemirror'}; } } catch (e) {}
      try { const ace = window.ace && document.querySelector('.ace_editor'); if (ace) { window.ace.edit(ace).setValue(a.text || '', 1); return {ok: true, editor: 'ace'}; } } catch (e) {}
      const ta = el && el.tagName === 'TEXTAREA' ? el : document.querySelector('textarea');
      if (ta) { ta.value = a.text || ''; fire(ta, 'input'); return {ok: true, editor: 'textarea'}; }
      return {ok: false, error: 'no code editor found on this page'};
    }
    default: return {ok: false, error: `unknown action ${a.action}`};
  }
}

export function bannerFn(on) {
  const id = '__agentos_banner__';
  let b = document.getElementById(id);
  if (!on) { if (b) b.remove(); return true; }
  if (!b) {
    b = document.createElement('div'); b.id = id;
    b.textContent = '🤖 AgentOS is controlling this tab';
    b.style.cssText = 'position:fixed;top:0;left:50%;transform:translateX(-50%);z-index:2147483647;background:#dc2626;' +
      'color:#fff;font:600 12px system-ui,sans-serif;padding:4px 12px;border-radius:0 0 8px 8px;pointer-events:none;' +
      'box-shadow:0 2px 8px rgba(0,0,0,.3)';
    (document.body || document.documentElement).appendChild(b);
  }
  return true;
}
