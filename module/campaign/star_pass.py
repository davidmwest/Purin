"""
Runs of each stage by a pass for 3 stars, kept in a file: the pass stops for commissions and other tasks, and starts
again with a run count of 0.

A stage 3 stars never come to, a star misread or a count of escort fleets the fleet does not reach, would take the oil
of the account run after run. After STAR_RUNS_MAX runs the pass goes on to the next stage. The escort star asks for
12 fleets on 3-1, 15 on 4-1, 18 on 5-1 and 20 on 6-1 over every run, and a run that clears the map defeats 4 to 8.
"""
import json
import os

STAR_RUNS_MAX = 6
STAR_PASS_DIR = './config/stars'


def star_runs_path(config_name, folder=STAR_PASS_DIR):
    return os.path.join(folder, f'{config_name}.json')


def load_star_runs(config_name, folder=STAR_PASS_DIR):
    """
    Returns:
        dict[str, int]: Runs of each stage, by its name in upper case like '1-1'
    """
    try:
        with open(star_runs_path(config_name, folder), encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): int(v) for k, v in data.items() if isinstance(v, int)}


def add_star_run(config_name, stage, folder=STAR_PASS_DIR):
    """
    Returns:
        int: Runs of the stage, this one counted
    """
    runs = load_star_runs(config_name, folder)
    runs[stage] = runs.get(stage, 0) + 1
    os.makedirs(folder, exist_ok=True)
    with open(star_runs_path(config_name, folder), 'w', encoding='utf-8') as f:
        json.dump(runs, f, indent=1, sort_keys=True)
    return runs[stage]
