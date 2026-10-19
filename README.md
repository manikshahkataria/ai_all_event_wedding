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

Choose **Choose Your Wedding Ceremonies** from a saved profile, follow-up questions, or an accepted extracted profile to review optional ceremony ideas. Matching is deterministic and uses explicitly stated preferences; region or cultural background alone never selects a religious ceremony. In an active wedding project, **Confirm My Ceremonies** saves the selected catalog and custom ceremonies, including custom names, to Supabase's `selected_ceremonies` JSONB column. The app verifies the update response and reads the owner-scoped project back before displaying success. Reopening the project restores its selections and checked boxes. No catalog is exhaustive, and all tradition-specific examples are optional.

Checkbox changes and custom ceremony edits remain drafts until confirmed again; the page warns when they differ from verified saved selections. Profile saves preserve saved ceremonies. Failed ceremony saves retain the choices for retry and block switching projects until the failed changes are saved. Draft checkbox choices survive navigation within the active project; switching projects or logging out clears local drafts. Only confirmed selections can be restored after login.

The requirements summary includes **Your Confirmed Wedding Ceremonies**, with compact cards for saved ceremony names and descriptions, including custom ceremonies. **Edit Ceremonies** opens the existing selection page. This overview uses the active project's verified saved snapshot without additional database requests on reruns; unconfirmed edits and failed saves do not replace its confirmed ceremony list. An empty saved list displays **No ceremonies selected yet.** Missing or invalid saved state displays an error instead.

## Wedding project database preparation

The proposed `wedding_projects` table is defined in `supabase/migrations/20261015000000_create_wedding_projects.sql`; this migration is not applied automatically. It enables row-level security and grants table access only to `authenticated` users whose `auth.uid()` matches `owner_id` for each CRUD operation. `owner_id` references `auth.users(id)` and cannot be reassigned by an owner update. The app uses the configured Supabase publishable key plus the user's access/refresh tokens; it does not use a service-role key.

`ai_event_planning_platform.wedding_projects` provides validated JSON serialization/restoration and owner-filtered repository methods for create, list, load, update, and planning progress. Profile dates are ISO strings, enums are stored by value, and selected ceremonies are stored as validated JSON objects. Custom ceremony data is preserved while catalog ceremony content is restored from the curated catalog. After login, **My Wedding Projects** supports starting a new project or continuing an existing one at its saved planning step. Permanent saving requires the existing `wedding_projects` table and an authenticated user session; the app does not apply migrations automatically.

The **Describe Your Dream Wedding** route uses Groq model `openai/gpt-oss-120b` to propose the same structured profile. Review and edit the result before accepting it. If a draft already exists, replacement requires explicit confirmation. Extraction uses one request per submission; failed requests are not automatically retried.

For a manual browser test, set `GROQ_API_KEY` in `.streamlit\secrets.toml` (or the environment), launch the app, open **Describe Your Dream Wedding**, edit the sample text, and select **Extract Wedding Details**. Review missing fields and edit the profile, save review changes, then explicitly accept it. If a step-by-step draft already exists, use the replacement checkbox only when you intend to replace it. Automated tests mock the Groq client and never call the API.

## Ceremony-wise planning (Sprint 4B)

From the Wedding Overview, select **Edit Ceremony Plans**, or use **Plan Your Wedding Ceremonies** after confirming ceremonies. Each confirmed catalog or custom ceremony has an independent editor for optional guests, date, date certainty, preferred timing, venue preference/notes, estimated INR budget, and additional preferences. Missing values stay blank. Incomplete plans can be saved; the page distinguishes not started, saved, incomplete, and unsaved states. **Back to Wedding Overview** shows only verified saved plan details under **Your Ceremony Plans**.

`CeremonyPlan` uses stable ceremony IDs. Guest counts must be non-negative whole numbers, and budgets must be non-negative finite numbers. Zero is valid. Counts exceeding the overall wedding guest total produce a warning and remain saveable. Dates must be valid calendar dates. An entered date must be explicitly marked Fixed or Tentative; changing certainty never silently clears a date. Incomplete status means optional details are missing, not that saving is blocked.

Optional deterministic suggestions can use the overall guest count. Only the general wedding ceremony can receive a date suggestion, and only from an overall fixed wedding date; the suggested date stays tentative. Suggestions never assign religious dates or timings, never replace entered values, and require **Use suggestions as draft** followed by **Save Ceremony Plan**. They can also be dismissed.

Persistence extends each `selected_ceremonies` JSON object with an optional `plan` object (format version 1). ID/name identity stays in the enclosing ceremony. Existing objects without plans remain compatible, and no migration or RLS change is needed. Individual saves read the latest owner-scoped project, merge one plan while preserving other ceremonies/plans, and use the existing `updated_at` timestamp to reject conflicting updates. The update response and database read-back must match before success appears. Profile updates and ceremony reconfirmation preserve plans for retained ceremony IDs, including renamed custom ceremonies. Explicitly removing a ceremony also removes its nested plan; the selection page warns first.

Plan drafts survive navigation within the same project. Failed saves retain edits for retry and block starting/switching projects until recovered; successful navigation or profile saves do not clear that failure. Ordinary unsaved drafts are cleared when switching projects or logging out. Saved plans restore after login. Page reruns do not save or reload plan data; authentication checks retain their existing behavior.

Browser check using the existing server at **http://localhost:8505**:

1. Log in, continue a wedding project, confirm ceremonies, and open **Edit Ceremony Plans**.
2. Enter different guest counts and details for two ceremonies; save each separately. Try a partial plan and an explicitly tentative date.
3. Return to the overview and verify saved names, counts, certainty, venue, budget, and planning status. Edit wedding requirements and confirm plans remain.
4. Log out/in and continue the project. Verify the plans restore; switch to a second project and verify its plans remain separate.

Automated tests use mocked Supabase and include actual SDK request serialization. An authenticated live database round-trip is a separate manual check.

## Smart Wedding Budget (Sprint 5A)

Open a project, save its total wedding budget, confirm ceremonies, then select **Smart Wedding Budget** from the Wedding Overview. Per-ceremony budgets are optional. **Generate Suggested Budget** proposes meals and services using each saved ceremony's type, guests, timing, venue, notes and preferences together with the full WeddingProfile. Existing ceremony plans and their entered budgets are never replaced by these suggestions.

Review the catering/service form, adjust guests, meal, venue and included services, check the review box, then **Apply reviewed needs and recalculate**. This refreshes illustrative ranges while preserving edited amounts/names, custom rows, removals and ceremony allocations. Edit rows inside a ceremony section and select its **Recalculate Budget** action to update the totals. Review and accept the allocations, then **Save Wedding Budget**. Existing accepted plans remain stored while new suggestions are reviewed. Deliberate suggestion regeneration requires an explicit replacement checkbox and replaces draft row edits. Ordinary drafts survive navigation within the project and clear on logout or project switch.

`budget_engine.py` contains reusable deterministic calculations and configurable `CostAssumptions`. All amounts below are **illustrative planning assumptions, not actual market prices, quotations, or knowledge of local vendors**:

| Category | Illustrative low–high INR | Basis |
| --- | --- | --- |
| Breakfast | 150–250 | Per guest |
| Snacks | 100–200 | Per guest |
| Lunch | 300–500 | Per guest |
| Dinner | 400–700 | Per guest |
| Service staff | 30–60 | Per guest |
| Seating/furniture | 40–80 | Per guest |
| Home rental | 0 | No paid rental; other services can still cost money |
| Banquet / hotel / outdoor rental | 20,000–50,000 / 40,000–90,000 / 15,000–40,000 | Per ceremony |
| Other / undecided venue | 15,000–40,000 / 15,000–50,000 | Provisional rental; review choice |
| Decoration / lighting and sound | 5,000–15,000 each | Per ceremony |
| Entertainment | 8,000–20,000 | Per ceremony when included |
| Ceremony supplies | 2,000–5,000 | Generic placeholder; review actual requirements |
| Photography | 15,000–35,000 | One shared package; verify days and coverage |
| Destination travel/accommodation | 1,500–3,500 | One guest-night placeholder × largest included ceremony count, once |

Default allocations use the rounded-up midpoint. Simple style uses 85%, luxury 150%, and other/unspecified styles 100% of the illustrative service rates; destination travel/accommodation is not style-adjusted. These percentages are configurable assumptions, not pricing facts. City and dates inform context but introduce no claimed local/seasonal price multiplier. Morning suggests breakfast, afternoon lunch, evening snacks, and night dinner; unspecified timing uses provisional snacks. Entertainment is suggested for Mehendi, Sangeet and Reception. Every inference remains unconfirmed until reviewed. Missing ceremony guests propose the overall wedding count; missing both blocks acceptance. Explicit zero counts and budgets are preserved.

An entered ceremony budget is a direct-expense cap and earmark: unused money stays in that ceremony's visible unassigned reserve. Fractional INR constraints reserve the next whole rupee without changing the original value. Shared expenses and contingency are additional wedding-level allocations. Photography and destination logistics appear once, outside ceremony subtotals. Remove bundled venue services after reviewing inclusions; the app does not know package coverage, guest overlap or travel duration.

Contingency defaults to 10% of the total wedding ceiling, rounded upward, and is counted once. The review form can change the percentage and the allocation editor can explicitly change the amount. Changing the ceiling never silently reduces any service rates. The page shows each range, ceremony allocations/reserves, category totals, shared expenses, contingency, total allocated and remaining/over-budget amount in Indian INR formatting. It warns separately when minimum requirements cannot fit, when midpoint/edited allocations exceed the ceiling or a ceremony cap, and when the upper range exceeds the ceiling. Saving is blocked for infeasible allocations, unknown guests, stale context or edits below illustrative minimums. Resolve these by explicitly reducing guests/services, choosing a different venue, changing `CostAssumptions`, or revising the entered constraints. Estimates cover only the reviewed services; attire, jewellery, gifts, taxes and other unselected costs require separate review.

`budget_intelligence.py` uses the existing Groq client, model, timeout and retry behavior without modifying extraction. One structured JSON request interprets contextual meal/service needs and trade-offs; it cannot set costs, guest counts, venues, user budgets or allocations. Unknown/duplicate IDs, services, extra price fields, invalid/incomplete JSON and API failures fall back safely to rule-based suggestions. Successes and failures are cached by context in the active project's session (up to five entries); reruns, allocation edits and identical regeneration do not repeat requests. **Retry AI interpretation** explicitly retries. Automated tests mock all Groq calls.

Accepted plans use `wedding_profile.smart_budget_plan` in the existing JSONB column. Sprint 5A.1 writes `version: 2`, adding optional expense names, stable custom-row IDs, removed-row snapshots for undo, and fixed ceremony allocation snapshots. Version 1 budgets remain readable and upgrade only on an explicit save. The stored plan also includes context fingerprint, reviewed needs, ranges, edited amounts, contingency, assumption snapshot and acceptance. Legacy projects without this key still load. Profile saves merge and preserve this extension; ceremony saves leave it intact. Changes to wedding context preserve the old accepted plan while requiring regeneration before another save. Malformed/unsupported budget data reports a budget restoration error without discarding wedding details or ceremony plans. No schema, migration, authentication or RLS change is needed.

Budget saves read the latest owner-scoped project, validate feasibility against that stored context, update only the JSONB profile and planning step using an optimistic `updated_at` filter, verify the update response, and separately read back the saved project before confirming success. Failed saves retain drafts, show no saved confirmation, and block switching projects until retried. Other successful saves/navigation do not clear a pending budget failure. Saved plans restore after logout/login; separate projects and users remain isolated. Tests include real Supabase SDK serialization over a mocked HTTP transport, not a live database write.

Browser walkthrough on the **existing http://localhost:8505 server**:

1. Log in, continue a project, save a total budget, and confirm Haldi, Mehendi and Reception. Set saved ceremony plans to morning/home, evening, and night respectively, with different guest counts. Leave ceremony budgets blank.
2. From Wedding Overview open **Smart Wedding Budget**, generate suggestions, and check breakfast, snacks and dinner. Review Groq explanations or the visible fallback notice. Verify home rental, paid venues, shared photography and contingency.
3. Change meal/services/guests and apply reviewed needs. Open a ceremony, edit its rows, and click **Recalculate Budget**. Accept and **Save Wedding Budget**, then verify the saved/read-back confirmation. Add an entered ceremony budget through Ceremony Plans, regenerate, and check it remains preserved as a constraint/reserve.
4. Set an insufficient overall budget or a very small ceremony budget; regenerate and review the named shortfall/trade-offs. Verify saving stays disabled and no costs silently shrink.
5. Log out/in and continue the saved project; confirm restoration. Open another project and confirm no budget leaks across projects. A changed profile/ceremony plan must mark the old budget stale.

Automated checks: `.venv/Scripts/python.exe -m compileall -q app.py ai_event_planning_platform tests`, `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --tb=short`, and `git diff --check`. Authenticated live Supabase/Groq checks remain manual; automated tests do not require credentials or create external records.

## Ceremony-level budget editor (Sprint 5A.1)

The budget summary is at the top: wedding ceiling, sum of ceremony allocations, shared expenses, contingency and remaining/shortfall. Total committed uses the greater of each ceremony's allocation or calculated expenses, plus shared costs and contingency, so spending beyond a ceremony allocation cannot disappear from the overall shortfall.

Each confirmed ceremony in the active project has one expander, including custom ceremonies. Its header shows guests, timing, venue, fixed allocation, calculated expense total and remaining/shortfall. All expense rows appear together with editable names/INR amounts and a **Remove** button. Longer lists scroll vertically; there are no expense-level or nested expanders. **+ Add Expense** creates a custom row with a stable ID. Each removed row has an **Undo removal** action. Shared expenses and contingency have their own flat section and are never repeated inside ceremonies.

Row changes are staged in project-scoped session state. **Recalculate Budget** at the bottom of a ceremony validates and applies only that ceremony's rows/allocation; the shared section's action applies only shared rows/contingency. The top summary uses saved or recalculated data, and pending sections are visibly marked. Saving stays disabled until all changed sections are recalculated, needs are reviewed, feasibility checks pass, and the acceptance checkbox is checked. No Groq request or database access occurs for ordinary row changes or local recalculation.

Changing expenses does not silently change a ceremony allocation. Budgets entered in Ceremony Plans remain authoritative and must be changed there. For ceremonies without an entered budget, the generated allocation stays fixed until the user explicitly edits **Ceremony allocation (INR)** and recalculates. Reducing expenses leaves the unused allocation reserved. Costs below illustrative minimums still block saving; deletion is an explicit scope change, and removing catering or venue rental shows an incomplete-estimate warning without recreating the row.

Names do not change an expense's internal category or shared scope. Custom amounts are user-entered costs, not illustrative vendor ranges. The engine validates stable IDs, duplicate service rows and shared placement across both active and removed rows. Local recalculation never regenerates suggestions, and applying reviewed needs preserves custom rows and explicit removals. Only deliberate **Generate Suggested Budget** / **Retry AI interpretation** replacement actions start a new suggestion draft.

Saved row names, amounts, custom items, removals and allocation snapshots restore into the ceremony sections after logout/login. Unsaved row edits survive same-project navigation; switching projects or logging out clears local drafts. Failed saves retain edits and preserve the previously verified saved snapshot. Existing ownership, concurrency checks and separate database read-back remain in place.

Additional browser checks on the existing **http://localhost:8505** server:

1. Generate/review a budget with at least two ceremonies. Open one ceremony and verify its rows are visible together, with no nested expanders.
2. Rename a row and change its amount. Add **Welcome drinks**, enter its cost, remove catering, and verify undo is offered. Click **Recalculate Budget**; inspect the incomplete-estimate warning, unchanged fixed allocation and updated remaining/shortfall.
3. Verify the other ceremony's calculated values stay unchanged. Edit it without recalculating, then recalculate the first ceremony again; the second ceremony must remain pending.
4. Edit shared photography/contingency and use the shared section's recalculation. Verify each shared amount is counted once and the default 10% configuration remains until explicitly changed.
5. Accept and **Save Wedding Budget**, log out/in and verify edited names/custom/deleted rows restore. Switch to another project and confirm isolation. Exercise read-back failure with the mocked automated tests; a live success must be verified in the authenticated browser.

Automated coverage includes `tests/test_budget_rows.py` plus the updated budget UI tests, with mocked Groq/Supabase. No database schema changes or external configuration changes are part of this milestone.

## Wedding project UX (Sprint 5A.2)

Every **My Wedding Projects** card has a **Delete** action. It opens a modal showing the selected project's name, location, date and unique ID, plus a permanent-deletion warning. The user must check the acknowledgement and press **Delete permanently**; **Cancel**, dismissal, opening a dialog, or checking the acknowledgement alone never deletes anything. Only the end user's explicit confirmation in the running app may delete real data. Automated development tests use mocked databases; no actual Jaipur, Manali or other projects are deleted.

The repository deletes one validated `project_id`, with both the authenticated owner filter and an optimistic `updated_at` condition. It checks the owner-scoped project before deleting, rejects stale confirmations/concurrent edits, verifies the deleted-row response, and separately reads back the project to confirm absence before reporting success. The existing DELETE RLS policy already limits deletion to `auth.uid() = owner_id`; no schema, RLS or credential changes are required. Failed or unverified deletion shows an actionable error without a success message. Cancel/refresh retrieves the authoritative list after an uncertain network result.

Successful deletion refreshes the dashboard. Deleting the active project clears its saved snapshots, ceremony/budget drafts, pending saves and project navigation history, while retaining the Supabase login. Deleting another project preserves the current workspace and all other projects. Deletion confirmation state is cleared on cancellation, logout or authentication failure.

**Smart Wedding Budget** now displays **Wedding at a Glance** below navigation and before financial totals. Its compact panel uses only the active project's verified saved profile and confirmed ceremonies: project name, city/state, date/certainty, local/destination choice, wedding guests/budget/style, optional partner cultures, preferences and ceremony names. Missing basic fields display **Not provided**; absent optional cultures/preferences are omitted. Zero guests/budget remain explicit values. Unsaved profile edits never replace this summary. A successful profile save validates the returned profile before updating the saved snapshot. Project switching/reopening and logout/login restore the corresponding project context without extra database requests on ordinary budget reruns.

**Browser Back diagnosis:** the earlier router changed only `session_state["page"]` and kept every view at the same URL, so internal moves did not create browser-history entries. Browser Back could leave the app or reload its root, creating a fresh Streamlit session with no authentication tokens.

`navigation.py` now registers native `st.navigation` / `st.Page` routes and uses `st.switch_page` for app requests. Breadcrumbs use `st.page_link`. Page URLs contain static slugs such as `/step_by_step` and `/smart_wedding_budget`; tokens, names, cities, budgets and project IDs are never added to URLs. A consistent **Back** action on planning pages uses the current project's navigation trail. Breadcrumbs link **My Wedding Projects > Wedding Overview > current planning page**. Native Back/Forward selects registered pages while keeping the live session and active project; it is never treated as logout. Direct protected-route access still runs the existing Supabase session guard before displaying project data.

The authentication policy is unchanged: tokens live only in Streamlit's server-side session state. No cookies, local storage or session storage are introduced for tokens. A full refresh, typed/bookmarked/new-tab URL, lost WebSocket session, server restart or navigation outside the app can create a new Streamlit session and require login. Native history improvements do not promise authentication across those boundaries. Unsubmitted form inputs may not have reached the server; save forms before leaving. Budget working rows and ceremony draft snapshots already held by the app survive same-project routing. Switching projects/logout clears local drafts as before.

Verification is deliberately separated:

- The full pytest suite uses mocked Supabase/Groq and AppTest. It covers duplicate-name deletion, cancellation/confirmation, ownership, concurrency, response/read-back failures, other-project preservation, active-state cleanup, saved summary updates/isolation, protected routes, native page-selection events, Back controls, token retention and draft preservation. The installed AppTest does not carry a `st.switch_page` redirect into the next simulated widget event; `tests/conftest.py` carries its selected page hash forward to emulate the browser client. This adapter does not change production routing or authentication.
- `tests/browser_navigation_smoke.py` uses a real headless Edge browser against the existing **localhost:8505** server. It verifies Sign up changes the URL to `/signup`, native Back/Forward returns between login and signup, and a fresh protected budget URL is denied without authentication. It creates no accounts, submits no credentials and deletes no projects. Run it with `.venv/Scripts/python.exe tests/browser_navigation_smoke.py` when Playwright/Edge are available.
- Authenticated project Back/Forward, summary rendering and permanent deletion remain manual browser checks. In this milestone, the existing server initially retained the previous imported router. A source refresh and rerun of the anonymous QA session loaded native routing without restarting the server or changing saved projects. If the development server shows old UI, use **Rerun** before interpreting browser-history results.

Manual checks on the existing **http://localhost:8505** server:

1. Log in, continue a project, open Wedding Overview then Smart Wedding Budget. Verify **Wedding at a Glance** precedes totals and matches only that project's saved data.
2. Edit budget rows without saving. Use native browser Back/Forward, the in-app **Back**, and breadcrumbs; verify the active project, authentication and draft rows remain intact. A full refresh is a separate fresh-session check and may require login.
3. Edit and save wedding requirements, return to the budget and verify the summary updates. Open another project and verify its details/ceremonies remain separate.
4. Open **Delete** on a project card and inspect its ID, name, location/date. Cancel and confirm no changes. To test permanent deletion, create a disposable project yourself, then explicitly acknowledge and **Delete permanently**. Verify only that project disappears, the login remains, and deleting the active disposable project clears its workspace. Do not use a real planning project for this check.

## Wedding Calendar & Timeline (Sprint 5B)

Open **Wedding Calendar & Timeline** from Wedding Overview, alongside **Smart Wedding Budget**. The overview counts scheduled/undated ceremonies, unfinished tasks and scheduling issues from saved project data. The protected native route is `/wedding_timeline`.

The workspace contains **Calendar**, **Preparation timeline** and **Ceremony execution**. Month view has seven weekday columns, previous/next month controls, selectable dates and all ceremony labels on a shared date. Week/day views order ceremonies and activities by explicit local start times; missing clock times stay **Flexible**. Tentative dates remain labelled. Undated ceremonies, relative tasks and incomplete timings appear separately, with editor actions. Accepted budget meal preferences are labelled separately from activity suggestions and never imply a booking. Calendar navigation performs no Groq generation or project database reads/writes beyond the existing authentication guard.

**Generate timeline & activities** makes one explicit Groq request, or uses optional rule-based generation. The full saved profile, selected ceremony plans and accepted budget provide context. Structured output is validated; unknown ceremonies, invented date/time fields, malformed responses and cyclic dependencies trigger a labelled fallback. Successes and failures are cached per context in this project session; **Retry Groq for this context** explicitly retries. All suggestions require review. No religious ritual is made mandatory. Fixed dates permit Python-calculated preparation deadlines, with past-due warnings. Approximate/undecided dates retain relative windows; independently saved ceremony dates remain visible.

Tasks support phases, priority, dependencies, notes, optional deadlines and completion. Each ceremony has one expander containing a flat activity list with add/edit/delete, up/down ordering, optional start/end/duration, status and notes. Drafts survive same-project navigation and widget reruns. Review and **Save timeline changes** before switching projects or logging out. The calendar excludes unsaved tasks, times and activities. When saved wedding context changes, explicitly refresh and review the draft; user deadlines and retained activity IDs are preserved. Removed ceremonies leave the draft only on refresh. Existing accepted calendar deadlines/times remain labelled as previously saved until the refreshed draft is saved.

Deterministic checks distinguish blocking invalid ranges/dependency errors, advisory overlaps/late preparation/setup readiness and incomplete information. A shared date or preferred time of day alone never constitutes an overlap. The app explains each issue and never silently moves an event. Parallel work can be retained after reviewing advisory warnings. Overnight ranges must be split; multi-day activity blocks, recurring events, external calendar synchronization, drag-and-drop and vendor bookings are outside this sprint.

Version 1 timeline JSON lives at `wedding_profile.wedding_timeline` in the existing JSONB column. Owner-scoped optimistic updates preserve other profile extensions, ceremony plans and budget versions. Responses and a separate database read-back must match before success appears. A saved-timeline baseline also rejects edits made in another session. Failed saves retain the draft and block project switching until resolved. **Load latest saved details for comparison** explicitly reads the latest project while preserving the local timeline draft, then requires review before replacement. Legacy projects need no migration; malformed timeline data is isolated from profile/budget restoration. No schema, RLS, credential storage or budget calculation changes are required.

Focused tests use mocked Groq/Supabase, AppTest and the actual Supabase SDK with mocked HTTP. `tests/browser_navigation_smoke.py` checks public navigation/history and both protected budget/timeline routes on the existing server; it does not authenticate or write live project data.

Manual live checks on **http://localhost:8505**:

1. Log in and open a project through Wedding Overview → Wedding Calendar & Timeline. Check its saved ceremony dates, tentative labels, venue/guests and accepted budget meal preferences. Click dates and use month/week/day controls, including a year boundary and shared ceremony date.
2. Generate once. Edit a task/deadline/completion and add/reorder/edit/delete activities. Switch workspace views and use native Back/Forward; confirm drafts remain. Before saving, verify the calendar still shows only saved information.
3. Review warnings and save. Confirm database read-back success, reopen the project, then log out/in and reopen again. Verify tasks, activity IDs/order, statuses, times and notes restore. Open another project and confirm isolation.
4. Use an approximate/undecided overall date and confirm no invented day appears. Save an explicit ceremony date and verify it appears independently. Edit saved ceremony details, refresh the timeline draft, review and save.
5. In a disposable planning project, try invalid ranges, overlapping explicit ranges and dependency cycles. Check errors block saving and advisory warnings explain the issue. With two sessions, save a different timeline in the second, attempt the older draft save, then load latest details for comparison, review and retry. Do not delete real projects.

## Central Wedding Dashboard (Sprint 5C)

Opening or creating a wedding now enters **Wedding Dashboard** at `/wedding_dashboard`; login continues to enter **My Wedding Projects**. A compact shared header provides one Back action, My Wedding Projects, Logout and native route links for **Overview**, **Budget Dashboard**, **Calendar & Timeline** and **Wedding Invitations**. The existing detail forms, Smart Wedding Budget editor and calendar remain available. Returning to the active wedding keeps its session drafts; if a save failed, returning opens the editing page needed to retry it. No tokens or project identifiers are added to page URLs, and authentication persistence is unchanged.

Optional **Bride's name** and **Groom's name** fields appear in project creation and wedding requirements. Existing projects offer **Personalize Your Wedding**, with Skip and later editing. Both names produce `<Bride> & <Groom>'s Wedding` on project cards, dashboard, detail/budget/calendar headers and the saved glance panel. Otherwise the stored project name is used. Personalization never renames the stored project. Names use backward-compatible `bride_name` / `groom_name` profile JSON fields; missing fields restore as `None`. The name-only save merges into current JSONB, checks owner and version, protects against concurrent name changes and separately reads back the saved profile. Failed entries remain available, and **Load latest names for comparison** permits explicit review before replacing names changed elsewhere. Profile saves also verify JSON responses and database read-back. Names do not affect budget/timeline context fingerprints, including existing pre-name hashes.

Overview cards show saved couple names, date/certainty, city/state, guests, budget, selected ceremonies, dated schedules and unfinished preparation tasks. Accepted budget highlights show ceremony allocations, shared expenses, contingency and remaining funds. Actionable alerts identify missing information, flexible timing, pending work, budget feasibility, scheduling advisories and confirmed-time conflicts. Names remain optional; suggestions are not bookings. Counts come from saved data; no completion percentage is invented. Dashboard reruns, chart selection and native dashboard navigation do not load project rows or invoke Groq; the existing authenticated-session guard still runs.

**Budget Dashboard** at `/budget_dashboard` reads only the accepted saved plan. **Edit Budget** and ceremony actions open the existing editor, focusing the selected ceremony while preserving drafts. Cards show the overall ceiling, ceremony allocations, calculated ceremony expenses, shared expenses, contingency, committed total and remaining/shortfall. Ceremony rows retain fixed allocations and explain unused reservations or shortfalls. A native bar chart and category table aggregate the actual accepted expense records, with per-ceremony/shared contributions and original-row drill-down. Built-in seating/staffing categories are grouped; custom items retain their names and category in detail records. Combined travel/accommodation stays combined because its saved estimate cannot be split reliably. Shared rows are counted once, and equal names never merge distinct ceremony IDs.

All financial calculations continue to use `assess_budget`. Reconciliation is **category expense rows + unused ceremony reservations + contingency = total committed**. Category sums are not represented as the committed budget. Saved plans whose context changed retain their accepted rows/allocations, including previously selected ceremonies, and show review notices against the current saved ceiling. New ceremonies without an accepted allocation show “Not in accepted budget,” not an invented zero. The existing feasibility warnings, estimate ranges and regeneration safeguards remain intact; dashboard viewing never recalculates or overwrites a budget.

The existing calendar workspace now begins with **Planning Status**, with direct ceremony-date/detail and timeline editing actions. Month/week/day views and timeline drafts are unchanged. The protected `/wedding_invitations` route was introduced in Sprint 5C and becomes the working Invitation Studio in Sprint 6A below.

Implementation files added: `dashboard_data.py`, `wedding_dashboard.py`; tests: `test_dashboard_data.py`, `test_couple_names.py`, `test_central_wedding_dashboard.py`. Integration changes cover `wedding_profile.py`, `wedding_projects.py`, `wedding_project_state.py`, `wedding_form.py`, `wedding_description.py`, `budget_engine.py` (display-name exclusion from context only), `wedding_summary.py`, `navigation.py`, `home.py`, `smart_wedding_budget.py`, `ceremony_planning.py`, `wedding_ceremonies.py`, `timeline_planning.py`. Updated regression files: `test_app.py`, `test_wedding_form.py`, `test_navigation.py`, `test_smart_wedding_budget.py`, `test_timeline_planning.py`, `test_ceremony_planning.py`, `test_wedding_project_dashboard.py`, `test_wedding_summary.py`, `browser_navigation_smoke.py` and this README.

Manual checks on the existing **http://localhost:8505** server:

1. Log in, open an existing wedding and verify central Overview, saved cards and all four top links. Use native Back/Forward between dashboard, budget and calendar; check the active wedding and drafts remain correct. Login must still enter My Wedding Projects.
2. Skip personalization, reopen it, save/edit both names and verify project cards and all workspace headings. Reopen after logout/login. Add just one name and confirm the stored project name remains the display fallback. Verify another wedding's names and planning data stay separate.
3. Open Budget Dashboard and inspect ceremony totals, shared rows, categories, contributions and original custom names. Compare reconciliation with the editor, including a fixed reservation larger than its expense rows. Make unsaved editor changes and confirm dashboard totals remain saved amounts. Change a saved ceremony constraint and check the accepted dashboard preserves its prior reservation with a review notice.
4. Follow Planning Status actions for a missing ceremony date, venue and execution times; verify the correct editor opens without traversing onboarding. Shared dates with no explicit overlap must not become conflicts. Verify Wedding Invitations opens directly from the shared navigation.
5. In a disposable project, use two sessions to change names concurrently. The older edit must fail without false success; load latest names for comparison, review and retry. Automated tests mock Supabase/Groq; authenticated live persistence and authenticated browser history still require these manual checks.

No database migration, RLS/auth policy change, cloud configuration change, invitation generation, commit or push is part of this sprint.

## Wedding Invitation Studio (Sprint 6A)

Open **Wedding Invitations** or **Create an invitation** from the central dashboard immediately after creating/opening a project. Completing requirements, budget or timeline is unnecessary. The existing protected URL `/wedding_invitations`, authentication and stable project selection are preserved. This sprint changes the invitation entry and its integration, without redesigning other workspaces.

The guided screens are **Choose a card → Details → Photo → Preview → Save & download**. Choosing type and template shares the first screen so all four template previews appear immediately. Each screen has one primary next/save action; essential controls appear beside the actual PNG preview, with design/announcement options collapsed. Back a step, native same-project Back/Forward, template changes and switching between local cards preserve drafts. Save before switching weddings or logging out. Creating another invitation retains the current local card; saved designs can be reopened through the design selector. Reset requires an explicit discard checkbox and removes the local photo; a saved photo expectation then requires re-upload or explicit removal.

Supported types are **Combined wedding**, **Bride's family**, **Groom's family** and **Individual ceremony**. Combined cards let users select ceremonies. Family-side types start with no assumed shared ceremonies or relatives; hosts, optional parents/relatives, family wording and per-event details belong to that card. Individual cards include exactly one confirmed catalog/custom ceremony for export. Use separate designs for different families; changing type also retains a session copy of that type's edits. No religion-specific phrase is required.

Original Pillow artwork provides **Traditional Royal** (maroon/gold ornament), **Elegant Floral** (ivory/champagne flowers), **Modern Minimal** (clean teal/neutral geometry) and **Festive Celebration** (yellow/green/pink garlands), each with an alternate palette and an original-photo frame. Decorations are code-drawn; no external invitation artwork or proprietary template is copied. Short cards are vertically balanced; long names/messages and large ceremony lists wrap and paginate rather than shrinking into unreadable text. Template-gallery thumbnails show the first events; the full preview/download includes the entire chosen itinerary.

Autofill reads the active project's saved names, date/certainty, city/state and selected ceremony plans. Accepted saved execution start times are used when available; preferred dayparts remain explicitly labelled preferences. Approximate months never become arbitrary exact days. A ceremony without its own date does not inherit an assumed date from the overall wedding. Saved venue notes are offered for review rather than inventing an address. Invitation-specific names, dates, times, locations, venues and family details are overrides; saving a card does not update the wedding profile, ceremony plans, budget or timeline. **Load latest saved details for comparison** refreshes saved source data/baselines while keeping local invitation overrides and photos.

Missing-detail checks are deterministic. Users can preview and save incomplete drafts, with visible draft text/watermarks and contextual questions. PNG download requires reviewed names, selected ceremony/hosts where applicable, valid essential event details and an explicit review of spelling, tentative labels and rendering. Unresolved template tokens such as `[Guest]`/`{{venue}}` block final download. An announcement-style card can use **To be announced** for dates/times/venues only after the user explicitly selects that wording; names and family hosts still need real entries. Switching an approximate month to Fixed/Tentative clears the date and asks for an exact day. Saved-source or photo changes invalidate export review.

**Photos are session-only.** Upload JPG/JPEG/PNG, at most 8 MB, 64–8000 px per side and 20 megapixels. Actual image contents/extension, animation and decompression limits are validated. Images are oriented, reduced to a maximum 2400 px edge, stripped of EXIF/GPS metadata and locally normalized. Choose the full original image or a centre crop, replace it, or explicitly remove it. Photos appear in all four templates and are never sent to Groq, uploaded to cloud storage or encoded into wedding JSONB. Only photo role/framing and a photo-expectation flag are saved. After logout/project switching/new-session reopening, re-upload or remove that expectation before export. No private Supabase Storage integration was present, so no bucket or policy was created.

Durable photo restoration would require approval to configure a **private bucket**, owner/project-scoped upload/read/delete policies, authenticated or short-lived signed retrieval, and replacement/project-deletion cleanup. Public buckets and service-role credentials are not used. Actual AI photo-to-cartoon transformation remains Sprint 6B; the local portrait layer accepts original image bytes and can support future portrait sources, without claiming a cartoon service exists.

Preview and download use identical locally rendered **1080 × 1920 PNG bytes**. One-page cards download as PNG; multipage cards download as a ZIP containing real numbered PNGs. The renderer uses available system fonts, including Nirmala on Windows for non-Latin text when available, with fallback fonts elsewhere. Full script coverage is installation-dependent. This Windows Pillow build lacks RAQM complex-script shaping: Indic/Arabic joins may render incorrectly. The preview warns users to inspect the PNG or use a transliteration, and export requires an explicit rendering review. No font files or paid image-generation service are added.

**Suggest Invitation Wording** is optional. One explicit Groq request selects and arranges 1–3 verified neutral phrases using invitation type, selected events, host context, cultural preferences and tone. This constrained JSON/text contract prevents invented names, dates, addresses or religious assumptions from entering the generated message. Users review the proposal and choose **Use this wording** before it replaces anything; they can then edit freely. Successes and failures are cached per context in this project session, with explicit retry. Missing-field checks, previews, edits, photos, navigation and saves make no Groq calls. Provider failures produce a labelled neutral fallback proposal. No image bytes or photo filenames are included in the wording context.

Multiple cards live in version 1 `wedding_profile.wedding_invitations` JSONB with stable UUIDs, type, selected ceremony IDs, template, wording, overrides, family/design settings and an aware updated timestamp. Existing projects restore an empty collection; malformed collections are isolated and never silently overwritten. Each save merges one card into the latest collection, preserving other cards and unrelated JSONB extensions. Owner filters, same-card baselines and optimistic `updated_at` conditions protect concurrent edits. Matching update responses and a separate database read-back are required for success. Independent card saves merge; concurrent edits to the same card require explicit comparison/review. Failed saves retain drafts/photos and the existing project-switch safeguard. No schema, RLS, auth-persistence or cloud changes are applied.

Added source files: `invitations.py`, `invitation_rendering.py`, `invitation_wording.py`, `invitation_state.py`, `invitation_studio.py`. Modified: `wedding_projects.py`, `wedding_project_state.py`, `home.py`, `navigation.py`, `wedding_dashboard.py`, `requirements.txt`, this README, existing dashboard/navigation tests and the browser smoke script. Added focused tests: `test_invitations.py`, `test_invitation_rendering.py`, `test_invitation_wording.py`, `test_invitation_persistence.py`, `test_invitation_studio.py`.

Real headless Edge smoke verification on **19 October 2026** passed on `localhost:8505`: the current Invitation Studio route/title is registered, native Sign up navigation and browser Back/Forward work, and fresh protected overview/budget/calendar/studio/editor URLs return to login without project content. Port 8505 had no listener, so the local Streamlit app was started there for verification. No accounts or projects were modified. Authenticated studio editing, upload/download, Groq and database lifecycle tests use AppTest/service mocks or Supabase SDK mock transport; they are not authenticated live-browser verification.

Final automated validation: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --tb=short` returned **541 passed in 134.73s (0:02:14)**. Python compilation of `app.py`, `ai_event_planning_platform` and `tests`, plus `git diff --check`, passed.

Manual live checks on the existing **http://localhost:8505** server:

1. Log in, create a minimal wedding, and open Invitation Studio directly. Confirm the four original designs appear before completing other planning forms. Try every invitation type, including one custom ceremony and separate family-side selections.
2. Check autofilled names/date/city/ceremony details. Add card-only venue, location and family overrides; verify wedding requirements, budget and timeline remain intact. Switch templates and use Back a step plus native Back/Forward; confirm unsaved work remains.
3. Preview an incomplete card, save its draft, and confirm download is blocked. Enter missing names/hosts or explicitly choose announcement/TBA wording for undecided event details. Review spelling, certainty labels and Unicode rendering before download.
4. Upload an original JPG/PNG, inspect contain/crop, replace and remove it. Save with a photo, log out/in and reopen: text/design restore, photo pixels do not. Re-upload or remove the expectation. No real photograph should be sent to Groq or stored in wedding JSONB.
5. Generate wording once, inspect the proposal, then accept/edit it. Edits, preview navigation and save must not regenerate wording. Export every style; verify PNG dimensions, page order and readability with long names and a combined itinerary.
6. Save multiple designs, reopen them and switch weddings to check isolation. In two sessions, save distinct cards, then conflict on the same card; compare the latest saved details before replacing. Automated external-service and lifecycle checks use mocks; authenticated live verification remains manual.

Deferred: durable cloud photo storage, AI cartoon conversion (6B), inspiration-card upload, invitation websites, sending, bulk messaging, RSVPs and broader application redesign. No commit or push is part of Sprint 6A.

## Invitation Studio repairs (Sprint 6A.1)

The review crash was reproduced by checking the final review checkbox and clicking **Load latest saved details for comparison**. That action previously assigned `wedding_invitation_reviewed = False` after creating the checkbox with that key. The refresh now runs in a button callback before widgets are instantiated. Review invalidation still happens before checkbox creation when the rendered content changes. Actual browser interactions also exposed a stale formatted design-picker value during template switching: invalid selections are now ignored, the picker reflects the active draft, and a session with an earlier invalid entry can recover its valid unsaved cards. Reruns, template changes and source refreshes preserve the invitation draft.

Linked inputs now render their resolved model values into widget state before each control is created, instead of relying on defaults that a keyed widget ignores after its first render. Entering/re-entering the Studio reads the latest owner-scoped saved profile, ceremony plans and accepted timeline once; ordinary preview/edit reruns make no additional database or Groq requests. This source-only refresh retains local cards/photos, other unsaved form edits and invitation save-conflict baselines. Custom invitation overrides remain authoritative. Failed reads retain the last available context and show a retry message.

New cards carry `linking_version: 1`, meaning absent overrides are linked and deliberate edits are invitation-only. Older JSON without this marker loads as version 0 without deleting any values. **Linked wedding details → Use Latest Wedding Details** offers an explicit refresh and removes shared name/date/time/venue/location overrides only after confirmation when overrides exist. It keeps custom wording, ceremony selections, family details, event notes, template/palette and photos. This is a JSON compatibility marker; no database migration is needed. A labelled overall **Wedding date** appears on combined/family cards when known; individual cards retain only their ceremony's date. A main wedding date never silently replaces a separate ceremony date.

The final screen now says **Complete or confirm these invitation details to download your card.**, lists each blocking field plus review/photo requirements, and offers **Complete invitation details** to open the relevant editor. Save Draft remains available for incomplete cards. Explicit announcement/TBA consent and placeholder validation are preserved. The full preview uses a flexible display width and an explicit PNG output format, avoiding Streamlit's previous reduction from 1080 px to 430 px and automatic RGB-to-JPEG conversion; preview and download now serve the identical PNG bytes. The existing Pillow renderer, 1080 × 1920 output and multipage PNG ZIP format are retained.

Changed source: `home.py`, `invitations.py`, `invitation_state.py`, `invitation_studio.py`, `invitation_rendering.py`. Updated tests: `test_invitations.py`, `test_invitation_persistence.py`, `test_invitation_studio.py`, `test_invitation_rendering.py`. Added manual browser harness: `browser_invitation_studio_smoke.py`. Documentation: this README. Existing weddings, authentication, all four styles, budget/timeline data and saved drafts are preserved. No schema/RLS/cloud changes, commit or push are part of this repair.

Automated validation: the focused invitation run passed **112 tests**, with three additional picker/recovery regressions also passing. The final `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --tb=short` returned **570 passed in 94.18s (0:01:34)**. Python compilation and `git diff --check` passed. The real server's public navigation/Back/Forward and protected-route login guards also passed headless browser smoke checks.

`tests/browser_invitation_studio_smoke.py` passed actual Edge interactions on the existing `localhost:8505` frontend: editing/reruns, template-switch draft retention, checked review followed by load-latest, source updates with override preservation, incomplete draft save/checklist, explicit TBA, and PNG/three-page ZIP downloads. Every downloaded PNG was validated as 1080 × 1920, with the first page byte-identical to the browser's displayed PNG. Its QA-only websocket/media bridge runs the production Python UI in AppTest with mocked authentication/repository; native downloads receive the exact export payload through QA data URLs because Chromium bypasses the fixture's HTTP download routing. This checks actual browser interaction with mocked services/media, not authenticated live-account or live Supabase/Groq/download-endpoint behavior. No production auth change, real account/project modification or duplicate server was involved. Browser/process permissions are needed on Windows.

## Minimal Wedding Dashboard (Sprint 7A.1)

The central dashboard now shows a compact saved-data header with available couple names, the fixed/approximate date (or its undecided status), and city/state. Four consistent destinations open the existing **Wedding Details**, **Budget Dashboard**, **Calendar & Timeline** and **Invitations** routes. The duplicate dashboard breadcrumb/feature row is removed; shared navigation on other pages is unchanged.

**Your Next Step** shows one deterministic action in this order: complete missing essential wedding details, choose ceremonies, plan the first ceremony without a saved plan, review an unaccepted/missing budget, create an unaccepted/missing timeline, then review the dashboard summary. Essentials reuse the existing missing-field checks for city, state, location preference, guest count and overall budget; a fixed/approximate date needs its corresponding value. Optional names/culture/style and an explicitly undecided date do not block the recommendation. Only saved snapshots and accepted plans are used, without Groq, additional database reads or completion percentages. Invitations stays available at every stage.

Metrics, optional name editing and the existing dream-wedding entry are inside the collapsed **Wedding summary**. Detailed **Planning alerts** are separately collapsed, without nested expanders. Ceremony selection/planning remain available through Wedding Details and contextual recommendations; budget, timeline and invitation editing remain on their existing pages. Project management stays in the shared header. The complete-state recommendation opens the summary for review.

Changed files: `ai_event_planning_platform/wedding_dashboard.py`, `ai_event_planning_platform/dashboard_data.py`, `ai_event_planning_platform/navigation.py`, `tests/test_central_wedding_dashboard.py`, `tests/test_dashboard_data.py`, `tests/test_ceremony_planning.py`, `tests/browser_invitation_studio_smoke.py` and this README. No authentication, persistence, budget calculations, calendar scheduling, invitation rendering/export, schema/RLS or cloud configuration changes are included.

Verification: **128 focused tests passed**. The requested single full-suite run returned **585 passed, 13 failed in 91.12s (0:01:31)**; all failures came from ceremony tests clicking the removed dashboard shortcut. Their shared setup now follows Wedding Details → Edit Ceremony Plans, preserving the existing assertions, with the empty protected-route case tested directly. The affected module then passed **13 tests in 8.63s**. All 598 test cases have therefore passed across the full run and targeted rerun; the full suite was not repeated. Final compilation and `git diff --check` passed.

Headless Edge verification via `.venv/Scripts/python.exe tests/browser_invitation_studio_smoke.py --dashboard` passed on the existing `localhost:8505`: saved names/date/location, four unique destinations and Back, one recommended next action, collapsed summaries and summary disclosure, with zero Groq calls. This uses the existing frontend with the QA-only AppTest bridge and mocked authentication/repository/backend; it is not live-account or live-database verification. No duplicate server or real project changes were made.

## Launch

```powershell
python -m streamlit run app.py
```
