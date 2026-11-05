"""Weekly free arcade credits and the idle shooting game; no paid-credit exchange."""
from datetime import datetime, timedelta, timezone
import hashlib
import re
from pathlib import Path

from module.base.utils import save_image, load_image
from module.base.button import Button
from module.config.utils import server_timezone
from module.exception import ScriptError
from module.logger import logger
from module.minigame.arcade_state import ArcadeJournal, parse_count, parse_selection, server_period
from module.combat.assets import GET_ITEMS_1, GET_ITEMS_2
from module.minigame.minigame import MINIGAME_SCROLL
from module.ocr.ocr import Ocr
from module.ui.assets import ACADEMY_GOTO_GAME_ROOM, GAME_ROOM_CHECK
from module.ui.page import page_academy, page_game_room
from module.ui.ui import UI
from module.handler.assets import POPUP_CANCEL, POPUP_CONFIRM


class ArcadeTicketCap(ScriptError):
    pass

ARCADE_TITLE = Button(area=(123, 12, 272, 44), color=(112, 110, 140), button=(123, 12, 272, 44),
                      file='./assets/en/minigame/arcade_room.png', name='ARCADE_TITLE')
ARCADE_START = Button(area=(1050, 604, 1198, 661), color=(123, 129, 203), button=(1050, 604, 1198, 661),
                      file='./assets/en/minigame/arcade_room.png', name='ARCADE_GAME_LIST')
ARCADE_ROOM_TUTORIAL = Button(area=(254, 128, 550, 208), color=(), button=(254, 128, 550, 208),
                             file='./assets/en/minigame/arcade_room_tutorial.png', name='ARCADE_ROOM_TUTORIAL')
ARCADE_CREDIT_TUTORIAL = Button(area=(588, 132, 919, 231), color=(), button=(588, 132, 889, 221),
                               file='./assets/en/minigame/arcade_credit_tutorial.png', name='ARCADE_CREDIT_TUTORIAL')
ARCADE_PRIZE_TUTORIAL = Button(area=(109, 384, 422, 442), color=(), button=(109, 384, 422, 442),
                              file='./assets/en/minigame/arcade_prize_tutorial.png', name='ARCADE_PRIZE_TUTORIAL')
ARCADE_CREDIT_LABEL = Button(area=(557, 333, 648, 358), color=(), button=(557, 333, 648, 358),
                            file='./assets/en/minigame/arcade_credit_tutorial.png', name='ARCADE_CREDIT_LABEL')
ARCADE_PREP_BACK = Button(area=(23, 34, 180, 92), color=(), button=(40, 40, 160, 85),
                         file='./assets/en/minigame/arcade_credit_tutorial.png', name='ARCADE_PREP_BACK')
SHOOTING_CARD = Button(area=(242, 439, 502, 474), color=(), button=(260, 445, 500, 475),
                       file='./assets/en/minigame/arcade_games.png', name='ARCADE_SHOOTING_GAME')
SHOOTING_TITLE = Button(area=(109, 23, 278, 62), color=(128, 103, 59), button=(109, 23, 278, 62),
                       file='./assets/en/minigame/arcade_shooting_prepare.png', name='SHOOTING_TITLE')
SHOOTING_START = Button(area=(933, 566, 1041, 635), color=(179, 110, 69), button=(945, 572, 1030, 626),
                       file='./assets/en/minigame/arcade_shooting_prepare.png', name='SHOOTING_START')
SHOOTING_BACK = Button(area=(24, 17, 79, 70), color=(76, 47, 28), button=(30, 22, 74, 65),
                      file='./assets/en/minigame/arcade_shooting_prepare.png', name='SHOOTING_BACK')
SHOOTING_PLUS = Button(area=(783, 345, 837, 373), color=(197, 166, 207), button=(789, 347, 830, 370),
                      file='./assets/en/minigame/arcade_shooting_prepare.png', name='SHOOTING_ADD_CREDIT')
ARCADE_FREE = Button(area=(362, 292, 418, 338), color=(208, 216, 232), button=(369, 298, 409, 331),
                     file='./assets/en/minigame/arcade_room.png', name='ARCADE_FREE_WEEKLY_CREDITS')
ARCADE_LIST_TITLE = Button(area=(114, 12, 348, 45), color=(109, 106, 155), button=(114, 12, 348, 45),
                          file='./assets/en/minigame/arcade_games.png', name='ARCADE_LIST_TITLE')
ARCADE_LIST_BACK = Button(area=(37, 33, 80, 76), color=(64, 58, 100), button=(37, 33, 80, 76),
                         file='./assets/en/minigame/arcade_games.png', name='ARCADE_LIST_BACK')
HEADER_CREDITS = Ocr(Button(area=(1093, 24, 1138, 53), color=(), button=(1093, 24, 1138, 53)),
                     lang='azur_lane', letter=(255, 235, 115), threshold=128, name='ARCADE_CREDITS')
SELECTED_CREDITS = Ocr(Button(area=(659, 343, 734, 376), color=(), button=(659, 343, 734, 376)),
                       lang='azur_lane', letter=(33, 28, 49), threshold=128, name='ARCADE_SELECTED_CREDITS')
CAP_TEXT_AREA = Button(area=(240, 215, 1040, 440), color=(), button=(240, 215, 1040, 440))
ARCADE_TICKET_ICON = Button(area=(596, 304, 684, 368), color=(140, 159, 206), button=(596, 304, 684, 368),
                           file='./assets/en/minigame/arcade_ticket_receipt.png', name='ARCADE_TICKET_RECEIPT')
TICKET_AMOUNT = Ocr(Button(area=(641, 371, 687, 393), color=(), button=(641, 371, 687, 393)),
                    lang='azur_lane', letter=(255, 255, 255), threshold=128, name='ARCADE_TICKETS_EARNED')


def cap_kind(text):
    normalize = lambda value: re.sub('[^a-z]', '', str(value).lower())
    for kind, phrase in (
            ('monthly', "You've reached your monthly limit of Game Tickets, and will not be able to earn any more. Continue playing the minigame?"),
            ('storage', "You've reached the maximum number of Game Tickets, and will not be able to earn any more. Continue playing the minigame?")):
        if normalize(text) == normalize(phrase):
            return kind
    return None


def next_month(now=None):
    current = (now or datetime.now().astimezone()).astimezone(timezone(server_timezone()))
    future = (current.replace(day=1) + timedelta(days=32)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return future.astimezone().replace(tzinfo=None)


def ticket_receipt(image):
    """The actual single-item Game Ticket award; never credits or unrelated receipts."""
    if image.shape[:2] != (720, 1280) or not GET_ITEMS_1.match(image, offset=(20, 20), similarity=.85) \
            or not ARCADE_TICKET_ICON.match_template_color(image, offset=(2, 2), similarity=.94, threshold=15):
        return None
    count = parse_count(TICKET_AMOUNT.ocr(image), maximum=900)
    return count if count is not None and count > 0 else None


def next_week(now=None):
    """Next Monday midnight on the account's server, converted at that date for DST."""
    now = now or datetime.now().astimezone()
    current = now.astimezone(timezone(server_timezone()))
    days = 7 - current.weekday()
    return (current + timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0) \
        .astimezone().replace(tzinfo=None)


class ArcadeTickets(UI):
    def capture(self, label):
        folder = Path('log/arcade')
        folder.mkdir(parents=True, exist_ok=True)
        name = f'{datetime.now():%Y-%m-%d_%H-%M-%S_%f}_{self.config.config_name}_{label}.png'
        save_image(self.device.image, str(folder / name))
        logger.info(f'Arcade screen: {folder / name}')
        return str(folder / name)

    def matches(self, button):
        return button.match_template_color(self.device.image, offset=(3, 3), similarity=.92, threshold=18)

    def handle_cap_warning(self):
        if not self.appear(POPUP_CANCEL, offset=(30, 30)) or not self.appear(POPUP_CONFIRM, offset=(30, 30)):
            return False
        for letter in ((255, 255, 255), (60, 60, 60)):
            text = Ocr(CAP_TEXT_AREA, lang='cnocr', letter=letter, threshold=128, name='ARCADE_CAP_TEXT').ocr(self.device.image)
            kind = cap_kind(text)
            if kind:
                if getattr(self, '_cap_previous', None) != kind:
                    self._cap_previous = kind
                    return True
                if getattr(self, '_cap_acknowledged', False):
                    return True
                self.near_cap = True
                if self.handle_popup_confirm('ARCADE_EXACT_TICKET_CAP'):
                    self._cap_acknowledged = True
                return True
        return False

    def preparing(self):
        return self.matches(SHOOTING_TITLE) and self.matches(SHOOTING_START)

    def stable_credits(self, selection=False):
        previous = None
        for _ in self.loop(skip_first=False, timeout=8):
            if selection:
                if not self.preparing():
                    continue
                value = parse_selection(SELECTED_CREDITS.ocr(self.device.image))
            else:
                if not self.matches(ARCADE_TITLE) or not self.matches(ARCADE_START):
                    continue
                value = parse_count(HEADER_CREDITS.ocr(self.device.image))
            if value is not None and value == previous:
                return value
            previous = value
        raise ScriptError('Arcade credits did not give two agreeing full reads')

    def to_room(self):
        for _ in self.loop(skip_first=False, timeout=15):
            if self.matches(ARCADE_TITLE) and self.matches(ARCADE_START):
                return
            if self.preparing() and self.appear_then_click(SHOOTING_BACK, offset=(3, 3), interval=3):
                continue
            if self.matches(ARCADE_LIST_TITLE) and self.appear_then_click(ARCADE_LIST_BACK, offset=(3, 3), interval=3):
                continue
        raise ScriptError('Arcade safe return to lobby not verified')

    def credit_receipt(self):
        return self.appear(GET_ITEMS_1, offset=(20, 20)) or self.appear(GET_ITEMS_2, offset=(20, 20))

    def dismiss_receipt(self):
        if self.appear_then_click(GET_ITEMS_1, offset=(20, 20), interval=2):
            return True
        return self.appear_then_click(GET_ITEMS_2, offset=(20, 20), interval=2)

    def collect_weekly(self, credits):
        week, _ = server_period()
        row = self.journal.data['claims'].get(week)
        if row:
            if row['status'] != 'complete':
                raise ScriptError('An earlier weekly credit claim is unresolved; do not replay it')
            return credits
        if credits > 30 or not self.matches(ARCADE_FREE):
            return credits
        self.journal.reserve_claim(week, credits, self.capture('weekly_credits_before'))
        self.device.click(ARCADE_FREE)
        receipt = None
        for _ in self.loop(skip_first=False, timeout=20):
            if self.credit_receipt():
                receipt = self.capture('weekly_credit_receipt')
                self.dismiss_receipt()
                continue
            if receipt and self.matches(ARCADE_TITLE) and self.matches(ARCADE_START):
                after = self.stable_credits()
                if self.matches(ARCADE_FREE):
                    raise ScriptError('Weekly credit claim still available after one input')
                self.journal.finish_claim(week, after, receipt)
                logger.info(f'Arcade weekly free credits verified: {credits} -> {after}')
                return after
        raise ScriptError('Weekly credit claim did not produce a verified receipt')

    def select_credits(self, total):
        requested = self.config.Arcade_CreditsPerRound
        if type(requested) is not int or not 1 <= requested <= 5:
            raise ScriptError('Invalid arcade credit batch')
        target = min(requested, total, 1 if self.near_cap else 5)
        for _ in range(6):
            selected, available = self.stable_credits(selection=True)
            if available != total:
                raise ScriptError('Arcade owned credits changed during preparation')
            if selected == target and target > 0:
                return selected
            if selected > target or target == 0 or not self.matches(SHOOTING_PLUS):
                raise ScriptError('Arcade credit selection is not a verified payable batch')
            self.device.click(SHOOTING_PLUS)
            for _ in self.loop(skip_first=False, timeout=5):
                current = parse_selection(SELECTED_CREDITS.ocr(self.device.image)) if self.preparing() else None
                if current == (selected + 1, total):
                    break
            else:
                if selected == 0 and self.near_cap:
                    raise ArcadeTicketCap('Ticket cap: no reward-bearing credit can be selected')
                raise ScriptError('Arcade credit selection locked or unresponsive; no round started')
        raise ScriptError('Arcade credit selection exceeded its adjustment budget')

    def save_round_candidate(self, row):
        path = self.capture('round_' + row['id'] + '_receipt')
        row['receipt_candidate'] = dict(path=path, sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest())
        self.journal.save()
        return path

    def retained_ticket_receipt(self, row):
        candidate = row.get('receipt_candidate', {})
        path = Path(candidate.get('path', ''))
        if not candidate or not path.is_file() or path.parent != Path('log/arcade') \
                or f"_{self.config.config_name}_round_{row['id']}_" not in path.name \
                or hashlib.sha256(path.read_bytes()).hexdigest() != candidate.get('sha256'):
            return None
        return ticket_receipt(load_image(str(path)))

    def finish_pending(self, credits):
        row = self.journal.pending()
        if row is None:
            return
        tickets = self.retained_ticket_receipt(row)
        if tickets is None:
            raise ScriptError('Unresolved arcade round: no verified retained ticket receipt; no replay')
        self.journal.finish_round(credits, tickets, row['receipt_candidate']['path'])

    def idle_round(self, total):
        count = self.select_credits(total)
        # Fresh full selection proof immediately before the sole persisted Start.
        if self.stable_credits(selection=True) != (count, total) or not self.preparing():
            raise ScriptError('Shooting preparation changed before Start')
        row = self.journal.reserve_round(total, count, self.capture('shooting_selected'))
        self.device.click(SHOOTING_START)
        logger.info(f'Arcade idle shooting: one Start, {count} owned credits, no aim or fire inputs')
        receipt = None
        result_frames = 0
        for _ in self.loop(skip_first=False, timeout=45):
            if self.credit_receipt():
                path = self.save_round_candidate(row)
                receipt = ticket_receipt(self.device.image)
                if receipt is None:
                    raise ScriptError('Arcade ticket receipt retained for exact recognition; do not replay Start')
                self.dismiss_receipt()
                continue
            if receipt is not None and self.preparing():
                _, remaining = self.stable_credits(selection=True)
                self.journal.finish_round(remaining, receipt, row['receipt_candidate']['path'])
                if receipt < count * 180:
                    self.capped = True
                return remaining
            if receipt is not None:
                result_frames += 1
                if result_frames >= 3:
                    row['result_screen'] = self.capture('round_' + row['id'] + '_result')
                    self.journal.save()
                    raise ScriptError('Shooting result dialog retained for exact recognition')
        self.capture('round_' + row['id'] + '_result_unrecognized')
        raise ScriptError('Arcade round result not verified; do not replay Start')

    def play_to(self, credits, target):
        if credits <= target:
            return credits
        while credits > target and self.rounds < 10:
            self.open_games()
            self.open_shooting()
            credits = self.idle_round(credits)
            self.rounds += 1
            self.to_room()
            if self.stable_credits() != credits:
                raise ScriptError('Arcade lobby credits disagree with the verified round debit')
            if self.capped:
                break
            if self.config.task_switched():
                self.yielded = True
                break
        return credits

    def enter_arcade(self):
        self.ui_ensure(page_academy)
        for _ in self.loop(skip_first=False, timeout=30):
            if self.ui_page_appear(page_game_room):
                return
            if self.ui_page_appear(page_academy, interval=5):
                self.device.click(ACADEMY_GOTO_GAME_ROOM)
        raise ScriptError('Arcade entrance not recognized')

    def open_games(self):
        for _ in self.loop(skip_first=False, timeout=20):
            if self.handle_cap_warning():
                continue
            if self.appear_then_click(ARCADE_ROOM_TUTORIAL, offset=(2, 2), similarity=.97, interval=3) \
                    or self.appear_then_click(ARCADE_CREDIT_TUTORIAL, offset=(2, 2), similarity=.97, interval=3) \
                    or self.appear_then_click(ARCADE_PRIZE_TUTORIAL, offset=(2, 2), similarity=.97, interval=3):
                continue
            if self.matches(ARCADE_LIST_TITLE) and MINIGAME_SCROLL.appear(main=self):
                return
            if self.match_template_color(ARCADE_TITLE, offset=(2, 2), similarity=.95, threshold=10) \
                    and self.match_template_color(ARCADE_START, offset=(2, 2), similarity=.95, threshold=10, interval=4):
                self.device.click(ARCADE_START)
                continue
            # The first-visit guide may open the tower game. Leave its verified
            # preparation page without pressing Start or changing its credit count.
            if self.appear(ARCADE_CREDIT_LABEL, offset=(2, 2), similarity=.98) \
                    and self.appear_then_click(ARCADE_PREP_BACK, offset=(2, 2), similarity=.98, interval=3):
                continue
        raise ScriptError('Arcade game list not recognized')

    def run(self):
        try:
            self.rounds, self.yielded, self.capped, self.near_cap = 0, False, False, False
            self.journal = ArcadeJournal(self.config.config_name)
            self.enter_arcade()
            self.capture('room')
            if self.config.Arcade_ObserveOnly:
                self.open_games()
                self.capture('games')
                self.inspect_shooting()
                # A one-shot, purchase-free screen inspection used to validate a client layout.
                self.config.cross_set('ArcadeTickets.Scheduler.Enable', False)
                self.ui_goto_main()
                logger.info('Arcade inspection complete; no credits claimed or spent')
                return
            self.to_room()
            credits = self.stable_credits()
            self.finish_pending(credits)
            if credits > 30 and self.matches(ARCADE_FREE):
                credits = self.play_to(credits, 30)
            if not self.yielded and not self.capped:
                credits = self.collect_weekly(credits)
                credits = self.play_to(credits, 0)
            self.ui_goto_main()
            if self.capped:
                self.config.task_delay(target=min(next_week(), next_month()))
            elif credits > 0 or self.yielded:
                self.config.task_delay(minute=1)
            else:
                self.config.task_delay(target=next_week())
            logger.info(f'Arcade finished: {self.rounds} verified idle rounds, {credits} credits left')
        except ArcadeTicketCap as exc:
            self.capture('ticket_cap')
            self.to_room()
            self.ui_goto_main()
            self.config.task_delay(target=min(next_week(), next_month()))
            logger.info(f'Arcade stopped at ticket cap without another Start: {exc}')
        except ScriptError as exc:
            self.capture('blocked')
            logger.warning(f'Arcade deferred: {exc}')
            try:
                self.to_room()
                self.ui_goto_main()
            except ScriptError:
                self.config.task_call('Restart')
            self.config.task_delay(minute=60)

    def inspect_shooting(self):
        self.open_shooting()
        self.capture('shooting_prepare')
        self.to_room()

    def open_shooting(self):
        if not self.matches(ARCADE_LIST_TITLE):
            raise ScriptError('Shooting selection requires the verified Game Selection title')
        MINIGAME_SCROLL.set_top(main=self)
        for _ in self.loop(skip_first=False, timeout=12):
            if self.appear(SHOOTING_CARD, offset=(4, 500), similarity=.93):
                self.device.click(SHOOTING_CARD)
                break
        else:
            raise ScriptError('Shooting-game card not recognized')
        for _ in self.loop(skip_first=False, timeout=10):
            if self.handle_cap_warning():
                continue
            if self.preparing():
                return
        self.capture('shooting_unrecognized')
        raise ScriptError('Shooting preparation not recognized')
