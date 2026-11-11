<div align="center">

# 🌉 KaamSetu

### *Your bridge to the job you want.*

Import recruiter contacts ✉️ → write smart email templates 📝 → tailor your resume with AI 🤖 → send from your own Gmail 🚀

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![Django](https://img.shields.io/badge/Django-5.2_LTS-092E20?logo=django&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-ready-4169E1?logo=postgresql&logoColor=white)
![Ollama](https://img.shields.io/badge/AI-Ollama_%7C_Gemini-black?logo=ollama&logoColor=white)
![Status](https://img.shields.io/badge/status-active-success)

</div>

---

## ✨ Why KaamSetu?

Job hunting means sending the same kind of email dozens of times, tweaking your resume for every role, and praying you didn't paste the wrong company name. KaamSetu automates the boring parts and keeps you in control of the rest.

| 😩 The problem | 💡 What KaamSetu does |
|---|---|
| Copy-pasting recruiter emails from everywhere | Import from **CSV, Excel, Word, PDF, even screenshots** 📸 |
| Writing the same email again and again | **Templates with placeholders** like `{{first_name}}` and `{{company}}` |
| Resume never matches the job description | **ATS keyword scoring + AI suggestions**, with no invented facts |
| Fear of spamming or sending by mistake | **Explicit confirmation, daily limits, small batches** |
| Worry about privacy | **Encrypted credentials, per-user data isolation** 🔒 |

---

## 🚀 Features

### 📇 Contacts
- Paste addresses or upload `CSV` `TXT` `XLSX` `XLS` `DOCX` `DOC` `PDF` `PNG` `JPG`
- Preview before saving, with duplicates and invalid emails flagged
- Do-not-contact list that is always respected

### 📝 Templates
- Rich editor: bold, italic, lists, links, case changes, and a live preview
- Placeholders: `{{name}}` `{{first_name}}` `{{company}}` `{{role}}` `{{sender_name}}`
- Fallbacks: `{{first_name|there}}`
- 🤖 **AI email writer**: draft, improve or shorten, using only facts from your resume

### 📄 Resume Studio
- Import PDF, DOCX, DOC or TXT, or start blank
- Version history: your original is kept untouched
- Export to **PDF** or **Word**, automatically fitted to one page
- 🎯 **ATS tailoring**: paste a job description, get a match score, and accept or decline each suggestion

### 📬 Campaigns
- Choose template, recipients and resume, then preview every email
- Sends in **batches of 5** with gaps between messages
- **Daily limit** (default 40, max 200) so your Gmail stays healthy
- Auto-pause on rate limits, safe retries, and no blind re-sends

### 🔐 Accounts & Security
- Email verification, password reset, **2FA** (authenticator app), and **Google sign-in**
- Gmail app password stored with **Fernet encryption**
- CSRF protection, rate limiting, header-injection blocking, HTML sanitising

---

## 🧰 Tech Stack

| Layer | Technology |
|---|---|
| 🐍 Backend | Django 5.2 LTS |
| 🗄️ Database | SQLite (local) / PostgreSQL (production) |
| 🎨 Frontend | Django templates, custom CSS, Alpine.js |
| ✉️ Email | Gmail SMTP (`smtplib`) |
| 🔑 Encryption | `cryptography` (Fernet) |
| 🧼 Sanitising | `nh3` |
| 📑 Parsing | `openpyxl` `xlrd` `python-docx` `pypdf` `pytesseract` |
| 📤 Export | `python-docx` `fpdf2` |
| 🤖 AI | Ollama (local) / Ollama Cloud / Gemini, via `httpx` |
| ✅ Validation | `pydantic` |

---

## ⚡ Quick Start

```powershell
cd kaamsetu-py
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env      # fill in SECRET_KEY and TOKEN_ENCRYPTION_KEY
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py runserver
```

Open 👉 **http://127.0.0.1:8000** and create an account.

**Optional extras**

```powershell
manage.py seed_demo --password "choose-one"   # demo account with sample data
manage.py createsuperuser                     # admin at /admin/
manage.py run_sender                          # background sending
```

---

## 🧭 How to Use

1. ⚙️ **Settings → Gmail sending:** turn on 2-Step Verification, create an [app password](https://myaccount.google.com/apppasswords) and paste it in
2. 📇 **Contacts:** import your recruiter list and review the preview
3. 📝 **Templates:** pick a role starter and personalise it
4. 📄 **Resume Studio:** upload your resume and tailor it to a job
5. 🚀 **Campaigns → New:** choose everything, preview, tick the confirmation box, then send

---

## 🤖 AI Setup (optional)

AI is **off by default**, and only runs when *you* press the button. Nothing happens in the background.

| Provider | Setting | Notes |
|---|---|---|
| 🏠 **Ollama** (local) | `AI_PROVIDER=ollama` | Free. Run `ollama pull qwen3:4b` (8 GB RAM is enough) |
| ☁️ **Ollama Cloud** | `AI_PROVIDER=ollama_cloud` + `OLLAMA_API_KEY` | No local RAM needed |
| ✨ **Gemini** | `AI_PROVIDER=gemini` + `GEMINI_API_KEY` | Free tier available |
| 🚫 **Off** | `AI_PROVIDER=none` | Disables AI completely |

🛡️ **Anti-fabrication guard:** suggestions containing numbers that aren't in your resume are blocked, and new skills are marked *"accept only if true"*.

---

## 🖼️ Screenshot OCR

To read emails from images, install [Tesseract](https://github.com/UB-Mannheim/tesseract/wiki) and set in `.env`:

```
TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
```

Without it, everything else still works.

---

## ☁️ Deploying

> ⚠️ `localhost` settings (database, Ollama, Google redirect URI) will not work on a server. Point them at hosted services.

1. 🔧 Set `DEBUG=false`, plus `SECRET_KEY`, `TOKEN_ENCRYPTION_KEY`, `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`
2. 🗄️ Use hosted Postgres (Supabase, Neon, Railway…): set `DATABASE_URL`, then run `manage.py migrate`
3. 🤖 Use a hosted AI provider (`ollama_cloud` or `gemini`), since local Ollama won't exist on the server
4. 📦 Run `manage.py collectstatic`, then serve with `gunicorn kaamsetu.wsgi`
5. 🔁 Run `manage.py run_sender` as a second process for background sending
6. 🔑 Update the Google OAuth redirect URI to your real domain
7. 🛑 Restrict `/admin/`, which has no 2FA

---

## 🛡️ Security Highlights

- 👤 Every query is filtered by the logged-in user, so other users' data returns 404
- 📁 Uploaded resumes live outside any public URL and are served only to their owner
- 🚦 Rate limits on login, signup, imports and AI
- 🔒 Secure cookies, HSTS and SSL redirect switch on automatically in production
- 🙅 The app refuses to start without `SECRET_KEY` and `TOKEN_ENCRYPTION_KEY`

---

## ⚠️ Known Limitations

- Sending through a real Gmail account and real AI models is not fully tested end to end
- Resume import is heuristic: tables and graphics are not preserved (the original file is kept)
- The rate limiter is per process; use Redis for multi-process setups
- UGC creator outreach is a placeholder page

---

<div align="center">

### 🌟 If KaamSetu helps your job hunt, give it a star!

Made with ❤️ for job seekers everywhere

</div>
