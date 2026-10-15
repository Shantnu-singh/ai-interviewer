# Polish the interview setup form experience

- **Date:** 2026-10-15
- **Type:** enhancement
- **Run:** `daily-ai-interviewer-20261015053504-a3278b`
- **Branch:** `devflow/enhancement/20261015-polish-the-interview-setup-form`

## Problem
The interview setup flow is concentrated in a small template and script, making it suitable for a focused visual pass. Improve field grouping, labels, spacing, primary-action emphasis, and validation/loading presentation so users can confidently submit their GitHub profile.

### Evidence
- app/templates/form.html (27 lines) contains the interview setup page.
- app/static/js/form.js (39 lines) contains the setup-page interaction logic.
- app/static/css/app.css (65 lines) is the shared styling surface for the form.

## Why
The setup experience is concentrated in three small application surfaces, making a focused improvement practical within one PR. The current form presents the profile field and primary action as a single compact row, provides minimal visual grouping, and only exposes an empty error region until submission fails. Loading and validation states are functional but could be clearer and more accessible, especially on narrow screens and for users relying on assistive technology.

## How
1. **Restructure the setup form markup** — Update app/templates/form.html to give the form a clear visual section with a descriptive heading or supporting label, group the GitHub field and its guidance together, and keep the primary action visually prominent. Replace the visually hidden-only label with an explicit accessible field label while retaining the existing profile URL-or-username wording. Preserve the existing form id, input id, button id, error id, API behavior, maxlength, autocomplete/inputmode attributes, and aria-describedby relationship so the current script and backend contract remain compatible. Add appropriate loading-related accessibility attributes or status text hooks that the script can update without introducing a new page or component.
2. **Improve form layout and visual hierarchy** — Extend app/static/css/app.css with form-specific classes for the setup card, field group, label, helper text, and action area. Use consistent spacing, borders, focus treatment, and responsive behavior so the field and button stack cleanly on small screens while remaining aligned on wider screens. Strengthen the primary action contrast and disabled/loading presentation, ensure error text reserves space without causing excessive layout shift, and keep all changes limited to application styling rather than changing shared interview/result layouts.
3. **Refine client-side validation and loading state** — Update app/static/js/form.js to validate the trimmed GitHub value before making a request, set the error element's text and relevant accessibility state on validation or server errors, and clear that state when the user retries or edits the field. During submission, disable the input and button, expose a clear 'Starting…' loading label, and prevent duplicate submissions. In every failure path restore the original label and controls, focus the input, and leave the user-facing error actionable. Preserve same-origin credentials, JSON request format, response error handling, and redirect encoding. Add a small input event handler if needed to clear stale validation state while the user corrects the value.
4. **Add focused regression coverage** — Create tests/test_form.py using the repository's existing Flask test-client conventions. Verify the setup route renders the explicit GitHub label, helper/error hooks, form controls, and form-specific classes; verify the JavaScript source retains required submit behavior, empty-input messaging, disabled/loading handling, error restoration, and encoded redirect behavior; and verify the CSS source contains responsive form layout and disabled/focus styling. Keep these tests deterministic and independent of a browser or network so they run under the supplied pytest command.
5. **Run project validation** — Run .devflow-cache/venv/bin/python -m pytest -q and .devflow-cache/venv/bin/python -m ruff check .. Resolve only application or test issues surfaced by these commands; do not modify workflow, deployment, configuration, or secret files.

## Impact on the project
Users get a clearer, more approachable starting point for the interview, with stronger field grouping, better mobile behavior, more obvious primary-action emphasis, and understandable validation/loading feedback. Keyboard and assistive-technology users receive an explicit label, live error updates, and predictable focus restoration. Maintainers retain the existing API and routing contracts while gaining focused regression coverage for the small form surface.

## Files to change
- `app/templates/form.html`
- `app/static/js/form.js`
- `app/static/css/app.css`
- `tests/test_form.py`

## Test strategy
Add focused pytest coverage for the rendered form contract and the static application behavior that cannot be exercised by the repository's Python-only test stack. Tests should assert the explicit label, helper/error accessibility hooks, stable element ids, responsive/form-specific class hooks, validation text, loading-state text, control disabling/restoration, and encoded redirect logic. Run the full suite with .devflow-cache/venv/bin/python -m pytest -q, then run .devflow-cache/venv/bin/python -m ruff check . to validate formatting and lint compliance. The implementation should remain well below 600 changed lines and touch only the four listed application/test files.

## Acceptance criteria
- [ ] The setup page has an explicit accessible GitHub profile label, grouped helper guidance, and a clearly identifiable primary action.
- [ ] The form layout has consistent spacing and visual grouping, stacks cleanly on narrow screens, and preserves visible keyboard focus.
- [ ] Submitting an empty or whitespace-only value shows a clear inline error, does not call the API, and returns focus to the GitHub field.
- [ ] While a request is pending, the controls visibly communicate loading and prevent duplicate submissions.
- [ ] Network and server errors are announced through the existing live error region, and controls are restored with the original action label so the user can retry.
- [ ] A successful response redirects to the encoded interview URL exactly as before, with no backend contract changes.
- [ ] The full pytest suite passes and ruff reports no errors using the specified validation commands.
- [ ] Only the four listed application and test files are modified, with no more than 600 changed lines.

## Risks
- Static assertions may become coupled to exact wording or class names; keep assertions focused on stable accessibility hooks, behavior-critical strings, and semantic structure.
- Changing the form from a single row to a responsive grouped layout could expose overflow or spacing regressions at narrow widths; cover the mobile stacking rules in CSS and manually inspect the resulting markup during implementation.
- Adding client-side disabled states could leave controls locked if an unexpected exception occurs; use a single failure/finalization path that restores the original controls.
- The existing backend may return non-JSON error responses; preserve the current safe JSON parsing fallback and generic error message.

## Rollback
Revert the changes to app/templates/form.html, app/static/js/form.js, app/static/css/app.css, and tests/test_form.py. The original API endpoint, element ids, redirect behavior, and backend code remain unchanged, so rollback does not require data or deployment changes.

## Out of scope
- Changing interview, result, or shared page layouts beyond styles required by the setup form.
- Changing GitHub URL/username validation rules enforced by the backend.
- Adding a JavaScript test runner, browser automation framework, or new runtime dependency.
- Changing API endpoints, server-side interview creation behavior, authentication, deployment, CI, or workflow configuration.

---
_Generated by the automated development workflow. Review before merging._
