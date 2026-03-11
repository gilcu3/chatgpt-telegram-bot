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
            },
            {
                "name": "list_reminders",
                "description": "List all scheduled reminders and recurring schedules for the user. "
                               "Use this when a user asks what reminders they have, wants to see their "
                               "schedules, or needs to find a reminder ID to cancel. "
                               "The user_id is provided automatically.",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            },
            {
                "name": "cancel_reminder",
                "description": "Cancel a scheduled reminder or recurring schedule by its ID. "
                               "Use list_reminders first if you need to find the ID. "
                               "The user_id is provided automatically.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "string",
                            "description": "The ID of the reminder or schedule to cancel"
                        }
                    },
                    "required": ["job_id"]
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

        elif function_name == "list_reminders":
            jobs = self.scheduler.get_user_jobs(user_id)
            if not jobs:
                return {"result": "No scheduled reminders or recurring schedules found."}
            summaries = []
            for job in jobs:
                if job.get("recurring"):
                    days_desc = "daily"
                    if job.get("days") is not None:
                        day_labels = {0: 'Mon', 1: 'Tue', 2: 'Wed', 3: 'Thu', 4: 'Fri', 5: 'Sat', 6: 'Sun'}
                        days_desc = ", ".join(day_labels[d] for d in job["days"])
                    summaries.append(f"[{job['id']}] Recurring ({days_desc} at {job['time']}): {job['message']}")
                else:
                    summaries.append(f"[{job['id']}] Reminder at {job.get('run_at', 'unknown')}: {job['message']}")
            return {"result": "\n".join(summaries)}

        elif function_name == "cancel_reminder":
            job_id = kwargs["job_id"]
            if self.scheduler.cancel_job(job_id, user_id):
                return {"result": f"Cancelled reminder/schedule {job_id}."}
            return {"error": f"Could not find or cancel job {job_id}. It may not exist or belong to another user."}

        return {"error": f"Unknown function: {function_name}"}
