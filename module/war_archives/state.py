"""Account-wide archive budget and resumable goals, persisted before entry."""
import json
import os
import re
import tempfile
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime

from filelock import FileLock, Timeout

from module.exception import ScriptError
from module.war_archives.catalog import RAW_KEYS_CAPACITY, RAW_KEYS_PER_ENTRY


class ArchiveState:
    VERSION = 1

    def __init__(self, account, folder='./config/war_archives'):
        if not isinstance(account, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', account):
            raise ScriptError('Invalid archive account')
        self.account = account
        self.path = os.path.join(folder, account + '.json')
        self._reload()

    def _reload(self):
        try:
            with open(self.path, encoding='utf-8') as stream:
                self.data = json.load(stream)
        except FileNotFoundError:
            self.data = dict(version=self.VERSION, account=self.account, periods={}, goals={})
        except (OSError, ValueError) as exc:
            raise ScriptError(f'Cannot read archive budget: {exc}') from exc
        if (not isinstance(self.data, dict) or type(self.data.get('version')) is not int
                or self.data.get('version') != self.VERSION or self.data.get('account') != self.account
                or not isinstance(self.data.get('periods'), dict)
                or not isinstance(self.data.get('goals'), dict)):
            raise ScriptError('Invalid archive budget journal')
        for key, period in self.data['periods'].items():
            if (not isinstance(key, str) or not key or not isinstance(period, dict)
                    or not isinstance(period.get('entries'), list)):
                raise ScriptError('Invalid archive budget period')
            for entry in period['entries']:
                if (not isinstance(entry, dict) or entry.get('status') not in
                        ('reserved', 'won', 'lost', 'uncertain', 'refunded')
                        or not isinstance(entry.get('goal'), str) or not entry['goal']
                        or type(entry.get('raw_keys')) is not int
                        or entry['raw_keys'] != RAW_KEYS_PER_ENTRY):
                    raise ScriptError('Invalid archive reservation')
        if any(not isinstance(key, str) or not key or not isinstance(value, dict)
               for key, value in self.data['goals'].items()):
            raise ScriptError('Invalid archive goal journal')

    @contextmanager
    def _transaction(self):
        """Atomic files prevent torn writes; the lock also prevents lost budgets."""
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        try:
            with FileLock(self.path + '.lock', timeout=0):
                self._reload()
                try:
                    yield
                except Exception:
                    # A failed write must not leave an in-memory permission or
                    # completion that was never persisted.
                    self._reload()
                    raise
        except Timeout as exc:
            raise ScriptError('Archive budget is already being updated') from exc

    def save(self):
        folder = os.path.dirname(self.path)
        os.makedirs(folder, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.archive-', suffix='.json', dir=folder)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(self.data, stream, indent=2, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)

    def entries(self, period):
        self._reload()
        return deepcopy(self.data['periods'].get(period, {}).get('entries', []))

    def used(self, period):
        return sum(entry['status'] != 'refunded' for entry in self.entries(period))

    def reserve(self, period, goal, oil, raw_keys, key_cost=5, daily_tickets=4, extra_reserve=5000):
        """Literal True to the entry hook only after durable budget ownership.

        One current entry consumes five raw keys. Incomplete OCR or a failed
        write cannot grant permission. Both scheduler jobs share this file;
        stale instances reload under a lock before counting or appending.
        """
        if (not isinstance(period, str) or not period or not isinstance(goal, str) or not goal
                or any(type(value) is not int for value in
                       (oil, raw_keys, key_cost, daily_tickets, extra_reserve))
                or key_cost != RAW_KEYS_PER_ENTRY or not key_cost <= raw_keys <= RAW_KEYS_CAPACITY
                or not 0 <= daily_tickets <= 4 or extra_reserve < 0):
            return False
        with self._transaction():
            floor = 500 if self.used(period) < daily_tickets else max(500, extra_reserve)
            # This cutoff is checked before the sortie; fleet preview remains
            # authoritative for actual battle cost.
            if oil < floor:
                return False
            record = self.data['periods'].setdefault(period, dict(entries=[]))
            record['entries'].append(dict(goal=goal, raw_keys=key_cost, status='reserved',
                                          at=datetime.now().isoformat()))
            self.save()
        return True

    def finish(self, period, goal, status, **evidence):
        if status not in ('won', 'lost', 'uncertain', 'refunded'):
            raise ValueError(status)
        with self._transaction():
            entries = self.data['periods'].get(period, {}).get('entries', [])
            for entry in reversed(entries):
                if entry['goal'] == goal and entry['status'] == 'reserved':
                    entry.update(status=status, evidence=evidence)
                    self.save()
                    return
            raise ScriptError('Archive result has no reserved entry')

    def record(self, key):
        self._reload()
        value = self.data['goals'].get(key, {})
        if not isinstance(value, dict):
            raise ScriptError(f'Invalid archive goal: {key}')
        return deepcopy(value)

    def update(self, key, **values):
        if not isinstance(key, str) or not key:
            raise ScriptError('Invalid archive goal key')
        with self._transaction():
            self.data['goals'][key] = dict(self.record(key), **values)
            self.save()
