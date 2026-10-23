"""Scheduler entry point for archive completion and targeted guaranteed prizes."""
from module.base.base import ModuleBase
from module.logger import logger
from module.war_archives.progress import ArchiveProgress


class WarArchivesComplete(ModuleBase):
    def run(self, prize_only=False):
        observer = ArchiveProgress(config=self.config, device=self.device)
        observation = observer.run_observe()
        logger.attr('WarArchivesObservation', observation)
        # Combat is gated while the current archive layout is being observed.
        # No guessed entry, completion percentage, or reward counter is valid.
        self.config.task_delay(server_update=True)
