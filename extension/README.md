# AgentOS Chrome extension

Lets your AgentOS agents work in **your real Chrome tabs** (your profile, your logins): list tabs, open/switch/close
them, read pages (same element snapshot the browser agent uses), take screenshots, and — with approval — click,
type, scroll and paste code (`set_code`, e.g. LeetCode's editor).

## Install (one time, ~1 minute)
1. Open `chrome://extensions` in Chrome.
2. Turn on **Developer mode** (top-right).
3. Click **Load unpacked** and choose this folder: `C:\Users\raman\agent-platform\extension`.
4. In AgentOS (http://127.0.0.1:7860) open **Browser** → **Chrome extension** → **Copy token**.
5. Click the AgentOS puzzle icon in Chrome → paste the token → **Save & connect**. The badge shows **ON**.

## One extension per profile (full access to the profiles YOU want)
Chrome profiles are separate: install the extension in each profile you want AgentOS to use (same folder, same token)
and give it a name in the popup (e.g. `asdf`). In AgentOS → **Browser → Chrome extension** every connected profile is
listed with a switch **Allow AgentOS to use this profile** (default OFF) and its own allowlist. Only profiles that
are switched on are used: `browser_open(profile="asdf")` then works inside that profile's own tabs and logins.
The Linux test VM loads the extension by itself into its login windows (config.json with token + profile).

## Use
- Browser page → **Your tabs**: live list of your tabs. Click **Use this tab** to make it the agent's tab.
- In chat: "use my current tab and summarise it", "open these 3 links in tabs", "screenshot this tab".
- Settings → Browser mode **extension** makes every browser task use your real Chrome through the extension
  (falls back to the AgentOS Chrome profile when the extension isn't connected).

## Safety
- Reading and screenshots are allowed on any site; **clicking/typing on sites not in your allowlist needs an
  approval** in AgentOS (the extension itself refuses write actions without the gateway's approval flag).
- Add trusted domains on the Browser page (allowlist), e.g. `leetcode.com, github.com`.
- A red **"AgentOS is controlling this tab"** banner is shown on tabs being driven.
- The extension only talks to the gateway on `127.0.0.1` with your pairing token; regenerate the token to unpair.

## Protocol (for developers)
`ws://127.0.0.1:7860/ws/extension?token=…` — gateway sends `{id, cmd, args}`, extension replies
`{id, ok, result|error}`; extension pushes `{type:"hello"}` and `{type:"tabs", tabs:[…]}`.
Commands: `tabs.list`, `tabs.open{url}`, `tabs.activate{tab_id}`, `tabs.close{tab_id}`, `screenshot{tab_id}`,
`snapshot{tab_id}`, `act{tab_id, action, ref|selector, text, key, dy, submit, approved}`, `banner{tab_id, on}`.
Gateway side: `aap/extension.py`, `aap/api/routes_ext.py`, browser mode `extension` in `aap/tools/browser.py`.
