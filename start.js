module.exports = {
  daemon: true,
  run: [{
    method: "shell.run",
    params: {
      path: "app",
      venv: ".venv",
      // LAN remains the explicit default for n8n on another computer.
      // To opt into Windows-only access, change -Mode LAN to -Mode LocalOnly.
      // The firewall is configured separately: this launcher never changes it.
      message: "powershell -NoProfile -ExecutionPolicy Bypass -File run.ps1 -Mode LAN -Port 8080",
      on: [{
        event: "/(http:\\/\\/[0-9.:]+)/",
        done: true
      }]
    }
  }, {
    method: "local.set",
    params: {
      url: "{{input.event[1]}}"
    }
  }, {
    method: "process.wait",
    params: {
      uri: "{{local.url}}"
    }
  }]
};
