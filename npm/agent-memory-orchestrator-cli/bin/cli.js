#!/usr/bin/env node

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const DEFAULT_SPEC =
  process.env.AMO_PIP_SPEC ||
  "git+https://github.com/spurbey/agent-memory-orchestrator.git";
const PIPX_PACKAGE = process.env.AMO_PIPX_PACKAGE || "agent-memory-orchestrator";
const MODEL_PACKAGES = ["sentence-transformers", "faiss-cpu"];
const SLACK_PACKAGES = ["websocket-client"];
const VALUE_FLAGS = new Set([
  "--target",
  "--user-home",
  "--amo-home",
  "--preset",
  "--embedding-model",
  "--reranker-model",
  "--qwen-model",
  "--python-command",
]);

function printUsage() {
  console.log(`
Agent Memory Orchestrator installer

Usage:
  npx agent-memory-orchestrator-cli -- install [--from <pip_spec>] [wrapper flags] [amo install flags]
  npx agent-memory-orchestrator-cli -- doctor [--target codex|claude|all]
  npx agent-memory-orchestrator-cli --help

Wrapper flags:
  --from <pip_spec>     Install AMO from a custom pip spec.
  --with-models         Inject sentence-transformers and faiss-cpu into the pipx app.
  --with-slack          Inject websocket-client into the pipx app.
  --with-all-extras     Enable all optional runtime extras.

Examples:
  npx agent-memory-orchestrator-cli -- install --target codex --preset cpu-balanced --qwen-model qwen3.5:9b
  npx agent-memory-orchestrator-cli -- install --with-models --download-models --target all
  npx agent-memory-orchestrator-cli -- install --with-slack --target claude
  npx agent-memory-orchestrator-cli -- doctor --target codex
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

function getPipxRunner(options = {}) {
  const allowBootstrap = options.allowBootstrap !== false;
  if (commandExists("pipx")) {
    return { cmd: "pipx", prefix: [] };
  }
  const py = findPythonLauncher();
  if (!py) {
    return null;
  }

  const check = run(py, ["-m", "pipx", "--version"], { silent: true });
  if (!check.ok) {
    return allowBootstrap ? { cmd: py, prefix: ["-m", "pipx"], needsBootstrap: true } : null;
  }
  return { cmd: py, prefix: ["-m", "pipx"] };
}

function pipxArgs(runner, args) {
  return [...runner.prefix, ...args];
}

function parseInstallArgs(argv) {
  let spec = DEFAULT_SPEC;
  const amoArgs = [];
  const extras = new Set();

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
    if (arg === "--with-models") {
      extras.add("models");
      continue;
    }
    if (arg === "--with-slack") {
      extras.add("slack");
      continue;
    }
    if (arg === "--with-all-extras") {
      extras.add("models");
      extras.add("slack");
      continue;
    }
    if (arg === "--help" || arg === "-h") {
      printUsage();
      process.exit(0);
    }
    amoArgs.push(arg);
    const next = argv[i + 1];
    if (next && !next.startsWith("-") && VALUE_FLAGS.has(arg)) {
      amoArgs.push(next);
      i += 1;
    }
  }

  if (amoArgs.includes("--download-models")) {
    extras.add("models");
  }

  return { spec, amoArgs, extras };
}

function bootstrapPipx(pyCmd) {
  console.log("[1/5] Installing pipx...");
  const installPipx = run(pyCmd, ["-m", "pip", "install", "--user", "pipx"]);
  if (!installPipx.ok) {
    throw new Error("Failed to install pipx with python -m pip install --user pipx");
  }

  console.log("[2/5] Ensuring pipx app path...");
  run(pyCmd, ["-m", "pipx", "ensurepath"]);
}

function injectOptionalPackages(runner, extras) {
  const packages = [];
  if (extras.has("models")) {
    packages.push(...MODEL_PACKAGES);
  }
  if (extras.has("slack")) {
    packages.push(...SLACK_PACKAGES);
  }
  if (packages.length === 0) {
    return;
  }

  console.log(`[4/5] Installing optional runtime packages: ${packages.join(", ")}...`);
  const result = run(runner.cmd, pipxArgs(runner, ["inject", PIPX_PACKAGE, ...packages]));
  if (!result.ok) {
    throw new Error("pipx inject failed for optional AMO runtime packages.");
  }
}

function pipxBinDirs(runner) {
  const dirs = [];
  const envResult = run(runner.cmd, pipxArgs(runner, ["environment", "--value", "PIPX_BIN_DIR"]), { silent: true });
  const envDir = (envResult.stdout || "").trim();
  if (envResult.ok && envDir) {
    dirs.push(envDir);
  }

  dirs.push(path.join(os.homedir(), ".local", "bin"));
  if (process.platform === "win32") {
    const appData = process.env.APPDATA;
    if (appData) {
      dirs.push(path.join(appData, "Python", "Scripts"));
    }
  }
  return [...new Set(dirs)];
}

function executableName(appName) {
  return process.platform === "win32" ? `${appName}.exe` : appName;
}

function resolveInstalledApp(runner, appName) {
  if (commandExists(appName)) {
    return appName;
  }
  for (const dir of pipxBinDirs(runner)) {
    const candidate = path.join(dir, executableName(appName));
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }
  return null;
}

function runInstall(argv) {
  const { spec, amoArgs, extras } = parseInstallArgs(argv);
  const runner = getPipxRunner({ allowBootstrap: true });
  if (!runner) {
    throw new Error(
      "Python is not available on PATH. Install Python 3.10+ first, then rerun this command."
    );
  }

  if (runner.needsBootstrap) {
    bootstrapPipx(runner.cmd);
  }

  console.log("[3/5] Installing Agent Memory Orchestrator with pipx...");
  const install = run(runner.cmd, pipxArgs(runner, ["install", "--force", spec]));
  if (!install.ok) {
    throw new Error("pipx install failed.");
  }

  injectOptionalPackages(runner, extras);

  console.log("[5/5] Configuring local agent integrations...");
  const amoCli = resolveInstalledApp(runner, "amo-cli");
  if (!amoCli) {
    throw new Error(
      "Installed amo-cli was not found in the pipx app path. Run `pipx ensurepath`, open a new terminal, then run `amo-cli install`."
    );
  }
  const installConfig = run(amoCli, ["install", ...amoArgs]);
  if (!installConfig.ok) {
    console.error("`amo-cli install` failed or was cancelled.");
    console.error("You can rerun after opening a new terminal:");
    console.error(`  amo-cli install ${amoArgs.join(" ")}`.trim());
    process.exit(installConfig.status || 1);
  }

  console.log("Install complete.");
  console.log("Next:");
  console.log("  1) Restart Claude/Codex so they reload hooks and MCP config.");
  console.log(`  2) Run: ${amoCli} doctor --target codex`);
  console.log("  3) Run: amo-daemon");
}

function runDoctor(args) {
  const py = findPythonLauncher();
  const runner = getPipxRunner({ allowBootstrap: false });
  const amoCli = runner ? resolveInstalledApp(runner, "amo-cli") : commandExists("amo-cli") ? "amo-cli" : null;
  if (amoCli) {
    const result = run(amoCli, ["doctor", ...args]);
    process.exit(result.status || 0);
  }
  console.log(
    JSON.stringify(
      {
        python: py || null,
        pipx_available: Boolean(runner),
        amo_cli_available: false,
        hint: "Run `npx agent-memory-orchestrator-cli -- install --target codex --preset cpu-balanced --qwen-model qwen3.5:9b`.",
      },
      null,
      2
    )
  );
}

function main() {
  const argv = process.argv.slice(2);
  if (argv[0] === "--") {
    argv.shift();
  }
  const [command, ...args] = argv;
  if (!command || command === "--help" || command === "-h") {
    printUsage();
    process.exit(0);
  }

  if (command === "doctor") {
    runDoctor(args);
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
