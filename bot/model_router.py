import logging
import re

# Per-token prices (per 1K tokens) for cost savings calculation
# These are approximate and should be updated when pricing changes
MODEL_PRICES = {
    "claude-haiku-4-5-20251001": {"input": 0.001, "output": 0.005},
    "claude-sonnet-4-5-20250929": {"input": 0.003, "output": 0.015},
    "claude-opus-4-6": {"input": 0.015, "output": 0.075},
    "claude-opus-4-5-20251124": {"input": 0.015, "output": 0.075},
}

# Complexity signal patterns
SIMPLE_PATTERNS = re.compile(
    r'^(hi|hey|hello|thanks|thank you|ok|okay|yes|no|bye|good morning|good night|'
    r'gm|gn|sup|yo|lol|haha|cool|nice|great|sure|nope|yep|wow)[\s!?.]*$',
    re.IGNORECASE
)

CODE_PATTERNS = re.compile(r'```|`[^`]+`')

COMPLEX_KEYWORDS = re.compile(
    r'\b(algorithm|analyze|analyse|compare|contrast|debug|refactor|architect|'
    r'optimize|implement|design pattern|trade.?off|step by step|in detail|'
    r'comprehensive|thorough|explain why|prove|derive|mathematical|'
    r'security audit|code review|performance|benchmark)\b',
    re.IGNORECASE
)

MULTI_PART_PATTERNS = re.compile(
    r'(\d+[\.\)]\s)|(\band\s+also\b)|(\badditionally\b)|(\bfurthermore\b)|(\bmoreover\b)',
    re.IGNORECASE
)

# Timestamp prefix injected by __add_to_history, e.g. "[2026-03-11 12:36 UTC] "
TIMESTAMP_PREFIX = re.compile(r'^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2} \S+\]\s*')


class ModelRouter:
    """
    Heuristic-based model router that selects the cheapest adequate Claude model
    based on query complexity signals.
    """

    def __init__(self, config: dict):
        self.haiku_model = config.get('routing_haiku_model', 'claude-haiku-4-5-20251001')
        self.sonnet_model = config.get('routing_sonnet_model', 'claude-sonnet-4-5-20250929')
        self.opus_model = config.get('routing_opus_model', 'claude-opus-4-6')

    def route(self, query: str, conversation_length: int = 0) -> tuple[str, str, str]:
        """
        Analyze query complexity and select the optimal model.

        Returns: (model_id, complexity_label, model_emoji)
            - model_id: The Claude model to use
            - complexity_label: Human-readable label ("Haiku", "Sonnet", "Opus")
            - model_emoji: Emoji indicator for the footer
        """
        score = self._calculate_complexity(query, conversation_length)
        logging.info(f'Smart routing: complexity score = {score}')

        if score <= 30:
            return self.haiku_model, "Haiku", "\u26a1"
        elif score <= 65:
            return self.sonnet_model, "Sonnet", "\U0001f4a1"
        else:
            return self.opus_model, "Opus", "\U0001f9e0"

    def _calculate_complexity(self, query: str, conversation_length: int = 0) -> int:
        score = 25  # Start below Sonnet threshold; complexity signals push it up

        # Strip timestamp prefix before analysis so it doesn't affect length/pattern checks
        query = TIMESTAMP_PREFIX.sub('', query)

        # Simple greetings/acknowledgments
        if SIMPLE_PATTERNS.match(query.strip()):
            return 10

        # Message length
        if len(query) > 1000:
            score += 25
        elif len(query) > 500:
            score += 15
        elif len(query) < 50:
            score -= 10

        # Code blocks
        if CODE_PATTERNS.search(query):
            score += 20

        # Complex/technical keywords
        complex_matches = len(COMPLEX_KEYWORDS.findall(query))
        score += min(complex_matches * 10, 25)

        # Multi-part questions
        multi_parts = len(MULTI_PART_PATTERNS.findall(query))
        score += min(multi_parts * 8, 15)

        # Long conversation context
        if conversation_length > 10:
            score += 10
        elif conversation_length > 5:
            score += 5

        # Question mark count (multiple questions = more complex)
        question_marks = query.count('?')
        if question_marks > 2:
            score += 10

        return max(0, min(100, score))

    def calculate_savings(self, model_used: str, input_tokens: int, output_tokens: int) -> float | None:
        """
        Calculate cost savings compared to using Opus.
        Returns savings in dollars, or None if savings can't be calculated.
        """
        opus_model = self.opus_model
        if model_used == opus_model:
            return None

        used_prices = MODEL_PRICES.get(model_used)
        opus_prices = MODEL_PRICES.get(opus_model)

        if not used_prices or not opus_prices:
            return None

        actual_cost = (input_tokens / 1000 * used_prices["input"] +
                       output_tokens / 1000 * used_prices["output"])
        opus_cost = (input_tokens / 1000 * opus_prices["input"] +
                     output_tokens / 1000 * opus_prices["output"])

        return opus_cost - actual_cost
