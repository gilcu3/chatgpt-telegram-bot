import datetime
import fcntl
import json
import logging
import os
import pathlib
import re
import uuid

import dateparser


class BotScheduler:
    """
    Persistent job scheduler for reminders and recurring messages.
    Stores jobs in JSON and registers them with python-telegram-bot's JobQueue.
    """

    def __init__(self, data_dir="scheduler_data", default_timezone="UTC"):
        self.data_dir = data_dir
        self.jobs_file = os.path.join(data_dir, "jobs.json")
        self.default_timezone = default_timezone
        self.jobs = {}  # {job_id: job_data}
        self._application = None  # Set during run() setup
        self._send_callback = None  # Callback to send messages

        if os.path.isfile(self.jobs_file):
            with open(self.jobs_file, "r", encoding="utf-8") as f:
                try:
                    self.jobs = json.load(f)
                except json.JSONDecodeError:
                    logging.warning("Corrupted scheduler file, starting fresh")
                    self.jobs = {}
        else:
            pathlib.Path(data_dir).mkdir(exist_ok=True)

    def _save(self):
        pathlib.Path(self.data_dir).mkdir(exist_ok=True)
        with open(self.jobs_file, "w", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                json.dump(self.jobs, f, indent=2, ensure_ascii=False)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def set_application(self, application, send_callback):
        """Set the telegram application and message send callback for job execution."""
        self._application = application
        self._send_callback = send_callback

    def restore_jobs(self):
        """Re-register all persisted jobs with the JobQueue after bot restart."""
        if not self._application:
            logging.warning("Cannot restore jobs: application not set")
            return

        job_queue = self._application.job_queue
        expired = []

        for job_id, job_data in self.jobs.items():
            try:
                if job_data.get("recurring"):
                    self._register_recurring_job(job_queue, job_id, job_data)
                else:
                    # One-time reminder
                    run_at_str = job_data.get("run_at")
                    if not run_at_str:
                        expired.append(job_id)
                        continue
                    run_at = datetime.datetime.fromisoformat(run_at_str)
                    if run_at <= datetime.datetime.now(tz=run_at.tzinfo):
                        expired.append(job_id)
                        continue
                    job_queue.run_once(
                        self._job_callback,
                        when=run_at,
                        data={"job_id": job_id},
                        name=job_id
                    )
            except Exception as e:
                logging.error(f"Failed to restore job {job_id}: {e}")
                expired.append(job_id)

        for job_id in expired:
            del self.jobs[job_id]

        if expired:
            self._save()
            logging.info(f"Cleaned up {len(expired)} expired/invalid jobs")

        logging.info(f"Restored {len(self.jobs)} scheduled jobs")

    def _register_recurring_job(self, job_queue, job_id: str, job_data: dict):
        """Register a recurring job with the JobQueue."""
        time_str = job_data.get("time")  # "HH:MM"
        days = job_data.get("days")  # list of day numbers (0=Monday) or None for daily

        if not time_str:
            return

        parts = time_str.split(":")
        hour, minute = int(parts[0]), int(parts[1])
        run_time = datetime.time(hour=hour, minute=minute)

        if days:
            # Specific days of week
            day_tuple = tuple(days)
            job_queue.run_daily(
                self._job_callback,
                time=run_time,
                days=day_tuple,
                data={"job_id": job_id},
                name=job_id
            )
        else:
            # Every day
            job_queue.run_daily(
                self._job_callback,
                time=run_time,
                data={"job_id": job_id},
                name=job_id
            )

    async def _job_callback(self, context):
        """Executed when a scheduled job fires."""
        job_id = context.job.data.get("job_id")
        if job_id not in self.jobs:
            return

        job_data = self.jobs[job_id]
        chat_id = job_data["chat_id"]
        message = job_data["message"]

        if self._send_callback:
            try:
                await self._send_callback(chat_id, message)
            except Exception as e:
                logging.error(f"Failed to send scheduled message for job {job_id}: {e}")

        # Remove one-time reminders after firing
        if not job_data.get("recurring"):
            del self.jobs[job_id]
            self._save()

    def add_reminder(self, user_id: int, chat_id: int, message: str,
                     time_text: str) -> tuple[str | None, str | None]:
        """
        Parse natural language time and create a one-time reminder.
        Returns (job_id, human_readable_time) or (None, error_message).
        """
        parsed = dateparser.parse(
            time_text,
            settings={
                'PREFER_DATES_FROM': 'future',
                'TIMEZONE': self.default_timezone,
                'RETURN_AS_TIMEZONE_AWARE': True,
            }
        )
        if not parsed:
            return None, "Could not understand the time. Try formats like 'in 2 hours', 'tomorrow at 9am', '2024-03-15 14:00'."

        if parsed <= datetime.datetime.now(tz=parsed.tzinfo):
            return None, "The specified time is in the past. Please provide a future time."

        job_id = str(uuid.uuid4())[:8]
        self.jobs[job_id] = {
            "user_id": user_id,
            "chat_id": chat_id,
            "message": message,
            "run_at": parsed.isoformat(),
            "recurring": False,
            "created_at": datetime.datetime.now().isoformat(),
        }
        self._save()

        # Register with JobQueue if application is available
        if self._application:
            self._application.job_queue.run_once(
                self._job_callback,
                when=parsed,
                data={"job_id": job_id},
                name=job_id
            )

        return job_id, parsed.strftime("%Y-%m-%d %H:%M %Z")

    def add_recurring(self, user_id: int, chat_id: int, message: str,
                      schedule_text: str) -> tuple[str | None, str | None]:
        """
        Parse a recurring schedule spec and create a recurring job.
        Supports: "every day at HH:MM", "every monday at HH:MM", "every weekday at HH:MM"
        Returns (job_id, human_readable_schedule) or (None, error_message).
        """
        schedule_lower = schedule_text.lower().strip()

        # Parse time component
        time_match = re.search(r'at\s+(\d{1,2}):?(\d{2})?\s*(am|pm)?', schedule_lower)
        if not time_match:
            return None, "Could not find a time. Use format like 'every day at 8:00' or 'every monday at 14:30'."

        hour = int(time_match.group(1))
        minute = int(time_match.group(2) or 0)
        ampm = time_match.group(3)

        if ampm == 'pm' and hour < 12:
            hour += 12
        elif ampm == 'am' and hour == 12:
            hour = 0

        if hour > 23 or minute > 59:
            return None, "Invalid time. Hours should be 0-23 and minutes 0-59."

        time_str = f"{hour:02d}:{minute:02d}"

        # Parse day component
        day_names = {
            'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
            'friday': 4, 'saturday': 5, 'sunday': 6,
            'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3, 'fri': 4, 'sat': 5, 'sun': 6,
        }

        days = None
        human_days = "every day"

        if 'weekday' in schedule_lower or 'workday' in schedule_lower:
            days = [0, 1, 2, 3, 4]
            human_days = "every weekday"
        elif 'weekend' in schedule_lower:
            days = [5, 6]
            human_days = "every weekend"
        else:
            found_days = []
            for day_name, day_num in day_names.items():
                if day_name in schedule_lower:
                    if day_num not in found_days:
                        found_days.append(day_num)
            if found_days:
                days = sorted(found_days)
                day_labels = {0: 'Mon', 1: 'Tue', 2: 'Wed', 3: 'Thu', 4: 'Fri', 5: 'Sat', 6: 'Sun'}
                human_days = "every " + ", ".join(day_labels[d] for d in days)

        job_id = str(uuid.uuid4())[:8]
        self.jobs[job_id] = {
            "user_id": user_id,
            "chat_id": chat_id,
            "message": message,
            "time": time_str,
            "days": days,
            "recurring": True,
            "created_at": datetime.datetime.now().isoformat(),
        }
        self._save()

        # Register with JobQueue
        if self._application:
            self._register_recurring_job(self._application.job_queue, job_id, self.jobs[job_id])

        human_schedule = f"{human_days} at {time_str}"
        return job_id, human_schedule

    def get_user_jobs(self, user_id: int) -> list[dict]:
        """Returns all jobs for a given user."""
        result = []
        for job_id, data in self.jobs.items():
            if data["user_id"] == user_id:
                result.append({"id": job_id, **data})
        return result

    def cancel_job(self, job_id: str, user_id: int) -> bool:
        """Cancel a job. Returns True if found and cancelled."""
        if job_id not in self.jobs:
            return False
        if self.jobs[job_id]["user_id"] != user_id:
            return False

        del self.jobs[job_id]
        self._save()

        # Remove from JobQueue
        if self._application:
            current_jobs = self._application.job_queue.get_jobs_by_name(job_id)
            for job in current_jobs:
                job.schedule_removal()

        return True
