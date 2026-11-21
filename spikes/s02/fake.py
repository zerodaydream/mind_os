from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
class FakeToolModel(GenericFakeChatModel):
    """Fake chat model that tolerates bind_tools."""
    def bind_tools(self, tools, **kw):
        self._bound = [getattr(t, "name", getattr(t, "__name__", str(t))) for t in tools]
        return self
