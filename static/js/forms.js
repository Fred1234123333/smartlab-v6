/*
 * forms.js
 * Two independent jobs, both applied to every relevant form on the page:
 *
 * 1) CSRF token injection - every form that POSTs needs a hidden
 *    csrf_token field. Rather than hand-editing every <form> tag in every
 *    template, this reads the token from the page's <meta name="csrf-token">
 *    (set once in each base template) and injects it automatically.
 *
 * 2) "Done button" behavior:
 *   - The submit button stays disabled until every required field holds
 *     valid information (using the browser's own HTML5 validation rules:
 *     required, minlength, type=email, plus any custom rule a page adds
 *     with input.setCustomValidity(...)).
 *   - Optional fields never block the button.
 *   - On submit, the button is disabled immediately and its label swaps
 *     to a "please wait" state, so a slow connection can't result in the
 *     same form being submitted twice.
 *
 * Usage:
 *   <form data-smartlab-validate>          ... real field-by-field gating
 *     <input required> ...
 *     <button type="submit" data-done-btn>Save</button>
 *   </form>
 *
 *   <form data-smartlab-guard>             ... no fields can be "invalid"
 *     ...                                       (e.g. a set of toggles),
 *     <button type="submit">Save</button>        just guard against double-submit
 *   </form>
 */
(function () {
  function getCsrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") : null;
  }

  function injectCsrfTokens() {
    var token = getCsrfToken();
    if (!token) return; // page has no meta tag (shouldn't happen on form-bearing pages)
    document.querySelectorAll("form").forEach(function (form) {
      var method = (form.getAttribute("method") || "get").toLowerCase();
      if (method !== "post") return;
      if (form.querySelector('input[name="csrf_token"]')) return; // already has one
      var input = document.createElement("input");
      input.type = "hidden";
      input.name = "csrf_token";
      input.value = token;
      form.appendChild(input);
    });
  }

  // Exposed so JS-driven fetch() POST calls elsewhere (live.js, computers.html,
  // scan.html) can attach the same token as an X-CSRFToken header.
  window.smartlabCsrfToken = getCsrfToken;

  function wireValidatedForm(form) {
    var btn = form.querySelector("[data-done-btn]");
    if (!btn) return;
    var busyText = btn.getAttribute("data-busy-text") || "Please wait…";

    function refresh() {
      btn.disabled = !form.checkValidity();
    }

    form.addEventListener("input", refresh);
    form.addEventListener("change", refresh);
    refresh();

    form.addEventListener("submit", function (e) {
      if (!form.checkValidity()) {
        e.preventDefault();
        return;
      }
      btn.disabled = true;
      btn.textContent = busyText;
    });
  }

  function wireGuardOnlyForm(form) {
    var btn = form.querySelector("button[type='submit']");
    if (!btn) return;
    var busyText = btn.getAttribute("data-busy-text") || "Please wait…";
    form.addEventListener("submit", function () {
      btn.disabled = true;
      btn.textContent = busyText;
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    injectCsrfTokens();
    document.querySelectorAll("form[data-smartlab-validate]").forEach(wireValidatedForm);
    document.querySelectorAll("form[data-smartlab-guard]").forEach(wireGuardOnlyForm);
  });
})();
