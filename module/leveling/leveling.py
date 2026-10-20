"""
Keep the fleets of the campaign filled with the ships to level next, in the order of module.leveling.plan.

Ships level in the fleet that fights the mobs, 6 of the 7 battles of 12-4. Its vanguard takes one ship below
PASSENGER_LEVEL at most. The fleet that fights the boss is kept strong with ships at CARRY_LEVEL and above,
the most powerful ones free: two ships at 73 and 77 in its vanguard lost the boss of 12-4.

The queue comes from the dock as the Inventory task read it. The ships of the fleets are the ones whose levels go up,
the fleet page tells them every time and they are written back to the inventory.
Ships come in from the dock with their gear, a ship without gear stays in the queue until she has some.
"""
import json
from collections import Counter

from module.inventory.inventory import filepath_inventory, save_inventory
from module.leveling.fleet import LevelingFleet
from module.leveling.plan import PASSENGERS, campaign_fleets, carry_targets, is_passenger, leveling_queue, \
    load_ship_data, plan_carries, plan_swaps, update_levels
from module.logger import logger
from module.ui.page import page_fleet

# Ships of each row of the queue written to the log
QUEUE_LOG = 8


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


class Leveling(LevelingFleet):
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

    def leveling_execute(self, plans, slots=()):
        """
        Args:
            plans: plan_swaps() and plan_carries()
            slots (list[Slot]): All the slots read, for the ships below PASSENGER_LEVEL that stay in them

        Returns:
            int: Ships that came in

        Pages:
            in: page_fleet, the 3D view
            out: page_fleet, the 3D view
        """
        planned = {(slot.fleet, slot.row, slot.index) for slot, _ in plans}
        passengers = Counter(
            (slot.fleet, slot.row) for slot in slots
            if (slot.fleet, slot.row, slot.index) not in planned and slot.name and is_passenger(slot.level))
        taken, missing = set(), set()
        swapped = 0
        for slot, candidates in plans:
            came = None
            limit = PASSENGERS.get(slot.row)
            for target in candidates:
                if target.character in taken or target.character in missing:
                    continue
                if limit is not None and is_passenger(target.level) and passengers[slot.fleet, slot.row] >= limit:
                    continue
                self.formation_goto_fleet(slot.fleet)
                if self.fleet_swap(slot, target):
                    came = target
                    break
                # In an other fleet, on a commission, or not found
                missing.add(target.character)
            if came is not None:
                taken.add(came.character)
                swapped += 1
                if is_passenger(came.level):
                    passengers[slot.fleet, slot.row] += 1
            elif slot.name and is_passenger(slot.level):
                # She stays
                passengers[slot.fleet, slot.row] += 1
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
        plans = plan_carries([slot for slot in slots if slot.fleet in boss], slots, carry_targets(ships, data), data)
        plans += plan_swaps([slot for slot in slots if slot.fleet in mob], queue, data,
                            others=[slot for slot in slots if slot.fleet not in mob])
        swapped = self.leveling_execute(plans, slots)
        logger.info(f'Leveling: {swapped} ships came in, {changed} levels went up')
        if changed or swapped:
            save_inventory(self.config.config_name, inventory)

        interval = self.config.Leveling_CheckInterval
        if interval > 0:
            self.config.task_delay(minute=interval)
        else:
            self.config.task_delay(server_update=True)
