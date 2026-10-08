// AgentOS extension service worker: keeps a WebSocket to the local AgentOS gateway and executes its commands.
// Protocol: gateway → {id, cmd, args}; we reply {id, ok, result|error}. We also push {type:"tabs"} on tab changes.
import {snapshotFn, actFn, bannerFn} from './page.js';

const DEFAULT_URL = 'ws://127.0.0.1:7860/ws/extension';
const WRITE = new Set(['click', 'type', 'press', 'select', 'set_code']);
let ws = null;
let allowlist = [];
let status = 'disconnected';

// config: popup (chrome.storage) wins; else config.json bundled next to the extension (the Linux test VM writes one
// per profile: token + url + profile, so no pairing by hand); profile = this Chrome profile's name for AgentOS
async function bundled() {
  try { return await (await fetch(chrome.runtime.getURL('config.json'))).json(); } catch { return {}; }
}
async function email() {
  try { return (await chrome.identity.getProfileUserInfo({accountStatus: 'ANY'})).email || ''; } catch { return ''; }
}
async function cfg() {
  const c = await chrome.storage.local.get(['token', 'url', 'profile']);
  const b = await bundled();
  const mail = await email();
  const profile = (c.profile || b.profile || (mail ? mail.split('@')[0] : '') || 'default').trim();
  return {token: c.token || b.token || '', url: c.url || b.url || DEFAULT_URL, profile, email: mail};
}

function setStatus(s) {
  status = s;
  chrome.action.setBadgeText({text: s === 'connected' ? 'ON' : ''});
  chrome.action.setBadgeBackgroundColor({color: s === 'connected' ? '#16a34a' : '#9ca3af'});
}

async function connect() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  const {token, url, profile, email: mail} = await cfg();
  if (!token) { setStatus('no token'); return; }
  try { ws = new WebSocket(`${url}?token=${encodeURIComponent(token)}&profile=${encodeURIComponent(profile)}`); }
  catch (e) { setStatus('error'); return; }
  ws.onopen = async () => {
    setStatus('connected');
    ws.send(JSON.stringify({type: 'hello', version: chrome.runtime.getManifest().version, browser: navigator.userAgent,
                            profile, email: mail}));
    pushTabs();
  };
  ws.onclose = (e) => { setStatus(e.code === 4401 ? 'bad token' : 'disconnected'); ws = null; };
  ws.onerror = () => setStatus('error');
  ws.onmessage = async (m) => {
    let msg; try { msg = JSON.parse(m.data); } catch { return; }
    if (msg.type === 'config') { allowlist = msg.allowlist || []; return; }
    if (!msg.id) return;
    try { reply(msg.id, true, await handle(msg.cmd, msg.args || {})); }
    catch (e) { reply(msg.id, false, null, String(e && e.message || e)); }
  };
}

function reply(id, ok, result, error) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({id, ok, result, error}));
}

const tabInfo = (t) => ({id: t.id, windowId: t.windowId, index: t.index, url: t.url || '', title: t.title || '',
                         active: !!t.active, favIconUrl: t.favIconUrl || ''});

async function listTabs() {
  const tabs = await chrome.tabs.query({});
  return tabs.filter(t => !String(t.url || '').startsWith('chrome-extension://')).map(tabInfo);
}

let pushTimer = null;
function pushTabs() {
  clearTimeout(pushTimer);
  pushTimer = setTimeout(async () => {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({type: 'tabs', tabs: await listTabs()}));
  }, 300);
}

function allowed(url) {
  try { const h = new URL(url).hostname.toLowerCase(); return allowlist.some(d => h === d || h.endsWith('.' + d)); }
  catch { return false; }
}

async function exec(tabId, func, args, world = 'ISOLATED') {
  const [r] = await chrome.scripting.executeScript({target: {tabId}, func, args: args || [], world});
  return r ? r.result : null;
}

function waitLoaded(tabId, ms = 15000) {
  return new Promise((resolve) => {
    const done = () => { chrome.tabs.onUpdated.removeListener(l); clearTimeout(t); resolve(); };
    const l = (id, info) => { if (id === tabId && info.status === 'complete') done(); };
    const t = setTimeout(done, ms);
    chrome.tabs.onUpdated.addListener(l);
  });
}

async function handle(cmd, a) {
  switch (cmd) {
    case 'tabs.list': return {tabs: await listTabs()};
    case 'tabs.open': {
      const t = await chrome.tabs.create({url: a.url || 'about:blank', active: true});
      if (a.url && a.url !== 'about:blank') await waitLoaded(t.id);
      return tabInfo(await chrome.tabs.get(t.id));
    }
    case 'tabs.activate': {
      const t = await chrome.tabs.update(a.tab_id, {active: true});
      await chrome.windows.update(t.windowId, {focused: true});
      return tabInfo(t);
    }
    case 'tabs.close': await chrome.tabs.remove(a.tab_id); return {ok: true};
    case 'screenshot': {
      const t = await chrome.tabs.update(a.tab_id, {active: true});
      await chrome.windows.update(t.windowId, {focused: true});
      await new Promise(r => setTimeout(r, 250));
      const url = await chrome.tabs.captureVisibleTab(t.windowId, {format: 'png'});
      return {png_base64: url.split(',')[1], url: t.url, title: t.title};
    }
    case 'snapshot': {
      const t = await chrome.tabs.get(a.tab_id);
      const snap = await exec(a.tab_id, snapshotFn);
      return {...snap, tabs: (await listTabs()).map(x => x.url)};
    }
    case 'banner': return {ok: await exec(a.tab_id, bannerFn, [!!a.on])};
    case 'act': {
      const t = await chrome.tabs.get(a.tab_id);
      if (a.action === 'goto') {
        await chrome.tabs.update(a.tab_id, {url: a.url});
        await waitLoaded(a.tab_id);
        await exec(a.tab_id, bannerFn, [true]).catch(() => {});
        const t2 = await chrome.tabs.get(a.tab_id);
        return {ok: true, url: t2.url, title: t2.title};
      }
      if (a.action === 'wait') { await new Promise(r => setTimeout(r, Math.min(a.ms || 1000, 30000))); return {ok: true}; }
      // read-only by default: acting on a site that isn't allowlisted needs the gateway's approval flag
      if (WRITE.has(a.action) && !allowed(t.url) && !a.approved) {
        return {ok: false, error: `read-only on ${new URL(t.url).hostname} (not in allowlist, not approved)`};
      }
      const r = await exec(a.tab_id, actFn, [a], a.action === 'set_code' ? 'MAIN' : 'ISOLATED');
      const t2 = await chrome.tabs.get(a.tab_id);
      return {...(r || {ok: false, error: 'no result'}), url: t2.url, title: t2.title};
    }
    default: throw new Error(`unknown command ${cmd}`);
  }
}

chrome.tabs.onCreated.addListener(pushTabs);
chrome.tabs.onRemoved.addListener(pushTabs);
chrome.tabs.onUpdated.addListener((id, info) => { if (info.status === 'complete' || info.title) pushTabs(); });
chrome.tabs.onActivated.addListener(pushTabs);

// MV3 workers sleep: reconnect on a timer and on startup
chrome.alarms.create('agentos-keepalive', {periodInMinutes: 0.5});
chrome.alarms.onAlarm.addListener(() => connect());
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);
chrome.storage.onChanged.addListener(() => { if (ws) ws.close(); ws = null; connect(); });
chrome.runtime.onMessage.addListener((msg, _s, send) => {
  if (msg === 'status') { send({status}); return true; }
  if (msg === 'reconnect') { if (ws) ws.close(); ws = null; connect().then(() => send({status})); return true; }
});
connect();
