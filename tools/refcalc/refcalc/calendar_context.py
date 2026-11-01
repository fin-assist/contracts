"""Observable consumer policy for source zones; no fallback to device/profile offsets."""
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def calendar_context(response, *, has_calendar_data=True, verified_cache=None, lookup=ZoneInfo):
    """The caller passes only a cache already matched by user and request context."""
    if not has_calendar_data:
        return {'state': 'ready', 'refresh': False, 'response': response}
    name = response.get('time_zone')
    if not isinstance(name, str) or not 1 <= len(name) <= 128:
        return {'state': 'invalid_response', 'cache': verified_cache}
    try:
        zone = lookup(name)
    except ZoneInfoNotFoundError:
        return {'state': 'unsupported_time_zone', 'refresh': False, 'cache': verified_cache,
                'text': 'Устройство не поддерживает часовой пояс этих данных'}
    except ValueError:
        return {'state': 'invalid_response', 'cache': verified_cache}
    return {'state': 'ready', 'refresh': False, 'response': response, 'zone': zone}
