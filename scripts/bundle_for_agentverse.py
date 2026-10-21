#!/usr/bin/env python3
"""
Agentverse Bundler Script for AgentForge.
Inlines pure-Python core/ modules into a single, self-contained Python file per agent.
Ensures zero relative imports and strict adherence to Agentverse hosted agent allowed packages.
Removes agent.run() execution blocks for hosted serverless compatibility.
"""

import os
import re
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE_DIR = os.path.join(BASE_DIR, "core")
AGENTS_DIR = os.path.join(BASE_DIR, "agents")
DIST_DIR = os.path.join(BASE_DIR, "dist")

CORE_FILES_ORDER = [
    "catalog.py",
    "orders.py",
    "store.py",
    "qa.py",
    "cards.py",
    "payments.py",
]


def clean_module_code(code: str, remove_internal_imports: bool = True) -> str:
    """Strip internal package imports, future imports, and __main__ execution blocks."""
    lines = code.split("\n")
    cleaned_lines = []

    skip_main = False
    for line in lines:
        # Strip duplicate __future__ imports
        if re.match(r"^\s*from\s+__future__\s+import\s+annotations", line):
            continue

        # Stop at if __name__ == '__main__':
        if re.match(r"^\s*if\s+__name__\s*==\s*['\"]__main__['\"]\s*:", line):
            skip_main = True
            continue
        if skip_main:
            # Skip indented lines inside if __name__ == '__main__' block
            if line.startswith("    ") or line.strip() == "":
                continue
            else:
                skip_main = False

        # Filter out internal package imports
        if remove_internal_imports:
            if re.match(r"^\s*from\s+(?:core|\.|\.\.agents)\b", line):
                continue
            if re.match(r"^\s*from\s+\.messages\b", line):
                continue
            if re.match(r"^\s*from\s+\.extractor_agent\b", line):
                continue
            if re.match(r"^\s*from\s+\.storefront_agent\b", line):
                continue

        cleaned_lines.append(line)

    return "\n".join(cleaned_lines)


def read_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def build_bundle(agent_name: str, agent_file: str, output_file: str) -> None:
    """Combine core modules and agent logic into single standalone hosted agent file."""
    print(f"Bundling {agent_name} -> {output_file}...")

    header = f'''from __future__ import annotations
"""
AgentForge: {agent_name} (Agentverse Hosted Agent Bundle)
Generated automatically by scripts/bundle_for_agentverse.py
Single-file self-contained bundle with zero multi-file imports.
"""
'''
    # 1. Collect and clean core modules
    core_parts = []
    for cf in CORE_FILES_ORDER:
        cf_path = os.path.join(CORE_DIR, cf)
        if os.path.exists(cf_path):
            raw = read_file(cf_path)
            cleaned = clean_module_code(raw, remove_internal_imports=True)
            core_parts.append(f"# ==================== MODULE: core/{cf} ====================\n" + cleaned)

    # 2. Add messages model
    messages_path = os.path.join(AGENTS_DIR, "messages.py")
    if os.path.exists(messages_path):
        raw_msg = read_file(messages_path)
        core_parts.append("# ==================== MODULE: agents/messages.py ====================\n" + clean_module_code(raw_msg))

    # 3. Add agent logic
    raw_agent = read_file(agent_file)
    cleaned_agent = clean_module_code(raw_agent, remove_internal_imports=True)

    # If this is builder or storefront, ensure creation helper assigns agent globally for Agentverse
    global_init_block = f"""
# ==================== AGENT INITIALIZATION (Agentverse Hosted) ====================
# Agentverse executes the module and manages the event loop lifecycle natively.
# Notice: No agent.run() is called.
"""
    if "storefront" in agent_name.lower():
        global_init_block += "\nstorefront_agent = create_storefront_agent()\nagent = storefront_agent\n"
    elif "builder" in agent_name.lower():
        global_init_block += "\nbuilder_agent = create_builder_agent()\nagent = builder_agent\n"
    elif "extractor" in agent_name.lower():
        global_init_block += "\nextractor_agent = create_extractor_agent()\nagent = extractor_agent\n"

    full_bundle = (
        header
        + "\n\n"
        + "\n\n".join(core_parts)
        + "\n\n# ==================== AGENT IMPLEMENTATION ====================\n\n"
        + cleaned_agent
        + "\n\n"
        + global_init_block
    )

    os.makedirs(DIST_DIR, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(full_bundle)

    print(f"Bundle written ({len(full_bundle.encode('utf-8'))} bytes)")


def main():
    print("Starting Agentverse standalone bundling process...")
    agents = [
        ("Storefront Hub Agent", os.path.join(AGENTS_DIR, "storefront_agent.py"), os.path.join(DIST_DIR, "storefront_hosted.py")),
        ("Extractor Agent", os.path.join(AGENTS_DIR, "extractor_agent.py"), os.path.join(DIST_DIR, "extractor_hosted.py")),
        ("Builder Orchestrator Agent", os.path.join(AGENTS_DIR, "builder_agent.py"), os.path.join(DIST_DIR, "builder_hosted.py")),
    ]

    for name, src, dst in agents:
        build_bundle(name, src, dst)

    print("\nAll agents successfully bundled for Agentverse!")


if __name__ == "__main__":
    main()
