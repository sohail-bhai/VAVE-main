"use strict";

document.documentElement.classList.add("js");

const navButton = document.querySelector(".nav-toggle");
const nav = document.querySelector(".site-nav");
function closeNavigation() {
  nav.classList.remove("is-open");
  navButton.setAttribute("aria-expanded", "false");
  navButton.setAttribute("aria-label", "Open navigation");
}
navButton.addEventListener("click", () => {
  const open = navButton.getAttribute("aria-expanded") !== "true";
  nav.classList.toggle("is-open", open);
  navButton.setAttribute("aria-expanded", String(open));
  navButton.setAttribute("aria-label", open ? "Close navigation" : "Open navigation");
});
nav.addEventListener("click", (event) => {
  if (event.target.closest("a")) closeNavigation();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && nav.classList.contains("is-open")) {
    closeNavigation();
    navButton.focus();
  }
});
document.addEventListener("click", (event) => {
  if (!event.target.closest(".site-header")) closeNavigation();
});

const toast = document.querySelector(".toast");
let toastTimer;
function announce(message) {
  clearTimeout(toastTimer);
  toast.textContent = message;
  toast.hidden = false;
  toastTimer = setTimeout(() => { toast.hidden = true; }, 3500);
}
document.querySelectorAll("[data-copy]").forEach((button) => {
  button.addEventListener("click", async () => {
    const source = document.getElementById(button.dataset.copy);
    try {
      if (!navigator.clipboard) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(source.textContent.trim());
      announce("Commands copied.");
    } catch {
      const selection = window.getSelection();
      const range = document.createRange();
      range.selectNodeContents(source);
      selection.removeAllRanges();
      selection.addRange(range);
      announce("Commands selected. Press Ctrl+C or use your device’s Copy action.");
    }
  });
});

const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
if (!reducedMotion.matches && "IntersectionObserver" in window) {
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add("reveal-visible");
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.12 });
  document.querySelectorAll("[data-reveal]").forEach((item) => observer.observe(item));
}

const demo = document.querySelector("[data-demo]");
if (demo) {
  const scenarios = {
    research: {
      prompt: "Research local AI assistants and summarize the trade-offs.",
      steps: [
        ["Plan a focused comparison", "Scope: public web sources and a short written brief."],
        ["Browse source pages", "Use browser tools to collect relevant source material."],
        ["Compare the findings", "Organize capabilities, setup needs, and practical limits."],
        ["Return a useful brief", "Surface a summary and the sources behind it."]
      ],
      outputs: ["A bounded plan for the requested research.", "Relevant source material collected by browser tools.", "Findings organized around the user’s question.", "A concise comparison, source links, and practical next steps."],
      result: "Example complete: a short comparison with source links and a recommendation."
    },
    desktop: {
      prompt: "Open Calculator on my desktop.",
      steps: [
        ["Recognize a desktop command", "The command router matches the request."],
        ["Select the app action", "Use the Windows app-launching implementation."],
        ["Check the action boundary", "The request passes through the relevant safety path."],
        ["Launch and report", "Return the tool response to the user."]
      ],
      outputs: ["A recognized request to open a local app.", "Calculator selected as the requested application.", "A permission check before the desktop action.", "An app-launch request and its reported result."],
      result: "Example complete: Calculator launch requested. This browser demo has not opened an app."
    },
    approval: {
      prompt: "Draft an update for my teammate, then ask me before sending it.",
      approvalAt: 2,
      steps: [
        ["Prepare a draft", "Compose a message using the task context."],
        ["Show what will be sent", "Present the recipient and proposed message."],
        ["Pause for approval", "An outward action needs the relevant permission."],
        ["Run the authorized action", "Proceed only after the approval decision."]
      ],
      outputs: ["A message draft for review.", "A visible preview of the proposed communication.", "Waiting for your example approval decision.", "Example authorization granted; a tool call could now proceed."],
      result: "Example complete: the approved path reaches the send tool and reports its result. No message was sent."
    }
  };
  const tabs = [...demo.querySelectorAll("[data-scenario]")];
  const panel = demo.querySelector("#demo-panel");
  const prompt = demo.querySelector("[data-demo-prompt]");
  const steps = demo.querySelector("[data-demo-steps]");
  const output = demo.querySelector("[data-demo-output]");
  const status = demo.querySelector("[data-demo-status]");
  const progress = demo.querySelector("[data-demo-progress]");
  const next = demo.querySelector("[data-demo-next]");
  const approval = demo.querySelector("[data-demo-approval]");
  const live = demo.querySelector("[data-demo-announcement]");
  let selected = "research";
  let cursor = 0;
  let decision = null;

  function render(shouldAnnounce = true) {
    const scenario = scenarios[selected];
    const complete = cursor === scenario.steps.length;
    const waiting = cursor === scenario.approvalAt && decision === null;
    const declined = decision === "declined";
    tabs.forEach((tab) => {
      const active = tab.dataset.scenario === selected;
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
    });
    panel.setAttribute("aria-labelledby", "tab-" + selected);
    prompt.textContent = scenario.prompt;
    steps.replaceChildren();
    scenario.steps.forEach(([title, detail], index) => {
      const state = index < cursor ? "done" : index === cursor ? declined ? "blocked" : "active" : "pending";
      const item = document.createElement("li");
      item.className = "demo-step";
      item.dataset.state = state;
      const symbol = document.createElement("span");
      symbol.className = "step-symbol";
      symbol.setAttribute("aria-hidden", "true");
      symbol.textContent = state === "done" ? "✓" : state === "blocked" ? "×" : String(index + 1);
      const text = document.createElement("div");
      text.className = "step-text";
      const heading = document.createElement("strong");
      heading.textContent = title;
      const description = document.createElement("p");
      description.textContent = detail;
      if (state === "active") item.setAttribute("aria-current", "step");
      text.append(heading, description);
      item.append(symbol, text);
      steps.append(item);
    });
    output.textContent = declined ? "Example declined: the outward action is blocked. No message was sent." : complete ? scenario.result : scenario.outputs[cursor];
    status.textContent = declined ? "Example blocked" : complete ? "Example complete" : waiting ? "Approval needed" : cursor === 0 ? "Ready to explore" : "Example in progress";
    progress.textContent = complete ? "ALL SAMPLE STEPS COMPLETE" : "STEP " + String(cursor + 1).padStart(2, "0") + " / 04";
    approval.hidden = !waiting;
    next.disabled = waiting || declined || complete;
    next.childNodes[0].textContent = complete ? "Complete " : waiting ? "Awaiting decision " : declined ? "Action blocked " : "Next step ";
    if (shouldAnnounce) live.textContent = status.textContent + ". " + output.textContent;
  }
  function selectScenario(name) {
    selected = name;
    cursor = 0;
    decision = null;
    render();
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => selectScenario(tab.dataset.scenario));
    tab.addEventListener("keydown", (event) => {
      let target;
      if (event.key === "ArrowRight") target = (index + 1) % tabs.length;
      if (event.key === "ArrowLeft") target = (index - 1 + tabs.length) % tabs.length;
      if (event.key === "Home") target = 0;
      if (event.key === "End") target = tabs.length - 1;
      if (target !== undefined) {
        event.preventDefault();
        tabs[target].focus();
        selectScenario(tabs[target].dataset.scenario);
      }
    });
  });
  next.addEventListener("click", () => { cursor += 1; render(); });
  demo.querySelector("[data-demo-reset]").addEventListener("click", () => selectScenario(selected));
  demo.querySelector("[data-demo-approve]").addEventListener("click", () => {
    decision = "approved";
    cursor += 1;
    render();
    next.focus();
  });
  demo.querySelector("[data-demo-decline]").addEventListener("click", () => {
    decision = "declined";
    render();
    demo.querySelector("[data-demo-reset]").focus();
  });
  render(false);
  demo.hidden = false;
}

if (document.querySelector("[data-release-version]")) {
  fetch(new URL("downloads/release.json", document.baseURI), { cache: "no-cache" })
    .then((response) => {
      if (!response.ok) throw new Error("Release metadata unavailable");
      return response.json();
    })
    .then((release) => {
      document.querySelector("[data-release-version]").textContent = release.version;
      document.querySelector("[data-release-checksum]").textContent = release.sha256;
      document.querySelector("[data-release-size]").textContent = (release.size_bytes / 1048576).toFixed(1) + " MB · Source ZIP";
      const download = document.querySelector(".download-meta a[download]");
      const url = new URL("downloads/" + encodeURIComponent(release.file), document.baseURI);
      url.searchParams.set("sha", release.sha256);
      download.href = url.href;
      download.download = release.file;
    })
    .catch(() => { /* Direct file previews retain usable fallback copy. */ });
}
