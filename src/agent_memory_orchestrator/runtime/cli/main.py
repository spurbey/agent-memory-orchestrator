from __future__ import annotations

import argparse
import json
import sys

from ...infrastructure.kuzu import GraphBackendUnavailable
from ...infrastructure.llm import QwenUnavailable
from ..daemon.client import DaemonUnavailable
from .commands.antelligent import add_antelligent_subcommands as _add_antelligent_subcommands
from .commands.antelligent import handle_antelligent_command as _handle_antelligent_command
from .commands.bootstrap import add_bootstrap_subcommands as _add_bootstrap_subcommands
from .commands.bootstrap import handle_bootstrap_command as _handle_bootstrap_command
from .commands.debug import add_debug_subcommands as _add_debug_subcommands
from .commands.debug import handle_debug_command as _handle_debug_command
from .commands.connectors import add_connector_subcommands as _add_connector_subcommands
from .commands.connectors import handle_connector_command as _handle_connector_command
from .commands.graph import _retrieve_index_only as _graph_retrieve_index_only
from .commands.graph import add_graph_subcommands as _add_graph_subcommands
from .commands.graph import handle_graph_command as _handle_graph_command
from .commands.install import add_install_subcommands as _add_install_subcommands
from .commands.install import handle_install_command as _handle_install_command
from .commands.memory import add_memory_subcommands as _add_memory_subcommands
from .commands.memory import handle_memory_command as _handle_memory_command
from .commands.memory import rebuild_clean_db
from .commands.models import add_models_subcommands as _add_models_subcommands
from .commands.models import handle_models_command as _handle_models_command
from .commands.orchestration import add_orchestration_subcommands as _add_orchestration_subcommands
from .commands.orchestration import handle_orchestration_command as _handle_orchestration_command
from .commands.pipeline import add_pipeline_subcommands as _add_pipeline_subcommands
from .commands.pipeline import handle_pipeline_command as _handle_pipeline_command
from .commands.peer import add_peer_subcommands as _add_peer_subcommands
from .commands.peer import handle_peer_command as _handle_peer_command
from .commands.skill_checkpoint import add_skill_checkpoint_subcommands as _add_skill_checkpoint_subcommands
from .commands.skill_checkpoint import handle_skill_checkpoint_command as _handle_skill_checkpoint_command

_rebuild_clean_db = rebuild_clean_db
_retrieve_index_only = _graph_retrieve_index_only


def _print(payload: object) -> None:
    print(json.dumps(payload, indent=2))


def _print_line(payload: object) -> None:
    print(json.dumps(payload), flush=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Agent Memory Orchestrator CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    _add_bootstrap_subcommands(sub)
    _add_pipeline_subcommands(sub)

    _add_install_subcommands(sub)

    _add_memory_subcommands(sub)

    _add_graph_subcommands(sub)

    _add_connector_subcommands(sub)

    _add_peer_subcommands(sub)

    _add_debug_subcommands(sub)

    _add_skill_checkpoint_subcommands(sub)

    _add_models_subcommands(sub)

    _add_orchestration_subcommands(sub)

    _add_antelligent_subcommands(sub)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        install_status = _handle_install_command(args, emit=_print, emit_text=print)
        if install_status is not None:
            return install_status

        bootstrap_status = _handle_bootstrap_command(args, emit=_print)
        if bootstrap_status is not None:
            return bootstrap_status

        pipeline_status = _handle_pipeline_command(args, emit=_print)
        if pipeline_status is not None:
            return pipeline_status

        models_status = _handle_models_command(args, emit=_print)
        if models_status is not None:
            return models_status

        connector_status = _handle_connector_command(args, emit=_print)
        if connector_status is not None:
            return connector_status

        peer_status = _handle_peer_command(args, emit=_print, emit_line=_print_line)
        if peer_status is not None:
            return peer_status

        memory_status = _handle_memory_command(args, emit=_print, emit_text=print)
        if memory_status is not None:
            return memory_status

        graph_status = _handle_graph_command(args, emit=_print)
        if graph_status is not None:
            return graph_status

        debug_status = _handle_debug_command(args, emit=_print)
        if debug_status is not None:
            return debug_status

        skill_checkpoint_status = _handle_skill_checkpoint_command(args, emit=_print)
        if skill_checkpoint_status is not None:
            return skill_checkpoint_status

        orchestration_status = _handle_orchestration_command(args, emit=_print)
        if orchestration_status is not None:
            return orchestration_status

        antelligent_status = _handle_antelligent_command(args, emit=_print)
        if antelligent_status is not None:
            return antelligent_status

        parser.error(f"unknown command: {args.command}")
        return 2
    except (DaemonUnavailable, GraphBackendUnavailable, QwenUnavailable) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
