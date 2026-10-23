from types import SimpleNamespace

import pytest

from module.research.project import ResearchProject
from module.research.selector import ResearchSelector


@pytest.fixture
def selector():
    # Skip ModuleBase.__init__: these selection tests need neither a real config nor a device.
    controller = ResearchSelector.__new__(ResearchSelector)
    controller.config = SimpleNamespace(
        Research_UseCube='always_use', Research_UseCoin='always_use', Research_UsePart='always_use',
        Research_AllowDelay=True, Research_PresetFilter='custom', SERVER='en',
        Research_CustomFilter='S9-DR0.5 > S9-PRY0.5 > S9-H0.5 > S9-H1 > S9-Q0.5 '
                              '> S9-PRY2.5 > S9-DR2.5 > reset > shortest',
    )
    # Both 0.5-hour ship projects consume cubes, as do H projects. Q0.5 and D2.5 do not.
    controller.projects = [ResearchProject(name, series=9) for name in (
        'D-037-UL', 'D-032-UL', 'H-063-UL', 'H-387-MI', 'Q-002-UL', 'D-737-MI', 'D-732-MI')]
    assert all(project.valid for project in controller.projects)
    controller.storage_has_boxes = True
    return controller


def project_names(priority):
    return [project.name for project in priority if not isinstance(project, str)]


@pytest.mark.parametrize('enforce', [False, True])
def test_explicit_cube_priorities_are_vetoed_even_when_enforced(selector, enforce):
    # Positive control: the same filter can select all four cube-consuming fixtures when allowed.
    assert project_names(selector.research_sort_filter(enforce=enforce)) == [
        'D-037-UL', 'D-032-UL', 'H-063-UL', 'H-387-MI', 'Q-002-UL', 'D-732-MI', 'D-737-MI']

    selector.config.Research_UseCube = 'do_not_use'

    assert selector.research_sort_filter(enforce=enforce) == [
        selector.projects[4], selector.projects[6], selector.projects[5], 'reset', 'shortest']


def test_appended_default_filter_preserves_the_cube_veto(selector):
    selector.config.Research_UseCube = 'do_not_use'
    selector.config.Research_CustomFilter = 'reset > shortest'
    assert selector.research_sort_filter(enforce=False) == ['reset', 'shortest']

    priority = selector.research_sort_filter(enforce=True)

    # Enforcing appends the default filter. Its newly introduced candidates still need the veto.
    assert set(project_names(priority)) == {'Q-002-UL', 'D-732-MI', 'D-737-MI'}


@pytest.mark.parametrize('method', ['research_sort_shortest', 'research_sort_cheapest'])
@pytest.mark.parametrize('enforce', [False, True])
def test_fallback_never_selects_cube_projects_even_when_nothing_else_is_available(selector, method, enforce):
    sort = getattr(selector, method)
    cube_names = {'D-037-UL', 'D-032-UL', 'H-063-UL', 'H-387-MI'}
    assert cube_names.issubset(project_names(sort(enforce=enforce)))

    selector.config.Research_UseCube = 'do_not_use'

    assert set(project_names(sort(enforce=enforce))) == {'Q-002-UL', 'D-732-MI', 'D-737-MI'}
    selector.projects = [project for project in selector.projects if project.name in cube_names]
    assert sort(enforce=enforce) == []
