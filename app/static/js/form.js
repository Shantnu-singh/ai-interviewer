"use strict";

(() => {
  const form = document.getElementById("start-form");
  const input = document.getElementById("github");
  const button = document.getElementById("start-btn");
  const errorEl = document.getElementById("form-error");
  const statusEl = document.getElementById("form-status");
  const defaultLabel = "Start interview";
  const loadingLabel = "Starting…";

  const setError = (message) => {
    errorEl.textContent = message;
    errorEl.hidden = !message;
    input.setAttribute("aria-invalid", message ? "true" : "false");
  };

  const clearError = () => setError("");

  const restoreControls = () => {
    input.disabled = false;
    button.disabled = false;
    button.textContent = defaultLabel;
    button.setAttribute("aria-busy", "false");
    statusEl.textContent = "";
  };

  input.addEventListener("input", clearError);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearError();
    const github = input.value.trim();
    if (!github) {
      setError("Please enter your GitHub profile URL or username.");
      input.focus();
      return;
    }

    input.disabled = true;
    button.disabled = true;
    button.textContent = loadingLabel;
    button.setAttribute("aria-busy", "true");
    statusEl.textContent = loadingLabel;

    try {
      const resp = await fetch("/api/v1/interviews", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ github }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(data.error || "Something went wrong. Please try again.");
      window.location.assign(`/interview/${encodeURIComponent(data.id)}`);
    } catch (err) {
      setError(err.message || "Something went wrong. Please try again.");
      restoreControls();
      input.focus();
      return;
    }

    restoreControls();
  });
})();
