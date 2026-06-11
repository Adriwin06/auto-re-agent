"""re-agent prompt command — display prompt for a function without sending it."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from re_agent.config.loader import load_config
from re_agent.core.models import FunctionTarget


def cmd_prompt(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config))

    # Lazy imports
    from re_agent.backend.registry import create_backend
    from re_agent.core.session import Session
    from re_agent.agents.reverser import ReverserAgent
    from re_agent.parity.source_indexer import SourceIndexer

    backend = create_backend(config.backend)
    session = Session(config.output.session_file)

    class_name = args.class_name or ""
    function_name = ""

    try:
        dec = backend.decompile(args.address)
        if dec.name and "::" in dec.name:
            derived_class, _, function_name = dec.name.rpartition("::")
            if not class_name:
                class_name = derived_class
        elif dec.name:
            function_name = dec.name
    except Exception:
        pass

    target = FunctionTarget(
        address=args.address,
        class_name=class_name,
        function_name=function_name,
    )

    source_root = Path(config.project_profile.source_root)
    if not source_root.is_absolute():
        source_root = Path(args.config).parent / source_root

    indexer = SourceIndexer(source_root) if source_root.exists() else None

    # Instantiate ReverserAgent with None for LLM since build_prompts doesn't use it
    reverser = ReverserAgent(
        llm=None,  # type: ignore
        backend=backend,
        source_root=source_root,
        project_profile=config.project_profile,
        indexer=indexer,
        session=session,
        report_dir=Path(config.output.report_dir),
    )

    try:
        system_prompt, task_prompt = reverser.build_prompts(target)
    except Exception as exc:
        print(f"Error building prompts: {exc}", file=sys.stderr)
        return 1

    print("=================== SYSTEM PROMPT ===================")
    print(system_prompt)
    print("=====================================================")
    print()
    print("==================== TASK PROMPT ====================")
    print(task_prompt)
    print("=====================================================")

    return 0
