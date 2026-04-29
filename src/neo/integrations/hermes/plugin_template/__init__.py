from neo.integrations.hermes.provider import NeoMemoryProvider


def register(ctx):
    ctx.register_memory_provider(NeoMemoryProvider())
