from pathlib import Path


ROOT = Path(__file__).parents[1]
TEMPLATE = (ROOT / "app/templates/form.html").read_text()
SCRIPT = (ROOT / "app/static/js/form.js").read_text()
STYLES = (ROOT / "app/static/css/app.css").read_text()


def test_setup_form_has_accessible_controls_and_hooks():
    assert '<form id="start-form" class="setup-form" novalidate>' in TEMPLATE
    assert '<label for="github" class="setup-label">' in TEMPLATE
    assert 'GitHub profile URL or username' in TEMPLATE
    assert 'id="github"' in TEMPLATE
    assert 'id="start-btn"' in TEMPLATE
    assert 'id="form-help"' in TEMPLATE
    assert 'id="form-error"' in TEMPLATE
    assert 'aria-describedby="form-help form-error"' in TEMPLATE
    assert 'aria-invalid="false"' in TEMPLATE
    assert 'aria-busy="false"' in TEMPLATE
    assert 'class="setup-card"' in TEMPLATE
    assert 'class="setup-actions"' in TEMPLATE


def test_form_script_validates_and_restores_loading_state():
    assert 'const github = input.value.trim();' in SCRIPT
    assert 'Please enter your GitHub profile URL or username.' in SCRIPT
    assert 'input.disabled = true;' in SCRIPT
    assert 'button.disabled = true;' in SCRIPT
    assert 'button.textContent = loadingLabel;' in SCRIPT
    assert 'button.setAttribute("aria-busy", "true")' in SCRIPT
    assert 'credentials: "same-origin"' in SCRIPT
    assert 'JSON.stringify({ github })' in SCRIPT
    assert 'button.textContent = defaultLabel;' in SCRIPT
    assert 'input.focus();' in SCRIPT
    assert 'encodeURIComponent(data.id)' in SCRIPT
    assert 'input.addEventListener("input", clearError);' in SCRIPT


def test_form_styles_include_responsive_and_state_rules():
    assert ".setup-card" in STYLES
    assert ".setup-field" in STYLES
    assert ".setup-label" in STYLES
    assert ".setup-helper" in STYLES
    assert ".setup-actions" in STYLES
    assert '.setup-field input:focus-visible' in STYLES
    assert '.setup-field input[aria-invalid="true"]' in STYLES
    assert ".setup-actions .btn:disabled" in STYLES
    assert "@media (max-width: 30rem)" in STYLES
    assert "flex-direction: column" in STYLES
