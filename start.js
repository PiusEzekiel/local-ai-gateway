module.exports = {
  daemon: true,
  run: [{
    method: "shell.run",
    params: {
      path: "app",
      venv: ".venv",
      message: "powershell -NoProfile -ExecutionPolicy Bypass -File run.ps1 -Bind 0.0.0.0 -Port 8080",
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
}
