module.exports = {
  run: [{
    method: "shell.run",
    params: {
      path: "app",
      venv: ".venv",
      message: "uv pip install --upgrade -r requirements.txt"
    }
  }]
}
