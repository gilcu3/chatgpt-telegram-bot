from abc import abstractmethod, ABC
from typing import Dict


class Plugin(ABC):
    """
    A plugin interface which can be used to create plugins for the Claude API.
    """

    @abstractmethod
    def get_source_name(self) -> str:
        """
        Return the name of the source of the plugin.
        """
        pass

    @abstractmethod
    def get_spec(self) -> [Dict]:
        """
        Tool specs in the form of JSON schema as specified in the Anthropic documentation:
        https://docs.anthropic.com/en/docs/build-with-claude/tool-use
        """
        pass

    @abstractmethod
    async def execute(self, function_name, helper, **kwargs) -> Dict:
        """
        Execute the plugin and return a JSON serializable response
        """
        pass
