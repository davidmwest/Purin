"""
Keep the fleets of the campaign filled with the ships to level next, in the order of module.leveling.plan.

Ships level in the fleet that fights the mobs, 6 of the 7 battles of 12-4. Its vanguard takes one ship below
PASSENGER_LEVEL at most. The fleet that fights the boss is kept strong with ships at CARRY_LEVEL and above,
the most powerful ones free: two ships at 73 and 77 in its vanguard lost the boss of 12-4.

The queue comes from the dock as the Inventory task read it. The ships of the fleets are the ones whose levels go up,
the fleet page tells them every time and they are written back to the inventory.
A ship without gear gets gear from storage as she comes in, see module.leveling.gear.
"""
import json
from collections import Counter

from module.inventory.inventory import filepath_inventory, save_inventory
from module.leveling.gear import LevelingGear
from module.leveling.plan import PASSENGERS, campaign_fleets, carry_targets, is_passenger, leveling_queue, \
    load_ship_data, plan_carries, plan_swaps, update_levels
from module.logger import logger
from module.ui.page import page_fleet

# Ships of each row of the queue written to the log
QUEUE_LOG = 8
# Ships coming into the mob fleet in a run. The fleet changes a little at a time, a fleet too weak shows
# in a battle or two.
MOB_SWAPS = 2
# Ships without gear tried in a run, each takes a trip to the dock and the depot
GEAR_TRIES = 3


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


class Leveling(LevelingGear):
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

    def leveling_execute(self, plans, slots=(), ships=(), limit=None):
        """
        Args:
            plans: plan_swaps() or plan_carries()
            slots (list[Slot]): All the slots read, for the ships below PASSENGER_LEVEL in them
            ships (list[dict]): Ships of the inventory, those given gear are marked as equipped
            limit (int): Ships to come in at most, None for all

        Returns:
            int: Ships that came in

        Pages:
            in: page_fleet, the 3D view
            out: page_fleet, the 3D view
        """
        # Ships below PASSENGER_LEVEL in each row, as the swaps change it
        passengers = Counter((slot.fleet, slot.row) for slot in slots if slot.name and is_passenger(slot.level))
        taken, missing = set(), set()
        swapped = 0
        geared_tries = 0
        for slot, candidates in plans:
            if limit is not None and swapped >= limit:
                break
            budget = PASSENGERS.get(slot.row)
            leaving = 1 if slot.name and is_passenger(slot.level) else 0
            for target in candidates:
                if target.character in taken or target.character in missing:
                    continue
                coming = 1 if is_passenger(target.level) else 0
                if budget is not None and passengers[slot.fleet, slot.row] - leaving + coming > budget:
                    continue
                if not target.geared:
                    if geared_tries >= GEAR_TRIES:
                        continue
                    geared_tries += 1
                    if not self.gear_ship(target):
                        missing.add(target.character)
                        continue
                    target.geared = True
                    for ship in ships:
                        if ship.get('name') == target.name and ship.get('index', -1) == target.index:
                            ship['equipped'] = True
                    self.ui_ensure(page_fleet)
                    self.fleet_formation_ensure()
                self.formation_goto_fleet(slot.fleet)
                if self.fleet_swap(slot, target):
                    taken.add(target.character)
                    swapped += 1
                    passengers[slot.fleet, slot.row] += coming - leaving
                    break
                # In an other fleet, on a commission, or not found
                missing.add(target.character)
        return swapped

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
            targets = [target for target in queue if target.row == row and target.geared][:QUEUE_LOG]
            logger.info(f'Leveling queue, {row}: ' + ', '.join(
                f'{target.ship} Lv.{target.level}->{target.goal} ({target.chips} chips)' for target in targets))

    def run(self):
        inventory = load_inventory(self.config.config_name)
        if not inventory or not inventory.get('ships'):
            logger.warning('No inventory of the dock to level ships from, run the Inventory task first')
            self.config.task_delay(server_update=True)
            return
        ships = inventory['ships']
        data = load_ship_data()

        self.ui_ensure(page_fleet)
        mob, boss = self.leveling_fleets()
        slots = self.leveling_read(mob + boss)
        changed = update_levels(ships, slots, data)
        queue = leveling_queue(ships, data, ultra_to_125=self.config.Leveling_UltraTo125)
        self.leveling_log_queue(queue)
        # The boss fleet first, ships it lets go may level in the mob fleet
        carries = plan_carries([slot for slot in slots if slot.fleet in boss], slots, carry_targets(ships, data), data)
        swapped = self.leveling_execute(carries, slots, ships)
        # Then the mob fleet, the swaps that bring in the best ships first
        plans = plan_swaps([slot for slot in slots if slot.fleet in mob], queue, data,
                           others=[slot for slot in slots if slot.fleet not in mob])
        plans.sort(key=lambda plan: -plan[1][0].score)
        swapped += self.leveling_execute(plans, slots, ships, limit=MOB_SWAPS)
        logger.info(f'Leveling: {swapped} ships came in, {changed} levels went up')
        if changed or swapped:
            save_inventory(self.config.config_name, inventory)

        interval = self.config.Leveling_CheckInterval
        if interval > 0:
            self.config.task_delay(minute=interval)
        else:
            self.config.task_delay(server_update=True)
