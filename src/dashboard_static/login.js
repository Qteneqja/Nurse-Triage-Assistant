// Dashboard sign-in (PR 4.2). External file — the app's CSP is
// script-src 'self', so inline scripts never run. Keep ALL login logic here.
const tokenField = document.getElementById("login-token");
const errorEl = document.getElementById("login-error");

async function signIn() {
  const token = tokenField.value.trim();
  if (!token) {
    errorEl.textContent = "Token is required.";
    return;
  }
  errorEl.textContent = "Checking token...";
  let check;
  try {
    // Validate against a data endpoint before storing anything.
    check = await fetch("/api/v1/dashboard/records?limit=1", {
      headers: { "X-Dashboard-Token": token },
    });
  } catch (error) {
    errorEl.textContent = `Could not reach the API: ${error.message}`;
    return;
  }
  if (check.status === 401 || check.status === 403) {
    errorEl.textContent =
      "That token was rejected. Check the value in your deployment config.";
    return;
  }
  if (!check.ok) {
    errorEl.textContent = `Unexpected API response (${check.status}).`;
    return;
  }
  localStorage.setItem("dashboardAdminToken", token);
  const secure = window.location.protocol === "https:" ? "; Secure" : "";
  document.cookie = `dashboard_token=${encodeURIComponent(token)}; path=/; SameSite=Strict${secure}`;
  window.location.href = "/dashboard/records";
}

document.getElementById("login-submit").addEventListener("click", signIn);
tokenField.addEventListener("keydown", (event) => {
  if (event.key === "Enter") signIn();
});
