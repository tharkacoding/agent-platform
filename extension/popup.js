const $ = (id) => document.getElementById(id);
chrome.storage.local.get(['token', 'url', 'profile']).then((c) => {
  $('token').value = c.token || '';
  $('profile').value = c.profile || '';
  $('url').value = c.url || 'ws://127.0.0.1:7860/ws/extension';
});
const show = () => chrome.runtime.sendMessage('status', (r) => { $('status').textContent = 'Status: ' + ((r && r.status) || '?'); });
show();
$('save').onclick = async () => {
  await chrome.storage.local.set({token: $('token').value.trim(), url: $('url').value.trim(), profile: $('profile').value.trim()});
  $('status').textContent = 'Connecting…';
  setTimeout(show, 1500);
};
