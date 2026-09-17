# Connecting Google

VAVE reaches Drive, Gmail, Calendar, Docs, Sheets and Slides through one
Google account. The token lives on your computer and nowhere else: the phone
never holds it, and it is never put in a prompt or written to the timeline.

Until you finish the steps below, every Google screen shows **example data and
says so**. That is deliberate - an invented email that looks real is worse than
no email at all.

## What you have to do once

Only you can do this part; it happens in your Google account, not in the code.

**1. Make a project.** Open <https://console.cloud.google.com/>, create a
project (any name), and select it.

**2. Turn on the six APIs.** Under *APIs & Services → Library*, enable each of:

```
Google Drive API
Gmail API
Google Calendar API
Google Docs API
Google Sheets API
Google Slides API
```

An API that is not enabled fails at the moment you use it, not at sign-in, so
it is worth enabling all six now.

**3. Set up the consent screen.** *APIs & Services → OAuth consent screen*:

- User type **External** (unless you have a Workspace organisation, in which
  case **Internal** is simpler - it skips the test-user step).
- Fill in the app name and your own email.
- On the **Test users** step, add the Google address you will actually sign in
  with. An external app in testing only works for the accounts listed there.

You do not need to submit anything for verification. Verification is for
publishing an app to other people; this one is only for you.

**4. Create the credentials.** *APIs & Services → Credentials → Create
credentials → OAuth client ID*, application type **Desktop app**. Download the
JSON.

**5. Put it in the project.** Rename it and place it beside `main.py`:

```
/home/rav/Projects/vave/vave/credentials.json
```

That file is your client secret. It is listed in `.gitignore`, along with
`token.json` and `token_workspace.json`, so it stays on this machine and never
reaches the repository. Do not paste its contents into a chat or an issue.

**6. Connect.** Either:

- Desktop app → **Google** → **Connect Google**, or
- Phone → **Google Workspace** → **Connect your Google account** (the browser
  still opens on the computer, because that is where the token belongs), or
- from a shell: `python -c "from assistant.workspace import auth; print(auth.authorize())"`

A browser opens, you sign in, and Google shows the access being requested.
Approve it once. VAVE stores the token **encrypted** in its secret vault
(`secret://google_workspace_oauth`) and keeps `token_workspace.json` as a
fallback; an old plaintext token is imported into the vault automatically on
first use. Sign-in requests offline access, so the token refreshes on its own
after that — you sign in once, not every hour.

> Google will warn that the app is not verified. That is expected for an app
> you made for yourself. Choose **Advanced → Go to (your app name)**.

## What VAVE asks for, and why

| Scope | Why it is needed |
| --- | --- |
| `drive` | Search, read and create files in Drive |
| `gmail.modify` | Read mail, and write drafts. It also covers sending |
| `calendar` | See your schedule and add events |
| `documents` | Create and append to Google Docs |
| `spreadsheets` | Read and append to Sheets |
| `presentations` | Build Slides decks |

These are the narrowest scopes that still cover the features. Reading only
(`drive.readonly`, `gmail.readonly`) would rule out drafts, documents and
decks; the trade is stated here rather than buried.

## Checking it worked

```bash
python -c "from assistant.workspace.gateway import gateway; print(gateway.get_status())"
```

`state` is one of three things, and each says what to do next:

| State | Meaning |
| --- | --- |
| `not_configured` | No `credentials.json` yet. Do steps 1-5 |
| `needs_authorization` | Credentials are there; sign in once (step 6) |
| `live` | Google answers. `account` shows which address |

## What you can do once it is live

| Where | What |
| --- | --- |
| Desktop → Google → Drive | List and search your files, open one in the browser |
| Desktop → Google → Gmail | Read unread mail, draft a reply into Gmail |
| Desktop → Google → Calendar | See what is coming up |
| Desktop → Google → Docs | Create a document or a Slides deck in your Drive |
| Phone → Google Workspace | The same reads, and the connect flow |
| Ask VAVE | "summarize my unread emails", "put this in a doc", "make a deck about X" |

## The endpoints behind it

Every one needs a paired device token, like the rest of the API.

| Method | Path | Does |
| --- | --- | --- |
| `GET` | `/api/google/status` | Connected or not, and which account |
| `POST` | `/api/google/connect` | Start sign-in (browser opens on the computer) |
| `POST` | `/api/google/disconnect` | Forget this computer's token |
| `GET` | `/api/google/drive` | Recent Drive files |
| `GET` | `/api/google/drive/search?query=` | Find a file |
| `GET` | `/api/google/gmail?query=` | Search mail, Gmail query syntax |
| `GET` | `/api/google/calendar` | Upcoming events |
| `POST` | `/api/google/gmail/draft` | Write a draft |
| `POST` | `/api/google/gmail/send` | Send mail - **needs approval** |
| `POST` | `/api/google/calendar/events` | Create an event - **needs approval** |
| `POST` | `/api/google/drive/upload` | Upload a file - **needs approval** |
| `POST` | `/api/google/docs` | Create a document |
| `POST` | `/api/google/slides` | Create a presentation |

Every read answers with `live` beside the data:

```json
{"live": false,
 "notice": "Google isn't connected yet. These are examples, not your data.",
 "items": [...]}
```

## How approval works

Sending mail, creating a calendar event and uploading to Drive are things other
people see, so they do not happen on the first request:

```
POST /api/google/gmail/send        -> 202, and an approval appears
You approve it on the phone or the desktop
POST /api/google/gmail/send        -> 200, sent
POST /api/google/gmail/send again  -> 202, asks again
```

The approval is bound to a fingerprint of those exact arguments, so approving
*email Sam the budget* does not also authorise emailing someone else, and the
grant is spent by the send that uses it. One approval, one action.

## When something goes wrong

| What you see | What it means |
| --- | --- |
| `state: not_configured` after adding the file | It is not named exactly `credentials.json`, or not beside `main.py` |
| `access_denied` in the browser | Your address is not in **Test users** on the consent screen |
| `insufficient authentication scopes` | Scopes changed since you signed in. Delete `token_workspace.json` and connect again |
| Screens still say Demo mode | The token was never written, or was revoked. Check `get_status()` |
| `HttpError 403 ... has not been used in project` | That one API is not enabled. Go back to step 2 |

## What this does not do yet

- **Sheets and Slides have no desktop screen of their own.** They are reachable
  as tools and through the API; the Docs tab creates decks but does not list
  them.
- **No Shared Drives handling.** Searches cover My Drive. Shared Drives need
  `includeItemsFromAllDrives`, which is not passed yet.
- **Drive capabilities are not checked before offering an action.** A file you
  can only read still shows an Open button; Google refuses the write, and the
  refusal is what you see.
- **One account only.** There is no second-account switch.
