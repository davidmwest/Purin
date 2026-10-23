# War Archives catalog

Reviewed for the EN client on **2026-10-23**. This catalog contains **52 current archives**, **48 existing local campaign mappings**, and **38 one-time guaranteed counter prizes**. The four archives without local maps are listed for selection visibility; Purin must defer them rather than invent a campaign folder.

## Data Keys and daily policy

The current ordinary chapter path consumes **5 keys per fresh stage entry**, grants **20 raw keys daily**, and holds at most **300**. Thus the daily grant funds four fresh sorties; stored keys permit additional sorties. This is **not a timed access permit**. The retained legacy `reactivity_time=900` setting and old guides about fifteen-minute access do not describe the current entry debit. The official July 9 update confirms the fivefold key-unit change; the current client entry command establishes the actual five-key debit. The new activity-based archives use a separate helper and remain unsupported here.

Purin's requested policy is four daily sorties, followed by extra sorties only while oil is at least the configured **5,000 reserve**. Daily sorties retain a **500 oil safety floor**; extra sorties use `max(500, ExtraOilReserve)`, not the sum of both numbers. The runtime checks oil before entry and between battles. These thresholds do not promise that the whole sortie's final oil cost has already been paid or predicted.

Both new scheduler jobs share one account-wide daily ledger, so enabling both does not grant eight daily sorties. Losses, crashes and uncertain entries remain spent reservations. Each fresh entry still requires a verified five-key balance; a configured daily allowance does not create keys.

## Configuration and scheduler jobs

`WarArchivesComplete` and `WarArchivesPrize` are separate jobs and are **disabled by default**. Configure them in the selected account's Purin GUI, using its own fleets and scheduler. The legacy `WarArchives` farming job remains separate and does not use this planner's shared reservation policy.

| Archive setting | Default | Meaning |
| --- | --- | --- |
| `Target` | `all` | All supported catalog entries in catalog order, one exact EN title, or one exact local campaign folder from the table. |
| `Difficulty` | `hard` | `easy` selects Normal; `hard` selects Hard where it exists and the sole Normal route otherwise. It does not switch modes to obtain an inaccessible prize. |
| `Goal` | `stories` | `stories` satisfies campaign completion; `items_and_stories` then farms and claims that archive's accessible guaranteed prize. |
| `Prize` | `auto` | All guaranteed prizes in scope, or one exact reward name/numeric reward ID. This does not select random drops. |
| `DailyTickets` | `4` | Daily-reserve sorties, 0–4, shared by the two jobs. Later entries require the extra oil reserve. |
| `ExtraOilReserve` | `5000` | Minimum oil for extra entries and between their battles, subject to the 500 safety floor. |
| `StagesPerRun` | `4` | Maximum reserved sorties in one scheduler chunk; runtime bounds this to 1–20. Read-only inspections do not consume sorties. |
| `ObserveOnly` | `false` | When true, inspect known UI without reserving keys, starting a sortie or claiming a guaranteed prize. |

For the requested account policy, main `alas` uses `Difficulty=hard`, `Goal=stories`; F2P uses `Difficulty=easy`, `Goal=items_and_stories`. These are account choices, not different global defaults. A Hard-only guarantee remains explicitly deferred on the Easy account.

For a specific guaranteed ship, set `WarArchivesPrize` to `Target=Visitors Dyed in Red`, `Difficulty=hard`, `Prize=Zuikaku` (or reward ID `307061`). This job first satisfies that archive's campaign-completion goal, then observes and advances the Hard D3 counter to its verified 60-clear claim. For an Easy-accessible item, choose `Target=Passionate Polaris`, `Prize=Cosmic Kicks`; its single route is Normal SP1–SP5. Selecting one target does not farm unrelated archives. `WarArchivesComplete` includes prizes only when its `Goal=items_and_stories`; the Prize job requests them regardless of `Goal`.

The scheduler resumes bounded chunks from `config/war_archives/<account>.json`. Stage completion, shared spending reservations and prize progress remain separate. Completion waits until the next server update; a full chunk yields briefly to other jobs. Morale, loss, oil and preemption can defer work. Do not delete the ledger to retry an uncertain entry: that would discard its spending history.

Initial UI support is deliberately conservative: archive identities, list completion and key balances have captured readers; exact stage-unlock/progress and guaranteed-counter/claim readers still require observed UI fixtures. Until those execution proofs are available, the job records a diagnostic and blocks instead of starting or claiming by guessed coordinates. Unsupported catalog entries are reported by name, including when `Target=all`; “all” does not mean those four archives have been completed.

## Routes, unlocks and completion

`EX` and `SP` below are archive-list categories, not battle difficulty. Most SP archives have a single route recorded as Normal. **Interlude of Illusions is an exception:** it has Normal T1–T3 and Hard HT1–HT3. Hard may fall back to the sole Normal route only when an SP archive truly has no Hard stages.

Stage lists retain the client chapter order. AS/BS/CS/DS/TS/HTS nodes must not be skipped merely because their names end in S: some are actual battle maps. Stage codes are lowercase local campaign identifiers; the game displays their uppercase equivalents.

The client checks predecessor chapter groups, prerequisite story chapters and commander-level requirements separately. Hard preparation also enforces fleet composition and aggregate-stat requirements. These are live account gates, not assumptions that all Hard routes require all Normal stages first. The static catalog does not prove that a stage is unlocked, that a fleet can enter it, or that an account has completed it. No unverified global commander-level unlock is asserted here.

For this feature, the user's **story goal means beating the archive's Normal OR Hard campaign to 100%**. A fresh, identity-bound 100% archive-list reading satisfies that goal and skips replay of the other route. Otherwise Purin completes the configured route from verified stage evidence. Three stars and reading every memory are not required. The game does track memory playback separately; the planner's `stories` completion flag makes no claim that every memory was opened. Unsupported activity-based archives can have dedicated story-unlock behavior and cannot be run as ordinary chapter maps.

Campaign completion and guaranteed-prize completion are independent. An existing campaign 100% reading skips story progression, but never invents a 60-clear counter or a claimed reward. A prize requires a fresh verified counter, a verified increment after each boss win, and a verified final claim.

## Guaranteed prizes

The final column contains only the current client `drop_gain` counter rewards: **one claim after 60 clears of the stated stage and mode**, subject to the account's existing counter/claimed state. It excludes random ship/equipment drops, `drop_display` previews, first-clear rewards, three-star rewards and former event exchange rewards. A dash means no configured 60-clear `drop_gain` prize in this source snapshot, not that the archive has no random drops or other benefits. Equipment names are marked explicitly.

All supported folder names below are exact existing paths under `campaign/`. “Unsupported” means no existing local folder/navigation mapping.

| EN archive title | List category | Local campaign folder | Normal route | Hard route | Guaranteed 60-clear prize |
| --- | --- | --- | --- | --- | --- |
| Visitors Dyed in Red | EX | `war_archives_20190321_en` | a1, a2, a3, b1, b2, b3 | c1, c2, c3, d1, d2, d3 | Zuikaku — Hard `d3`, 60 clears |
| Strive, Wish and Strategize | SP | `war_archives_20181020_en` | sp1, sp2, sp3 | — | — |
| Winter's Crown | EX | `war_archives_20190221_en` | a1, a2, a3, b1, b2, b3 | c1, c2, c3, d1, d2, d3 | Duke of York — Hard `d3`, 60 clears |
| The Pursuit of Graf Spee | SP | `war_archives_20191010_en` | sp1, sp2, sp3 | — | — |
| Divergent Chessboard | EX | `war_archives_20191031_en` | a1, a2, a3, a4, b1, b2, b3, b4 | c1, c2, c3, c4, d1, d2, d3, d4 | Z46 — Hard `d4`, 60 clears |
| Fallen Wings | EX | `war_archives_20181026_en` | a1, a2, a3, b1, b2, b3 | c1, c2, c3, d1, d2, d3 | North Carolina — Hard `d3`, 60 clears |
| Iris of Light and Dark | EX | `war_archives_20180726_cn` | a1, a2, a3, b1, b2, b3 | c1, c2, c3, d1, d2, d3 | Le Triomphant — Hard `d3`, 60 clears |
| Ink-Stained Steel Sakura | EX | `war_archives_20180607_cn` | a1, a2, a3, a4, b1, b2 | c1, c2, c3, c4, d1, d2 | Kawakaze — Hard `d2`, 60 clears |
| Glorious Battle | SP | `war_archives_20190620_en` | sp1, sp2, sp3 | — | — |
| Crimson Echoes | EX | `war_archives_20181227_cn` | a1, a2, a3, b1, b2, b3 | c1, c2, c3, d1, d2, d3 | Kaga(BB) — Hard `d3`, 60 clears |
| Moonlit Overture | SP | `war_archives_20190314_en` | sp1, sp2, sp3 | — | — |
| Scherzo of Iron and Blood | EX | `war_archives_20200820_cn` | a1, a2, a3, b1, b2, b3 | c1, c2, c3, d1, d2, d3 | King George V — Hard `d3`, 60 clears |
| Passionate Polaris | SP | `war_archives_20211014_cn` | sp1, sp2, sp3, sp4, sp5 | — | Cosmic Kicks (equipment) — Normal `sp5`, 60 clears |
| Empyreal Tragicomedy | EX | `war_archives_20190911_cn` | a1, a2, a3, as1, as2, b1, b2, b3 | c1, c2, c3, cs1, cs2, d1, d2, d3 | Littorio — Hard `d3`, 60 clears |
| The Solomon Ranger | SP | `war_archives_20200312_cn` | sp1, sp2, sp3 | — | — |
| Ashen Simulacrum | EX | `war_archives_20210325_cn` | a1, a2, a3, b1, b2, b3, as1, as2, bs1, bs2 | c1, c2, c3, d1, d2, d3, cs1, cs2, ds1, ds2 | Cavalla — Hard `d3`, 60 clears |
| Swirling Cherry Blossoms | EX | `war_archives_20210624_cn` | a1, a2, a3, b1, b2, b3 | c1, c2, c3, d1, d2, d3 | Noshiro — Hard `d3`, 60 clears |
| The Enigma and the Shark | SP | `war_archives_20200806_cn` | sp1, sp2, sp3 | — | — |
| Universe in Unison | SP | `war_archives_20201029_cn` | sp1, sp2, sp3, sp4, sp5 | — | Celestial Body (equipment) — Normal `sp5`, 60 clears |
| Microlayer Medley | EX | `war_archives_20210819_cn` | a1, a2, a3, b1, b2, b3 | c1, c2, c3, d1, d2, d3 | Bremerton — Hard `d3`, 60 clears |
| Stars of the Shimmering Fjord | SP | `war_archives_20200903_cn` | sp0, sp1, sp2, sp3 | — | — |
| Skybound Oratorio | EX | `war_archives_20211028_cn` | a1, a2, a3, b1, b2, b3 | c1, c2, c3, d1, d2, d3 | Jeanne d'Arc — Hard `d3`, 60 clears |
| The Way Home in the Dark | SP | `war_archives_20200507_cn` | sp1, sp2, sp3 | — | — |
| Northern Overture | EX | `war_archives_20220210_cn` | a1, a2, a3, b1, b2, b3 | c1, c2, c3, d1, d2, d3 | Chapayev — Hard `d3`, 60 clears |
| Aurora Noctis | EX | `war_archives_20220414_cn` | a1, a2, a3, b1, b2, b3 | c1, c2, c3, d1, d2, d3 | Hermione — Hard `d3`, 60 clears |
| Counterattack Within the Fjord | SP | `war_archives_20200603_cn` | sp1, sp2, sp3 | — | — |
| Inverted Orthant | EX | `war_archives_20201229_cn` | a1, a2, a3, b1, b2, b3 | c1, c2, c3, d1, d2, d3 | U-37 — Hard `d3`, 60 clears |
| Dreamwaker's Butterfly | EX | `war_archives_20200917_cn` | t1, t2, t3, t4, t5, t6, ts1, ts2 | ht1, ht2, ht3, ht4, ht5, ht6, hts1, hts2 | Kii — Hard `ht6`, 60 clears |
| Mirror Involution | EX | `war_archives_20210527_cn` | a1, a2, a3, b1, b2, b3 | c1, c2, c3, d1, d2, d3 | Ticonderoga — Hard `d3`, 60 clears |
| Khorovod of Dawn's Rime | EX | `war_archives_20210225_cn` | a1, a2, a3, b1, b2, b3 | c1, c2, c3, d1, d2, d3 | Tallinn — Hard `d3`, 60 clears |
| Sundered Blue | SP | `war_archives_20201012_cn` | sp1, sp2, sp3 | — | — |
| The Flame-Touched Dagger | SP | `war_archives_20211111_cn` | sp1, sp2, sp3 | — | — |
| Upon the Shimmering Blue | EX | `war_archives_20210916_cn` | a1, a2, a3, b1, b2, b3 | c1, c2, c3, d1, d2, d3 | Chikuma — Hard `d3`, 60 clears |
| Daedalian Hymn | EX | `war_archives_20210422_cn` | a1, a2, a3, b1, b2, b3 | c1, c2, c3, d1, d2, d3 | Duca degli Abruzzi — Hard `d3`, 60 clears |
| Tower of Transcendence | EX | `war_archives_20211229_cn` | a1, a2, a3, b1, b2, bs1, b3 | c1, c2, c3, d1, d2, ds1, d3 | Magdeburg — Hard `d3`, 60 clears |
| Abyssal Refrain | EX | `war_archives_20220224_cn` | a1, a2, a3, b1, b2, b3 | c1, c2, c3, d1, d2, d3 | Kiev — Hard `d3`, 60 clears |
| Virtual Tower | SP | `war_archives_20220324_cn` | sp1, sp2, sp3, sp4 | — | Pompeo Magno — Normal `sp4`, 60 clears |
| Pledge of the Radiant Court | EX | `war_archives_20220526_cn` | a1, a2, a3, b1, b2, b3 | c1, c2, c3, d1, d2, d3 | Jervis — Hard `d3`, 60 clears |
| Aquilifer's Ballade | EX | `war_archives_20220728_cn` | a1, a2, a3, b1, b2, b3 | c1, c2, c3, d1, d2, d3 | Giuseppe Garibaldi — Hard `d3`, 60 clears |
| Rondo at Rainbow's End | EX | `war_archives_20220428_cn` | a1, a2, a3, b1, b2, b3 | c1, c2, c3, d1, d2, d3 | Seydlitz — Hard `d3`, 60 clears |
| Tempesta and the Fountain of Youth | SP | `war_archives_20231026_cn` | t1, t2, t3, t4, t5, t6 | — | Mary Celeste — Normal `t6`, 60 clears |
| Violet Tempest, Blooming Lycoris | EX | `war_archives_20220915_cn` | a1, a2, a3, b1, b2, b3 | c1, c2, c3, d1, d2, d3 | Wakatsuki — Hard `d3`, 60 clears |
| Parallel Superimposition | EX | `war_archives_20221222_cn` | a1, a2, a3, b1, b2, b3 | c1, c2, c3, d1, d2, d3 | Hornet II — Hard `d3`, 60 clears |
| Revelations of Dust | EX | `war_archives_20230223_cn` | a1, a2, a3, b1, b2, b3 | c1, c2, c3, d1, d2, d3 | Royal Oak — Hard `d3`, 60 clears |
| Operation Convergence | SP | `war_archives_20220818_cn` | sp1, sp2, sp3, sp4 | — | Blücher — Normal `sp4`, 60 clears |
| Anthem of Remembrance | SP | `war_archives_20230803_cn` | sp1, sp2, sp3 | — | — |
| Confluence of Nothingness | EX | `war_archives_20230525_cn` | t1, t2, t3, t4, t5, t6, ts1, ts2 | ht1, ht2, ht3, ht4, ht5, ht6, hts1, hts2 | U-556 META — Hard `ht6`, 60 clears |
| Interlude of Illusions | SP | `war_archives_20240725_cn` | t1, t2, t3 | ht1, ht2, ht3 | L'Audacieux — Hard `ht3`, 60 clears |
| Frostfall | SP | **Unsupported** | — | — | — |
| Welcome to Little Academy | SP | **Unsupported** | — | — | — |
| Tempesta and the Sleeping Sea | SP | **Unsupported** | t1, t2, t3, t4, t5, t6 | — | Amity — Normal `t6`, 60 clears |
| Tempesta and Islas de Libertád | SP | **Unsupported** | t1, t2, t3, t4, t5, t6 | — | Pearl — Normal `t6`, 60 clears |

## Sources and maintenance

The data is pinned to client-data revision `6a9e95792cd201463d4cf80a2ab731ee66c671b7`; this is a community mirror of the shipped EN client data/code, not a Yostar-authored API. Official notices establish availability and the key-unit update; the pinned client tables establish the exact routes, reward identifiers and entry debit.

- [Official July 9, 2026 maintenance notice](https://azurlane.yo-star.com/news/2026/07/08/maintenance-notice-7-9-12-a-m-utc-7-2/): Frostfall and the fivefold key-unit/capacity change.
- [Official EN maintenance/news archive](https://azurlane.yo-star.com/news/author/azur-lane/): current EN archive additions, including Little Academy and the October Tempesta archives.
- [Archive definitions, categories, chapter IDs and `drop_gain`](https://github.com/AzurLaneTools/AzurLaneLuaScripts/blob/6a9e95792cd201463d4cf80a2ab731ee66c671b7/EN/sharecfg/re_map_template.lua).
- [Chapter names, types, prerequisites and hard-fleet requirements](https://github.com/AzurLaneTools/AzurLaneLuaScripts/blob/6a9e95792cd201463d4cf80a2ab731ee66c671b7/EN/sharecfgdata/chapter_template.lua).
- [Ship reward names](https://github.com/AzurLaneTools/AzurLaneLuaScripts/blob/6a9e95792cd201463d4cf80a2ab731ee66c671b7/EN/sharecfgdata/ship_data_statistics.lua) and [equipment reward names](https://github.com/AzurLaneTools/AzurLaneLuaScripts/blob/6a9e95792cd201463d4cf80a2ab731ee66c671b7/EN/sharecfgdata/equip_data_statistics.lua).
- [Canonical EN memory-group titles](https://github.com/AzurLaneTools/AzurLaneLuaScripts/blob/6a9e95792cd201463d4cf80a2ab731ee66c671b7/EN/sharecfg/memory_group.lua).
- [Daily keys and capacity](https://github.com/AzurLaneTools/AzurLaneLuaScripts/blob/6a9e95792cd201463d4cf80a2ab731ee66c671b7/EN/sharecfg/gameset.lua), [per-entry key cost](https://github.com/AzurLaneTools/AzurLaneLuaScripts/blob/6a9e95792cd201463d4cf80a2ab731ee66c671b7/EN/model/proxy/chapterproxy.lua), [successful-entry debit](https://github.com/AzurLaneTools/AzurLaneLuaScripts/blob/6a9e95792cd201463d4cf80a2ab731ee66c671b7/EN/controller/command/stage/trackingcommand.lua), and [daily key acquisition](https://github.com/AzurLaneTools/AzurLaneLuaScripts/blob/6a9e95792cd201463d4cf80a2ab731ee66c671b7/EN/controller/command/task/getremastercommand.lua).
- [Chapter unlock and clear-state predicates](https://github.com/AzurLaneTools/AzurLaneLuaScripts/blob/6a9e95792cd201463d4cf80a2ab731ee66c671b7/EN/model/vo/chapter.lua).
- [Archive reward counters, activity ticket handling and memory-group story behavior](https://github.com/AzurLaneTools/AzurLaneLuaScripts/blob/6a9e95792cd201463d4cf80a2ab731ee66c671b7/EN/view/helper/bossrushchapterremasterhelper.lua).

The older local display labels “Crescendo of Polaris”, “Encircling Graf Spee”, “Prelude Under the Moon”, and “The Way Home in the Night” remain explicit lookup aliases for their current titles. Unsupported archive names intentionally have no guessed folder alias. Update the source revision, catalog, guarantees and regression tests together when new archives arrive.
