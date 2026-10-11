"""Server-authoritative certificate regressions; all persistence is mocked."""
import json
import unittest
from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.quiz_bank import get_quiz, missing_required_sections, LINEAR_ALGEBRA_REQUIRED_SECTIONS
from routers import quiz, certificates

QUIZ_ID = 'quiz-linear-algebra'
BANK = get_quiz(QUIZ_ID)
COMPLETE = {'completedSections': dict.fromkeys(LINEAR_ALGEBRA_REQUIRED_SECTIONS, True)}

def request(body=None, quiz_id=QUIZ_ID):
    return SimpleNamespace(path_params={'quiz_id': quiz_id}, json=AsyncMock(return_value=body or {}))

def data(response):
    return json.loads(response.body)

class BankTests(unittest.TestCase):
    def test_difficulty_ratio_and_six_topic_coverage(self):
        questions = BANK['questions']
        self.assertEqual(len(questions), 99)
        self.assertEqual(Counter(q['difficulty'] for q in questions), {'Easy': 63, 'Medium': 18, 'Hard': 18})
        expansion = questions[66:]
        self.assertEqual(len({q['topic'] for q in expansion}), 6)
        for topic in {q['topic'] for q in expansion}:
            levels = Counter(q['difficulty'] for q in expansion if q['topic'] == topic)
            self.assertEqual(levels['Medium'], 1)
            self.assertEqual(levels['Hard'], 1)
        for q in expansion:
            self.assertEqual(len(q['options']), 4)
            self.assertEqual(len(set(q['options'])), 4)
            self.assertIn(q['correct'], range(4))
            self.assertTrue(q['explanation'])
        self.assertEqual(len({q['q'] for q in questions}), 99)

    def test_all_24_sections_required_and_truthy_strings_rejected(self):
        self.assertEqual(len(set(LINEAR_ALGEBRA_REQUIRED_SECTIONS)), 24)
        self.assertEqual(missing_required_sections(BANK, COMPLETE), [])
        self.assertEqual(len(missing_required_sections(BANK, None)), 24)
        flags = dict(COMPLETE['completedSections'])
        flags['la-modern-applications-2'] = 'true'
        self.assertEqual(missing_required_sections(BANK, {'completedSections': flags}), ['la-modern-applications-2'])

class EndpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patches = [patch.object(quiz, 'require_user', return_value=7),
                        patch.object(certificates, 'require_user', return_value=7)]
        for item in self.patches: item.start()
        self.addCleanup(patch.stopall)
        self.progress = patch.object(quiz.storage, 'get_progress', new=AsyncMock(return_value=COMPLETE)).start()
        self.save = patch.object(quiz.storage, 'save_quiz_score', new=AsyncMock()).start()
        self.history = patch.object(quiz.storage, 'record_quiz_attempt', new=AsyncMock()).start()
        patch.object(certificates.storage, 'get_user_profile', new=AsyncMock(return_value={'username': 'test'})).start()
        self.scores = patch.object(certificates.storage, 'list_quiz_scores', new=AsyncMock(return_value={})).start()
        self.cert = patch.object(certificates.storage, 'save_certificate_record', new=AsyncMock(return_value={'cert_id': 'test'})).start()
        patch.object(certificates.qr_utils, 'generate_qr_svg', return_value='svg').start()
        patch.object(certificates.qr_utils, 'generate_qr_png_data_uri', return_value='png').start()

    async def test_missing_sections_block_start_and_submit(self):
        self.progress.return_value = {'completedSections': {}}
        for endpoint in (quiz.start_quiz, quiz.submit_quiz):
            response = await endpoint(request())
            self.assertEqual(response.status_code, 403)
            self.assertEqual(len(data(response)['missing_sections']), 24)
        self.save.assert_not_awaited()

    async def test_start_has_99_shuffled_questions_without_answers(self):
        response = await quiz.start_quiz(request())
        self.assertEqual(response.status_code, 200)
        result = data(response)
        self.assertEqual(len(result['questions']), 99)
        self.assertTrue(all(set(q) == {'index', 'q', 'options', 'topic', 'difficulty'} for q in result['questions']))
        payload, error = quiz._decode_attempt(result['attempt_token'])
        self.assertIsNone(error)
        self.assertEqual(sorted(payload['order']), list(range(99)))
        for permutation in payload['opt_perm']: self.assertEqual(sorted(permutation), [0, 1, 2, 3])

    def submission(self, correct_count):
        # Deliberately reverse both orders to exercise server unshuffling.
        order = list(reversed(range(99)))
        permutations = [[3, 2, 1, 0] for _ in order]
        answers = [permutations[i].index(BANK['questions'][j]['correct']) if i < correct_count else None
                   for i, j in enumerate(order)]
        token = quiz._sign_attempt(7, QUIZ_ID, order, permutations, 300)
        return {'attempt_token': token, 'answers': answers, 'score': 99, 'passed': True}

    async def test_exact_threshold_79_of_99_fails_without_misleading_integer_rounding(self):
        response = await quiz.submit_quiz(request(self.submission(79)))
        result = data(response)
        self.assertEqual((result['score'], result['pct'], result['passed']), (79, 79.8, False))
        self.save.assert_awaited_once_with(7, QUIZ_ID, 79, 99)
        self.history.assert_awaited_once_with(7, QUIZ_ID, 79, 99, False)

    async def test_80_of_99_passes_and_server_review_matches(self):
        response = await quiz.submit_quiz(request(self.submission(80)))
        result = data(response)
        self.assertTrue(result['passed'])
        self.assertEqual(sum(q['correct'] for q in result['review']), 80)
        self.assertIsNone(result['review'][-1]['your_answer'])

    async def test_invalid_answers_and_tampered_tokens_rejected(self):
        for bad in (True, -1, 4, '1'):
            body = self.submission(0); body['answers'][0] = bad
            self.assertEqual((await quiz.submit_quiz(request(body))).status_code, 400)
        body = self.submission(0); body['attempt_token'] += 'tampered'
        self.assertEqual((await quiz.submit_quiz(request(body))).status_code, 400)
        self.save.assert_not_awaited()

    async def test_other_user_token_rejected(self):
        body = self.submission(0)
        with patch.object(quiz, 'require_user', return_value=8):
            self.assertEqual((await quiz.submit_quiz(request(body))).status_code, 403)

    async def test_client_cannot_post_a_certificate_score(self):
        response = await quiz.save_score(request({'quiz_id': QUIZ_ID, 'score': 99, 'total': 99}))
        self.assertEqual(response.status_code, 403)
        self.save.assert_not_awaited()

    async def test_issuance_cannot_bypass_quiz_or_lower_threshold(self):
        body = {'course_id': 'linear-algebra', 'course_title': 'Forged',
                'quiz_id': 'another-quiz', 'min_quiz_score': 1}
        self.scores.return_value = {QUIZ_ID: {'score': 79, 'total': 99}, 'another-quiz': {'score': 1, 'total': 1}}
        self.assertEqual((await certificates.generate_certificate(request(body))).status_code, 403)
        del body['quiz_id']; del body['min_quiz_score']
        self.assertEqual((await certificates.generate_certificate(request(body))).status_code, 403)
        self.cert.assert_not_awaited()

    async def test_issuance_requires_sections_even_with_passing_score(self):
        self.scores.return_value = {QUIZ_ID: {'score': 99, 'total': 99}}
        self.progress.return_value = {'completedSections': {}}
        response = await certificates.generate_certificate(request({'course_id': 'linear-algebra', 'course_title': 'Linear Algebra'}))
        self.assertEqual(response.status_code, 403)
        self.cert.assert_not_awaited()

    async def test_passing_issuance_uses_canonical_title_and_score(self):
        self.scores.return_value = {QUIZ_ID: {'score': 80, 'total': 99}}
        response = await certificates.generate_certificate(request({'course_id': 'linear-algebra', 'course_title': 'Changed'}))
        self.assertEqual(response.status_code, 201)
        self.assertEqual(data(response)['score'], 80)
        self.assertEqual(self.cert.await_args.args[3], 'Linear Algebra')

    async def test_other_courses_do_not_gain_la_requirements(self):
        response = await quiz.start_quiz(request(quiz_id='quiz-multivariable-calculus'))
        self.assertEqual(response.status_code, 200)
        self.progress.assert_not_awaited()

    async def test_authentication_required(self):
        with patch.object(quiz, 'require_user', return_value=None), patch.object(certificates, 'require_user', return_value=None):
            for endpoint in (quiz.start_quiz, quiz.submit_quiz, certificates.generate_certificate):
                self.assertEqual((await endpoint(request())).status_code, 401)

if __name__ == '__main__':
    unittest.main()
