import json

from plugins.gtts_text_to_speech import GTTSTextToSpeech
from plugins.dice import DicePlugin
from plugins.youtube_audio_extractor import YouTubeAudioExtractorPlugin
from plugins.ddg_image_search import DDGImageSearchPlugin
from plugins.spotify import SpotifyPlugin
from plugins.crypto import CryptoPlugin
from plugins.weather import WeatherPlugin
from plugins.ddg_web_search import DDGWebSearchPlugin
from plugins.wolfram_alpha import WolframAlphaPlugin
from plugins.deepl import DeeplTranslatePlugin
from plugins.worldtimeapi import WorldTimeApiPlugin
from plugins.whois_ import WhoisPlugin
from plugins.webshot import WebshotPlugin
from plugins.iplocation import IpLocationPlugin
from plugins.url_content import UrlContentPlugin
from plugins.image_generation import ImageGenerationPlugin
from plugins.explicit_memory_plugin import ExplicitMemoryPlugin
from plugins.scheduler_plugin import SchedulerPlugin


class PluginManager:
    """
    A class to manage the plugins and call the correct functions
    """

    def __init__(self, config, memory_store=None, ollama_client=None, scheduler=None):
        enabled_plugins = config.get('plugins', [])
        plugin_mapping = {
            'wolfram': WolframAlphaPlugin,
            'weather': WeatherPlugin,
            'crypto': CryptoPlugin,
            'ddg_web_search': DDGWebSearchPlugin,
            'ddg_image_search': DDGImageSearchPlugin,
            'spotify': SpotifyPlugin,
            'worldtimeapi': WorldTimeApiPlugin,
            'youtube_audio_extractor': YouTubeAudioExtractorPlugin,
            'dice': DicePlugin,
            'deepl_translate': DeeplTranslatePlugin,
            'gtts_text_to_speech': GTTSTextToSpeech,
            'whois': WhoisPlugin,
            'webshot': WebshotPlugin,
            'iplocation': IpLocationPlugin,
            'url_content': UrlContentPlugin,
            'image_generation': ImageGenerationPlugin,
        }
        self.plugins = [plugin_mapping[plugin]() for plugin in enabled_plugins if plugin in plugin_mapping]

        # Explicit memory plugin is always active when memory store and ollama are provided
        if memory_store is not None and ollama_client is not None:
            self.plugins.append(ExplicitMemoryPlugin(memory_store, ollama_client))

        # Scheduler plugin is always active when scheduler is provided
        if scheduler is not None:
            self.plugins.append(SchedulerPlugin(scheduler))

    def get_functions_specs(self):
        """
        Return the list of function specs that can be called by the model
        """
        return [spec for specs in map(lambda plugin: plugin.get_spec(), self.plugins) for spec in specs]

    async def call_function(self, function_name, helper, arguments, user_id=None, chat_id=None):
        """
        Call a function based on the name and parameters provided
        """
        plugin = self.__get_plugin_by_function_name(function_name)
        if not plugin:
            return json.dumps({'error': f'Function {function_name} not found'})
        parsed_args = json.loads(arguments)
        if user_id is not None:
            parsed_args['_user_id'] = user_id
        if chat_id is not None:
            parsed_args['_chat_id'] = chat_id
        return json.dumps(await plugin.execute(function_name, helper, **parsed_args), default=str)

    def get_plugin_source_name(self, function_name) -> str:
        """
        Return the source name of the plugin
        """
        plugin = self.__get_plugin_by_function_name(function_name)
        if not plugin:
            return ''
        return plugin.get_source_name()

    def __get_plugin_by_function_name(self, function_name):
        return next((plugin for plugin in self.plugins
                    if function_name in map(lambda spec: spec.get('name'), plugin.get_spec())), None)
