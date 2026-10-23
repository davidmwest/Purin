"""One scheduler observation of the current event's additional story tab."""
from module.base.button import Button
from module.campaign.event_completion import EventCompletion, REWARD_OBSERVATION_DIR


# Exact sidebar label observed by Purin on 2026-10-22 at 22:57:59.
# The destination is captured before its own story controls can be supported.
STRICT_MAID_TAB = Button(
    area=(44, 558, 175, 599), color=(51, 55, 70), button=(45, 556, 185, 602),
    file='./assets/en/event_completion/TEMPESTA_STRICT_MAID_20261022.png', name='STRICT_MAID_TAB')


class StrictMaidObserver(EventCompletion):
    def run(self, event, folder=REWARD_OBSERVATION_DIR):
        """Spend one durable tab-observation budget; never act on its destination."""
        return self._observe_event_tab(event, STRICT_MAID_TAB, 'strict_maid', folder=folder)
