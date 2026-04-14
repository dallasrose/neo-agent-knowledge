from __future__ import annotations

import logging
from functools import lru_cache

from neo.config import settings
from neo.core.api import NeoAPI
from neo.core.sparks import AnthropicSparkLLM, NullSparkLLM, SparkGenerator
from neo.embedding.client import EmbeddingClient
from neo.store import create_store
from neo.store.interface import StoreInterface

logger = logging.getLogger(__name__)

_hierarchy_ensured: bool = False


def _build_spark_generator(store) -> SparkGenerator:
    if settings.llm_configured_for("spark"):
        llm = AnthropicSparkLLM(
            api_key=settings.llm_api_key_for("spark"),
            model=settings.llm_model_for("spark"),
            base_url=settings.llm_base_url_for("spark"),
            provider=settings.llm_provider_for("spark"),
        )
    else:
        llm = NullSparkLLM()
    return SparkGenerator(store, llm=llm)


def _build_relationship_judge():
    from neo.core.relationships import HeuristicRelationshipJudge, LLMRelationshipJudge

    fallback = HeuristicRelationshipJudge()
    if not settings.llm_configured_for("relationship"):
        return fallback
    return LLMRelationshipJudge(
        api_key=settings.llm_api_key_for("relationship"),
        model=settings.llm_model_for("relationship"),
        base_url=settings.llm_base_url_for("relationship"),
        provider=settings.llm_provider_for("relationship"),
        fallback=fallback,
    )


@lru_cache(maxsize=1)
def get_api_singleton() -> NeoAPI:
    store = create_store()
    return NeoAPI(
        store,
        embedding_client=EmbeddingClient(),
        spark_generator=_build_spark_generator(store),
        relationship_judge=_build_relationship_judge(),
    )


async def _migrate_agent_name_if_needed(store: StoreInterface, new_agent: dict, target_name: str) -> dict:
    """If target_name agent was just created (empty) but an old 'default' agent has data,
    delete the empty new agent and rename 'default' to target_name."""
    if target_name == "default":
        return new_agent

    nodes = await store.get_nodes_by_agent(new_agent["id"], limit=1)
    # If new agent already has user data, nothing to migrate.
    target_nodes: list[dict] = []
    if nodes:
        target_nodes = await store.get_nodes_by_agent(new_agent["id"], limit=10)
        has_user_data = any(not (node.get("metadata") or {}).get("system") for node in target_nodes)
        if has_user_data or len(target_nodes) == 10:
            return new_agent

    # Check for legacy "default" agent with data
    default_agent = await store.get_agent_by_name("default")
    if default_agent is None or default_agent["id"] == new_agent["id"]:
        return new_agent

    default_nodes = await store.get_nodes_by_agent(default_agent["id"], limit=1)
    if not default_nodes:
        return new_agent

    # Rename: delete the empty new agent, then rename "default" → target_name
    logger.info("Neo: migrating agent 'default' → '%s'", target_name)
    for node in target_nodes:
        await store.delete_node(node["id"])
    await store.delete_agent(new_agent["id"])
    return await store.update_agent(default_agent["id"], name=target_name)


async def ensure_agent_root_hierarchy(store: StoreInterface, agent: dict) -> str | None:
    """Ensure the canonical Agents → {AgentName} root hierarchy exists, plus a
    'Neo Instructions' sibling node for system/policy content.

    On first run (or if nodes were deleted) this will:
      1. Find-or-create 'Agents' root concept node.
      2. Find-or-create '{AgentName}' concept node as child of Agents.
         Renames any existing node whose title is the old default 'Default'.
      3. Find-or-create 'Neo Instructions' concept node as a sibling of the
         agent root (also a direct child of 'Agents').
         If a 'Neo Usage Policy' node exists anywhere in the graph, it is
         re-parented here and renamed to 'Neo Instructions'.
      4. Migrate every existing orphan node (parent_id IS NULL, not a system root)
         under the agent root.
      5. Persist root IDs in agent.config.

    Returns the agent root node ID (e.g. the 'Atlas' node).
    """
    agent_id = agent["id"]
    agent_name = agent["name"]
    agent_title = agent_name.capitalize()
    config = dict(agent.get("config") or {})

    # ── Fast path ────────────────────────────────────────────────────────────
    cached_root = config.get("root_node_id")
    cached_agents = config.get("agents_root_node_id")
    cached_neo_instructions = config.get("neo_instructions_node_id")
    if cached_root and cached_agents and cached_neo_instructions:
        root_ok = await store.get_node(cached_root)
        agents_ok = await store.get_node(cached_agents)
        neo_ok = await store.get_node(cached_neo_instructions)
        if root_ok and agents_ok and neo_ok:
            # Even on fast path: rename the agent root node if its title doesn't match
            if root_ok["title"].lower() != agent_title.lower():
                logger.info(
                    "Neo: renaming agent root concept '%s' → '%s'",
                    root_ok["title"], agent_title,
                )
                await store.update_node(cached_root, title=agent_title)
            return cached_root

    logger.info("Neo: bootstrapping root node hierarchy for agent '%s'", agent_name)

    all_nodes = await store.get_nodes_by_agent(agent_id, limit=2000)

    def _is_system(n: dict) -> bool:
        return bool((n.get("metadata") or {}).get("system"))

    # ── Step 1: find or create 'Agents' root ─────────────────────────────────
    agents_node = next(
        (n for n in all_nodes if n["title"].lower() == "agents"
         and n["node_type"] == "concept" and _is_system(n)),
        None,
    ) or next(
        (n for n in all_nodes if n["title"].lower() == "agents"
         and n["node_type"] == "concept" and n.get("parent_id") is None),
        None,
    )

    if agents_node is None:
        agents_node = await store.create_node(
            agent_id, "concept", "Agents",
            "Root node representing all agents in this knowledge system.",
            summary="Root node for all agents.",
            confidence=1.0,
            parent_id=None,
            source_id=None,
            spark_id=None,
            embedding=None,
            domain=None,
            metadata={"system": True, "role": "agents_root"},
        )
        logger.info("Neo: created 'Agents' root node %s", agents_node["id"])

    system_ids = {agents_node["id"]}

    # ── Step 2: find or create agent root node ────────────────────────────────
    # Match by title (case-insensitive) — also match "default" as a legacy title
    agent_root = next(
        (n for n in all_nodes
         if n["title"].lower() == agent_title.lower()
         and n["node_type"] == "concept"
         and n.get("parent_id") == agents_node["id"]),
        None,
    ) or next(
        (n for n in all_nodes
         if n["title"].lower() in {agent_title.lower(), "default"}
         and n["node_type"] == "concept"
         and _is_system(n)),
        None,
    ) or next(
        (n for n in all_nodes
         if n["title"].lower() in {agent_title.lower(), "default"}
         and n["node_type"] == "concept"),
        None,
    )

    if agent_root is None:
        agent_root = await store.create_node(
            agent_id, "concept", agent_title,
            f"Root knowledge node for {agent_title}. "
            f"All of {agent_title}'s knowledge is stored under this node.",
            summary=f"Root node for {agent_title}'s knowledge base.",
            confidence=1.0,
            parent_id=agents_node["id"],
            source_id=None,
            spark_id=None,
            embedding=None,
            domain=None,
            metadata={"system": True, "role": "agent_root"},
        )
        logger.info("Neo: created '%s' root node %s", agent_title, agent_root["id"])
    else:
        updates: dict = {}
        # Rename legacy "Default" title
        if agent_root["title"].lower() == "default" and agent_title.lower() != "default":
            updates["title"] = agent_title
            logger.info("Neo: renaming agent root '%s' → '%s'", agent_root["title"], agent_title)
        # Fix wrong parent
        if agent_root.get("parent_id") != agents_node["id"]:
            updates["parent_id"] = agents_node["id"]
            logger.info("Neo: re-parenting '%s' node under Agents", agent_title)
        if updates:
            await store.update_node(agent_root["id"], **updates)
            agent_root = {**agent_root, **updates}

    system_ids.add(agent_root["id"])

    # ── Step 3: find or create 'Neo Instructions' top-level node ─────────────
    # Neo Instructions lives at root level (parent=None), as a sibling of Agents.
    # Look for an existing "Neo Instructions" or legacy "Neo Usage Policy" node.
    neo_instructions_node_id = config.get("neo_instructions_node_id")
    neo_instructions_node = None

    if neo_instructions_node_id:
        neo_instructions_node = await store.get_node(neo_instructions_node_id)

    if neo_instructions_node is None:
        # Try to find by title across all agent nodes
        neo_instructions_node = next(
            (n for n in all_nodes
             if n["title"].lower() in {"neo instructions", "neo usage policy", "how to manage knowledge"}
             and n["node_type"] == "concept"),
            None,
        )

    if neo_instructions_node is None:
        # Create fresh at root level
        neo_instructions_node = await store.create_node(
            agent_id, "concept", "Neo Instructions",
            "System reference node: how to use Neo, maintain the knowledge hierarchy, "
            "and apply node/edge conventions. Do not store research here — use the agent root.",
            summary="Reference: Neo usage rules, hierarchy, node types, edge types.",
            confidence=1.0,
            parent_id=None,
            source_id=None,
            spark_id=None,
            embedding=None,
            domain=None,
            metadata={"system": True, "role": "neo_instructions"},
        )
        logger.info("Neo: created 'Neo Instructions' node %s", neo_instructions_node["id"])
    else:
        # Rename if needed; ensure it lives at root level (parent=None)
        updates = {}
        if neo_instructions_node["title"] != "Neo Instructions":
            updates["title"] = "Neo Instructions"
        if neo_instructions_node.get("parent_id") is not None:
            updates["parent_id"] = None
        if updates:
            await store.update_node(neo_instructions_node["id"], **updates)
            neo_instructions_node = {**neo_instructions_node, **updates}
            logger.info("Neo: updated 'Neo Instructions' node (re-parented/renamed)")

    system_ids.add(neo_instructions_node["id"])

    # ── Step 4: migrate existing orphan nodes ─────────────────────────────────
    if not config.get("hierarchy_migrated"):
        orphans = [
            n for n in all_nodes
            if n.get("parent_id") is None
            and n["id"] not in system_ids
            and not _is_system(n)
        ]
        if orphans:
            logger.info(
                "Neo: migrating %d orphan nodes under '%s' root", len(orphans), agent_title
            )
            for orphan in orphans:
                await store.update_node(orphan["id"], parent_id=agent_root["id"])

    # ── Step 5: persist IDs in agent config ──────────────────────────────────
    new_config = {
        **config,
        "root_node_id": agent_root["id"],
        "agents_root_node_id": agents_node["id"],
        "neo_instructions_node_id": neo_instructions_node["id"],
        "hierarchy_migrated": True,
    }
    await store.update_agent(agent_id, config=new_config)
    return agent_root["id"]


async def ensure_default_agent(api: NeoAPI | None = None) -> dict:
    global _hierarchy_ensured
    runtime_api = api or get_api_singleton()
    agent = await runtime_api.store.get_or_create_agent(settings.agent_name)
    # Migrate data from old "default" agent if agent_name was changed
    if settings.agent_name != "default":
        agent = await _migrate_agent_name_if_needed(runtime_api.store, agent, settings.agent_name)
    if not _hierarchy_ensured:
        await ensure_agent_root_hierarchy(runtime_api.store, agent)
        _hierarchy_ensured = True
        agent = await runtime_api.store.get_agent(agent["id"])
    return agent


def reset_runtime_singletons() -> None:
    global _hierarchy_ensured
    _hierarchy_ensured = False
    get_api_singleton.cache_clear()
