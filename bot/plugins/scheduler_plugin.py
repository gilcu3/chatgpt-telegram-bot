from typing import Dict

from .plugin import Plugin


class SchedulerPlugin(Plugin):
    """
    A plugin that allows Claude to set reminders and recurring schedules
    for users during conversation.
    """

    def __init__(self, scheduler):
        self.scheduler = scheduler

    def get_source_name(self) -> str:
        return "Scheduler"

    def get_spec(self) -> [Dict]:
        return [
            {
                "name": "set_reminder",
                "description": "Set a one-time reminder for the user. Use this when a user asks to be "
                               "reminded about something at a specific time. Examples: 'remind me in 2 hours "
                               "to call mom', 'remind me tomorrow at 9am about the meeting'. "
                               "The user_id and chat_id are provided automatically.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "The reminder message to send"
                        },
                        "time": {
                            "type": "string",
                            "description": "When to send the reminder (natural language, e.g. 'in 2 hours', 'tomorrow at 9am', '2024-03-15 14:00')"
                        }
                    },
                    "required": ["message", "time"]
                }
            },
            {
                "name": "set_recurring_schedule",
                "description": "Set a recurring scheduled message for the user. Use this when a user wants "
                               "regular reminders or briefings. Examples: 'every day at 8am send me a motivation quote', "
                               "'every monday at 10am remind me about standup'. "
                               "The user_id and chat_id are provided automatically.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "The message to send on each occurrence"
                        },
                        "schedule": {
                            "type": "string",
                            "description": "The recurring schedule (e.g. 'every day at 8:00', 'every monday at 14:30', 'every weekday at 9am')"
                        }
                    },
                    "required": ["message", "schedule"]
                }
            }
        ]

    async def execute(self, function_name, helper, **kwargs) -> Dict:
        user_id = kwargs.get('_user_id')
        chat_id = kwargs.get('_chat_id')
        if user_id is None or chat_id is None:
            return {"error": "No user/chat context available"}
        user_id = int(user_id)
        chat_id = int(chat_id)

        if function_name == "set_reminder":
            message = kwargs["message"]
            time_text = kwargs["time"]
            job_id, result = self.scheduler.add_reminder(user_id, chat_id, message, time_text)
            if job_id:
                return {"result": f"Reminder set for {result} (ID: {job_id}): {message}"}
            return {"error": result}

        elif function_name == "set_recurring_schedule":
            message = kwargs["message"]
            schedule_text = kwargs["schedule"]
            job_id, result = self.scheduler.add_recurring(user_id, chat_id, message, schedule_text)
            if job_id:
                return {"result": f"Recurring schedule set: {result} (ID: {job_id}): {message}"}
            return {"error": result}

        return {"error": f"Unknown function: {function_name}"}
