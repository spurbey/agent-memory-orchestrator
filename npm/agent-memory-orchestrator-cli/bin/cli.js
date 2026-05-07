#!/usr/bin/env node

const { spawnSync } = require("node:child_process");

const DEFAULT_SPEC =
  process.env.AMO_PIP_SPEC ||
  "git+https://github.com/spurbey/agent-memory-orchestrator.git";

function printUsage() {
  console.log(`
Agent Memory Orchestrator installer

Usage:
  npx agent-memory-orchestrator-cli install [--from <pip_spec>] [amo install flags]
  npx agent-memory-orchestrator-cli doctor
  npx agent-memory-orchestrator-cli --help

Examples:
  npx agent-memory-orchestrator-cli install
  npx agent-memory-orchestrator-cli install --target codex --preset cpu-balanced
  npx agent-memory-orchestrator-cli install --download-models --target all
  npx agent-memory-orchestrator-cli install --from git+https://github.com/<you>/agent-memory-orchestrator.git --target claude
  `);
}

function run(cmd, args, options = {}) {
  const result = spawnSync(cmd, args, {
    stdio: options.silent ? "pipe" : "inherit",
    encoding: "utf-8",
    shell: false,
  });

  if (result.error) {
    return { ok: false, error: result.error.message, status: result.status ?? 1 };
  }
  return { ok: result.status === 0, status: result.status ?? 0, stdout: result.stdout, stderr: result.stderr };
}

function commandExists(cmd) {
  const checker = process.platform === "win32" ? "where" : "which";
  const result = spawnSync(checker, [cmd], { stdio: "ignore", shell: false });
  return result.status === 0;
}

function findPythonLauncher() {
  const candidates = process.platform === "win32" ? ["py", "python", "python3"] : ["python3", "python"];
  for (const cmd of candidates) {
    if (commandExists(cmd)) {
      const check = run(cmd, ["--version"], { silent: true });
      if (check.ok) {
        return cmd;
      }
    }
  }
  return null;
}

function getPipxRunner() {
  if (commandExists("pipx")) {
    return { cmd: "pipx", prefix: [] };
  }
  const py = findPythonLauncher();
  if (!py) {
    return null;
  }

  const check = run(py, ["-m", "pipx", "--version"], { silent: true });
  if (!check.ok) {
    return { cmd: py, prefix: ["-m", "pipx"], needsBootstrap: true };
  }
  return { cmd: py, prefix: ["-m", "pipx"] };
}

function parseInstallArgs(argv) {
  let spec = DEFAULT_SPEC;
  const amoArgs = [];

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--from") {
      const next = argv[i + 1];
      if (!next) {
        throw new Error("--from requires a value");
      }
      spec = next;
      i += 1;
      continue;
    }
    if (arg === "--help" || arg === "-h") {
      printUsage();
      process.exit(0);
    }
    amoArgs.push(arg);
    const next = argv[i + 1];
    if (next && !next.startsWith("-") && ["--target", "--user-home", "--amo-home", "--preset", "--embedding-model", "--reranker-model", "--python-command"].includes(arg)) {
      amoArgs.push(next);
      i += 1;
    }
  }

  return { spec, amoArgs };
}

function bootstrapPipx(pyCmd) {
  console.log("[1/4] Installing pipx...");
  const installPipx = run(pyCmd, ["-m", "pip", "install", "--user", "pipx"]);
  if (!installPipx.ok) {
    throw new Error("Failed to install pipx with python -m pip install --user pipx");
  }

  console.log("[2/4] Ensuring pipx path...");
  run(pyCmd, ["-m", "pipx", "ensurepath"]);
}

function runInstall(argv) {
  const { spec, amoArgs } = parseInstallArgs(argv);
  const runner = getPipxRunner();
  if (!runner) {
    throw new Error(
      "Python is not available on PATH. Install Python 3.10+ first, then rerun this command."
    );
  }

  if (runner.needsBootstrap) {
    bootstrapPipx(runner.cmd);
  }

  console.log("[3/4] Installing Agent Memory Orchestrator with pipx...");
  const install = run(runner.cmd, [...runner.prefix, "install", "--force", spec]);
  if (!install.ok) {
    throw new Error("pipx install failed.");
  }

  console.log("[4/4] Configuring local agent integrations...");
  const installConfig = run("amo-cli", ["install", ...amoArgs]);
  if (!installConfig.ok) {
    console.error("`amo-cli install` failed or was cancelled.");
    console.error("You can rerun after opening a new terminal:");
    console.error(`  amo-cli install ${amoArgs.join(" ")}`.trim());
    process.exit(installConfig.status || 1);
  }

  console.log("Install complete.");
  console.log("Next:");
  console.log("  1) Restart Claude/Codex so they reload hooks and MCP config.");
  console.log("  2) Run: amo-cli doctor");
}

function runDoctor() {
  const py = findPythonLauncher();
  const pipxCmd = commandExists("pipx");
  const amoCli = commandExists("amo-cli");
  if (amoCli) {
    const result = run("amo-cli", ["doctor"]);
    process.exit(result.status || 0);
  }
  console.log(JSON.stringify({ python: py || null, pipx_on_path: pipxCmd, amo_cli_on_path: false }, null, 2));
}

function main() {
  const [command, ...args] = process.argv.slice(2);
  if (!command || command === "--help" || command === "-h") {
    printUsage();
    process.exit(0);
  }

  if (command === "doctor") {
    runDoctor();
    process.exit(0);
  }

  if (command === "install") {
    runInstall(args);
    process.exit(0);
  }

  console.error(`Unknown command: ${command}`);
  printUsage();
  process.exit(1);
}

try {
  main();
} catch (error) {
  console.error(`Error: ${error.message}`);
  process.exit(1);
}
