/* live.js - shared "live dashboard" utilities used across admin pages.
   Loaded on every admin page via base_admin.html. */

// ---------- Toast notifications ----------

function smartlabToast(message, type) {
  type = type || "success";
  var container = document.getElementById("toast-container");
  if (!container) {
    container = document.createElement("div");
    container.id = "toast-container";
    document.body.appendChild(container);
  }
  var toast = document.createElement("div");
  toast.className = "toast toast-" + type;
  toast.textContent = message;
  container.appendChild(toast);

  requestAnimationFrame(function () { toast.classList.add("show"); });

  setTimeout(function () {
    toast.classList.remove("show");
    setTimeout(function () { toast.remove(); }, 300);
  }, 3500);
}

// ---------- Animated number counting ----------

function smartlabAnimateNumber(el, toValue, duration) {
  if (!el) return;
  duration = duration || 600;
  var fromValue = parseFloat(el.getAttribute("data-value") || el.textContent) || 0;
  toValue = parseFloat(toValue);
  if (fromValue === toValue) { el.setAttribute("data-value", toValue); return; }

  var startTime = null;
  function step(timestamp) {
    if (!startTime) startTime = timestamp;
    var progress = Math.min((timestamp - startTime) / duration, 1);
    var eased = 1 - Math.pow(1 - progress, 3); // ease-out cubic
    var current = fromValue + (toValue - fromValue) * eased;
    var isPercent = el.hasAttribute("data-percent");
    el.textContent = isPercent ? (Math.round(current * 10) / 10) + "%" : Math.round(current);
    if (progress < 1) {
      requestAnimationFrame(step);
    } else {
      el.setAttribute("data-value", toValue);
    }
  }
  requestAnimationFrame(step);
}

// ---------- Relative time (e.g. "2 minutes ago") ----------

function smartlabRelativeTime(timestampStr) {
  // timestampStr format: "YYYY-MM-DD HH:MM"
  if (!timestampStr) return "";
  var parts = timestampStr.split(/[- :]/);
  var then = new Date(parts[0], parts[1] - 1, parts[2], parts[3], parts[4]);
  var diffSec = Math.floor((Date.now() - then.getTime()) / 1000);
  if (diffSec < 0) diffSec = 0;
  if (diffSec < 60) return "Just now";
  var diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return diffMin + (diffMin === 1 ? " minute ago" : " minutes ago");
  var diffHour = Math.floor(diffMin / 60);
  if (diffHour < 24) return diffHour + (diffHour === 1 ? " hour ago" : " hours ago");
  var diffDay = Math.floor(diffHour / 24);
  return diffDay + (diffDay === 1 ? " day ago" : " days ago");
}

function smartlabRefreshRelativeTimes() {
  document.querySelectorAll("[data-timestamp]").forEach(function (el) {
    el.textContent = smartlabRelativeTime(el.getAttribute("data-timestamp"));
  });
}

// ---------- Global notification bell polling ----------
// Runs on every admin page so the bell badge stays current even while the
// admin is on the Computers or Reports page, not just the Dashboard.

var SMARTLAB_POLL_MS = 7000;
var smartlabLastOpenCount = null;

function smartlabUpdateBell(stats, notifEnabled) {
  var badge = document.querySelector(".bell-badge");
  var bell = document.querySelector(".bell");
  var count = notifEnabled ? stats.open_reports : 0;

  if (count > 0) {
    if (!badge) {
      badge = document.createElement("span");
      badge.className = "bell-badge";
      if (bell) bell.appendChild(badge);
    }
    badge.textContent = count;
  } else if (badge) {
    badge.remove();
  }

  if (smartlabLastOpenCount !== null && count > smartlabLastOpenCount && bell) {
    bell.classList.remove("bell-ring");
    void bell.offsetWidth; // restart animation
    bell.classList.add("bell-ring");
  }
  smartlabLastOpenCount = count;
}

function smartlabPollDashboard() {
  fetch("/api/dashboard-data")
    .then(function (res) { return res.ok ? res.json() : null; })
    .then(function (data) {
      if (!data) return;
      var notifEnabled = document.body.getAttribute("data-notif-new-report") !== "off";
      smartlabUpdateBell(data.stats, notifEnabled);
      document.dispatchEvent(new CustomEvent("smartlab:dashboard-data", { detail: data }));
    })
    .catch(function () { /* silent - a missed poll isn't worth alarming the user */ });
}

document.addEventListener("DOMContentLoaded", function () {
  smartlabRefreshRelativeTimes();
  setInterval(smartlabRefreshRelativeTimes, 30000);

  if (document.body.getAttribute("data-logged-in") === "true") {
    // Seed smartlabLastOpenCount from the server-rendered badge so the very
    // first poll doesn't ring the bell for reports that were already open.
    var existingBadge = document.querySelector(".bell-badge");
    smartlabLastOpenCount = existingBadge ? parseInt(existingBadge.textContent, 10) : 0;
    setInterval(smartlabPollDashboard, SMARTLAB_POLL_MS);
  }
});
