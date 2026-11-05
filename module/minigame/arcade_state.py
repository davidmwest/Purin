"""Account-owned arcade receipts: reserve a start before input, never replay an uncertain round."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import tempfile
from uuid import uuid4

from module.config.utils import server_timezone
from module.exception import ScriptError


def server_period(now=None):
    current = (now or datetime.now().astimezone()).astimezone(timezone(server_timezone()))
    return (current - timedelta(days=current.weekday())).date().isoformat(), current.strftime('%Y-%m')


def parse_count(text, maximum=40):
    text = str(text).strip()
    if not re.fullmatch(r'\d{1,5}', text):
        return None
    number = int(text)
    return number if 0 <= number <= maximum else None


def parse_selection(text):
    match = re.fullmatch(r'\s*(\d)\s*/\s*(\d{1,2})\s*', str(text))
    if match is None:
        return None
    count, total = map(int, match.groups())
    return (count, total) if 0 <= count <= min(5, total) and total <= 40 else None


class ArcadeJournal:
    def __init__(self, account, folder='config/arcade'):
        if not re.fullmatch(r'[A-Za-z0-9_-]+', account):
            raise ScriptError('Invalid arcade account')
        self.path = Path(folder) / (account + '.json')
        try:
            self.data = json.loads(self.path.read_text())
        except FileNotFoundError:
            self.data = {'version': 1, 'account': account, 'claims': {}, 'rounds': []}
        except (OSError, ValueError) as exc:
            raise ScriptError(f'Unreadable arcade receipt journal: {exc}') from exc
        if not isinstance(self.data, dict) or self.data.get('version') != 1 or self.data.get('account') != account \
                or not isinstance(self.data.get('claims'), dict) or not isinstance(self.data.get('rounds'), list) \
                or any(not isinstance(row, dict) for row in self.data['rounds']):
            raise ScriptError('Invalid arcade receipt journal')
        for row in self.data['rounds']:
            if row.get('status') not in ('started', 'complete') or row.get('start_clicks') != 1 \
                    or type(row.get('credits_before')) is not int or not 1 <= row['credits_before'] <= 40 \
                    or type(row.get('credits_used')) is not int \
                    or not 1 <= row['credits_used'] <= min(5, row['credits_before']):
                raise ScriptError('Invalid arcade round receipt')
        for row in self.data['claims'].values():
            if not isinstance(row, dict) or row.get('status') not in ('requested', 'complete') \
                    or row.get('clicks') != 1 or type(row.get('credits_before')) is not int \
                    or not 0 <= row['credits_before'] <= 30:
                raise ScriptError('Invalid arcade credit receipt')

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix='.arcade-', suffix='.json', dir=str(self.path.parent))
        try:
            with os.fdopen(descriptor, 'w') as stream:
                json.dump(self.data, stream, indent=2, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, str(self.path))
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)

    def pending(self):
        return next((row for row in self.data['rounds'] if row.get('status') == 'started'), None)

    def reserve_round(self, credits, count, evidence):
        if self.pending() is not None or any(row['status'] == 'requested' for row in self.data['claims'].values()) \
                or type(credits) is not int or type(count) is not int \
                or not 1 <= count <= min(5, credits) or credits > 40 or not evidence:
            raise ScriptError('Arcade round cannot be safely started')
        week, month = server_period()
        row = dict(id=uuid4().hex, status='started', week=week, month=month, credits_before=credits,
                   credits_used=count, started=datetime.now().isoformat(), before_screen=str(evidence), start_clicks=1)
        self.data['rounds'].append(row)
        self.save()
        return row

    def finish_round(self, credits, tickets, evidence):
        row = self.pending()
        if row is None or type(credits) is not int or type(tickets) is not int or not evidence \
                or credits != row['credits_before'] - row['credits_used'] \
                or not 1 <= tickets <= row['credits_used'] * 180:
            raise ScriptError('Arcade result or credit debit not verified')
        row.update(status='complete', credits_after=credits, tickets=tickets, receipt=str(evidence),
                   finished=datetime.now().isoformat())
        self.save()
        return row

    def reserve_claim(self, week, credits, evidence):
        if self.pending() is not None or week in self.data['claims'] or type(credits) is not int \
                or not 0 <= credits <= 30 or not evidence:
            raise ScriptError('Weekly arcade credits cannot be safely claimed')
        self.data['claims'][week] = dict(status='requested', credits_before=credits, screen=str(evidence), clicks=1)
        self.save()

    def finish_claim(self, week, credits, evidence):
        row = self.data['claims'].get(week)
        if not row or row.get('status') != 'requested' or credits != row['credits_before'] + 10 or not evidence:
            raise ScriptError('Weekly arcade credit receipt not verified')
        row.update(status='complete', credits_after=credits, receipt=str(evidence))
        self.save()
