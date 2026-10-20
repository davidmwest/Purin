"""
Keep the fleets of the campaign filled with the ships to level next, in the order of module.leveling.plan.

The queue comes from the dock as the Inventory task read it. The ships of the fleets are the ones whose levels go up,
the fleet page tells them every time and they are written back to the inventory.
Ships come in from the dock with their gear, a ship without gear stays in the queue until she has some.
"""
import json

from module.inventory.inventory import filepath_inventory, save_inventory
from module.leveling.fleet import LevelingFleet
from module.leveling.plan import leveling_queue, load_ship_data, plan_swaps, update_levels
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

    def leveling_execute(self, plans):
        """
        Args:
            plans: plan_swaps()

        Returns:
            int: Ships that came in

        Pages:
            in: page_fleet, the 3D view
            out: page_fleet, the 3D view
        """
        taken, missing = set(), set()
        swapped = 0
        for slot, candidates in plans:
            for target in candidates:
                if target.character in taken or target.character in missing:
                    continue
                self.formation_goto_fleet(slot.fleet)
                if self.fleet_swap(slot, target):
                    taken.add(target.character)
                    swapped += 1
                    break
                # In an other fleet, on a commission, or not found
                missing.add(target.character)
        return swapped

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
        slots = self.leveling_read(self.formation_fleets())
        changed = update_levels(ships, slots, data)
        queue = leveling_queue(ships, data, ultra_to_125=self.config.Leveling_UltraTo125)
        self.leveling_log_queue(queue)
        plans = plan_swaps(slots, queue, data)
        swapped = self.leveling_execute(plans)
        logger.info(f'Leveling: {swapped} ships came in, {changed} levels went up')
        if changed or swapped:
            save_inventory(self.config.config_name, inventory)

        interval = self.config.Leveling_CheckInterval
        if interval > 0:
            self.config.task_delay(minute=interval)
        else:
            self.config.task_delay(server_update=True)
