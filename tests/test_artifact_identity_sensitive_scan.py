"""Proposed generic regression tests; executes only the pure scanner."""
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('identity_workflow_lint', ROOT/'tools/lint_workflow_contract.py')
lint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lint)


def descriptors():
    return [{'path': 'build/component_'+str(i)+'/'+'nested/'*8+'payload.bin', 'sha256': 'a'*64} for i in range(24)]


def identity():
    return {'candidate_id': 'b'*64, 'identities': descriptors(), 'scope': 'Read-only artifact descriptors'}


class ArtifactIdentitySensitiveScanTests(unittest.TestCase):
    def check_identity(self, value):
        return lint.scan_sensitive({'artifact_identity': value}, '$.verification_runs[0]')

    def test_plain_short_identity_compatible(self):
        self.assertFalse(self.check_identity('sha256:'+'a'*64))

    def test_long_json_object_scanned_as_structure(self):
        value=json.dumps(identity())
        self.assertGreater(len(value),lint.MAX_EVIDENCE_VALUE_CHARS)
        self.assertFalse(self.check_identity(value))

    def test_long_json_array_scanned_as_structure(self):
        value=json.dumps(descriptors())
        self.assertGreater(len(value),lint.MAX_EVIDENCE_VALUE_CHARS)
        self.assertFalse(self.check_identity(value))

    def test_native_structure_keeps_existing_scan(self):
        self.assertFalse(self.check_identity(identity()))

    def test_non_json_long_text_rejected(self):
        self.assertTrue(self.check_identity('x'*(lint.MAX_EVIDENCE_VALUE_CHARS+1)))

    def test_other_fields_do_not_gain_json_exemption(self):
        self.assertTrue(lint.scan_sensitive({'evidence':json.dumps(identity())}))

    def test_long_json_leaf_rejected(self):
        self.assertTrue(self.check_identity(json.dumps({'description':'x'*(lint.MAX_EVIDENCE_VALUE_CHARS+1)})))

    def test_credential_key_still_rejected(self):
        self.assertTrue(self.check_identity(json.dumps({'password':'invalid-test-value','identities':descriptors()})))

    def test_credential_value_still_rejected(self):
        self.assertTrue(self.check_identity(json.dumps({'description':'Bearer invalid-test-value','identities':descriptors()})))

    def test_encoded_credential_key_is_scanned_after_decode(self):
        self.assertTrue(self.check_identity('{"ap\\u0069_key":"invalid-test-value"}'))

    def test_raw_keyword_not_hidden_by_duplicate_key(self):
        text='{"description":"Bearer invalid-test-value","description":"safe","identities":'+json.dumps(descriptors())+'}'
        self.assertTrue(self.check_identity(text))

    def test_malformed_long_json_not_exempted(self):
        self.assertTrue(self.check_identity(json.dumps(identity())[:-1]))

    def test_json_scalar_not_exempted(self):
        self.assertTrue(self.check_identity(json.dumps('x'*(lint.MAX_EVIDENCE_VALUE_CHARS+1))))

    def test_nonfinite_json_not_exempted(self):
        text='{"not_finite":NaN,"identities":'+json.dumps(descriptors())+'}'
        self.assertTrue(self.check_identity(text))

    def test_excessive_json_nesting_fails_without_crash(self):
        self.assertTrue(self.check_identity('['*1200+'0'+']'*1200))

    def test_short_legacy_description_compatible(self):
        self.assertFalse(self.check_identity('manual artifact description'))


if __name__=='__main__':
    unittest.main()
