"""Pipeline step modules — faithful Python ports of the bash orchestrators.

Each module exposes ``run(args) -> int`` (0 = success). The dispatcher
(:mod:`prmonitor.__main__`) handles arg parsing and venv bootstrap; these
modules own the pipeline logic.
"""
