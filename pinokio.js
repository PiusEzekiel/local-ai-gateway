module.exports = {
  version: "7.0",
  title: "Local AI Gateway",
  description: "Run and monitor the local AI service used by n8n.",
  menu: async (kernel, info) => {
    const installed = info.exists("app/.venv");
    const installing = info.running("install.js");
    const starting = info.running("start.js");
    if (installing) {
      return [{ default: true, icon: "fa-solid fa-plug", text: "Installing", href: "install.js" }];
    }
    if (!installed) {
      return [{ default: true, icon: "fa-solid fa-plug", text: "Install", href: "install.js" }];
    }
    if (starting) {
      const local = info.local("start.js");
      if (info.ready("start.js") && local && local.url) {
        return [
          { default: true, icon: "fa-solid fa-gauge", text: "Dashboard", href: local.url },
          { icon: "fa-solid fa-terminal", text: "Server log", href: "start.js" }
        ];
      }
      return [{ default: true, icon: "fa-solid fa-terminal", text: "Starting", href: "start.js" }];
    }
    return [
      { default: true, icon: "fa-solid fa-power-off", text: "Start", href: "start.js" },
      { icon: "fa-solid fa-arrow-up", text: "Update dependencies", href: "update.js" },
      { icon: "fa-solid fa-plug", text: "Install", href: "install.js" },
      { icon: "fa-solid fa-rotate", text: "Reset environment", href: "reset.js", confirm: "Remove the Pinokio Python environment? The gateway token and app code will stay." }
    ];
  }
}
