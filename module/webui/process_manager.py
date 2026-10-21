import argparse
import os
import queue
import threading
import time
from multiprocessing import Process
from typing import Dict, List, Optional, Union

import inflection
from rich.console import Console, ConsoleRenderable

# Since this file does not run under the same process or subprocess of app.py
# the following code needs to be repeated
# Import fake module before import pywebio to avoid importing unnecessary module PIL
from module.webui.fake_pil_module import *

import_fake_pil_module()

from module.config.scheduler_state import read_scheduler_state, remove_scheduler_state
from module.config.utils import filepath_pause
from module.logger import logger, set_file_logger, set_func_logger
from module.submodule.submodule import load_mod
from module.submodule.utils import get_available_func, get_available_mod, get_available_mod_func, get_config_mod, \
    get_func_mod, list_mod_instance
from module.webui.external import LogFileFollower, STATUS_INACTIVE, latest_log_file, log_line_renderable, \
    scheduler_status
from module.webui.setting import State


class ProcessManager:
    _processes: Dict[str, "ProcessManager"] = {}

    def __init__(self, config_name: str = "alas") -> None:
        self.config_name = config_name
        self._renderable_queue: queue.Queue[ConsoleRenderable] = State.manager.Queue()
        self.renderables: List[ConsoleRenderable] = []
        self.renderables_max_length = 400
        self.renderables_reduce_length = 80
        self._process: Process = None
        self._process_locks: Dict[str, threading.Lock] = {}
        self.thd_log_queue_handler: threading.Thread = None
        self.thd_log_file_follower: threading.Thread = None

    def start(self, func, ev: threading.Event = None) -> None:
        if self.external is not None:
            logger.warning(f"[{self.config_name}] is running outside the GUI already, not starting another one")
            return
        if not self.alive:
            if func is None:
                func = get_config_mod(self.config_name)
            self._process = Process(
                target=ProcessManager.run_process,
                args=(
                    self.config_name,
                    func,
                    self._renderable_queue,
                    ev,
                ),
            )
            self._process.start()
            self.start_log_queue_handler()

    def start_log_queue_handler(self):
        if (
            self.thd_log_queue_handler is not None
            and self.thd_log_queue_handler.is_alive()
        ):
            return
        self.thd_log_queue_handler = threading.Thread(
            target=self._thread_log_queue_handler
        )
        self.thd_log_queue_handler.start()

    def stop(self) -> None:
        try:
            lock = self._process_locks[self.config_name]
        except KeyError:
            lock = threading.Lock()
            self._process_locks[self.config_name] = lock

        with lock:
            if self.alive:
                self._process.kill()
                self.renderables.append(
                    f"[{self.config_name}] exited. Reason: Manual stop\n"
                )
            if self.thd_log_queue_handler is not None:
                self.thd_log_queue_handler.join(timeout=1)
                if self.thd_log_queue_handler.is_alive():
                    logger.warning(
                        "Log queue handler thread does not stop within 1 seconds"
                    )
        logger.info(f"[{self.config_name}] exited")

    def _thread_log_queue_handler(self) -> None:
        while self.alive:
            try:
                log = self._renderable_queue.get(timeout=1)
            except queue.Empty:
                continue
            self.renderables.append(log)
            if len(self.renderables) > self.renderables_max_length:
                self.renderables = self.renderables[self.renderables_reduce_length :]
        logger.info("End of log queue handler loop")

    @property
    def alive(self) -> bool:
        if self._process is not None:
            return self._process.is_alive()
        else:
            return False

    @property
    def external(self) -> Optional[dict]:
        """
        What a scheduler of this config started outside the GUI, from a terminal or a script, tells,
        see module/config/scheduler_state.py. None if there is none.
        """
        state = read_scheduler_state(self.config_name)
        if state is None:
            return None
        if self._process is not None and state["pid"] == self._process.pid:
            return None
        self.follow_log_file()
        return state

    @property
    def pause_requested(self) -> bool:
        return os.path.exists(filepath_pause(self.config_name))

    def pause(self) -> None:
        """
        Hold the scheduler between tasks, the task running now ends after its current sortie.
        """
        with open(filepath_pause(self.config_name), "w"):
            pass
        logger.info(f"[{self.config_name}] Pause requested")

    def resume(self) -> None:
        try:
            os.remove(filepath_pause(self.config_name))
        except FileNotFoundError:
            pass
        logger.info(f"[{self.config_name}] Resumed")

    def stop_external(self) -> None:
        """
        Stop a scheduler started outside the GUI, only while it holds between tasks.
        Killed in the middle of a sortie, it would leave the game playing on its own.
        Not part of stop(), closing the GUI leaves such a scheduler running.
        """
        state = self.external
        if state is None or not state.get("paused"):
            return
        import psutil
        try:
            psutil.Process(state["pid"]).kill()
        except psutil.Error as e:
            logger.warning(f"[{self.config_name}] Failed to stop: {e}")
            return
        # Only the file of the scheduler killed, one started since then keeps its own
        remove_scheduler_state(self.config_name, owner=state["pid"])
        self.renderables.append(f"[{self.config_name}] exited. Reason: Manual stop\n")

    def follow_log_file(self) -> None:
        """
        Show the log of a scheduler started outside the GUI, by following its log file.
        """
        if (
            self.thd_log_file_follower is not None
            and self.thd_log_file_follower.is_alive()
        ):
            return
        self.thd_log_file_follower = threading.Thread(
            target=self._thread_log_file_follower, daemon=True
        )
        self.thd_log_file_follower.start()

    def _thread_log_file_follower(self) -> None:
        follower = None
        manual_stop = f"[{self.config_name}] exited. Reason: Manual stop\n"
        while True:
            state = read_scheduler_state(self.config_name)
            if state is None:
                if self.renderables[-1:] != [manual_stop]:
                    self.renderables.append(f"[{self.config_name}] exited outside the GUI\n")
                break
            if self._process is not None and state["pid"] == self._process.pid:
                break
            # The scheduler starts a new file on the day it starts
            file = latest_log_file(self.config_name)
            if file is not None and (follower is None or follower.file != file):
                follower = LogFileFollower(file)
            if follower is not None:
                for line in follower.read():
                    self.renderables.append(log_line_renderable(line))
                    if len(self.renderables) > self.renderables_max_length:
                        self.renderables = self.renderables[self.renderables_reduce_length:]
            time.sleep(0.5)
        logger.info("End of log file follower loop")

    @property
    def state(self) -> int:
        alive = self.alive
        scheduler = read_scheduler_state(self.config_name)
        if scheduler is not None and not alive:
            self.follow_log_file()
        status = scheduler_status(alive, scheduler, self.pause_requested)
        if status is not None:
            return status
        elif len(self.renderables) == 0:
            return 2
        else:
            console = Console(no_color=True)
            with console.capture() as capture:
                console.print(self.renderables[-1])
            s = capture.get().strip()
            if s.endswith("Reason: Manual stop"):
                return 2
            elif s.endswith("Reason: Finish"):
                return 2
            elif s.endswith("Reason: Update"):
                return 4
            elif s.endswith("exited outside the GUI"):
                return STATUS_INACTIVE
            else:
                return 3

    @classmethod
    def get_manager(cls, config_name: str) -> "ProcessManager":
        """
        Create a new alas if not exists.
        """
        if config_name not in cls._processes:
            cls._processes[config_name] = ProcessManager(config_name)
        return cls._processes[config_name]

    @staticmethod
    def run_process(
        config_name, func: str, q: queue.Queue, e: threading.Event = None
    ) -> None:
        parser = argparse.ArgumentParser()
        parser.add_argument(
            "--electron", action="store_true", help="Runs by electron client."
        )
        args, _ = parser.parse_known_args()
        State.electron = args.electron

        # Setup logger
        set_file_logger(name=config_name)
        if State.electron:
            # https://github.com/LmeSzinc/AzurLaneAutoScript/issues/2051
            logger.info("Electron detected, remove log output to stdout")
            from module.logger import console_hdlr
            logger.removeHandler(console_hdlr)
        set_func_logger(func=q.put)

        from module.config.config import AzurLaneConfig

        # Remove fake PIL module, because subprocess will use it
        remove_fake_pil_module()

        AzurLaneConfig.stop_event = e
        try:
            # Run alas
            if func == "alas":
                from alas import AzurLaneAutoScript

                if e is not None:
                    AzurLaneAutoScript.stop_event = e
                AzurLaneAutoScript(config_name=config_name).loop()
            elif func in get_available_func():
                from alas import AzurLaneAutoScript

                AzurLaneAutoScript(config_name=config_name).run(inflection.underscore(func), skip_first_screenshot=True)
            elif func in get_available_mod():
                mod = load_mod(func)

                if e is not None:
                    mod.set_stop_event(e)
                mod.loop(config_name)
            elif func in get_available_mod_func():
                getattr(load_mod(get_func_mod(func)), inflection.underscore(func))(config_name)
            else:
                logger.critical(f"No function matched: {func}")
            logger.info(f"[{config_name}] exited. Reason: Finish\n")
        except Exception as e:
            logger.exception(e)

    @classmethod
    def running_instances(cls) -> List["ProcessManager"]:
        l = []
        for process in cls._processes.values():
            if process.alive:
                l.append(process)
        return l

    @staticmethod
    def restart_processes(
        instances: List[Union["ProcessManager", str]] = None, ev: threading.Event = None
    ):
        """
        After update and reload, or failed to perform an update,
        restart all alas that running before update
        """
        logger.hr("Restart alas")

        # Load MOD_CONFIG_DICT
        list_mod_instance()

        if instances is None:
            instances = []

        _instances = set()

        for instance in instances:
            if isinstance(instance, str):
                _instances.add(ProcessManager.get_manager(instance))
            elif isinstance(instance, ProcessManager):
                _instances.add(instance)

        try:
            with open("./config/reloadalas", mode="r") as f:
                for line in f.readlines():
                    line = line.strip()
                    _instances.add(ProcessManager.get_manager(line))
        except FileNotFoundError:
            pass

        for process in _instances:
            logger.info(f"Starting [{process.config_name}]")
            process.start(func=get_config_mod(process.config_name), ev=ev)

        try:
            os.remove("./config/reloadalas")
        except:
            pass
        logger.info("Start alas complete")
