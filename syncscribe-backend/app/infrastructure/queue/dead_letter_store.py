import json
from datetime import datetime, timezone
from redis import Redis

DEAD_LETTER_KEY = "syncscribe:analysis:dead_letter"


class DeadLetterStore:
    def __init__(self, redis_client: Redis):
        self._redis = redis_client

    def push(self, job_id: str, source_id: str, error_code: str, error_message: str) -> None:
        entry = {"job_id": job_id, "source_id": source_id, "error_code": error_code, "error_message": error_message, "failed_at": datetime.now(timezone.utc).isoformat()}
        self._redis.lpush(DEAD_LETTER_KEY, json.dumps(entry, ensure_ascii=False))
