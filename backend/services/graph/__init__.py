__all__ = ["plugin"]


def __getattr__(name: str):
    if name == "plugin":
        from backend.services.graph.plugin import plugin as _plugin

        globals()["plugin"] = _plugin
        return _plugin
    raise AttributeError(name)