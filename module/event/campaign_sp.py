import json
import os
import tempfile
from datetime import datetime

from module.campaign.stage import stage_names
from module.config.config import TaskEnd
from module.config.utils import get_server_last_update
from module.event.base import EventBase
from module.exception import ScriptError
from module.logger import logger


DAILY_SP_EVENT = 'event_20261022_cn'
DAILY_SP_VERSION = 1


def daily_sp_path(folder='./config/stars'):
    # This budget deliberately does not share the EventStars/EX journals.
    return os.path.join(folder, 'alas-daily-sp.json')


def load_daily_sp(folder='./config/stars'):
    try:
        with open(daily_sp_path(folder), encoding='utf-8') as file:
            state = json.load(file)
    except FileNotFoundError:
        return dict(version=DAILY_SP_VERSION, account='alas', event=DAILY_SP_EVENT, periods={})
    except (OSError, ValueError) as exc:
        raise ScriptError(f'Cannot safely read daily SP budget: {exc}') from exc
    try:
        if (not isinstance(state, dict) or type(state['version']) is not int
                or state['version'] != DAILY_SP_VERSION or state['account'] != 'alas'
                or state['event'] != DAILY_SP_EVENT or not isinstance(state['periods'], dict)):
            raise ValueError('Invalid identity')
        for period, record in state['periods'].items():
            if (datetime.fromisoformat(period).isoformat() != period or not isinstance(record, dict)
                    or record['period'] != period or type(record['attempts']) is not int
                    or record['attempts'] != 1 or record['status'] not in ('reserved', 'complete')):
                raise ValueError('Invalid reservation')
            datetime.fromisoformat(record['reserved_at'])
            if record['status'] == 'complete':
                if (type(record['run_count']) is not int or record['run_count'] <= 0
                        or record['run_lost'] is not False):
                    raise ValueError('Invalid completion')
                datetime.fromisoformat(record['completed_at'])
    except (KeyError, TypeError, ValueError) as exc:
        raise ScriptError('Invalid daily SP budget; refusing another attempt') from exc
    return state


def save_daily_sp(state, folder='./config/stars'):
    os.makedirs(folder, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.daily-sp-', suffix='.json', dir=folder)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as file:
            json.dump(state, file, indent=2, sort_keys=True)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, daily_sp_path(folder))
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


class CampaignSP(EventBase):
    _daily_sp_folder = './config/stars'

    def run(self, *args, **kwargs):
        if 'sp' not in stage_names(self.config.Campaign_Event):
            logger.info(f'{self.config.Campaign_Event} has no stage sp')
            logger.info(f'This event do not have SP, skip')
            self.config.Scheduler_Enable = False
            self.config.task_stop()

        daily_state = None
        if self.config.config_name == 'alas' and self.config.Campaign_Event == DAILY_SP_EVENT:
            period = get_server_last_update(self.config.Scheduler_ServerUpdate).replace(microsecond=0).isoformat()
            daily_state = load_daily_sp(self._daily_sp_folder)
            if period in daily_state['periods']:
                logger.info(f'Daily SP attempt already spent for server period {period}; wait for reset')
                self.config.task_delay(server_update=True)
                return
            record = dict(period=period, status='reserved', attempts=1,
                          reserved_at=datetime.now().isoformat())
            daily_state['periods'][period] = record
            # Reserve before EventBase.run can navigate, enter the map or switch tasks.
            save_daily_sp(daily_state, self._daily_sp_folder)
            self.run_count = 0

        try:
            super().run(name=self.config.Campaign_Name, folder=self.config.Campaign_Event, total=1)
        except TaskEnd:
            # Catch task switch
            pass
        if (daily_state is not None and self.run_count > 0
                and getattr(getattr(self, 'campaign', None), 'run_lost', None) is False):
            record.update(status='complete', completed_at=datetime.now().isoformat(),
                          run_count=self.run_count, run_lost=False)
            save_daily_sp(daily_state, self._daily_sp_folder)
        if self.run_count > 0:
            self.config.task_delay(server_update=True)
        else:
            self.config.task_stop()
