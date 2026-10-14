# AI Event Planning Platform

A Streamlit application with event selection and a wedding onboarding entry flow. The step-by-step wedding route collects an initial requirements draft; the describe-your-wedding route remains a placeholder for future AI work.

## Supabase local configuration

Create a local secrets file from the example and edit it with your own Supabase project URL and publishable key:

```powershell
New-Item -ItemType Directory -Force .streamlit
Copy-Item .streamlit\secrets.toml.example .streamlit\secrets.toml
notepad .streamlit\secrets.toml
```

Set `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY` in `.streamlit\secrets.toml` using the values from your Supabase project. Use only the publishable key; never put a secret or service-role key in the Streamlit app. The real secrets file is excluded by `.gitignore` and must not be committed.

For AI wedding-description extraction, also set `GROQ_API_KEY` in `.streamlit\secrets.toml` or the process environment. The key is never included in application output or committed files.

## Setup (Windows PowerShell)

From the project folder, create and activate a virtual environment, then install the dependencies:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If PowerShell blocks activation, run the environment's Python directly instead:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Test

```powershell
python -m pytest -q
```

## Signup testing

Automated signup tests mock Supabase Auth and do not create real accounts:

```powershell
python -m pytest -q tests/test_signup.py
```

To test signup manually, configure `.streamlit\secrets.toml`, launch the app, and select **Sign up** from the login screen. Use a test email address for the Supabase project you intend to use; submitting the form creates a real Auth account in that project.

Email verification is disabled in the Supabase project for local development. Re-enable it and test the confirmation flow before deploying to production.

## Login and logout testing

Automated login, protected-route, expiry, session-isolation, and logout tests use mocked Supabase responses and do not make real login calls:

```powershell
python -m pytest -q tests/test_auth.py
```

For manual testing, create an account first, then log in with that email and password. Protected event and wedding screens validate the current Auth session. Select **Logout** to call Supabase sign-out with local scope and clear the app's session state.

The app keeps only the access and refresh token strings in Streamlit's server-side `session_state`, scoped to the current browser tab. Passwords are handled by the clear-on-submit form and are not stored separately by application code. SDK client objects and session objects are not retained, and tokens are not persisted across a full browser refresh. A full refresh starts a new Streamlit session and requires logging in again. An expired access token is renewed when its refresh token is still valid; an expired or revoked refresh session requires logging in again. Client-side SDK persistence and automatic background token refresh are disabled.

## Wedding requirements draft

The **Plan Step by Step** route collects an optional wedding date, city/state, local or destination preference, guest count, INR budget, style, cultural backgrounds/regions, traditions, and additional preferences. Only the wedding date has a status: fixed date, approximate date, or not decided yet. Other fields are straightforward optional values. Python validation checks dates, guest counts, budgets, and field types before saving.

The reusable `WeddingProfile` is stored in Streamlit `session_state` only. The draft is temporary, is not saved to Supabase or another database, and is lost when the Streamlit session ends. The submitted summary remains editable through the form.

After saving a profile, choose **Complete Wedding Details** for one-at-a-time follow-up questions. Answers update the same temporary profile. Questions already answered are skipped; you can skip any question, edit an earlier answer, or continue with unanswered details later. Cultural and tradition questions record preferences only and do not infer ceremonies from identity.

Choose **Choose Your Wedding Ceremonies** from a saved profile, follow-up questions, or an accepted extracted profile to review optional ceremony ideas. Matching is deterministic and uses explicitly stated preferences; region or cultural background alone never selects a religious ceremony. Ceremony selections and custom ceremonies are separate from `WeddingProfile`, confirmed in the current Streamlit session, and are not stored in Supabase. You can revisit the page to edit or reconfirm choices. No catalog is exhaustive, and all tradition-specific examples are optional.

## Wedding project database preparation

The proposed `wedding_projects` table is defined in `supabase/migrations/20261015000000_create_wedding_projects.sql`; this migration is not applied automatically. It enables row-level security and grants table access only to `authenticated` users whose `auth.uid()` matches `owner_id` for each CRUD operation. `owner_id` references `auth.users(id)` and cannot be reassigned by an owner update. The app uses the configured Supabase publishable key plus the user's access/refresh tokens; it does not use a service-role key.

`ai_event_planning_platform.wedding_projects` provides validated JSON serialization/restoration and owner-filtered repository methods for create, list, load, update, and planning progress. Profile dates are ISO strings, enums are stored by value, and selected ceremonies are stored as validated JSON objects. Custom ceremony data is preserved while catalog ceremony content is restored from the curated catalog. The planned dashboard can map `planning_step` to Continue Existing Wedding / Edit Wedding / Start New Wedding; no dashboard or database writes are wired into the app yet.

The **Describe Your Dream Wedding** route uses Groq model `openai/gpt-oss-120b` to propose the same structured profile. Review and edit the result before accepting it. If a draft already exists, replacement requires explicit confirmation. Extraction uses one request per submission; failed requests are not automatically retried.

For a manual browser test, set `GROQ_API_KEY` in `.streamlit\secrets.toml` (or the environment), launch the app, open **Describe Your Dream Wedding**, edit the sample text, and select **Extract Wedding Details**. Review missing fields and edit the profile, save review changes, then explicitly accept it. If a step-by-step draft already exists, use the replacement checkbox only when you intend to replace it. Automated tests mock the Groq client and never call the API.

## Launch

```powershell
python -m streamlit run app.py
```