import asyncio
import logging
import os
import uuid
from typing import Dict

from .plugin import Plugin


class ImageGenerationPlugin(Plugin):
    """
    A plugin to generate images from text prompts using Gemini or OpenAI DALL-E.
    """

    def __init__(self):
        self.provider = os.getenv('IMAGE_GENERATION_PROVIDER', 'gemini').lower()
        self.gemini_api_key = os.getenv('GEMINI_API_KEY', '')
        self.openai_api_key = os.getenv('OPENAI_IMAGE_API_KEY', os.getenv('OPENAI_API_KEY', ''))
        self._gemini_client = None
        self._openai_client = None

    def get_source_name(self) -> str:
        return "Image Generation"

    def get_spec(self) -> [Dict]:
        return [{
            "name": "generate_image",
            "description": "Generate an image from a text prompt. Use this when the user asks to create, "
                           "draw, generate, paint, or make an image, picture, illustration, or artwork.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "A detailed text description of the image to generate"
                    }
                },
                "required": ["prompt"]
            }
        }]

    async def execute(self, function_name, helper, **kwargs) -> Dict:
        prompt = kwargs.get('prompt', '')
        if not prompt:
            return {"result": "No prompt provided for image generation."}

        if self.provider == 'openai':
            return await self._generate_openai(prompt)
        else:
            return await self._generate_gemini(prompt)

    async def _generate_gemini(self, prompt: str) -> Dict:
        try:
            loop = asyncio.get_event_loop()
            path = await loop.run_in_executor(None, self._sync_gemini_generate, prompt)
            return {
                'direct_result': {
                    'kind': 'photo',
                    'format': 'path',
                    'value': path
                }
            }
        except Exception as e:
            logging.warning(f'Gemini image generation failed: {e}')
            return {"result": f"Image generation failed: {e}"}

    def _sync_gemini_generate(self, prompt: str) -> str:
        from google import genai
        from google.genai import types

        if self._gemini_client is None:
            self._gemini_client = genai.Client(api_key=self.gemini_api_key)

        response = self._gemini_client.models.generate_content(
            model='gemini-2.5-flash-image',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
            ),
        )

        for part in response.parts:
            if part.inline_data:
                image = part.as_image()
                path = f'/tmp/img_gen_{uuid.uuid4().hex}.png'
                image.save(path)
                return path

        raise RuntimeError("Gemini returned no image data")

    async def _generate_openai(self, prompt: str) -> Dict:
        try:
            from openai import OpenAI

            if self._openai_client is None:
                self._openai_client = OpenAI(api_key=self.openai_api_key)

            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self._openai_client.images.generate(
                    model="dall-e-3",
                    prompt=prompt,
                    size="1024x1024",
                    n=1,
                )
            )
            image_url = response.data[0].url
            return {
                'direct_result': {
                    'kind': 'photo',
                    'format': 'url',
                    'value': image_url
                }
            }
        except Exception as e:
            logging.warning(f'OpenAI image generation failed: {e}')
            return {"result": f"Image generation failed: {e}"}
