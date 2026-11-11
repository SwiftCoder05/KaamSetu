# KaamSetu (Python / Django)

Job-hunt outreach app built entirely in Python with Django. You import recruiter contacts, write email templates, attach a resume, and send from your own Gmail. Every step is safe and honest about what happened.

Small amounts of browser JavaScript are used only where a page needs them: the rich email editor, the campaign progress loop, and the resume editor (Alpine.js).

## Stack

| Part | Choice | Why |
|---|---|---|
| Web framework | Django 5.2 (LTS) | Login, sessions, ORM, migrations, admin, forms and CSRF protection built in |
| Database | SQLite by default; PostgreSQL via `DATABASE_URL` | Zero setup locally; Postgres (e.g. Supabase) for production |
| UI | Django templates + hand-written CSS; Alpine.js (CDN) for the resume editor | No Node or build step |
| Email | Gmail SMTP with an app password (`smtplib`) | No Google Cloud Console project needed |
| Credential storage | Fernet encryption (`cryptography`) | App passwords are never stored in plain text |
| HTML safety | `nh3` allow-list sanitiser | Email HTML is cleaned when saved and again when sent |
| File parsing | `csv`, `openpyxl`, `xlrd`, `python-docx`, `olefile`, `pypdf`, `pytesseract` | CSV, Excel, Word, PDF and screenshot imports |
| Resume export | `python-docx`, `fpdf2` | DOCX and PDF |
| AI (optional) | Ollama (free, local open models) or Gemini, via `httpx` | Opt-in, and every suggestion passes an anti-fabrication guard |
| Validation | `pydantic` | Resume data and AI output are strictly validated |
| Tests | `pytest` + `pytest-django` | 138 tests |

## Run it locally (Windows)

```powershell
cd kaamsetu-py
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env        # then fill in SECRET_KEY and TOKEN_ENCRYPTION_KEY (commands are in the file)
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py runserver
```

Open http://127.0.0.1:8000 and create an account.

Optional:

- **Demo account:** `.\.venv\Scripts\python.exe manage.py seed_demo --password "choose-one"` creates `test@kaamsetu.dev` with sample contacts, templates and a resume.
- **Admin:** `.\.venv\Scripts\python.exe manage.py createsuperuser`, then open `/admin/`.

## Using it

1. **Settings → Gmail sending.** Turn on 2-Step Verification on your Google account, create an app password at https://myaccount.google.com/apppasswords, and paste it in. The app logs in once to check it and sends nothing. The password is stored encrypted.
2. **Contacts → Add contacts.** Paste addresses or upload CSV, TXT, XLSX, XLS, DOCX, DOC, PDF, PNG or JPG. You review a preview with duplicates and invalid addresses marked, then choose what to save. Nothing is saved before that.
3. **Templates.** Pick a role starter or a blank template. The editor has bold, italic, underline, strikethrough, UPPERCASE / lowercase / Title Case, lists, links and placeholder chips, with a live preview. Placeholders: `{{name}}`, `{{first_name}}`, `{{company}}`, `{{role}}`, `{{sender_name}}`. Add a fallback with a bar: `{{first_name|there}}`.
4. **Resume studio.** Import a PDF, DOCX, DOC or TXT, or start blank. The original file is kept unchanged, and the imported text becomes a locked first version. Edits create new versions. Export to PDF or Word.
5. **Campaigns → New.** Choose a template, recipients and an optional resume version. You preview every email (unfilled placeholders are flagged), tick the confirmation box and press Send. If no resume is attached, the first click shows a short warning and the second click sends.

## How sending works

- **Explicit confirmation.** Nothing is sent until the campaign owner ticks the box and confirms. Importing contacts or creating a draft never sends anything.
- **Small batches.** Emails go out 5 at a time with a short gap between messages. The campaign page drives the batches while it is open. To keep sending in the background, run:
  ```powershell
  .\.venv\Scripts\python.exe manage.py run_sender          # loops; or --once for Task Scheduler/cron
  ```
- **Daily limit.** The default is 40 accepted emails per rolling 24 hours, set in Settings (max 200). When the limit is reached the campaign pauses; resume it later.
- **Errors.**
  - Rate-limit and login errors pause the campaign and put the email back in the queue.
  - Brief network errors are retried.
  - Permanent errors fail only that recipient. "Retry failed" re-queues failed recipients.
  - A send interrupted mid-flight is marked failed with a note to check your Sent folder, never resent blindly.
- **Do-not-contact.** Contacts marked do-not-contact are excluded and skipped even if they are in a queued campaign.
- **Status meaning.** "Accepted" means Gmail accepted the message for delivery. It is not proof of inbox delivery. Replies and bounces arrive in your Gmail.
- **Attachment.** The resume PDF is generated once, at confirmation, so every recipient gets the version you previewed.

## AI tailoring (optional)

Two switches must both be on before anything is sent to a model:

- **AI is on in Settings.** It is off by default.
- **The button is pressed.** You press "Tailor with AI" on a specific resume version and job description. There is no background AI.

**ATS tailoring (resume studio → Tailor to a job):**

1. **Analyse.** The JD's ATS keywords are found by the model and a built-in skills dictionary. Only words that really appear in the JD count.
2. **Score.** Code (not the model) computes the match: the share of JD keywords in your resume, with required keywords counting double. You see the score before and live while you decide.
3. **Suggestions aimed at missing keywords.** Each missing skill is a separate suggestion, plus a headline matching the job title, a summary rewrite, reworded bullets and new bullet lines. Anything that adds no missing keyword is dropped, so the resume is not padded with filler.
4. **You decide each one.** Accept or Decline each suggestion; one decision never changes another. New skills and new lines are marked "accept only if true". Suggestions with numbers that are not in your resume are blocked (no invented metrics).
5. **Final resume, one page.** "Create final resume" unlocks once everything is decided. It is saved as a new version, and the PDF and Word exports tighten spacing automatically to stay on one page. If the accepted changes cannot fit on one page, nothing is created and you are asked to decline a few additions.

**AI email writer (templates).** In the template editor, the "AI email writer" panel can draft a new email from a job description, improve the current one, or shorten it. Pick a resume version and the AI may only mention facts from it. The same guard applies: invented numbers block the draft, unknown placeholders, `[blanks]` and new terms show warnings. The draft changes the editor only when you press "Use this draft" ("Restore my previous text" undoes it), and nothing is saved until you press Save.

Both features use the same provider setting, so switching `AI_PROVIDER` between `ollama`, `gemini` and `ollama_cloud` needs no code change. Ollama runs with structured output (the pydantic JSON schema is sent as `format`) and thinking turned off.

**Free local setup with Ollama:**

```powershell
# install from https://ollama.com, then:
ollama pull qwen3:4b      # default; runs on 8 GB machines. qwen3:8b is better but needs ~6 GB free RAM
```

**Ollama Cloud (hosted, no local RAM needed):** set `AI_PROVIDER=ollama_cloud`, `OLLAMA_API_KEY=<your key>` and optionally `OLLAMA_CLOUD_MODEL` in `.env`. Your resume text is sent to ollama.com only when you press "Tailor with AI". Keep the key in `.env`, never in code.

Set `AI_PROVIDER=ollama` (the default) in `.env` for local models. To use Gemini instead, set `AI_PROVIDER=gemini` and `GEMINI_API_KEY`. `AI_PROVIDER=none` disables AI.

## Image OCR

Reading emails from screenshots uses the Tesseract program, which pip cannot install. On Windows, install it from https://github.com/UB-Mannheim/tesseract/wiki and set `TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe` in `.env`. Without it, image uploads show a clear message and every other format still works.

## Sign-in, verification, 2FA and Google

Built on django-allauth, with pages restyled to match the app.

- **Email verification.** After sign-up nobody can sign in until they click the emailed link. In development the email is printed in the server log (`server.err.log`); set `EMAIL_HOST_USER` and `EMAIL_HOST_PASSWORD` in `.env` to send real emails.
- **Forgot password.** `/accounts/password/reset/` emails a one-time link. The page gives the same answer for registered and unregistered addresses, so it cannot be used to find out who has an account.
- **Two-factor login.** Settings → Sign-in & security → Two-factor login. Scan the QR code with an authenticator app (Google Authenticator, Microsoft Authenticator, Authy). You also get one-time backup codes; keep them somewhere safe. The 2FA secret is stored encrypted with `TOKEN_ENCRYPTION_KEY`. Changing security settings asks for your password again.
- **Google sign-in.** Create an OAuth client at console.cloud.google.com (APIs & Services → Credentials → OAuth client ID → Web application), add the redirect URI `http://localhost:8000/accounts/google/login/callback/` (your real domain online), and put the values in `.env` as `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`. The "Continue with Google" button appears once both are set. Google accounts that already have the same email as a password account are not merged automatically: sign in with your password, then link Google in Settings.
- **Demo account.** `seed_demo` marks its email as verified so it can sign in without a link.
- **Admin site.** `/admin/` still uses Django's own login without 2FA; restrict or disable it before deploying.

## Security

- **Per-user data.** Every query is filtered by the logged-in user, so other users' objects return 404. `tests/test_isolation.py` checks every URL.
- **Uploaded resumes.** They are stored outside any public URL (`private_media/`) and served only through an owner-checked download.
- **Request protection.** CSRF protection is on every form and JSON POST. The rate limiter covers login (10 / 5 min), signup, Gmail connect, imports and AI (12 / hour).
- **Header injection.** It is blocked: CR/LF is stripped from subjects and names, and recipients are validated.
- **DEBUG off.** The app refuses to start without `SECRET_KEY`, and refuses to encrypt or decrypt without `TOKEN_ENCRYPTION_KEY`. Secure cookies, HSTS and SSL redirect switch on automatically.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```

The 138 tests cover:

- Email validation, paste parsing and duplicate detection.
- Every import file type, including corrupt files and wrong extensions.
- Template rendering and HTML escaping, and XSS sanitising.
- MIME building and header injection, and SMTP error mapping.
- The send engine: confirmation gate, daily limit, retries, pause on auth error, exclusive claiming, stale sends, do-not-contact.
- Cross-user isolation.
- End-to-end views: signup, login throttling, import, templates, the full campaign flow with attachment, pause/resume/cancel, Gmail connect.
- Resume import and export, and the AI guard and opt-in rules.
- Ollama with mocked HTTP: success, not running, model missing, timeouts, malformed JSON, wrong shape, and the full tailoring flow. Also the template email writer and its guard.

## Deploying

- **Server.** Set `DEBUG=false` and fill in `SECRET_KEY`, `TOKEN_ENCRYPTION_KEY`, `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`. Run `python manage.py collectstatic`, then serve with a WSGI server (e.g. `gunicorn kaamsetu.wsgi` on Linux, or `waitress-serve kaamsetu.wsgi:application` on Windows).
- **Database.** Use Postgres: `pip install "psycopg[binary]"` and set `DATABASE_URL`.
- **Background sending.** Run `python manage.py run_sender` as a second process.
- **Uploads.** Keep `private_media/` on persistent disk, or move to object storage.

## Known limitations

- **Not tested end to end:** sending through a real Gmail account, a real Ollama or Gemini model, and how the HTML emails look in Gmail and Outlook.
- **Resume import is heuristic.** Columns, tables and graphics are not preserved (the original file is kept), and old `.doc` files give rough text only.
- **PDF export is Unicode only if a font is found.** It uses Arial or DejaVu when present; otherwise it falls back to Latin-1 characters.
- **The rate limiter is per process** (local-memory cache). For several server processes, switch `CACHES` to Redis or a database cache.
- **The daily limit can be exceeded slightly** if the browser loop and `run_sender` work on the same user at the same moment. Each email is still sent at most once.
- **UGC creator outreach** is a placeholder page.
