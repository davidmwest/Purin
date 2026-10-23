"""Current EN War Archives metadata, reviewed against the 2026-10-23 client.

Only ``drop_gain`` supplies guaranteed prizes here. Random map drops,
``drop_display`` previews and first/three-star rewards are deliberately excluded.
A prize is a one-time claim after the specified stage-clear count; this catalog
never proves that an account has unlocked, cleared or claimed it.

Stage names are local lowercase campaign codes in the client's chapter order.
Side stages (AS/BS/CS/DS/TS/HTS) remain included: some contain real battles, so
an ``S`` suffix is not proof of a story-only node. Most SP archives have one
normal route; Interlude of Illusions also has a real hard route. Two current EN archives
lack local maps and remain metadata-only with ``supported=False``.
"""
from dataclasses import dataclass
from types import MappingProxyType
from typing import Optional, Tuple


CATALOG_AS_OF = '2026-10-23'
CLIENT_DATA_REVISION = '6a9e95792cd201463d4cf80a2ab731ee66c671b7'
CLIENT_DATA_URL = (
    'https://github.com/AzurLaneTools/AzurLaneLuaScripts/blob/'
    + CLIENT_DATA_REVISION + '/EN/sharecfg/re_map_template.lua'
)
# Current ordinary chapter entry path charges five keys per fresh entry.
# gameset.lua: reactivity_ticket_daily=20, reactivity_ticket_max=300.
RAW_KEYS_PER_ENTRY = 5
RAW_KEYS_DAILY = 20
RAW_KEYS_CAPACITY = 300
DAILY_SORTIES = RAW_KEYS_DAILY // RAW_KEYS_PER_ENTRY


@dataclass(frozen=True)
class Prize:
    id: int
    name: str
    stage: str
    mode: str
    clears: int
    reward_kind: str


@dataclass(frozen=True)
class Archive:
    id: Optional[str]
    name: str
    category: str
    normal_stages: Tuple[str, ...]
    hard_stages: Tuple[str, ...]
    prizes: Tuple[Prize, ...]
    supported: bool = True
    game_id: int = 0
    aliases: Tuple[str, ...] = ()

    def stages(self, mode):
        """Return the actual route; a missing hard route never falls back to normal."""
        if mode == 'normal':
            return self.normal_stages
        if mode == 'hard':
            return self.hard_stages
        raise ValueError(f'Unknown archive mode: {mode!r}')


ARCHIVES = (
    Archive('war_archives_20190321_en', 'Visitors Dyed in Red', 'ex',
            ('a1', 'a2', 'a3', 'b1', 'b2', 'b3'),
            ('c1', 'c2', 'c3', 'd1', 'd2', 'd3'),
            (Prize(307061, 'Zuikaku', 'd3', 'hard', 60, 'ship'),), supported=True, game_id=1),
    Archive('war_archives_20181020_en', 'Strive, Wish and Strategize', 'sp',
            ('sp1', 'sp2', 'sp3'),
            (),
            (), supported=True, game_id=2),
    Archive('war_archives_20190221_en', "Winter's Crown", 'ex',
            ('a1', 'a2', 'a3', 'b1', 'b2', 'b3'),
            ('c1', 'c2', 'c3', 'd1', 'd2', 'd3'),
            (Prize(205071, 'Duke of York', 'd3', 'hard', 60, 'ship'),), supported=True, game_id=4),
    Archive('war_archives_20191010_en', 'The Pursuit of Graf Spee', 'sp',
            ('sp1', 'sp2', 'sp3'),
            (),
            (), supported=True, game_id=5, aliases=('Encircling Graf Spee',)),
    Archive('war_archives_20191031_en', 'Divergent Chessboard', 'ex',
            ('a1', 'a2', 'a3', 'a4', 'b1', 'b2', 'b3', 'b4'),
            ('c1', 'c2', 'c3', 'c4', 'd1', 'd2', 'd3', 'd4'),
            (Prize(401461, 'Z46', 'd4', 'hard', 60, 'ship'),), supported=True, game_id=6),
    Archive('war_archives_20181026_en', 'Fallen Wings', 'ex',
            ('a1', 'a2', 'a3', 'b1', 'b2', 'b3'),
            ('c1', 'c2', 'c3', 'd1', 'd2', 'd3'),
            (Prize(105121, 'North Carolina', 'd3', 'hard', 60, 'ship'),), supported=True, game_id=7),
    Archive('war_archives_20180726_cn', 'Iris of Light and Dark', 'ex',
            ('a1', 'a2', 'a3', 'b1', 'b2', 'b3'),
            ('c1', 'c2', 'c3', 'd1', 'd2', 'd3'),
            (Prize(801011, 'Le Triomphant', 'd3', 'hard', 60, 'ship'),), supported=True, game_id=8),
    Archive('war_archives_20180607_cn', 'Ink-Stained Steel Sakura', 'ex',
            ('a1', 'a2', 'a3', 'a4', 'b1', 'b2'),
            ('c1', 'c2', 'c3', 'c4', 'd1', 'd2'),
            (Prize(301491, 'Kawakaze', 'd2', 'hard', 60, 'ship'),), supported=True, game_id=9),
    Archive('war_archives_20190620_en', 'Glorious Battle', 'sp',
            ('sp1', 'sp2', 'sp3'),
            (),
            (), supported=True, game_id=10),
    Archive('war_archives_20181227_cn', 'Crimson Echoes', 'ex',
            ('a1', 'a2', 'a3', 'b1', 'b2', 'b3'),
            ('c1', 'c2', 'c3', 'd1', 'd2', 'd3'),
            (Prize(305071, 'Kaga(BB)', 'd3', 'hard', 60, 'ship'),), supported=True, game_id=11),
    Archive('war_archives_20190314_en', 'Moonlit Overture', 'sp',
            ('sp1', 'sp2', 'sp3'),
            (),
            (), supported=True, game_id=12, aliases=('Prelude Under the Moon',)),
    Archive('war_archives_20200820_cn', 'Scherzo of Iron and Blood', 'ex',
            ('a1', 'a2', 'a3', 'b1', 'b2', 'b3'),
            ('c1', 'c2', 'c3', 'd1', 'd2', 'd3'),
            (Prize(205051, 'King George V', 'd3', 'hard', 60, 'ship'),), supported=True, game_id=13),
    Archive('war_archives_20211014_cn', 'Passionate Polaris', 'sp',
            ('sp1', 'sp2', 'sp3', 'sp4', 'sp5'),
            (),
            (Prize(89200, 'Cosmic Kicks', 'sp5', 'normal', 60, 'equipment'),), supported=True, game_id=14, aliases=('Crescendo of Polaris',)),
    Archive('war_archives_20190911_cn', 'Empyreal Tragicomedy', 'ex',
            ('a1', 'a2', 'a3', 'as1', 'as2', 'b1', 'b2', 'b3'),
            ('c1', 'c2', 'c3', 'cs1', 'cs2', 'd1', 'd2', 'd3'),
            (Prize(605021, 'Littorio', 'd3', 'hard', 60, 'ship'),), supported=True, game_id=15),
    Archive('war_archives_20200312_cn', 'The Solomon Ranger', 'sp',
            ('sp1', 'sp2', 'sp3'),
            (),
            (), supported=True, game_id=16),
    Archive('war_archives_20210325_cn', 'Ashen Simulacrum', 'ex',
            ('a1', 'a2', 'a3', 'b1', 'b2', 'b3', 'as1', 'as2', 'bs1', 'bs2'),
            ('c1', 'c2', 'c3', 'd1', 'd2', 'd3', 'cs1', 'cs2', 'ds1', 'ds2'),
            (Prize(108031, 'Cavalla', 'd3', 'hard', 60, 'ship'),), supported=True, game_id=17),
    Archive('war_archives_20210624_cn', 'Swirling Cherry Blossoms', 'ex',
            ('a1', 'a2', 'a3', 'b1', 'b2', 'b3'),
            ('c1', 'c2', 'c3', 'd1', 'd2', 'd3'),
            (Prize(302211, 'Noshiro', 'd3', 'hard', 60, 'ship'),), supported=True, game_id=18),
    Archive('war_archives_20200806_cn', 'The Enigma and the Shark', 'sp',
            ('sp1', 'sp2', 'sp3'),
            (),
            (), supported=True, game_id=19),
    Archive('war_archives_20201029_cn', 'Universe in Unison', 'sp',
            ('sp1', 'sp2', 'sp3', 'sp4', 'sp5'),
            (),
            (Prize(89220, 'Celestial Body', 'sp5', 'normal', 60, 'equipment'),), supported=True, game_id=20),
    Archive('war_archives_20210819_cn', 'Microlayer Medley', 'ex',
            ('a1', 'a2', 'a3', 'b1', 'b2', 'b3'),
            ('c1', 'c2', 'c3', 'd1', 'd2', 'd3'),
            (Prize(103241, 'Bremerton', 'd3', 'hard', 60, 'ship'),), supported=True, game_id=21),
    Archive('war_archives_20200903_cn', 'Stars of the Shimmering Fjord', 'sp',
            ('sp0', 'sp1', 'sp2', 'sp3'),
            (),
            (), supported=True, game_id=22),
    Archive('war_archives_20211028_cn', 'Skybound Oratorio', 'ex',
            ('a1', 'a2', 'a3', 'b1', 'b2', 'b3'),
            ('c1', 'c2', 'c3', 'd1', 'd2', 'd3'),
            (Prize(802021, "Jeanne d'Arc", 'd3', 'hard', 60, 'ship'),), supported=True, game_id=23),
    Archive('war_archives_20200507_cn', 'The Way Home in the Dark', 'sp',
            ('sp1', 'sp2', 'sp3'),
            (),
            (), supported=True, game_id=24, aliases=('The Way Home in the Night',)),
    Archive('war_archives_20220210_cn', 'Northern Overture', 'ex',
            ('a1', 'a2', 'a3', 'b1', 'b2', 'b3'),
            ('c1', 'c2', 'c3', 'd1', 'd2', 'd3'),
            (Prize(702031, 'Chapayev', 'd3', 'hard', 60, 'ship'),), supported=True, game_id=25),
    Archive('war_archives_20220414_cn', 'Aurora Noctis', 'ex',
            ('a1', 'a2', 'a3', 'b1', 'b2', 'b3'),
            ('c1', 'c2', 'c3', 'd1', 'd2', 'd3'),
            (Prize(202271, 'Hermione', 'd3', 'hard', 60, 'ship'),), supported=True, game_id=26),
    Archive('war_archives_20200603_cn', 'Counterattack Within the Fjord', 'sp',
            ('sp1', 'sp2', 'sp3'),
            (),
            (), supported=True, game_id=27),
    Archive('war_archives_20201229_cn', 'Inverted Orthant', 'ex',
            ('a1', 'a2', 'a3', 'b1', 'b2', 'b3'),
            ('c1', 'c2', 'c3', 'd1', 'd2', 'd3'),
            (Prize(408101, 'U-37', 'd3', 'hard', 60, 'ship'),), supported=True, game_id=28),
    Archive('war_archives_20200917_cn', "Dreamwaker's Butterfly", 'ex',
            ('t1', 't2', 't3', 't4', 't5', 't6', 'ts1', 'ts2'),
            ('ht1', 'ht2', 'ht3', 'ht4', 'ht5', 'ht6', 'hts1', 'hts2'),
            (Prize(305121, 'Kii', 'ht6', 'hard', 60, 'ship'),), supported=True, game_id=29),
    Archive('war_archives_20210527_cn', 'Mirror Involution', 'ex',
            ('a1', 'a2', 'a3', 'b1', 'b2', 'b3'),
            ('c1', 'c2', 'c3', 'd1', 'd2', 'd3'),
            (Prize(107141, 'Ticonderoga', 'd3', 'hard', 60, 'ship'),), supported=True, game_id=30),
    Archive('war_archives_20210225_cn', "Khorovod of Dawn's Rime", 'ex',
            ('a1', 'a2', 'a3', 'b1', 'b2', 'b3'),
            ('c1', 'c2', 'c3', 'd1', 'd2', 'd3'),
            (Prize(703011, 'Tallinn', 'd3', 'hard', 60, 'ship'),), supported=True, game_id=31),
    Archive('war_archives_20201012_cn', 'Sundered Blue', 'sp',
            ('sp1', 'sp2', 'sp3'),
            (),
            (), supported=True, game_id=32),
    Archive('war_archives_20211111_cn', 'The Flame-Touched Dagger', 'sp',
            ('sp1', 'sp2', 'sp3'),
            (),
            (), supported=True, game_id=33),
    Archive('war_archives_20210916_cn', 'Upon the Shimmering Blue', 'ex',
            ('a1', 'a2', 'a3', 'b1', 'b2', 'b3'),
            ('c1', 'c2', 'c3', 'd1', 'd2', 'd3'),
            (Prize(303061, 'Chikuma', 'd3', 'hard', 60, 'ship'),), supported=True, game_id=34),
    Archive('war_archives_20210422_cn', 'Daedalian Hymn', 'ex',
            ('a1', 'a2', 'a3', 'b1', 'b2', 'b3'),
            ('c1', 'c2', 'c3', 'd1', 'd2', 'd3'),
            (Prize(602011, 'Duca degli Abruzzi', 'd3', 'hard', 60, 'ship'),), supported=True, game_id=35),
    Archive('war_archives_20211229_cn', 'Tower of Transcendence', 'ex',
            ('a1', 'a2', 'a3', 'b1', 'b2', 'bs1', 'b3'),
            ('c1', 'c2', 'c3', 'd1', 'd2', 'ds1', 'd3'),
            (Prize(402061, 'Magdeburg', 'd3', 'hard', 60, 'ship'),), supported=True, game_id=36),
    Archive('war_archives_20220224_cn', 'Abyssal Refrain', 'ex',
            ('a1', 'a2', 'a3', 'b1', 'b2', 'b3'),
            ('c1', 'c2', 'c3', 'd1', 'd2', 'd3'),
            (Prize(701101, 'Kiev', 'd3', 'hard', 60, 'ship'),), supported=True, game_id=37),
    Archive('war_archives_20220324_cn', 'Virtual Tower', 'sp',
            ('sp1', 'sp2', 'sp3', 'sp4'),
            (),
            (Prize(601071, 'Pompeo Magno', 'sp4', 'normal', 60, 'ship'),), supported=True, game_id=38),
    Archive('war_archives_20220526_cn', 'Pledge of the Radiant Court', 'ex',
            ('a1', 'a2', 'a3', 'b1', 'b2', 'b3'),
            ('c1', 'c2', 'c3', 'd1', 'd2', 'd3'),
            (Prize(201341, 'Jervis', 'd3', 'hard', 60, 'ship'),), supported=True, game_id=39),
    Archive('war_archives_20220728_cn', "Aquilifer's Ballade", 'ex',
            ('a1', 'a2', 'a3', 'b1', 'b2', 'b3'),
            ('c1', 'c2', 'c3', 'd1', 'd2', 'd3'),
            (Prize(602021, 'Giuseppe Garibaldi', 'd3', 'hard', 60, 'ship'),), supported=True, game_id=40),
    Archive('war_archives_20220428_cn', "Rondo at Rainbow's End", 'ex',
            ('a1', 'a2', 'a3', 'b1', 'b2', 'b3'),
            ('c1', 'c2', 'c3', 'd1', 'd2', 'd3'),
            (Prize(404031, 'Seydlitz', 'd3', 'hard', 60, 'ship'),), supported=True, game_id=41),
    Archive('war_archives_20231026_cn', 'Tempesta and the Fountain of Youth', 'sp',
            ('t1', 't2', 't3', 't4', 't5', 't6'),
            (),
            (Prize(9600041, 'Mary Celeste', 't6', 'normal', 60, 'ship'),), supported=True, game_id=42),
    Archive('war_archives_20220915_cn', 'Violet Tempest, Blooming Lycoris', 'ex',
            ('a1', 'a2', 'a3', 'b1', 'b2', 'b3'),
            ('c1', 'c2', 'c3', 'd1', 'd2', 'd3'),
            (Prize(301881, 'Wakatsuki', 'd3', 'hard', 60, 'ship'),), supported=True, game_id=43),
    Archive('war_archives_20221222_cn', 'Parallel Superimposition', 'ex',
            ('a1', 'a2', 'a3', 'b1', 'b2', 'b3'),
            ('c1', 'c2', 'c3', 'd1', 'd2', 'd3'),
            (Prize(107121, 'Hornet II', 'd3', 'hard', 60, 'ship'),), supported=True, game_id=44),
    Archive('war_archives_20230223_cn', 'Revelations of Dust', 'ex',
            ('a1', 'a2', 'a3', 'b1', 'b2', 'b3'),
            ('c1', 'c2', 'c3', 'd1', 'd2', 'd3'),
            (Prize(205141, 'Royal Oak', 'd3', 'hard', 60, 'ship'),), supported=True, game_id=45),
    Archive('war_archives_20220818_cn', 'Operation Convergence', 'sp',
            ('sp1', 'sp2', 'sp3', 'sp4'),
            (),
            (Prize(403021, 'Blücher', 'sp4', 'normal', 60, 'ship'),), supported=True, game_id=46),
    Archive('war_archives_20230803_cn', 'Anthem of Remembrance', 'sp',
            ('sp1', 'sp2', 'sp3'),
            (),
            (), supported=True, game_id=47),
    Archive('war_archives_20230525_cn', 'Confluence of Nothingness', 'ex',
            ('t1', 't2', 't3', 't4', 't5', 't6', 'ts1', 'ts2'),
            ('ht1', 'ht2', 'ht3', 'ht4', 'ht5', 'ht6', 'hts1', 'hts2'),
            (Prize(9708011, 'U-556 META', 'ht6', 'hard', 60, 'ship'),), supported=True, game_id=48),
    Archive('war_archives_20240725_cn', 'Interlude of Illusions', 'sp',
            ('t1', 't2', 't3'),
            ('ht1', 'ht2', 'ht3'),
            (Prize(901141, "L'Audacieux", 'ht3', 'hard', 60, 'ship'),), supported=True, game_id=49),
    Archive(None, 'Frostfall', 'sp',
            (),
            (),
            (), supported=False, game_id=50),
    Archive(None, 'Welcome to Little Academy', 'sp',
            (),
            (),
            (), supported=False, game_id=51),
    Archive('war_archives_20241024_cn', 'Tempesta and the Sleeping Sea', 'sp',
            ('t1', 't2', 't3', 't4', 't5', 't6'),
            (),
            (Prize(9600091, 'Amity', 't6', 'normal', 60, 'ship'),), supported=True, game_id=52),
    Archive('war_archives_20251023_cn', 'Tempesta and Islas de Libertád', 'sp',
            ('t1', 't2', 't3', 't4', 't5', 't6'),
            (),
            (Prize(9600141, 'Pearl', 't6', 'normal', 60, 'ship'),), supported=True, game_id=53),
)

ARCHIVES_BY_ID = MappingProxyType({archive.id: archive for archive in ARCHIVES if archive.id is not None})
ARCHIVES_BY_GAME_ID = MappingProxyType({archive.game_id: archive for archive in ARCHIVES})
_ARCHIVES_BY_NAME = MappingProxyType({name.casefold(): archive for archive in ARCHIVES
                                     for name in (archive.name,) + archive.aliases})


def get_archive(identifier):
    """Resolve an exact folder, EN archive title or documented old title alias.

    Unsupported entries remain discoverable by name. Callers must check
    ``supported`` and must not turn their missing folder into a guessed path.
    """
    if not isinstance(identifier, str):
        raise ValueError(f'Unknown archive: {identifier!r}')
    archive = ARCHIVES_BY_ID.get(identifier) or _ARCHIVES_BY_NAME.get(identifier.strip().casefold())
    if archive is None:
        raise ValueError(f'Unknown archive: {identifier!r}')
    return archive


def guaranteed_prizes(identifier, mode=None):
    """Guaranteed counter rewards only, with no mode substitution or drop inference."""
    archive = get_archive(identifier)
    if mode is None:
        return archive.prizes
    archive.stages(mode)  # Validate even when this archive has no guaranteed prize.
    return tuple(prize for prize in archive.prizes if prize.mode == mode)
