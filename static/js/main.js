document.addEventListener("DOMContentLoaded", function () {
  var menuToggle = document.querySelector(".menu-toggle");
  var sidebar = document.querySelector(".sidebar");
  var overlay = document.querySelector(".sidebar-overlay");
  if (menuToggle && sidebar) {
    menuToggle.addEventListener("click", function () {
      sidebar.classList.toggle("open");
      if (overlay) overlay.classList.toggle("open");
    });
  }
  if (overlay) {
    overlay.addEventListener("click", function () {
      sidebar.classList.remove("open");
      overlay.classList.remove("open");
    });
  }

  var bell = document.querySelector(".bell");
  var dropdown = document.querySelector(".bell-dropdown");
  if (bell && dropdown) {
    bell.addEventListener("click", function (e) {
      e.stopPropagation();
      dropdown.classList.toggle("open");
    });
    document.addEventListener("click", function (e) {
      if (!dropdown.contains(e.target) && !bell.contains(e.target)) {
        dropdown.classList.remove("open");
      }
    });
  }

  // Quick light/dark toggle in the topbar
  var themeBtn = document.getElementById("theme-toggle-btn");
  if (themeBtn) {
    themeBtn.addEventListener("click", function () {
      var current = document.documentElement.getAttribute("data-theme");
      var next = current === "light" ? "dark" : "light";
      document.documentElement.setAttribute("data-theme", next);
      localStorage.setItem("smartlab-theme-override", next);
      fetch("/api/theme", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": window.smartlabCsrfToken ? window.smartlabCsrfToken() : ""
        },
        body: JSON.stringify({ theme: next })
      }).catch(function () { /* quick-toggle still works locally even if the save fails */ });
    });
  }

  // Auto-dismiss flash alerts after a few seconds
  document.querySelectorAll(".alert").forEach(function (el) {
    setTimeout(function () {
      el.style.transition = "opacity 0.4s ease";
      el.style.opacity = "0";
      setTimeout(function () { el.remove(); }, 400);
    }, 4000);
  });
});
