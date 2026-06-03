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
const PYTHON_MIN_MINOR = 10;
const PYTHON_MAX_MINOR = 13;
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
  npx -y agent-memory-orchestrator-cli -- install [--from <pip_spec>] [wrapper flags] [amo install flags]
  npx -y agent-memory-orchestrator-cli -- doctor [--target codex|claude|all]
  npx agent-memory-orchestrator-cli --help

Wrapper flags:
  --from <pip_spec>     Install AMO from a custom pip spec.
  --pipx-python <path>  Override the Python interpreter used for the pipx AMO env.
  --with-models         Inject sentence-transformers and faiss-cpu into the pipx app.
  --with-slack          Inject websocket-client into the pipx app.
  --with-all-extras     Enable all optional runtime extras.

Examples:
  npx -y agent-memory-orchestrator-cli -- install --target codex --preset cpu-balanced --qwen-model qwen3:1.7b
  npx -y agent-memory-orchestrator-cli -- install --with-models --download-models --target all
  npx -y agent-memory-orchestrator-cli -- install --with-slack --target claude
  npx -y agent-memory-orchestrator-cli -- doctor --target codex
  `);
}

function run(cmd, args, options = {}) {
  const result = spawnSync(cmd, args, {
    stdio: options.silent ? "pipe" : "inherit",
    encoding: "utf-8",
    shell: false,
    env: { ...process.env, ...(options.env || {}) },
  });

  if (result.error) {
    return { ok: false, error: result.error.message, status: result.status ?? 1 };
  }
  return { ok: result.status === 0, status: result.status ?? 0, stdout: result.stdout, stderr: result.stderr };
}

function outputTail(text, maxChars = 4000) {
  const value = String(text || "").trim();
  if (!value) {
    return "";
  }
  return value.length > maxChars ? value.slice(value.length - maxChars) : value;
}

function runQuietOrThrow(cmd, args, message, options = {}) {
  const result = run(cmd, args, { ...options, silent: true });
  if (result.ok) {
    return result;
  }
  const detail = outputTail(`${result.stdout || ""}\n${result.stderr || ""}`);
  throw new Error(detail ? `${message}\n${detail}` : message);
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

function pythonProbeScript() {
  return [
    "import json, sys",
    "print(json.dumps({'major': sys.version_info[0], 'minor': sys.version_info[1], 'executable': sys.executable}))",
  ].join("; ");
}

function probePython(cmd, args = []) {
  const label = [cmd, ...args].join(" ");
  const result = run(cmd, [...args, "-c", pythonProbeScript()], { silent: true });
  if (!result.ok) {
    return { ok: false, label, error: outputTail(`${result.stdout || ""}\n${result.stderr || ""}`, 500) };
  }
  try {
    const parsed = JSON.parse((result.stdout || "").trim());
    return {
      ok: true,
      label,
      cmd,
      args,
      major: Number(parsed.major),
      minor: Number(parsed.minor),
      version: `${parsed.major}.${parsed.minor}`,
      executable: String(parsed.executable || cmd),
    };
  } catch (error) {
    return { ok: false, label, error: `Could not parse Python version output: ${error.message}` };
  }
}

function isSupportedPython(info) {
  return info && info.major === 3 && info.minor >= PYTHON_MIN_MINOR && info.minor <= PYTHON_MAX_MINOR;
}

function pythonUnsupportedHint(checked) {
  const seen = checked.length
    ? ` Checked: ${checked.map((item) => `${item.label}${item.version ? `=${item.version}` : ""}`).join(", ")}.`
    : "";
  return (
    `AMO currently needs Python 3.${PYTHON_MIN_MINOR}-3.${PYTHON_MAX_MINOR} for installation because Kuzu wheels ` +
    "are not available for every newer Python/platform combination yet. Install Python 3.13, 3.12, 3.11, or 3.10 " +
    "and rerun the same npx command." +
    seen
  );
}

function addPythonCandidate(candidates, seen, cmd, args = []) {
  const key = [cmd, ...args].join("\u0000");
  if (seen.has(key)) {
    return;
  }
  seen.add(key);
  candidates.push({ cmd, args });
}

function pythonCandidates() {
  const candidates = [];
  const seen = new Set();

  const preferred = [process.env.AMO_PIPX_PYTHON, process.env.PIPX_DEFAULT_PYTHON].filter(Boolean);
  for (const value of preferred) {
    addPythonCandidate(candidates, seen, value, []);
  }

  if (process.platform === "win32") {
    for (const minor of [13, 12, 11, 10]) {
      addPythonCandidate(candidates, seen, "py", [`-3.${minor}`]);
    }
    for (const minor of [13, 12, 11, 10]) {
      addPythonCandidate(candidates, seen, `python3.${minor}`, []);
    }
    addPythonCandidate(candidates, seen, "python", []);
    addPythonCandidate(candidates, seen, "python3", []);
  } else {
    for (const minor of [13, 12, 11, 10]) {
      addPythonCandidate(candidates, seen, `python3.${minor}`, []);
    }
    addPythonCandidate(candidates, seen, "python3", []);
    addPythonCandidate(candidates, seen, "python", []);
  }

  return candidates;
}

function selectInstallPython(explicitPython) {
  const checked = [];
  if (explicitPython) {
    const info = probePython(explicitPython);
    checked.push(info);
    if (info.ok && isSupportedPython(info)) {
      return info;
    }
    throw new Error(`--pipx-python must point to Python 3.${PYTHON_MIN_MINOR}-3.${PYTHON_MAX_MINOR}. ${pythonUnsupportedHint(checked)}`);
  }

  for (const candidate of pythonCandidates()) {
    const info = probePython(candidate.cmd, candidate.args);
    if (!info.ok) {
      continue;
    }
    checked.push(info);
    if (isSupportedPython(info)) {
      return info;
    }
  }

  throw new Error(pythonUnsupportedHint(checked));
}

function getPipxRunner(options = {}) {
  const allowBootstrap = options.allowBootstrap !== false;
  if (commandExists("pipx")) {
    return { cmd: "pipx", prefix: [] };
  }
  const py = options.bootstrapPython || findPythonLauncher();
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
  let pipxPython = null;
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
    if (arg === "--pipx-python") {
      const next = argv[i + 1];
      if (!next) {
        throw new Error("--pipx-python requires a value");
      }
      pipxPython = next;
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

  return { spec, pipxPython, amoArgs, extras };
}

function bootstrapPipx(pyCmd) {
  console.log("[1/5] Installing pipx...");
  runQuietOrThrow(
    pyCmd,
    ["-m", "pip", "install", "--disable-pip-version-check", "--user", "pipx"],
    "Failed to install pipx with python -m pip install --user pipx.",
    { env: { PIP_DISABLE_PIP_VERSION_CHECK: "1" } }
  );

  console.log("[2/5] Ensuring pipx app path...");
  runQuietOrThrow(pyCmd, ["-m", "pipx", "ensurepath"], "Failed to update the pipx app path.");
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
  runQuietOrThrow(
    runner.cmd,
    pipxArgs(runner, ["inject", PIPX_PACKAGE, ...packages]),
    "pipx inject failed for optional AMO runtime packages."
  );
}

function pipxEnvValue(runner, name) {
  const result = run(runner.cmd, pipxArgs(runner, ["environment", "--value", name]), { silent: true });
  if (!result.ok) {
    return null;
  }
  const value = (result.stdout || "").trim();
  return value || null;
}

function pipxBinDirs(runner, packageName = PIPX_PACKAGE) {
  const dirs = [];
  const envDir = pipxEnvValue(runner, "PIPX_BIN_DIR");
  if (envDir) {
    dirs.push(envDir);
  }

  const scriptsDir = process.platform === "win32" ? "Scripts" : "bin";
  const pipxHome = pipxEnvValue(runner, "PIPX_HOME");
  if (pipxHome) {
    dirs.push(path.join(pipxHome, "venvs", packageName, scriptsDir));
  }

  dirs.push(path.join(os.homedir(), ".local", "bin"));
  dirs.push(path.join(os.homedir(), "pipx", "venvs", packageName, scriptsDir));
  dirs.push(path.join(os.homedir(), ".local", "pipx", "venvs", packageName, scriptsDir));
  if (process.platform === "win32") {
    const appData = process.env.APPDATA;
    const localAppData = process.env.LOCALAPPDATA;
    if (appData) {
      dirs.push(path.join(appData, "Python", "Scripts"));
    }
    if (localAppData) {
      dirs.push(path.join(localAppData, "pipx", "pipx", "venvs", packageName, scriptsDir));
      dirs.push(path.join(localAppData, "Programs", "Python", "Python313", "Scripts"));
      dirs.push(path.join(localAppData, "Programs", "Python", "Python312", "Scripts"));
      dirs.push(path.join(localAppData, "Programs", "Python", "Python311", "Scripts"));
      dirs.push(path.join(localAppData, "Programs", "Python", "Python310", "Scripts"));
    }
  }
  return [...new Set(dirs)];
}

function executableName(appName) {
  return process.platform === "win32" ? `${appName}.exe` : appName;
}

function validateInstalledApp(candidate) {
  const result = run(candidate, ["--help"], { silent: true });
  if (result.ok) {
    return { ok: true };
  }
  const detail = outputTail(`${result.stdout || ""}\n${result.stderr || ""}`);
  const staleNamespace = detail.includes("agent_memory_orchestrator.app.");
  return { ok: false, staleNamespace, detail };
}

function resolveInstalledApp(runner, appName) {
  const candidates = [];
  for (const dir of pipxBinDirs(runner, PIPX_PACKAGE)) {
    candidates.push(path.join(dir, executableName(appName)));
  }
  if (commandExists(appName)) {
    candidates.push(appName);
  }

  const checked = [];
  for (const candidate of [...new Set(candidates)]) {
    if (candidate !== appName && !fs.existsSync(candidate)) {
      continue;
    }
    const validation = validateInstalledApp(candidate);
    if (validation.ok) {
      return candidate;
    }
    checked.push({ candidate, ...validation });
    if (!validation.staleNamespace && candidate !== appName) {
      const detail = validation.detail ? `\n${validation.detail}` : "";
      throw new Error(`Installed ${appName} exists but failed its startup check: ${candidate}${detail}`);
    }
  }

  const stale = checked.find((item) => item.staleNamespace);
  if (stale) {
    throw new Error(
      `Found a stale ${appName} entrypoint that still imports agent_memory_orchestrator.app.*: ${stale.candidate}\n` +
        "Remove the old pip-installed AMO package or rerun the npx installer after opening a fresh terminal."
    );
  }

  return null;
}

function runInstall(argv) {
  const { spec, pipxPython, amoArgs, extras } = parseInstallArgs(argv);
  const installPython = selectInstallPython(pipxPython);
  const runner = getPipxRunner({ allowBootstrap: true, bootstrapPython: installPython.executable });
  if (!runner) {
    throw new Error(
      `Python 3.${PYTHON_MIN_MINOR}-3.${PYTHON_MAX_MINOR} is not available. Install a supported Python first, then rerun this command.`
    );
  }

  if (runner.needsBootstrap) {
    bootstrapPipx(runner.cmd);
  }

  console.log(
    `[3/5] Installing Agent Memory Orchestrator with pipx using Python ${installPython.version} (${installPython.executable})...`
  );
  runQuietOrThrow(
    runner.cmd,
    pipxArgs(runner, ["install", "--force", "--python", installPython.executable, spec]),
    "pipx install failed."
  );

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

  console.log("Installer finished.");
}

function runDoctor(args) {
  const py = findPythonLauncher();
  let installPython = null;
  try {
    installPython = selectInstallPython(null);
  } catch (_error) {
    installPython = null;
  }
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
        compatible_install_python: installPython
          ? { version: installPython.version, executable: installPython.executable }
          : null,
        pipx_available: Boolean(runner),
        amo_cli_available: false,
        hint: "Run `npx -y agent-memory-orchestrator-cli -- install --target codex --preset cpu-balanced --qwen-model qwen3:1.7b`.",
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
