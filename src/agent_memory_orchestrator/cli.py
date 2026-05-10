from __future__ import annotations

from .app import cli as _impl

for _name in dir(_impl):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_impl, _name)


def main(argv: list[str] | None = None) -> int:
    # Preserve old monkeypatch/import behavior for callers that patch this module.
    for _name in (
        "download_models",
        "list_model_presets",
        "model_status",
        "preflight_models",
    ):
        if _name in globals():
            setattr(_impl, _name, globals()[_name])
    return _impl.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
