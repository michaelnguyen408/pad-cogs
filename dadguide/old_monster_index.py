import difflib

from redbot.core.utils import AsyncIter
from discord.utils import find as find_first
import tsutils

from collections import defaultdict

from .models.monster_model import MonsterModel
from .models.enum_types import Attribute
from .models.enum_types import EvoType
from .models.enum_types import InternalEvoType
from dadguide.models.enum_types import Attribute, MonsterType
from dadguide.models.monster_model import MonsterModel
from dadguide.models.series_model import SeriesModel
from .database_context import DbContext


class MonsterIndex(tsutils.aobject):
    async def __init__(self, monster_database: DbContext, nickname_overrides, basename_overrides,
                       panthname_overrides, accept_filter=None):
        # Important not to hold onto anything except IDs here so we don't leak memory
        self.db_context = monster_database
        base_monster_ids = monster_database.get_base_monster_ids()

        self.attr_short_prefix_map = {
            Attribute.Fire: ['r'],
            Attribute.Water: ['b'],
            Attribute.Wood: ['g'],
            Attribute.Light: ['l'],
            Attribute.Dark: ['d'],
            Attribute.Unknown: ['h'],
            Attribute.Nil: ['x'],
        }
        self.attr_long_prefix_map = {
            Attribute.Fire: ['red', 'fire'],
            Attribute.Water: ['blue', 'water'],
            Attribute.Wood: ['green', 'wood'],
            Attribute.Light: ['light'],
            Attribute.Dark: ['dark'],
            Attribute.Unknown: ['unknown'],
            Attribute.Nil: ['null', 'none'],
        }

        self.series_to_prefix_map = {
            130: ['halloween', 'hw', 'h'],
            136: ['xmas', 'christmas', 'x'],
            125: ['summer', 'beach'],
            114: ['school', 'academy', 'gakuen'],
            139: ['new years', 'ny'],
            149: ['wedding', 'bride'],
            154: ['padr'],
            175: ['valentines', 'vday', 'v'],
            183: ['gh', 'gungho'],
            117: ['gh', 'gungho'],
        }

        monster_id_to_nicknames = defaultdict(set)
        for monster_id, nicknames in nickname_overrides.items():
            monster_id_to_nicknames[monster_id] = nicknames

        named_monsters = []
        async for base_mon in AsyncIter(base_monster_ids):
            base_id = base_mon.monster_id
            base_monster = monster_database.graph.get_monster(base_id)
            series = base_monster.series
            group_basename_overrides = basename_overrides.get(base_id, [])
            evolution_tree = [monster_database.graph.get_monster(m) for m in
                              monster_database.get_evolution_tree_ids(base_id)]
            named_mg = NamedMonsterGroup(evolution_tree, group_basename_overrides)
            named_evolution_tree = []
            for monster in evolution_tree:
                if accept_filter and not accept_filter(monster):
                    continue
                prefixes = self.compute_prefixes(monster, evolution_tree)
                extra_nicknames = monster_id_to_nicknames[monster.monster_id]

                # The query mis-handles transforms so we have to fetch base monsters
                # from the graph properly ourselves instead of just listening to whatever
                # the query says the base monster is above
                named_monster = NamedMonster(
                    monster, named_mg, prefixes, extra_nicknames, series,
                    base_monster=monster_database.graph.get_base_monster(monster))
                named_monsters.append(named_monster)
                named_evolution_tree.append(named_monster)
            for named_monster in named_evolution_tree:
                named_monster.set_evolution_tree(named_evolution_tree)

        # Sort the NamedMonsters into the opposite order we want to accept their nicknames in
        # This order is:
        #  1) High priority first
        #  2) Larger group sizes
        #  3) Minimum ID size in the group
        #  4) Monsters with higher ID values
        def named_monsters_sort(named_mon: NamedMonster):
            return (not named_mon.is_low_priority, named_mon.group_size, -1 *
                    named_mon.base_monster_no_na, named_mon.monster_no_na)

        named_monsters.sort(key=named_monsters_sort)

        # set up a set of all pantheon names, a set of all pantheon nicknames, and a dictionary of nickname -> full name
        # later we will set up a dictionary of pantheon full name -> monsters
        self.all_pantheon_names = set()
        self.all_pantheon_names.update(panthname_overrides.values())

        self.pantheon_nick_to_name = panthname_overrides
        self.pantheon_nick_to_name.update(panthname_overrides)

        self.all_pantheon_nicknames = set()
        self.all_pantheon_nicknames.update(panthname_overrides.keys())

        self.all_prefixes = set()
        self.pantheons = defaultdict(set)
        self.all_entries = {}
        self.two_word_entries = {}
        for nm in named_monsters:
            self.all_prefixes.update(nm.prefixes)
            for nickname in nm.final_nicknames:
                self.all_entries[nickname] = nm
            for nickname in nm.final_two_word_nicknames:
                self.two_word_entries[nickname] = nm
            if nm.series:
                for pantheon in self.all_pantheon_names:
                    if pantheon.lower() == nm.series.lower():
                        self.pantheons[pantheon.lower()].add(nm)

        self.all_monsters = named_monsters
        self.all_en_name_to_monsters = {m.name_en.lower(): m for m in named_monsters}
        self.monster_no_na_to_named_monster = {m.monster_no_na: m for m in named_monsters}
        self.monster_id_to_named_monster = {m.monster_id: m for m in named_monsters}

        for monster_id, nicknames in nickname_overrides.items():
            nm = self.monster_id_to_named_monster.get(monster_id)
            if nm:
                for nickname in nicknames:
                    self.all_entries[nickname] = nm

    def init_index(self):
        pass

    def compute_prefixes(self, m: MonsterModel, evotree: list):
        prefixes = set()

        attr1_short_prefixes = self.attr_short_prefix_map[m.attr1]
        attr1_long_prefixes = self.attr_long_prefix_map[m.attr1]
        prefixes.update(attr1_short_prefixes)
        prefixes.update(attr1_long_prefixes)

        # If no 2nd attribute, use x so we can look those monsters up easier
        attr2_short_prefixes = self.attr_short_prefix_map.get(m.attr2, ['x'])
        for a1 in attr1_short_prefixes:
            for a2 in attr2_short_prefixes:
                prefixes.add(a1 + a2)
                prefixes.add(a1 + '/' + a2)

        # TODO: add prefixes based on type

        # Chibi monsters have the same NA name, except lowercased
        lower_name = m.name_en.lower()
        if m.name_en != m.name_ja:
            if lower_name == m.name_en:
                prefixes.add('chibi')
        elif 'ミニ' in m.name_ja:
            # Guarding this separately to prevent 'gemini' from triggering (e.g. 2645)
            prefixes.add('chibi')

        true_evo_type = self.db_context.graph.true_evo_type_by_monster(m)
        awoken = lower_name.startswith('awoken') or '覚醒' in lower_name
        revo = true_evo_type == InternalEvoType.Reincarnated
        srevo = lower_name.startswith('super reincarnated') or '超転生' in lower_name
        mega = lower_name.startswith('mega awoken') or '極醒' in lower_name
        awoken_or_revo_or_equip_or_mega = awoken or revo or m.is_equip or mega

        # These clauses need to be separate to handle things like 'Awoken Thoth' which are
        # actually Evos but have awoken in the name
        if awoken:
            prefixes.add('a')
            prefixes.add('awoken')

        if revo:
            prefixes.add('revo')
            prefixes.add('reincarnated')

        if mega:
            prefixes.add('mega')
            prefixes.add('mega awoken')
            prefixes.add('awoken')
            prefixes.add('ma')

        if srevo:
            prefixes.add('srevo')
            prefixes.add('super reincarnated')

        # Prefixes for evo type
        cur_evo_type = self.db_context.graph.cur_evo_type_by_monster(m)
        if cur_evo_type == EvoType.Base:
            prefixes.add('base')
        elif cur_evo_type == EvoType.Evo:
            prefixes.add('evo')
        elif cur_evo_type == EvoType.UvoAwoken and not awoken_or_revo_or_equip_or_mega:
            prefixes.add('uvo')
            prefixes.add('uevo')
        elif cur_evo_type == EvoType.UuvoReincarnated and not awoken_or_revo_or_equip_or_mega:
            prefixes.add('uuvo')
            prefixes.add('uuevo')

        # Other Prefixes
        if self.db_context.graph.monster_is_farmable_evo(m):
            prefixes.add('farmable')

        # If any monster in the group is a pixel, add 'nonpixel' to all the versions
        # without pixel in the name. Add 'pixel' as a prefix to the ones with pixel in the name.
        def is_pixel(n):
            n = n.name_en.lower()
            return n.startswith('pixel') or n.startswith('ドット')

        for gm in evotree:
            if is_pixel(gm):
                prefixes.update(['pixel'] if is_pixel(m) else ['np', 'nonpixel'])
                break

        if m.is_equip:
            prefixes.add('assist')
            prefixes.add('equip')

        # Collab prefixes
        prefixes.update(self.series_to_prefix_map.get(m.series.series_id, []))

        return prefixes

    def find_monster(self, query):
        query = tsutils.rmdiacritics(query).lower().strip()

        # id search
        if query.isdigit():
            m = self.monster_no_na_to_named_monster.get(int(query))
            if m is None:
                return None, 'Looks like a monster ID but was not found', None
            else:
                return m, None, "ID lookup"
            # special handling for na/jp

        # TODO: need to handle na_only?

        # handle exact nickname match
        if query in self.all_entries:
            return self.all_entries[query], None, "Exact nickname"

        contains_ja = tsutils.contains_ja(query)
        if len(query) < 2 and contains_ja:
            return None, 'Japanese queries must be at least 2 characters', None
        elif len(query) < 4 and not contains_ja:
            return None, 'Your query must be at least 4 letters', None

        # TODO: this should be a length-limited priority queue
        matches = set()

        # prefix search for ids, take max id
        for nickname, m in self.all_entries.items():
            if query.endswith("base {}".format(m.monster_id)):
                matches.add(
                    find_first(lambda mo: m.base_monster_no == mo.monster_id, self.all_entries.values()))
        if len(matches):
            return self.pick_best_monster(matches), None, "Base ID match, max of 1".format()

        # prefix search for nicknames, space-preceeded, take max id
        for nickname, m in self.all_entries.items():
            if nickname.startswith(query + ' '):
                matches.add(m)
        if len(matches):
            return self.pick_best_monster(matches), None, "Space nickname prefix, max of {}".format(len(matches))

        # prefix search for nicknames, take max id
        for nickname, m in self.all_entries.items():
            if nickname.startswith(query):
                matches.add(m)
        if len(matches):
            all_names = ",".join(map(lambda x: x.name_en, matches))
            return self.pick_best_monster(matches), None, "Nickname prefix, max of {}, matches=({})".format(
                len(matches), all_names)

        # prefix search for full name, take max id
        for nickname, m in self.all_entries.items():
            if m.name_en.lower().startswith(query) or m.name_ja.lower().startswith(query):
                matches.add(m)
        if len(matches):
            return self.pick_best_monster(matches), None, "Full name, max of {}".format(len(matches))

        # for nicknames with 2 names, prefix search 2nd word, take max id
        if query in self.two_word_entries:
            return self.two_word_entries[query], None, "Second-word nickname prefix, max of {}".format(len(matches))

        # TODO: refactor 2nd search characteristcs for 2nd word

        # full name contains on nickname, take max id
        for nickname, m in self.all_entries.items():
            if query in m.name_en.lower() or query in m.name_ja.lower():
                matches.add(m)
        if len(matches):
            return self.pick_best_monster(matches), None, 'Nickname contains nickname match ({})'.format(
                len(matches))

        # No decent matches. Try near hits on nickname instead
        matches = difflib.get_close_matches(query, self.all_entries.keys(), n=1, cutoff=.8)
        if len(matches):
            match = matches[0]
            return self.all_entries[match], None, 'Close nickname match ({})'.format(match)

        # Still no decent matches. Try near hits on full name instead
        matches = difflib.get_close_matches(
            query, self.all_en_name_to_monsters.keys(), n=1, cutoff=.9)
        if len(matches):
            match = matches[0]
            return self.all_en_name_to_monsters[match], None, 'Close name match ({})'.format(match)

        # About to give up, try matching all words
        matches = set()
        for nickname, m in self.all_entries.items():
            if (all(map(lambda x: x in m.name_en.lower(), query.split())) or
                            all(map(lambda x: x in m.name_ja.lower(), query.split()))):
                matches.add(m)
        if len(matches):
            return self.pick_best_monster(matches), None, 'All word match on full name, max of {}'.format(
                len(matches))

        # couldn't find anything
        return None, "Could not find a match for: " + query, None

    def find_monster2(self, query):
        """Search with alternative method for resolving prefixes.

        Implements the lookup for id2, where you are allowed to specify multiple prefixes for a card.
        All prefixes are required to be exactly matched by the card.
        Follows a similar logic to the regular id but after each check, will remove any potential match that doesn't
        contain every single specified prefix.
        """
        query = tsutils.rmdiacritics(query).lower().strip()
        # id search
        if query.isdigit():
            m = self.monster_no_na_to_named_monster.get(int(query))
            if m is None:
                return None, 'Looks like a monster ID but was not found', None
            else:
                return m, None, "ID lookup"

        # handle exact nickname match
        if query in self.all_entries:
            return self.all_entries[query], None, "Exact nickname"

        contains_ja = tsutils.contains_ja(query)
        if len(query) < 2 and contains_ja:
            return None, 'Japanese queries must be at least 2 characters', None
        elif len(query) < 4 and not contains_ja:
            return None, 'Your query must be at least 4 letters', None

        # we want to look up only the main part of the query, and then verify that each result has the prefixes
        # so break up the query into an array of prefixes, and a string (new_query) that will be the lookup
        query_prefixes = []
        parts_of_query = query.split()
        new_query = ''
        for i, part in enumerate(parts_of_query):
            if part in self.all_prefixes:
                query_prefixes.append(part)
            else:
                new_query = ' '.join(parts_of_query[i:])
                break

        # if we don't have any prefixes, then default to using the regular id lookup
        if len(query_prefixes) < 1:
            return self.find_monster(query)

        matches = PotentialMatches()

        # prefix search for ids, take max id
        for nickname, m in self.all_entries.items():
            if query.endswith("base {}".format(m.monster_id)):
                matches.add(
                    find_first(lambda mo: m.base_monster_no == mo.monster_id, self.all_entries.values()))
        matches.update_list(query_prefixes)

        # first try to get matches from nicknames
        for nickname, m in self.all_entries.items():
            if new_query in nickname:
                matches.add(m)
        matches.update_list(query_prefixes)

        # if we don't have any candidates yet, pick a new method
        if not matches.length():
            # try matching on exact names next
            for nickname, m in self.all_en_name_to_monsters.items():
                if new_query in m.name_en.lower() or new_query in m.name_ja.lower():
                    matches.add(m)
            matches.update_list(query_prefixes)

        # check for exact match on pantheon name but only if needed
        if not matches.length():
            for pantheon in self.all_pantheon_nicknames:
                if new_query == pantheon.lower():
                    matches.get_monsters_from_potential_pantheon_match(pantheon, self.pantheon_nick_to_name,
                                                                       self.pantheons)
            matches.update_list(query_prefixes)

        # check for any match on pantheon name, again but only if needed
        if not matches.length():
            for pantheon in self.all_pantheon_nicknames:
                if new_query in pantheon.lower():
                    matches.get_monsters_from_potential_pantheon_match(pantheon, self.pantheon_nick_to_name,
                                                                       self.pantheons)
            matches.update_list(query_prefixes)

        if matches.length():
            return matches.pick_best_monster(), None, None
        return None, "Could not find a match for: " + query, None

    @staticmethod
    def pick_best_monster(named_monster_list):
        return max(named_monster_list, key=lambda x: (not x.is_low_priority, x.rarity, x.monster_no_na))


class PotentialMatches(object):
    def __init__(self):
        self.match_list = set()

    def add(self, m):
        self.match_list.add(m)

    def update(self, monster_list):
        self.match_list.update(monster_list)

    def length(self):
        return len(self.match_list)

    def update_list(self, query_prefixes):
        self._add_trees()
        self._remove_any_without_all_prefixes(query_prefixes)

    def _add_trees(self):
        to_add = set()
        for m in self.match_list:
            for evo in m.evolution_tree:
                to_add.add(evo)
        self.match_list.update(to_add)

    def _remove_any_without_all_prefixes(self, query_prefixes):
        to_remove = set()
        for m in self.match_list:
            for prefix in query_prefixes:
                if prefix not in m.prefixes:
                    to_remove.add(m)
                    break
        self.match_list.difference_update(to_remove)

    def get_monsters_from_potential_pantheon_match(self, pantheon, pantheon_nick_to_name, pantheons):
        full_name = pantheon_nick_to_name[pantheon]
        self.update(pantheons[full_name])

    def pick_best_monster(self):
        return max(self.match_list, key=lambda x: (not x.is_low_priority, x.rarity, x.monster_no_na))


class NamedMonsterGroup(object):
    def __init__(self, evolution_tree: list, basename_overrides: list):
        self.is_low_priority = (
                        self._is_low_priority_monster(evolution_tree[0])
                        or self._is_low_priority_group(evolution_tree))

        base_monster = evolution_tree[0]

        self.group_size = len(evolution_tree)
        self.base_monster_no = base_monster.monster_id
        self.base_monster_no_na = base_monster.monster_no_na

        self.monster_no_to_basename = {
            m.monster_id: self._compute_monster_basename(m) for m in evolution_tree
        }

        self.computed_basename = self._compute_group_basename(evolution_tree)
        self.computed_basenames = {self.computed_basename}
        if '-' in self.computed_basename:
            self.computed_basenames.add(self.computed_basename.replace('-', ' '))

        self.basenames = basename_overrides or self.computed_basenames

    @staticmethod
    def _compute_monster_basename(m: MonsterModel):
        basename = m.name_en.lower()
        if ',' in basename:
            name_parts = basename.split(',')
            if name_parts[1].strip().startswith('the '):
                # handle names like 'xxx, the yyy' where xxx is the name
                basename = name_parts[0]
            else:
                # otherwise, grab the chunk after the last comma
                basename = name_parts[-1]

        for x in ['awoken', 'reincarnated']:
            if basename.startswith(x):
                basename = basename.replace(x, '')

        # Fix for DC collab garbage
        basename = basename.replace('(comics)', '')
        basename = basename.replace('(film)', '')

        return basename.strip()

    def _compute_group_basename(self, monsters):
        """Computes the basename for a group of monsters.

        Prefer a basename with the largest count across the group. If all the
        groups have equal size, prefer the lowest monster number basename.
        This monster in general has better names, particularly when all the
        names are unique, e.g. for male/female hunters."""

        def count_and_id():
            return [0, 0]

        basename_to_info = defaultdict(count_and_id)

        for m in monsters:
            basename = self.monster_no_to_basename[m.monster_id]
            entry = basename_to_info[basename]
            entry[0] += 1
            entry[1] = max(entry[1], m.monster_id)

        entries = [[count_id[0], -1 * count_id[1], bn] for bn, count_id in basename_to_info.items()]
        return max(entries)[2]

    @staticmethod
    def _is_low_priority_monster(m: MonsterModel):
        lp_types = [MonsterType.Evolve, MonsterType.Enhance, MonsterType.Awoken, MonsterType.Vendor]
        lp_substrings = ['tamadra']
        lp_min_rarity = 2
        name = m.name_en.lower()

        failed_type = m.type1 in lp_types
        failed_ss = any([x in name for x in lp_substrings])
        failed_rarity = m.rarity < lp_min_rarity
        failed_chibi = name == m.name_en and m.name_en != m.name_ja
        failed_equip = m.is_equip
        return failed_type or failed_ss or failed_rarity or failed_chibi or failed_equip

    @staticmethod
    def _is_low_priority_group(mg: list):
        lp_grp_min_rarity = 5
        max_rarity = max(m.rarity for m in mg)
        failed_max_rarity = max_rarity < lp_grp_min_rarity
        return failed_max_rarity


class NamedMonster(object):
    def __init__(self, monster: MonsterModel, monster_group: NamedMonsterGroup, prefixes: set, extra_nicknames: set,
                 series: SeriesModel, base_monster: MonsterModel = None):

        self.evolution_tree = None

        # Hold on to the IDs instead
        self.monster_id = monster.monster_id
        self.monster_no_na = monster.monster_no_na
        self.monster_no_jp = monster.monster_no_jp

        # ID of the root of the tree for this monster
        self.base_monster_no = base_monster.monster_id
        self.base_monster_no_na = base_monster.monster_no_na

        # This stuff is important for nickname generation
        self.group_basenames = monster_group.basenames
        self.prefixes = prefixes

        # Pantheon
        series = series
        self.series = series.name if series else None

        # Data used to determine how to rank the nicknames
        self.is_low_priority = monster_group.is_low_priority or monster.is_equip
        self.group_size = monster_group.group_size
        self.rarity = monster.rarity

        # Used in fallback searches
        self.name_en = monster.name_en
        self.name_ja = monster.name_ja

        # These are just extra metadata
        self.monster_basename = monster_group.monster_no_to_basename[self.monster_id]
        self.group_computed_basename = monster_group.computed_basename
        self.extra_nicknames = extra_nicknames

        # Compute any extra prefixes
        if self.monster_basename in ('ana', 'ace'):
            self.prefixes.add(self.monster_basename)

        # Compute extra basenames by checking for two-word basenames and using the second half
        self.two_word_basenames = set()
        for basename in self.group_basenames:
            basename_words = basename.split(' ')
            if len(basename_words) == 2:
                self.two_word_basenames.add(basename_words[1])

        # The primary result nicknames
        self.final_nicknames = set()
        # Set the configured override nicknames
        self.final_nicknames.update(self.extra_nicknames)
        # Set the roma subname for JP monsters
        if monster.roma_subname:
            self.final_nicknames.add(monster.roma_subname)

        # For each basename, add nicknames
        for basename in self.group_basenames:
            # Add the basename directly
            self.final_nicknames.add(basename)
            # Add the prefix plus basename, and the prefix with a space between basename
            for prefix in self.prefixes:
                self.final_nicknames.add(prefix + basename)
                self.final_nicknames.add(prefix + ' ' + basename)

        self.final_two_word_nicknames = set()
        # Slightly different process for two-word basenames. Does this make sense? Who knows.
        for basename in self.two_word_basenames:
            self.final_two_word_nicknames.add(basename)
            # Add the prefix plus basename, and the prefix with a space between basename
            for prefix in self.prefixes:
                self.final_two_word_nicknames.add(prefix + basename)
                self.final_two_word_nicknames.add(prefix + ' ' + basename)

    def set_evolution_tree(self, evolution_tree):
        """
        Set the evolution tree to a list of NamedMonsters so that we can have
        nice things like prefix lookups on the entire tree in id2 and not cry
        about Diablos equip
        """
        self.evolution_tree = evolution_tree
