"""Explicit stage-tail transitions and immutable inherited obligations.

The existing stage module owns authority, locks and transactions. This helper
only validates/derives transition data; it never writes tickets or invokes a
provider. Frozen history is checked structurally, without rereading old files.
"""
import copy
from pathlib import Path
import time


def fail(api, message):
    raise api.ControlError('stage: '+message, gate_id='stage_gate')


def unique(api, values, label):
    if len(values) != len(set(values)):
        fail(api, label+' must be unique')


def retired_ids(program):
    return {row['source_stage_id'] for revision in program.get('plan_revisions', [])
            for row in (revision.get('consolidation') or {}).get('descriptor', {}).get('mappings', [])}


def replacements(program, stage_id):
    """Follow all replacements, including unbound intermediate stage IDs."""
    current, changed = [stage_id], False
    for revision in program.get('plan_revisions', []):
        mappings = {row['source_stage_id']: row['target_stage_ids']
                    for row in (revision.get('consolidation') or {}).get('descriptor', {}).get('mappings', [])}
        result = []
        for sid in current:
            if sid in mappings:
                changed = True
            for target in mappings.get(sid, [sid]):
                if target not in result:
                    result.append(target)
        current = result
    if not changed:
        return None
    order = [row['id'] for row in program['plan']['stages']]
    return [sid for sid in order if sid in current]


def inherited(program):
    """Ordinary revisions keep obligations; a merge transfers its full snapshot."""
    result = {}
    for revision in program.get('plan_revisions', []):
        snapshot = revision.get('consolidation')
        if snapshot is not None:
            for mapping in snapshot['descriptor']['mappings']:
                result.pop(mapping['source_stage_id'], None)
            result.update(copy.deepcopy(snapshot['targets']))
    return result


def validate_transition(api, previous, proposed, accepted, descriptor, *, retired=()):
    problems = api.validate_against(descriptor, api.SCHEMA_DIR/'stage-consolidation.schema.json', 'stage_consolidation_schema')
    problems += api.validate_against(proposed, api.SCHEMA_DIR/'stage-program.schema.json', 'stage_plan_schema')
    if problems:
        fail(api, 'invalid consolidation schema: '+str(problems))
    if descriptor['old_plan_digest'] != api.canonical_digest(previous):
        fail(api, 'consolidation source plan digest differs')
    if any(previous[key] != proposed[key] for key in ('schema_version', 'program_id', 'project_root')):
        fail(api, 'consolidation cannot change program/project identity')
    if set(previous['authorized_actions']) != set(proposed['authorized_actions']):
        fail(api, 'consolidation cannot change authorized actions')
    unique(api, proposed['authorized_actions'], 'authorized actions')
    old_ids = [row['id'] for row in previous['stages']]
    new_ids = [row['id'] for row in proposed['stages']]
    unique(api, new_ids, 'stage IDs')
    count = len(accepted)
    if old_ids[:count] != accepted or proposed['stages'][:count] != previous['stages'][:count]:
        fail(api, 'consolidation must preserve the exact accepted prefix')
    tail = new_ids[count:]
    if not tail or set(tail) & (set(old_ids) | set(retired)):
        fail(api, 'consolidation targets must be fresh stage IDs')
    for row in proposed['stages']:
        unique(api, row['required_actions'], 'stage actions')
        if not set(row['required_actions']).issubset(proposed['authorized_actions']):
            fail(api, 'consolidation stage actions exceed authority')
    mappings = descriptor['mappings']
    if [row['source_stage_id'] for row in mappings] != old_ids[count:]:
        fail(api, 'consolidation must map the complete unaccepted tail in order')
    sources = descriptor['sources']
    unique(api, [row['id'] for row in sources], 'source IDs')
    unique(api, [row['path'] for row in sources], 'source paths')
    definitions = {}
    for row in sources:
        unique(api, row['item_ids'], 'source obligation IDs')
        definitions[row['id']] = set(row['item_ids'])
    covered, targets = set(), set()
    stages = {row['id']: row for row in proposed['stages']}
    for old, mapping in zip(previous['stages'][count:], mappings):
        assigned = mapping['target_stage_ids']
        unique(api, assigned, 'mapped target IDs')
        if not set(assigned).issubset(tail) or assigned != [sid for sid in tail if sid in assigned]:
            fail(api, 'mapped targets must follow the proposed tail order')
        if 'contract_target' in mapping and mapping['contract_target'] not in assigned:
            fail(api, 'contract target must be an assigned target')
        actions = iter(action for sid in assigned for action in stages[sid]['required_actions'])
        if not all(any(action == expected for action in actions) for expected in old['required_actions']):
            fail(api, 'consolidation cannot delete or reorder source actions')
        targets.update(assigned)
        for obligation in mapping['obligations']:
            source = obligation['source_id']
            unique(api, obligation['item_ids'], 'mapped obligation IDs')
            if source not in definitions or not set(obligation['item_ids']).issubset(definitions[source]):
                fail(api, 'unknown source obligation')
            covered.update((source, item) for item in obligation['item_ids'])
    if targets != set(tail) or covered != {(source, item) for source, ids in definitions.items() for item in ids}:
        fail(api, 'every target and declared source obligation must be covered')


def _compatible(api, contracts):
    """Reject incompatible conjunctions instead of silently choosing a baseline."""
    profiles, baselines, items, metrics, visual = set(), set(), {}, {}, None
    for entry in contracts:
        contract = entry['contract']
        if contract.get('profile') is not None:
            profiles.add(contract['profile'])
        if contract.get('baseline_ref') is not None:
            baselines.add(contract['baseline_ref'])
        evidence = contract.get('evidence_contract') or {}
        for row in evidence.get('required_items') or []:
            signature = {k: row.get(k) for k in ('kind', 'method', 'layer', 'consumer', 'scenario', 'expected_mode')}
            if row['id'] in items and items[row['id']] != signature:
                fail(api, 'incompatible inherited evidence item; assign different contract targets')
            items[row['id']] = signature
        for row in contract.get('required_metrics') or []:
            key = tuple(row[k] for k in ('name', 'layer', 'consumer', 'scenario'))
            signature = row['operator'], row['unit']
            if key in metrics and metrics[key] != signature:
                fail(api, 'incompatible inherited metric')
            metrics[key] = signature
        proposed = evidence.get('visual')
        if proposed is not None:
            if visual is not None and visual['sample_fractions'] != proposed['sample_fractions']:
                fail(api, 'incompatible inherited visual sampling')
            visual = proposed
    if len(profiles) > 1 or len(baselines) > 1:
        fail(api, 'incompatible inherited profile/baseline_ref; assign different contract targets')


def _append_unique(rows, row, api):
    identity = api.canonical_digest(row)
    if not any(api.canonical_digest(old) == identity for old in rows):
        rows.append(copy.deepcopy(row))


def build_snapshot(api, program, proposed, accepted, descriptor, **access):
    validate_transition(api, program['plan'], proposed, accepted, descriptor, retired=retired_ids(program))
    bundle = api.verification_evidence_module().bundle_module()
    limits = bundle.DEFAULT_LIMITS
    started, total, sources = time.monotonic(), 0, {}
    for source in descriptor['sources']:
        if time.monotonic()-started > limits['max_elapsed_seconds']:
            fail(api, 'consolidation source read budget exceeded')
        path = Path(source['path'])
        if not path.is_absolute():
            fail(api, 'consolidation source path must be explicit and absolute')
        try:
            reader = bundle.Reader(path.parent)
            raw = reader.read(str(path), source['sha256'])
            reader.finish()
        except (ValueError, OSError) as exc:
            fail(api, 'consolidation source invalid: '+str(exc))
        total += len(raw)
        if total > limits['max_total_bytes']:
            fail(api, 'consolidation source total byte budget exceeded')
        sources[source['id']] = dict(source, bytes=len(raw))
    previous = inherited(program)
    targets = {row['id']: dict(contracts=[], documents=[]) for row in proposed['stages'][len(accepted):]}
    for mapping in descriptor['mappings']:
        sid = mapping['source_stage_id']
        ancestor = previous.get(sid, dict(contracts=[], documents=[]))
        contracts = copy.deepcopy(ancestor['contracts'])
        reference = program['bindings'].get(sid)
        if reference is not None:
            # Read the real protected child, never a caller-supplied replacement.
            metadata = api.stage_module()._verified(reference['control_root'], **access)[0]
            contract = metadata.get('verification_contract')
            if contract:
                _append_unique(contracts, dict(origin_stage_id=sid, contract=contract,
                    contract_digest=api.canonical_digest(contract)), api)
        if contracts:
            target = mapping.get('contract_target')
            if target not in mapping['target_stage_ids']:
                fail(api, 'observed/inherited contract requires an explicit contract target')
            for contract in contracts:
                _append_unique(targets[target]['contracts'], contract, api)
        documents = copy.deepcopy(ancestor['documents'])
        for obligation in mapping['obligations']:
            source = sources[obligation['source_id']]
            _append_unique(documents, dict(origin_stage_id=sid, source=source,
                item_ids=obligation['item_ids']), api)
        for target in mapping['target_stage_ids']:
            for document in documents:
                _append_unique(targets[target]['documents'], document, api)
    for target in targets.values():
        _compatible(api, target['contracts'])
    return dict(version=1, descriptor=copy.deepcopy(descriptor), sources=list(sources.values()), targets=targets)


def validate_snapshot(api, snapshot, previous, proposed, accepted, *, retired=()):
    """History proof is structural; external documents are not re-signed later."""
    if not isinstance(snapshot, dict) or set(snapshot) != {'version', 'descriptor', 'sources', 'targets'} or snapshot['version'] != 1:
        fail(api, 'invalid frozen consolidation snapshot')
    validate_transition(api, previous, proposed, accepted, snapshot['descriptor'], retired=retired)
    expected = {row['id'] for row in proposed['stages'][len(accepted):]}
    if set(snapshot['targets']) != expected:
        fail(api, 'frozen consolidation targets differ')
    declarations = snapshot['descriptor']['sources']
    if len(snapshot['sources']) != len(declarations):
        fail(api, 'frozen source count differs')
    for original, frozen in zip(declarations, snapshot['sources']):
        if set(frozen) != set(original) | {'bytes'} or any(frozen[k] != v for k, v in original.items()) or type(frozen['bytes']) is not int or frozen['bytes'] < 0:
            fail(api, 'invalid frozen source identity')
    for obligations in snapshot['targets'].values():
        if set(obligations) != {'contracts', 'documents'} or not isinstance(obligations['contracts'], list) or not isinstance(obligations['documents'], list):
            fail(api, 'invalid inherited obligation lists')
        for contract in obligations['contracts']:
            if set(contract) != {'origin_stage_id', 'contract', 'contract_digest'} or api.canonical_digest(contract['contract']) != contract['contract_digest']:
                fail(api, 'invalid frozen contract identity')
        _compatible(api, obligations['contracts'])


def require_contract(api, program, stage_id, proposed):
    obligations = inherited(program).get(stage_id)
    if obligations is None:
        return
    for entry in obligations['contracts']:
        old = entry['contract']
        for key in ('profile', 'baseline_ref'):
            if old.get(key) is not None and proposed.get(key) != old[key]:
                fail(api, 'inherited '+key+' cannot be deleted or replaced')
        api.stage_module().require_contract_strength(old, proposed)
