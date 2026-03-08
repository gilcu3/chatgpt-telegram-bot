import fcntl
import os.path
import pathlib
import json
from datetime import date


def year_month(date_str):
    # extract string of year-month from date, eg: '2023-03'
    return str(date_str)[:7]


class UsageTracker:
    """
    UsageTracker class
    Enables tracking of daily/monthly usage per user.
    User files are stored as JSON in /usage_logs directory.
    JSON example:
    {
        "user_name": "@user_name",
        "current_cost": {
            "day": 0.45,
            "month": 3.23,
            "all_time": 3.23,
            "last_update": "2023-03-14"},
        "usage_history": {
            "chat_tokens": {
                "2023-03-13": 520,
                "2023-03-14": 1532
            },
            "vision_tokens": {
                "2023-03-13": 200,
                "2023-03-14": 150
            }
        }
    }
    """

    def __init__(self, user_id, user_name, logs_dir="usage_logs"):
        """
        Initializes UsageTracker for a user with current date.
        Loads usage data from usage log file.
        :param user_id: Telegram ID of the user
        :param user_name: Telegram user name
        :param logs_dir: path to directory of usage logs, defaults to "usage_logs"
        """
        self.user_id = user_id
        self.logs_dir = logs_dir
        # path to usage file of given user
        self.user_file = f"{logs_dir}/{user_id}.json"

        if os.path.isfile(self.user_file):
            with open(self.user_file, "r") as file:
                self.usage = json.load(file)
            if 'vision_tokens' not in self.usage['usage_history']:
                self.usage['usage_history']['vision_tokens'] = {}
        else:
            # ensure directory exists
            pathlib.Path(logs_dir).mkdir(exist_ok=True)
            # create new dictionary for this user
            self.usage = {
                "user_name": user_name,
                "current_cost": {"day": 0.0, "month": 0.0, "all_time": 0.0, "last_update": str(date.today())},
                "usage_history": {"chat_tokens": {}, "vision_tokens": {}}
            }

    def _save(self):
        """Write usage data to user file with file locking for concurrency safety."""
        pathlib.Path(self.logs_dir).mkdir(exist_ok=True)
        with open(self.user_file, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                json.dump(self.usage, f)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    # token usage functions:

    def add_chat_tokens(self, tokens, tokens_price=0.003):
        """Adds used tokens from a request to a users usage history and updates current cost
        :param tokens: total tokens used in last request
        :param tokens_price: price per 1000 tokens, defaults to 0.003
        """
        today = date.today()
        token_cost = round(float(tokens) * tokens_price / 1000, 6)
        self.add_current_costs(token_cost)

        # update usage_history
        if str(today) in self.usage["usage_history"]["chat_tokens"]:
            # add token usage to existing date
            self.usage["usage_history"]["chat_tokens"][str(today)] += tokens
        else:
            # create new entry for current date
            self.usage["usage_history"]["chat_tokens"][str(today)] = tokens

        self._save()

    def get_current_token_usage(self):
        """Get token amounts used for today and this month

        :return: total number of tokens used per day and per month
        """
        today = date.today()
        if str(today) in self.usage["usage_history"]["chat_tokens"]:
            usage_day = self.usage["usage_history"]["chat_tokens"][str(today)]
        else:
            usage_day = 0
        month = str(today)[:7]  # year-month as string
        usage_month = 0
        for today, tokens in self.usage["usage_history"]["chat_tokens"].items():
            if today.startswith(month):
                usage_month += tokens
        return usage_day, usage_month

    # vision usage functions

    def add_vision_tokens(self, tokens, vision_token_price=0.003):
        """
         Adds requested vision tokens to a users usage history and updates current cost.
        :param tokens: total tokens used in last request
        :param vision_token_price: price per 1K tokens, defaults to 0.003
        """
        today = date.today()
        token_price = round(tokens * vision_token_price / 1000, 2)
        self.add_current_costs(token_price)

        # update usage_history
        if str(today) in self.usage["usage_history"]["vision_tokens"]:
            self.usage["usage_history"]["vision_tokens"][str(today)] += tokens
        else:
            # create new entry for current date
            self.usage["usage_history"]["vision_tokens"][str(today)] = tokens

        self._save()

    def get_current_vision_tokens(self):
        """Get vision tokens for today and this month.

        :return: total amount of vision tokens per day and per month
        """
        today = date.today()
        if str(today) in self.usage["usage_history"]["vision_tokens"]:
            tokens_day = self.usage["usage_history"]["vision_tokens"][str(today)]
        else:
            tokens_day = 0
        month = str(today)[:7]  # year-month as string
        tokens_month = 0
        for today, tokens in self.usage["usage_history"]["vision_tokens"].items():
            if today.startswith(month):
                tokens_month += tokens
        return tokens_day, tokens_month

    # general functions

    def add_current_costs(self, request_cost):
        """
        Add current cost to all_time, day and month cost and update last_update date.
        """
        today = date.today()
        last_update = date.fromisoformat(self.usage["current_cost"]["last_update"])

        # add to all_time cost, initialize with calculation of total_cost if key doesn't exist
        self.usage["current_cost"]["all_time"] = \
            self.usage["current_cost"].get("all_time", self.initialize_all_time_cost()) + request_cost
        # add current cost, update new day
        if today == last_update:
            self.usage["current_cost"]["day"] += request_cost
            self.usage["current_cost"]["month"] += request_cost
        else:
            if today.month == last_update.month:
                self.usage["current_cost"]["month"] += request_cost
            else:
                self.usage["current_cost"]["month"] = request_cost
            self.usage["current_cost"]["day"] = request_cost
            self.usage["current_cost"]["last_update"] = str(today)

    def get_current_cost(self):
        """Get total USD amount of all requests of the current day and month

        :return: cost of current day and month
        """
        today = date.today()
        last_update = date.fromisoformat(self.usage["current_cost"]["last_update"])
        if today == last_update:
            cost_day = self.usage["current_cost"]["day"]
            cost_month = self.usage["current_cost"]["month"]
        else:
            cost_day = 0.0
            if today.month == last_update.month:
                cost_month = self.usage["current_cost"]["month"]
            else:
                cost_month = 0.0
        # add to all_time cost, initialize with calculation of total_cost if key doesn't exist
        cost_all_time = self.usage["current_cost"].get("all_time", self.initialize_all_time_cost())
        return {"cost_today": cost_day, "cost_month": cost_month, "cost_all_time": cost_all_time}

    def initialize_all_time_cost(self, tokens_price=0.003, vision_token_price=0.003):
        """Get total USD amount of all requests in history

        :param tokens_price: price per 1000 tokens, defaults to 0.003
        :param vision_token_price: price per 1K vision tokens, defaults to 0.003
        :return: total cost of all requests
        """
        total_tokens = sum(self.usage['usage_history']['chat_tokens'].values())
        token_cost = round(total_tokens * tokens_price / 1000, 6)

        total_vision_tokens = sum(self.usage['usage_history']['vision_tokens'].values())
        vision_cost = round(total_vision_tokens * vision_token_price / 1000, 2)

        all_time_cost = token_cost + vision_cost
        return all_time_cost
