"""Presentation copy tests: no model inference and no semantic paraphrasing."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

import pytest

from backend.analyzer import EvidenceFrame, Finding
from backend.analyzer.vlm import build_prompt
from backend.api.display_text import ANALYSIS_ERROR, normalize_observation
from backend.api.result import format_result
from backend.api.rules import load_rules


DATA = Path(__file__).resolve().parents[1] / "backend/data"
TYPE_IDS = {
    'cash_split_transfer', 'identity_check_not_visible', 'cash_left_unattended', 'key_left_in_safe',
    'cash_bag_handling', 'items_left_unattended', 'cash_bag_received_without_visible_check',
    'third_person_at_counter', 'safe_door_open', 'documents_left_unattended', 'workstation_left_active',
    'uncontrolled_access', 'cash_room_door_left_open', 'customer_leaves_during_count',
    'cash_not_secured_between_clients', 'personal_items_on_counter', 'banknotes_disordered', 'drinks_at_workstation',
}


@pytest.mark.parametrize('before,after', [
    ('  Возможно,\nпередача\u00a0наличности не завершена .  ', 'Возможно, передача наличных денежных средств не завершена.'),
    ('ПЕРЕДАЧА НАЛИЧНОСТИ НЕ НАБЛЮДАЕТСЯ.', 'ПЕРЕДАЧА НАЛИЧНЫХ ДЕНЕЖНЫХ СРЕДСТВ НЕ НАБЛЮДАЕТСЯ.'),
    ('Не видно передачи наличности.', 'Не видно передачи наличных денежных средств.'),
    ('Действия с наличностью,вероятно, продолжаются.', 'Действия с наличными денежными средствами, вероятно, продолжаются.'),
    ('Сеи\u0306ф закрыт; е\u0308мкость не различима.', 'Сейф закрыт; ёмкость не различима.'),
    ('Возможно, проверка не наблюдаеться; банкноты находяться на столе.', 'Возможно, проверка не наблюдается; банкноты находятся на столе.'),
    ('', ''), ('\n \t\u00a0', ''),
])
def test_precise_russian_typography_and_inflected_phrases(before, after):
    assert normalize_observation(before) == after
    assert normalize_observation(after) == after


@pytest.mark.parametrize('text', [
    'Сейф открыт.', 'Сейф не оставлен открытым.', 'Не установлено, остались ли деньги без присмотра.',
    'Кассир не ушёл. Возможно, проверка выполнена вне кадра.',
    'Третий человек может быть сотрудником; полномочия не установлены.',
    'Банкноты не смешиваются. Идентификация не наблюдается.',
    'Наличность осталась на столе.', 'Отсутствие наличности не установлено.',
    'Неизвестна сумма денег. Денежный перевод не виден.',
    'Непередача наличности не установлена.', 'передача наличностиXYZ не установлена.',
    'https://example.test/a?x=1.5 ID=X-1 12,50 KZT 00:12.500',
    'Наличные, документы и ключ находятся на столе — дальнейшее действие неизвестно.',
    'Текст қазақша: ә, ғ, қ, ң, ө, ұ, ү, һ, і. Emoji 🔑.',
])
def test_no_mass_replacement_or_changes_to_roles_actions_numbers_uncertainty(text):
    assert normalize_observation(text) == text


def test_catalog_ids_and_model_descriptions_are_unchanged():
    catalog = json.loads((DATA / 'violation_types.json').read_text(encoding='utf-8'))
    assert set(catalog) == TYPE_IDS
    assert all(set(entry) == {'name', 'description'} for entry in catalog.values())
    descriptions = {key: info['description'] for key, info in catalog.items()}
    digest = hashlib.sha256(json.dumps(descriptions, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    assert digest == '11f4d3ab9a29b1714b56db35e609edf9ee780c5081dbc8857fd3c414b8bdc087'
    assert catalog['key_left_in_safe']['name'] == 'Ключ оставлен в замке сейфа'
    assert 'наличности' not in ' '.join(info['name'] for info in catalog.values()).lower()
    assert 'оставлен' not in catalog['safe_door_open']['name'].lower()


@pytest.mark.parametrize('version', ['baseline', 'sequence_v1', 'frame_bound_v2'])
def test_display_names_never_enter_vlm_prompt(version):
    catalog = json.loads((DATA / 'violation_types.json').read_text(encoding='utf-8'))
    renamed = {key: {**info, 'name': 'Другое название для отображения'} for key, info in catalog.items()}
    policy = json.loads((DATA / 'violation_type_policy.json').read_text(encoding='utf-8'))
    assert build_prompt(catalog, [0., 1., 2., 3.], policy, version=version) == build_prompt(renamed, [0., 1., 2., 3.], policy, version=version)


def test_normalization_changes_only_display_and_preserves_private_original(tmp_path):
    raw = '  Возможно, передача наличности не завершена .\n'
    finding = Finding('cash_split_transfer', 12.3456789, raw, .812345,
                      [EvidenceFrame(12.3456789, b'\xff\xd8\xff\xd9')])
    before = asdict(finding)
    private = tmp_path / 'diagnostics/text/job.json'
    output = format_result([finding], DATA, diagnostics_path=private)['violations'][0]
    assert asdict(finding) == before
    assert output['timecode_sec'] == 12.3456789
    assert output['evidence_frames'] == [{'timestamp_sec': 12.3456789, 'image_base64': '/9j/2Q=='}]
    assert set(output) == {'type', 'description', 'timecode_sec', 'evidence_frames'}
    assert raw not in output['description']
    assert 'Возможно, передача наличных денежных средств не завершена.' in output['description']
    saved = json.loads(private.read_text(encoding='utf-8'))[0]
    assert saved['original_description'] == raw and saved['violation_type'] == finding.violation_type
    assert saved['display_description'] == normalize_observation(raw)


@pytest.mark.parametrize('description', ['', ' \t\n\u00a0'])
def test_empty_observation_remains_an_invalid_finding(description):
    finding = dict(violation_type='cash_split_transfer', description=description, confidence=.8,
                   timecode_sec=1., evidence_frames=[dict(timestamp_sec=1., jpeg_bytes=b'\xff\xd8\xff\xd9')])
    with pytest.raises(ValueError, match='description'):
        format_result([finding], DATA)


def test_diagnostics_storage_failure_never_changes_a_valid_result(tmp_path, caplog):
    blocker = tmp_path / 'not-a-directory'
    blocker.write_text('synthetic fixture', encoding='utf-8')
    finding = Finding('safe_door_open', 1., 'Сейф открыт.', .8, [EvidenceFrame(1., b'\xff\xd8\xff\xd9')])
    result = format_result([finding], DATA, diagnostics_path=blocker / 'job.json')
    assert result['verdict'] == 'violation'
    assert 'private description diagnostics' in caplog.text and finding.description not in caplog.text


def test_reference_statuses_and_verified_rules_are_preserved():
    assert all(value == {'status': 'needs_review'} for value in load_rules(DATA).values())
    assert ANALYSIS_ERROR == 'Не удалось завершить анализ видеозаписи. Повторите попытку позже.'


def test_original_diagnostics_are_private_and_browser_json_has_only_display_copy(tmp_path):
    from fastapi.testclient import TestClient
    from backend.api.app import create_app
    from backend.api.config import Settings
    from backend.api.jobs import JobStore

    raw = '  Передача наличности не наблюдаеться .\n'
    finding = Finding('cash_split_transfer', 1., raw, .8, [EvidenceFrame(1., b'\xff\xd8\xff\xd9')])
    store = JobStore(tmp_path / 'jobs.sqlite3')
    job_id = store.create(tmp_path / 'synthetic-unused.mov')
    private = tmp_path / 'diagnostics/text' / f'{job_id}.json'
    store.finish(job_id, format_result([finding], DATA, diagnostics_path=private))
    settings = Settings(api_key='synthetic-copy-key', data_dir=tmp_path, frontend_dist=Path('frontend'), start_worker=False)
    with TestClient(create_app(settings)) as site:
        csrf = site.get('/ui-api/v1/session').json()['csrf_token']
        response = site.get(f'/ui-api/v1/jobs/{job_id}', headers={'X-CSRF-Token': csrf})
        assert response.status_code == 200
        assert 'передача наличности' not in response.text.lower()
        assert 'original_description' not in response.text
        assert site.get(f'/diagnostics/text/{job_id}.json').status_code == 404
    assert json.loads(private.read_text(encoding='utf-8'))[0]['original_description'] == raw
