"""
Keep the fleets of the campaign filled with the ships to level next, in the order of module.leveling.plan.

Ships level in the fleet that fights the mobs, 6 of the 7 battles of 12-4. The fleets that farm hold ships under
FARM_LEVEL_MAX only, the user's rule of 2026-10-21: a ship done leaves for a ship of the queue, or a carry.
The mob fleet keeps its average level at a minimum,
the user's rule: 95, as 86.8 lost a run in three (see AVERAGE_STEPS), module.leveling.record keeps the trial.
A ship comes in if the fleet keeps the minimum with her, raised with EXP Data Packs if she is short of it, and to 100
with packs storage can spare (module.leveling.boost). A fleet below the minimum has its lowest ships raised.
The fleet that fights the boss is kept strong with ships at CARRY_LEVEL and above, the most powerful ones free:
two ships at 73 and 77 in its vanguard lost the boss of 12-4.
Every ship of the fleets has gear at +10 in all of her slots, from storage, the gear module.leveling.policy picks
for her hull type, see module.leveling.gear.

The queue comes from the dock as the Inventory task read it. The ships of the fleets are the ones whose levels go up,
the fleet page tells them every time and they are written back to the inventory.
"""
import json
from datetime import datetime, timedelta

from module.equipment.assets import FLEET_DETAIL_CHECK
from module.inventory.inventory import filepath_inventory, save_inventory
from module.leveling.boost import LevelingBoost
from module.leveling.plan import BOOST_CAP, CARRY_LEVEL, FARM_LEVEL_MAX, campaign_fleets, carry_targets, \
    fleet_average, is_geared, level_needed, leveling_queue, load_ship_data, mark_slot, plan_boosts, plan_carries, \
    plan_done, plan_lift, plan_swaps, slot_records, update_levels
from module.leveling.readiness import RECOVERY_MINUTES, assess_fleets
from module.leveling.record import load_record, record_average, save_record
from module.logger import logger
from module.ui.page import page_fleet

# Ships of each row of the queue written to the log
QUEUE_LOG = 8
# Ships coming into the mob fleet in a run. The fleet changes one ship at a time, a fleet too weak shows
# in a battle or two: two ships changed at once lost battles of the mobs of 12-4.
MOB_SWAPS = 1
# Trips to the detail page of a ship in a run for gear or EXP Data Packs, each takes a minute or so
GEAR_TRIES = 3
# Gear from storage for ships without gear at +10 in all 6 slots, decent gear for the slot and the hull, the user's
# rule of 2026-10-20, see module.leveling.policy: gold or better first, no sonar nor depth charges on 12-4, no AVI
# but on carriers, no TRP on battleships and carriers, a Repair Toolkit on destroyers.
# Off, a ship without gear at +10 in all 6 slots does not come in, ships of the fleets keep theirs.
GEAR_TRANSFER = True
# A ship storage could not gear well enough is tried again after this
GEAR_RETRY = timedelta(days=1)
TIME_FORMAT = '%Y-%m-%d %H:%M:%S'


def gear_tried_recently(ship, now):
    """
    Args:
        ship (dict): Ship of the inventory
        now (datetime):

    Returns:
        bool: If storage was tried for her not long ago and did not gear her well enough
    """
    tried = ship.get('gear_tried') if ship else None
    if not tried:
        return False
    try:
        return now - datetime.strptime(tried, TIME_FORMAT) < GEAR_RETRY
    except ValueError:
        return False


def load_inventory(config_name):
    """
    Returns:
        dict: ./config/inventory/<config name>.json, None if there is none
    """
    try:
        with open(filepath_inventory(config_name), encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def inventory_ships_of(ships, slots, data=None):
    """
    Args:
        ships (list[dict]): Ships of the inventory
        slots (list[Slot]):
        data (dict[str, ShipData]):

    Returns:
        dict[int, dict]: Ship of the inventory in each slot read, by id() of the slot: the copy in the slot for a
            ship with copies, a slot whose copy is not known for sure is left out, see plan.slot_records()
    """
    return slot_records(ships, slots, data)


def set_level(ship, level):
    """
    A ship of the inventory raised to a level. Her EXP is not known, at a level cap she waits for an awakening.
    """
    ship['level'] = level
    ship['exp'] = None
    ship['exp_next'] = 0 if level >= 100 and level % 5 == 0 else 1


class Leveling(LevelingBoost):
    def leveling_read(self, fleets):
        """
        Args:
            fleets (list[int]):

        Returns:
            list[Slot]:

        Pages:
            in: page_fleet
            out: page_fleet, the 3D view
        """
        slots = []
        for fleet in fleets:
            self.fleet_formation_ensure()
            self.formation_goto_fleet(fleet)
            slots += self.fleet_read_slots(fleet)
        self.fleet_formation_ensure()
        return slots

    def leveling_execute(self, plans, slots=(), ships=(), limit=None, save=None, minimum=None, use_packs=False):
        """
        Args:
            plans: plan_swaps() or plan_carries()
            slots (list[Slot]): All the slots read. A slot whose ship changed takes the new one, for the plans after.
            ships (list[dict]): Ships of the inventory, their gear and level are written as they change
            limit (int): Ships to come in at most, None for all
            save (callable): Writes the inventory, before a ship is given gear: if the trip to the depot ends in an
                error, she is not taken there again and again
            minimum (float): Average level the fleets of the plans keep, None for none
            use_packs (bool): If a ship short of the level the minimum takes is raised with EXP Data Packs,
                and a ship below BOOST_CAP goes to it with spare packs, see module.leveling.boost

        Returns:
            int: Ships that came in

        Pages:
            in: page_fleet, the 3D view
            out: page_fleet, the 3D view
        """
        taken, missing = set(), set()
        swapped = 0
        trips = 0
        for slot, candidates in plans:
            if limit is not None and swapped >= limit:
                break
            fleet = [other for other in slots if other.fleet == slot.fleet]
            need = level_needed(fleet, slot, minimum) if minimum is not None and fleet else 0
            for target in candidates:
                if target.character in taken or target.character in missing:
                    continue
                boost = need if target.level < need else None
                if boost is not None and (not use_packs or boost > BOOST_CAP):
                    continue
                spare = use_packs and 0 < target.level < BOOST_CAP
                if not target.geared and not GEAR_TRANSFER:
                    continue
                if not target.geared or boost is not None or spare:
                    record = next((ship for ship in ships if ship.get('name') == target.name
                                   and ship.get('index', -1) == target.index), None)
                    if trips >= GEAR_TRIES:
                        continue
                    if not target.geared and gear_tried_recently(record, datetime.now()):
                        continue
                    trips += 1
                    if record is not None and not target.geared:
                        record['gear_tried'] = datetime.now().strftime(TIME_FORMAT)
                        if save is not None:
                            save()
                    gear, level = self.ship_prepare(target, gear=not target.geared, level=boost, spare=spare)
                    self.ui_ensure(page_fleet)
                    self.fleet_formation_ensure()
                    if record is not None and (gear is not None or level is not None):
                        if gear is not None:
                            record.update(gear)
                        if level is not None:
                            set_level(record, level)
                        if save is not None:
                            save()
                    if not target.geared:
                        # Gear at +10 that passes the policy, a sonar storage could not replace keeps her out
                        if gear is None or not is_geared(gear):
                            logger.info(f'{target.ship} can not be geared from storage, gear {gear}')
                            missing.add(target.character)
                            continue
                        target.geared = True
                    if boost is not None and (level is None or level < boost):
                        logger.info(f'{target.ship} can not be raised to level {boost}, at {level}')
                        missing.add(target.character)
                        continue
                    if level is not None:
                        target.level = level
                self.formation_goto_fleet(slot.fleet)
                read_level = target.level
                if self.fleet_swap(slot, target):
                    taken.add(target.character)
                    swapped += 1
                    slot.name, slot.level = target.name, target.level
                    # The copy that came in, told apart from her others by it
                    record = next((ship for ship in ships if ship.get('name') == target.name
                                   and ship.get('index', -1) == target.index), None)
                    if record is not None:
                        mark_slot(ships, record, slot)
                        if save is not None:
                            save()
                    if target.level != read_level:
                        # Up since the dock read, fleet_swap took the level of her card
                        record = next((ship for ship in ships if ship.get('name') == target.name
                                       and ship.get('index', -1) == target.index), None)
                        if record is not None:
                            set_level(record, target.level)
                            if save is not None:
                                save()
                    break
                # In an other fleet, on a commission, or not found
                missing.add(target.character)
        return swapped

    def leveling_member(self, slot, gear=False, level=None, hull=None):
        """
        Gear from storage and EXP Data Packs for the ship in a slot.

        Args:
            slot (Slot):
            gear (bool): If she gets gear, see gear_fill()
            level (int): Level she goes to, None to leave it
            hull (str): Her hull type, for her gear

        Returns:
            tuple[dict, int]: As ship_prepare()

        Pages:
            in: page_fleet
            out: page_fleet, the 3D view
        """
        logger.hr(f'Fleet {slot.fleet} {slot.row} {slot.index + 1}: {slot.name}', level=2)
        self.fleet_formation_ensure()
        self.formation_goto_fleet(slot.fleet)
        self.fleet_ship_enter(slot)
        record = None
        if gear:
            record = self.gear_fill(hull=hull)
            if record is None:
                logger.warning(f'Gear of {slot.name} left unfinished')
                self.ui_ensure(page_fleet)
                self.fleet_formation_ensure()
                return None, None
            logger.info(f'{slot.name} has gear {record}')
        boosted = self.boost_to(level) if level is not None else None
        self.ui_back(check_button=FLEET_DETAIL_CHECK)
        self.fleet_formation_ensure()
        return record, boosted

    def leveling_gear_members(self, slots, records, save=None):
        """
        Ships of the fleets not geared get gear from storage, GEAR_TRIES of them at most: gear below +10 in a slot,
        or gear not checked against the policy, see module.leveling.plan.is_geared(). Ships at FARM_LEVEL_MAX and
        above are left out, they leave the fleets.

        Args:
            slots (list[Slot]):
            records (dict[int, dict]): inventory_ships_of()
            save (callable):

        Returns:
            int: Ships taken to the depot
        """
        trips = 0
        now = datetime.now()
        for slot in slots:
            if trips >= GEAR_TRIES:
                break
            record = records.get(id(slot))
            if record is None or is_geared(record) or gear_tried_recently(record, now):
                continue
            if slot.level >= FARM_LEVEL_MAX:
                continue
            trips += 1
            record['gear_tried'] = now.strftime(TIME_FORMAT)
            if save is not None:
                save()
            gear, _ = self.leveling_member(slot, gear=True, hull=record.get('hull'))
            if gear is not None:
                record.update(gear)
                if save is not None:
                    save()
        return trips

    def leveling_boost_members(self, slots, minimum, records, save=None, replacing=(), problems=None):
        """
        The lowest ships of a fleet below its minimum average go up with EXP Data Packs, see plan_boosts().

        Args:
            slots (list[Slot]): Slots of the fleet
            minimum (float):
            records (dict[int, dict]): inventory_ships_of()
            save (callable):
            replacing (list[Slot]): Slots whose ship leaves, see plan_boosts()
            problems (list[str]): Boosts that failed are added to it, for the assessment of the fleets

        Returns:
            int: Ships raised
        """
        raised = 0
        for slot, level in plan_boosts(slots, minimum, replacing=replacing):
            _, boosted = self.leveling_member(slot, level=level)
            if boosted is None:
                logger.warning(f'{slot.name} was not raised to level {level}')
                if problems is not None:
                    problems.append(f'EXP Data Packs did not raise {slot.name} to level {level}')
                continue
            slot.level = boosted
            raised += 1
            record = records.get(id(slot))
            if record is not None:
                set_level(record, boosted)
                if save is not None:
                    save()
        return raised

    def leveling_fleets(self):
        """
        Returns:
            tuple[list[int], list[int]]: The fleet of Main that fights the mobs and the one that fights the boss
        """
        return campaign_fleets(
            self.config.cross_get('Main.Fleet.Fleet1', default=1),
            self.config.cross_get('Main.Fleet.Fleet2', default=0),
            self.config.cross_get('Main.Fleet.FleetOrder', default='fleet1_mob_fleet2_boss'),
        )

    @staticmethod
    def leveling_log_queue(queue):
        for row in ('main', 'vanguard', 'submarine'):
            targets = [target for target in queue if target.row == row][:QUEUE_LOG]
            logger.info(f'Leveling queue, {row}: ' + ', '.join(
                f'{target.ship} Lv.{target.level}->{target.goal} ({target.chips} chips'
                f'{"" if target.geared else ", gear not checked" if target.gear_done else ", no gear"})'
                for target in targets))

    def leveling_assess(self, record, mob_slots, boss_slots, fleets, problems):
        """
        The fleets of Main as Leveling leaves them, kept in the record for Main to ask, see module.leveling.readiness.

        Returns:
            dict: The assessment
        """
        assessment = assess_fleets(mob_slots, boss_slots, record['minimum'], fleets=fleets,
                                   stage=self.config.cross_get('Main.Campaign.Name', default=None), problems=problems)
        record['assessment'] = assessment
        if assessment['ok']:
            logger.info('The fleets of Main keep the rules of Leveling')
        else:
            logger.warning('The fleets of Main break the rules of Leveling, Main holds: '
                           + '; '.join(assessment['reasons']))
        return assessment

    def leveling_delay(self, assessment):
        """
        The next run of Leveling: after CheckInterval, or in RECOVERY_MINUTES while the fleets break the rules.
        """
        interval = self.config.Leveling_CheckInterval
        if not assessment['ok']:
            self.config.task_delay(minute=min(interval, RECOVERY_MINUTES) if interval > 0 else RECOVERY_MINUTES)
        elif interval > 0:
            self.config.task_delay(minute=interval)
        else:
            self.config.task_delay(server_update=True)

    def run(self):
        record = load_record(self.config.config_name)
        minimum = record['minimum']
        use_packs = self.config.Leveling_ExpDataPacks
        # What kept the fleets from the rules, for the assessment
        problems = []
        inventory = load_inventory(self.config.config_name)
        ships = inventory.get('ships') if inventory else None

        self.ui_ensure(page_fleet)
        mob, boss = self.leveling_fleets()
        slots = self.leveling_read(mob + boss)
        if not ships:
            # The fleets are looked at all the same, Main needs to know
            logger.warning('No inventory of the dock to level ships from, run the Inventory task first')
            problems.append('no inventory of the dock to find ships in, the Inventory task reads it')
            assessment = self.leveling_assess(record, [slot for slot in slots if slot.fleet in mob],
                                              [slot for slot in slots if slot.fleet in boss], (mob, boss), problems)
            save_record(self.config.config_name, record)
            if not assessment['ok']:
                self.config.task_call('Inventory')
            self.leveling_delay(assessment)
            return
        data = load_ship_data()
        changed = update_levels(ships, slots, data)
        queue = leveling_queue(ships, data, ultra_to_125=self.config.Leveling_UltraTo125)
        self.leveling_log_queue(queue)
        carry_list = carry_targets(ships, data)
        mob_slots = [slot for slot in slots if slot.fleet in mob]
        other_slots = [slot for slot in slots if slot.fleet not in mob]
        records = inventory_ships_of(ships, slots, data)

        def save():
            save_inventory(self.config.config_name, inventory)

        # Gear at +10 for the ships of the fleets
        if GEAR_TRANSFER:
            self.leveling_gear_members(slots, records, save=save)
        # The boss fleet, ships it lets go may level in the mob fleet
        boss_slots = [slot for slot in slots if slot.fleet in boss]
        carries = plan_carries(boss_slots, slots, carry_list, data)
        swapped = self.leveling_execute(carries, slots, ships, save=save)
        for slot in boss_slots:
            if slot.name and (slot.level >= FARM_LEVEL_MAX or 0 < slot.level < CARRY_LEVEL):
                problem = (f'no geared ship of level {CARRY_LEVEL} to {FARM_LEVEL_MAX - 1} was free to take the place '
                           f'of {slot.name} Lv.{slot.level} in fleet {slot.fleet}')
                logger.warning(problem)
                problems.append(problem)
        logger.info(f'Mob fleet average level {fleet_average(mob_slots):.1f}, minimum {minimum}')
        # Ships done leave the mob fleet. The others go up first, for the fleet to keep its minimum with a ship
        # coming in at BOOST_CAP.
        done = [slot for slot in mob_slots if slot.name and slot.level >= FARM_LEVEL_MAX]
        replaced = 0
        if done:
            logger.info('Ships done in the mob fleet: ' + ', '.join(f'{slot.name} Lv.{slot.level}' for slot in done))
            if use_packs:
                self.leveling_boost_members(mob_slots, minimum, records, save=save, replacing=done,
                                            problems=problems)
            plans = plan_done(mob_slots, queue, carry_list, data, others=other_slots)
            replaced = self.leveling_execute(plans, slots, ships, save=save, minimum=minimum, use_packs=use_packs)
            swapped += replaced
            for slot in done:
                if slot.level >= FARM_LEVEL_MAX:
                    problem = (f'no ship under {FARM_LEVEL_MAX} could take the place of {slot.name} Lv.{slot.level} '
                               f'in fleet {slot.fleet} and keep the minimum average {minimum}')
                    logger.warning(problem)
                    problems.append(problem)
        # A mob fleet below its minimum average, as when a battle lost raised it
        if fleet_average(mob_slots) < minimum and use_packs:
            self.leveling_boost_members(mob_slots, minimum, records, save=save, problems=problems)
        if fleet_average(mob_slots) < minimum:
            if not use_packs:
                problems.append('EXP Data Packs are off, Leveling.ExpDataPacks')
            lift = plan_lift(mob_slots, carry_list, minimum, data, others=other_slots)
            swapped += self.leveling_execute(lift, slots, ships, limit=1, save=save)
            if fleet_average(mob_slots) < minimum:
                problems.append(f'no free ship of level {CARRY_LEVEL} to {FARM_LEVEL_MAX - 1} could lift the mob '
                                f'fleet to its minimum')
        elif not replaced:
            # The swaps that bring in the best ships first
            plans = plan_swaps(mob_slots, queue, data, others=other_slots)
            plans.sort(key=lambda plan: -plan[1][0].score)
            swapped += self.leveling_execute(plans, slots, ships, limit=MOB_SWAPS, save=save, minimum=minimum,
                                             use_packs=use_packs)
        average = fleet_average(mob_slots)
        record_average(record, average)
        assessment = self.leveling_assess(record, mob_slots, boss_slots, (mob, boss), problems)
        save_record(self.config.config_name, record)
        logger.info(f'Leveling: {swapped} ships came in, {changed} levels went up, '
                    f'mob fleet average level {average:.1f} with a minimum of {minimum}')
        if changed or swapped:
            save_inventory(self.config.config_name, inventory)
        self.leveling_delay(assessment)
