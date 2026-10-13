# AI Event Planning Platform

A Streamlit application with event selection and a wedding onboarding entry flow. The wedding planning paths are placeholders for future milestones.

## Supabase local configuration

Create a local secrets file from the example and edit it with your own Supabase project URL and publishable key:

```powershell
New-Item -ItemType Directory -Force .streamlit
Copy-Item .streamlit\secrets.toml.example .streamlit\secrets.toml
notepad .streamlit\secrets.toml
```

Set `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY` in `.streamlit\secrets.toml` using the values from your Supabase project. Use only the publishable key; never put a secret or service-role key in the Streamlit app. The real secrets file is excluded by `.gitignore` and must not be committed.

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

## Launch

```powershell
python -m streamlit run app.py
```