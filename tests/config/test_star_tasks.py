import json

from module.config.config_manual import ManualConfig
from module.config.config_updater import ConfigUpdater


def test_new_jobs_default_disabled_and_preserve_existing_farming_configuration():
    old = {
        'Alas': {'Emulator': {'PackageName': 'com.YoStarEN.AzurLane'}},
        'Main2': {'Campaign': {'Name': '3-2'}, 'Fleet': {'Fleet1': 3, 'Fleet2': 4},
                  'StopCondition': {'ThenTask': 'Event'}},
    }
    new = ConfigUpdater().config_update(old)
    for task in ('CampaignStars', 'EventStars', 'EventExtra'):
        assert new[task]['Scheduler']['Enable'] is False
        assert new[task]['Scheduler']['Command'] == task
    assert new['CampaignStars']['Campaign']['Event'] == 'campaign_main'
    assert new['CampaignStars']['Campaign']['Name'] == '1-1'
    for task in ('EventStars', 'EventExtra'):
        assert new[task]['Campaign']['Event'] == 'event_20261022_cn'
    assert new['EventExtra']['Campaign']['Name'] == 'EX'
    assert new['Main2']['Campaign']['Name'] == '3-2'
    assert (new['Main2']['Fleet']['Fleet1'], new['Main2']['Fleet']['Fleet2']) == (3, 4)
    assert new['Main2']['StopCondition']['ThenTask'] == 'Event'


def test_generated_task_fields_match_job_guards_and_safe_defaults():
    with open('module/config/argument/args.json', encoding='utf-8') as stream:
        args = json.load(stream)
    for task in ('CampaignStars', 'EventStars'):
        assert args[task]['StarPass']['MaxRunsPerStage']['value'] == 6
        assert args[task]['StarPass']['CollectRewards']['value'] is True
        assert args[task]['StarPass']['CompleteStory']['value'] is False
        assert args[task]['Campaign']['UseClearMode']['value'] is False
        assert args[task]['Campaign']['UseAutoSearch']['value'] is False
        assert args[task]['StopCondition']['ThenTask'].get('display') != 'hide'
    for task in ('Event', 'Event2'):
        assert args[task]['EventCompletion']['CollectRewards']['value'] is True
        assert args[task]['EventCompletion']['CompleteStory']['value'] is False
        assert args[task]['EventCompletion']['Audit']['value'] is False
    assert args['General']['Retirement']['CommonDuplicates']['value'] is False
    assert ManualConfig.SCHEDULER_PRIORITY.index('EventExtra') < ManualConfig.SCHEDULER_PRIORITY.index('EventStars')


def test_template_keeps_each_jobs_fleet_settings_separate():
    with open('config/template.json', encoding='utf-8') as stream:
        template = json.load(stream)
    template['CampaignStars']['Fleet']['Fleet1'] = 3
    assert template['EventStars']['Fleet']['Fleet1'] == 1
    assert template['EventExtra']['Fleet']['Fleet1'] == 1
