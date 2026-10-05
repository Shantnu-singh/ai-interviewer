"use strict";

(() => {
  const form = document.getElementById("start-form");
  const input = document.getElementById("github");
  const button = document.getElementById("start-btn");
  const errorEl = document.getElementById("form-error");

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    errorEl.textContent = "";
    const github = input.value.trim();
    if (!github) {
      errorEl.textContent = "Please enter your GitHub profile URL or username.";
      input.focus();
      return;
    }

    button.disabled = true;
    button.textContent = "Starting…";
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
      errorEl.textContent = err.message;
      button.disabled = false;
      button.textContent = "Start interview";
      input.focus();
    }
  });
})();
