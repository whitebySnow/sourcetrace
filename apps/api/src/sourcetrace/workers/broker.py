import dramatiq
from dramatiq.brokers.redis import RedisBroker

from sourcetrace.core.config import get_settings

broker = RedisBroker(url=get_settings().redis_url)
dramatiq.set_broker(broker)
