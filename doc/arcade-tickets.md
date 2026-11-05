# Weekly Arcade Tickets

Enable **Arcade Tickets** to claim Akashi's ten free weekly arcade credits and use owned credits in **Shooting Game**. The task starts a round, leaves the aiming and firing controls untouched, collects the ticket receipt, acknowledges the result, and returns home. It never exchanges gold or gems for credits and never buys prizes.

The normal batch is five credits, configurable from one to five. An idle round scores zero and earns 180 tickets per credit: normally 1,800 tickets from the weekly ten-credit grant. Scoring well can earn 260 per credit; this task deliberately uses idle play. Stored and first-visit credits can provide additional rounds.

The game limits ticket earnings to 10,000 per server month and ticket inventory to 50,000. The job acknowledges only the complete source-verified ticket-limit notices, uses single-credit batches near the limit, and stops when credits cannot yield rewards or a receipt is clipped by a cap. Unrecognized screens defer the job rather than authorize a start. Existing paid maps and commissions are unaffected; the job yields between completed arcade rounds when higher-priority work becomes due.

The next normal run is Monday at 00:00 on the account's server. It uses the server's fixed UTC offset and converts to local time at the reset date, including DST changes. A cap can schedule an earlier check at the next server-month reset. A bounded partial batch resumes later instead of postponing unfinished work for a week.

Claims and starts are recorded before input in `config/arcade/<account>.json`; screenshots and immutable receipt candidates are in `log/arcade/`. A round requires the Game Ticket artwork, readable amount, and the exact credit decrease. An unresolved start is never replayed. A retained account-owned receipt can settle a recent unresolved round only with a fresh matching credit balance; older or contradictory records require review. Keep these files together when backing up or deploying.

**Inspect Only** visits and captures the arcade, list and shooting preparation, then returns home and disables its one-shot inspection. Entering the arcade for the first time can automatically grant the game's welcome credits even in this mode. The legacy **Minigame** task should remain disabled when Arcade Tickets is enabled.

Reward and cap sources: [game-room table](https://github.com/AzurLaneTools/AzurLaneLuaScripts/blob/main/EN/sharecfg/game_room_template.lua), [credit/ticket limits](https://github.com/AzurLaneTools/AzurLaneLuaScripts/blob/main/EN/sharecfg/gameset.lua), [shooting-game timer and result flow](https://github.com/AzurLaneTools/AzurLaneLuaScripts/blob/main/EN/view/minigame/gameview/gameroomshootingview.lua), [weekly claim](https://github.com/AzurLaneTools/AzurLaneLuaScripts/blob/main/EN/controller/command/gameroom/gameroomweekcoincommand.lua).
