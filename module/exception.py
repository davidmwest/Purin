class CampaignEnd(Exception):
    pass


class OilExhausted(Exception):
    pass


class OilMaxed(Exception):
    pass


class MapDetectionError(Exception):
    pass


class MapWalkError(Exception):
    pass


class MapEnemyMoved(Exception):
    pass


class CampaignNameError(Exception):
    pass


class ScriptError(Exception):
    # This is likely to be a mistake of developers, but sometimes a random issue
    pass


class ScriptEnd(Exception):
    pass


class GameStuckError(Exception):
    pass


class GameBugError(Exception):
    # An error has occurred in Azur Lane game client. Alas is unable to handle.
    # A restart should fix it.
    pass


class GameTooManyClickError(Exception):
    pass


class EmulatorNotRunningError(Exception):
    pass


class GameLoggedOutError(Exception):
    # The game went back to its login page during a task, most likely because the account
    # logged in on another device
    pass


class GameNotRunningError(Exception):
    pass


class GamePageUnknownError(Exception):
    pass


class UserPlaying(Exception):
    # Someone touched the emulator, Purin does not tap over them.
    # The task runs again once nobody has touched it for Optimization.UserInputWait seconds.
    pass


class RequestHumanTakeover(Exception):
    # Request human takeover
    # Alas is unable to handle such error, probably because of wrong settings.
    pass


class HardNotSatisfied(RequestHumanTakeover):
    pass
