# VAVE Mobile Clients

There are two ways to drive VAVE from a phone. Pick whichever fits:

1. **The PWA (this document)** — a lightweight web app served by the API
   server itself at `/m`. No install toolchain, no build step, works in any
   modern phone browser, installable to the home screen.
2. **The native app** — a separately built Expo/React Native client living in
   `mobile/` (`com.vave.app`, see `mobile/package.json`). Built and released
   by the mobile developer with `build-apk.*`; it talks to the same `/api/*`
   endpoints and holds no coordination logic of its own.

Both are thin clients over one source of truth: the control plane.

## The PWA

Served from `mobile/pwa/` by the API server. There is no separate mobile
backend and no app-store build: the phone's browser loads the UI from
`http://<pc>:8765/m` and every data call goes to the same `/api/*`
endpoints over the same authenticated control plane.

## What the phone can do

- Sign in with a device token, or pair fresh with a one-time code.
- Submit goals (`POST /api/tasks` with `autoplan` + `run`) and watch steps
  complete, live over the SSE stream.
- Approve or deny held actions (destructive-looking ones need a second tap).
- Read notifications, mark them read, clear them all.
- Install to the home screen and run standalone.

## Server setup (on the computer that owns the model)

```powershell
venv\Scripts\python.exe -m assistant.api --host 0.0.0.0 --port 8765
```

Note the computer's LAN address (e.g. `192.168.1.20`). The phone must be on
the same Wi-Fi network.

## Pairing (on the phone)

1. Open `http://192.168.1.20:8765/m` in the phone browser (your address).
2. On the computer, mint a pairing code:
   ```powershell
   venv\Scripts\python.exe -m assistant.api --pair --port 8765
   ```
3. In the phone UI choose "Pair new device", enter the host and the code.
   The returned token is stored in the browser's local storage and used as
   the Bearer token for every API call.

Alternatively, if the token is already known, choose "I have a token" and
paste it. The UI validates it against `GET /api/status` before proceeding.

## Install as an app

- **Android (Chrome):** menu -> "Add to Home screen" / "Install app".
- **iOS (Safari):** Share -> "Add to Home Screen".

The service worker (`sw.js`) caches only the static shell. API responses
are never cached: a stale task list or a cached token-bearing response would
be wrong and a leak.

## Live updates

The app opens one `EventSource` to
`/api/events/stream?token=<token>` (query param, because `EventSource`
cannot set request headers — the server's auth middleware accepts it). On
any activity event the visible tab refreshes. A 15-second poll is the safety
net while the app is open, and SSE reconnects with backoff capped at 30s.

## Security notes

- **LAN only.** The PWA has no extra protection beyond the API: whoever holds
  a device token controls the computer. Do not expose port 8765 to the
  internet.
- Tokens are bearer secrets. Log out (footer button) clears local storage,
  but if a phone is lost, revoke it properly:
  `DELETE /api/devices/{id}/token` from the desktop GUI or the API.
- Tokens expire (30-day default) and rotate through
  `POST /api/auth/rotate`. When a token dies, the app returns to the login
  screen on its next 401/403.
- Approvals pause execution until decided: a destructive action can be held
  on the desktop and answered from the phone.
