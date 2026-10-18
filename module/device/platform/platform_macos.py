import os
import re
import subprocess
import typing as t
from dataclasses import dataclass

import psutil

from module.base.decorator import cached_property, run_once
from module.base.timer import Timer
from module.device.connection import AdbDeviceWithStatus
from module.device.method.utils import get_serial_pair
from module.device.platform.emulator_base import EmulatorInstanceBase
from module.device.platform.platform_base import PlatformBase
from module.logger import logger

BLUESTACKS_APP = '/Applications/BlueStacks.app'
BLUESTACKS_CONF = '/Users/Shared/Library/Application Support/BlueStacks/bluestacks.conf'


# eq=False to keep __eq__ and __hash__ of EmulatorInstanceBase
@dataclass(eq=False)
class EmulatorInstanceMacOS(EmulatorInstanceBase):
    # Name shown in the multi-instance manager, like "BlueStacks Azur Lane"
    display_name: str = ''

    @property
    def type(self) -> str:
        return 'BlueStacksAir'

    def __str__(self):
        return f'{self.type}(serial="{self.serial}", name="{self.name}", display_name="{self.display_name}")'


def iter_bluestacks_instances(content, path=BLUESTACKS_APP) -> t.Iterable[EmulatorInstanceMacOS]:
    """
    Args:
        content (str): Content of bluestacks.conf
        path (str): BlueStacks app that runs the instances

    Yields:
        EmulatorInstanceMacOS: Instances of BlueStacks Air, each has an ADB port of its own
    """
    # bst.instance.Tiramisu64_12.status.adb_port="5675" is the port in use,
    # bst.instance.Tiramisu64_12.adb_port="5675" the one it was given
    ports = dict(re.findall(r'bst\.instance\.(\w+)\.adb_port="(\d+)"', content))
    ports.update(re.findall(r'bst\.instance\.(\w+)\.status\.adb_port="(\d+)"', content))
    names = dict(re.findall(r'bst\.instance\.(\w+)\.display_name="([^"]*)"', content))
    for name, port in ports.items():
        yield EmulatorInstanceMacOS(
            serial=f'127.0.0.1:{port}',
            name=name,
            path=path,
            display_name=names.get(name, ''),
        )


def is_instance_cmdline(cmdline, name) -> bool:
    """
    Args:
        cmdline (list[str]): Like ['/Applications/BlueStacks.app/Contents/MacOS/BlueStacks', '--instance', 'Tiramisu64_12']
        name (str): Instance name

    Returns:
        bool: If this is the process of the given instance.
            Every instance is a process of its own, and the multi-instance manager is another one.
    """
    if not cmdline or os.path.basename(cmdline[0]) != 'BlueStacks':
        return False
    for index, arg in enumerate(cmdline[:-1]):
        if arg == '--instance' and cmdline[index + 1] == name:
            return True
    return False


def instance_processes(name) -> t.List[psutil.Process]:
    """
    Args:
        name (str): Instance name

    Returns:
        list[psutil.Process]: Running processes of the instance, usually one
    """
    out = []
    for proc in psutil.process_iter(['cmdline']):
        if is_instance_cmdline(proc.info.get('cmdline'), name):
            out.append(proc)
    return out


class PlatformMacOS(PlatformBase):
    """
    Starts and stops instances of BlueStacks Air.
    Each instance is a process of its own, an instance is stopped alone, never the whole app.
    """

    @cached_property
    def all_emulator_instances(self) -> t.List[EmulatorInstanceMacOS]:
        try:
            with open(BLUESTACKS_CONF, encoding='utf-8') as f:
                content = f.read()
        except (FileNotFoundError, PermissionError) as e:
            logger.warning(f'Cannot read {BLUESTACKS_CONF}: {e}')
            return []
        path = BLUESTACKS_APP if os.path.exists(BLUESTACKS_APP) else 'BlueStacks'
        return list(iter_bluestacks_instances(content, path=path))

    @cached_property
    def emulator_instance(self) -> t.Optional[EmulatorInstanceMacOS]:
        """
        BlueStacks Air gives every instance an ADB port of its own, so the serial tells the instance.
        Unlike Windows, the instance is not written to EmulatorInfo, whose options are emulators of Windows.

        Returns:
            EmulatorInstanceMacOS: Emulator instance or None
        """
        logger.hr('Find emulator instance', level=2)
        serial = self.serial
        port_serial, _ = get_serial_pair(serial)
        if port_serial is not None:
            serial = port_serial
        for instance in self.all_emulator_instances:
            if instance.serial == serial:
                logger.info(f'Found emulator instance: {instance}')
                return instance
        logger.warning(f'No BlueStacks Air instance with serial "{serial}" in {BLUESTACKS_CONF}')
        return None

    def _emulator_start(self, instance: EmulatorInstanceMacOS):
        # -g to start it in the background, without taking the focus
        # -n to start another instance while the app is running other ones
        command = ['open', '-g', '-n', '-a', instance.path, '--args', '--instance', instance.name]
        logger.info(f'Execute: {" ".join(command)}')
        subprocess.run(command, check=True, timeout=30, capture_output=True)

    def _emulator_stop(self, instance: EmulatorInstanceMacOS):
        procs = instance_processes(instance.name)
        if not procs:
            logger.info(f'Emulator instance {instance.name} is not running')
        for proc in procs:
            logger.info(f'Terminate {instance.name}, pid={proc.pid}')
            try:
                proc.terminate()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(procs, timeout=30)
        for proc in alive:
            logger.warning(f'{instance.name} did not end in 30s, kill it, pid={proc.pid}')
            try:
                proc.kill()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(alive, timeout=10)
        if alive:
            raise OSError(f'Failed to stop {instance.name}, pids={[proc.pid for proc in alive]}')
        # Forget the connection to the closed instance
        self.adb_client.disconnect(instance.serial)

    def _emulator_function_wrapper(self, func: callable):
        """
        Args:
            func (callable): _emulator_start or _emulator_stop

        Returns:
            bool: If success
        """
        instance = self.emulator_instance
        if instance is None:
            logger.error(f'Emulator function {func.__name__}() failed, no emulator instance')
            return False
        try:
            func(instance)
            return True
        except Exception as e:
            logger.exception(e)

        logger.error(f'Emulator function {func.__name__}() failed')
        return False

    def emulator_start_watch(self):
        """
        Returns:
            bool: True if startup completed
                False if timeout
        """
        logger.hr('Emulator start', level=2)
        serial = self.emulator_instance.serial

        def adb_connect():
            m = self.adb_client.connect(serial)
            # Connected to 127.0.0.1:5675
            # failed to connect to '127.0.0.1:5675': Connection refused
            return 'connected' in m and 'failed' not in m

        @run_once
        def show_online(m):
            logger.info(f'Emulator online: {m}')

        @run_once
        def show_ping(m):
            logger.info(f'Command ping: {m}')

        @run_once
        def show_package(m):
            logger.info(f'Found azurlane packages: {m}')

        interval = Timer(0.5).start()
        timeout = Timer(180).start()
        while 1:
            interval.wait()
            interval.reset()
            if timeout.reached():
                logger.warning(f'Emulator start timeout')
                return False

            # Check device connection
            devices = self.list_device().select(serial=serial)
            if devices:
                device: AdbDeviceWithStatus = devices.first_or_none()
                if device.status == 'offline':
                    self.adb_client.disconnect(serial)
                    adb_connect()
                    continue
            else:
                adb_connect()
                continue
            show_online(devices.first_or_none())

            # Check command availability
            try:
                pong = self.adb_shell(['echo', 'pong'])
            except Exception as e:
                logger.info(e)
                continue
            show_ping(pong)

            # Android has finished booting
            try:
                booted = self.adb_shell(['getprop', 'sys.boot_completed']).strip()
            except Exception as e:
                logger.info(e)
                continue
            if booted != '1':
                continue

            # Check azurlane package
            packages = self.list_known_packages(show_log=False)
            if not len(packages):
                continue
            show_package(packages)

            # All check passed
            break

        logger.info('Emulator start completed')
        return True

    def emulator_start(self):
        logger.hr('Emulator start', level=1)
        for _ in range(3):
            # Stop an instance that runs but does not answer
            if not self._emulator_function_wrapper(self._emulator_stop):
                return False
            # Start
            if self._emulator_function_wrapper(self._emulator_start):
                if self.emulator_start_watch():
                    return True
                else:
                    # Start command was sent but emulator didn't come online, stop and start again
                    continue
            else:
                # Failed to start, stop and start again
                if self._emulator_function_wrapper(self._emulator_stop):
                    continue
                else:
                    return False

        logger.error('Failed to start emulator 3 times, stopped')
        return False

    def emulator_stop(self):
        logger.hr('Emulator stop', level=1)
        for _ in range(3):
            if self._emulator_function_wrapper(self._emulator_stop):
                return True

        logger.error('Failed to stop emulator 3 times, stopped')
        return False
