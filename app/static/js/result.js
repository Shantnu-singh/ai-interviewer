"use strict";

(() => {
  const root = document.querySelector("[data-interview-id]");
  const url = `/api/v1/interviews/${encodeURIComponent(root.dataset.interviewId)}/result`;
  const pending = document.getElementById("pending");
  const resultEl = document.getElementById("result");
  const errorEl = document.getElementById("result-error");

  // All server text is rendered with textContent (never innerHTML).
  function fillList(id, items) {
    const ul = document.getElementById(id);
    ul.replaceChildren(
      ...(items.length ? items : ["—"]).map((text) => {
        const li = document.createElement("li");
        li.textContent = text;
        return li;
      }),
    );
  }

  function renderTranscript(lines) {
    const ol = document.getElementById("transcript");
    if (!lines.length) {
      const li = document.createElement("li");
      li.className = "muted";
      li.textContent = "No messages were recorded for this interview.";
      ol.replaceChildren(li);
      return;
    }
    ol.replaceChildren(
      ...lines.map((m) => {
        const li = document.createElement("li");
        li.className = m.role === "assistant" ? "assistant" : "user";
        const who = document.createElement("span");
        who.className = "who";
        who.textContent = m.role === "assistant" ? "Interviewer" : "You";
        li.append(who, document.createTextNode(m.content));
        return li;
      }),
    );
  }

  async function poll() {
    try {
      const resp = await fetch(url, { credentials: "same-origin" });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || "Could not load results.");
      renderTranscript(data.transcript || []);

      if (data.status === "done") {
        pending.hidden = true;
        resultEl.hidden = false;
        document.getElementById("score").textContent = String(data.score ?? 0);
        document.getElementById("feedback").textContent = data.feedback || "";
        fillList("strengths", data.strengths || []);
        fillList("improvements", data.improvements || []);
        return;
      }
      if (data.status === "failed") {
        pending.hidden = true;
        errorEl.hidden = false;
        errorEl.textContent = data.error || "This interview could not be evaluated.";
        return;
      }
    } catch (err) {
      errorEl.hidden = false;
      errorEl.textContent = err.message;
    }
    setTimeout(poll, 3000);
  }

  poll();
})();
