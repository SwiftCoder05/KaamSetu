// Settings > Email sending: provider presets.
// The server re-validates everything (host, port, security); this only makes the form pleasant.
(function () {
  const form = document.getElementById("mail-form");
  const presetsEl = document.getElementById("mail-presets");
  if (!form || !presetsEl) return;
  const presets = JSON.parse(presetsEl.textContent);
  const PORT = { ssl: 465, starttls: 587 };
  const el = (n) => form.querySelector('[name$="-' + n + '"]');
  const row = (n) => form.querySelector('[data-field="' + n + '"]');
  const provider = el("provider"), host = el("smtp_host"), security = el("security"), port = el("smtp_port");
  const passLabel = row("password").querySelector("label");
  const passHelp = document.getElementById("mail-pass-help");
  const help = document.getElementById("mail-provider-help");

  function apply() {
    const p = presets[provider.value];
    const custom = provider.value === "custom";
    row("smtp_host").hidden = !custom; // presets fix the host server-side, so there is nothing to type
    host.required = custom;
    if (!custom) host.value = p.host;
    // Only offer the security modes the provider supports.
    Array.from(security.options).forEach((o) => { o.hidden = o.disabled = p.securities.indexOf(o.value) < 0; });
    if (p.securities.indexOf(security.value) < 0) security.value = p.security;
    port.value = PORT[security.value]; // the port follows the security mode: 465 = SSL/TLS, 587 = STARTTLS
    port.readOnly = true;
    passLabel.textContent = p.password_label;
    if (passHelp) passHelp.textContent = "Stored encrypted and never shown again.";
    help.innerHTML = p.help; // static, server-defined text with our own links — not user input
  }

  provider.addEventListener("change", apply);
  security.addEventListener("change", () => { port.value = PORT[security.value]; });
  apply();
})();
